"""주기 조회 -> 판정 -> 프리셋별 보드 갱신.

FastAPI lifespan 에서 asyncio 태스크로 돈다.

비용 모델이 이 파일의 핵심이다:

  - 호출은 **정류장당 1건**. 프리셋 둘이 같은 정류장을 쓰면 호출은 한 번이다
  - **지금 보고 있는 프리셋만** 폴링한다. 저장해둔 개수는 공짜다
  - 기본 프리셋은 아무도 안 봐도 항상 폴링한다 (키오스크가 늘 띄우고 있고,
    여기서 빠지면 관측 로그 수집이 멈춘다)
  - 주기는 활성 정류장 수에서 역산한다 (config.intervals_for)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime

import httpx

from trueeta.clock import wait_for_sync
from trueeta.config import BoardConfig, Preset, Settings, Stop
from trueeta.gbis import GbisClient, GbisError
from trueeta.gbis.envelope import as_list, find_key
from trueeta.gbis.parser import parse_arrivals
from trueeta.judge import judge_arrival
from trueeta.judge_absence import (
    MISSING, OFF, UNSEEN, BVerdict, RouteSnapshot, default_min_travel, judge_absence,
)
from trueeta.models import Board, BoardEntry, Status
from trueeta.presets import PresetStore
from trueeta.quota import QuotaCounter
from trueeta.routeinfo import RouteInfoCache
from trueeta.state import BoardState
from trueeta.storage import Observation, ObservationLog, RouteCycle
from trueeta.window import in_window, parse_hhmm

log = logging.getLogger("trueeta.poller")


def _describe(exc: Exception) -> str:
    """타임아웃은 str() 이 비어 있어 로그에 아무것도 안 남는다."""
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class RouteState:
    """한 정류장의 한 노선 판정 결과.

    프리셋이 아니라 (정류장, 노선) 단위다. 프리셋 둘이 같은 정류장을 봐도
    판정은 한 번만 하고 결과를 나눠 쓴다.
    """

    dest_name: str
    status: Status
    eta_sec: int | None
    second_eta_sec: int | None
    second_status: Status | None
    estimated_wait: bool
    at_standing: bool
    wait_min_sec: int | None = None
    wait_expected_sec: int | None = None
    wait_overdue: bool = False


def with_absence(judged: RouteState, verdict: BVerdict | None) -> RouteState:
    """A안이 '차 없음' 이라고 한 줄에만 B안을 덧씌운다.

    A안의 '차 없음' 은 사실상 회차지 대기다 — GBIS 는 회차지를 떠난 차만
    보여준다. B안은 그걸 '최소 N분 · 보통 M분' 으로 읽는다. 차가 보이거나
    API 가 flag=WAIT 로 확정해 준 경우는 A안이 더 정확하므로 건드리지 않는다.
    """
    if verdict is None or judged.status != Status.NO_BUS:
        return judged
    if verdict.status == UNSEEN:
        return replace(
            judged,
            wait_min_sec=verdict.min_sec,
            wait_expected_sec=verdict.expected_sec,
            wait_overdue=verdict.overdue,
        )
    if verdict.status == OFF:
        # 노선별 막차(+N+10분 여유)가 지났다. 전체 운행창(00:10)보다 이르다
        return replace(judged, status=Status.ENDED)
    return judged


def wanted_routes(presets: list[Preset]) -> dict[str, set[str]]:
    """활성 프리셋들이 원하는 {정류장: 노선들}. 정류장이 겹치면 합친다."""
    wanted: dict[str, set[str]] = {}
    for preset in presets:
        for stop in preset.stops:
            wanted.setdefault(stop.station_id, set()).update(stop.routes)
    return wanted


@dataclass
class BContext:
    """B안 판정과 route_cycles 기록에 필요한 것들. 한 사이클 동안 쓴다."""

    now: datetime
    route_info: RouteInfoCache | None = None
    learned: dict[tuple[str, str], int] = field(default_factory=dict)
    cycles: list[RouteCycle] = field(default_factory=list)
    #: 정류장 -> 화면에 띄울 설정 경고 (응답에 노선이 계속 없음)
    warnings: dict[str, str] = field(default_factory=dict)


#: 응답에 노선이 이만큼 연속으로 없으면 설정 오류로 보고 경고한다.
#: 한두 번은 GBIS 쪽 일시 오류일 수 있다.
MISSING_WARN_AFTER = 3


def record_cycle(
    station_id: str,
    route_name: str,
    arrival,
    a_status: str,
    state: BoardState,
    bctx: BContext,
) -> BVerdict:
    """(정류장, 노선) 한 줄을 B안으로 판정해 남기고, 판정을 돌려준다.
    차가 없어도 남긴다.

    arrival 이 None 이면 응답에 그 노선 자체가 없었던 것이다.
    """
    key = (station_id, route_name)
    n = len(arrival.vehicles) if arrival else 0
    first = arrival.vehicles[0] if arrival and arrival.vehicles else None
    listed = arrival is not None

    if listed:
        state.missing.pop(key, None)
        since = state.absences.update(key, n, bctx.now)
    else:
        # 노선이 응답에 아예 없다. '차 없음' 이 아니므로 차 없음 추적은 건드리지 않는다
        since = None
        count = state.missing.get(key, 0) + 1
        state.missing[key] = count
        if count >= MISSING_WARN_AFTER:
            msg = f"{route_name}번이 이 정류장 응답에 없음 — 설정 확인"
            bctx.warnings[station_id] = (
                bctx.warnings[station_id] + "; " + msg if station_id in bctx.warnings else msg
            )
            if count == MISSING_WARN_AFTER:
                log.warning("%s: %s (%d사이클 연속)", station_id, msg, count)

    order = arrival.sta_order if arrival and arrival.sta_order > 0 else None
    turn = arrival.turn_seq if arrival and arrival.turn_seq > 0 else None
    route_id = arrival.route_id if arrival else ""

    info = bctx.route_info.get(route_id) if (bctx.route_info and route_id) else None
    after_turn = bool(order and turn and turn < order)
    travel = bctx.learned.get(key) or default_min_travel(order, turn)
    in_service = (
        info.in_service(bctx.now, after_turn=after_turn, travel_sec=travel) if info else None
    )
    headway = info.headway_sec(bctx.now) if info else None

    verdict = judge_absence(
        RouteSnapshot(
            n_vehicles=n,
            eta1_sec=first.eta_sec if first else None,
            flag=arrival.flag if arrival else "",
            in_service=in_service,
            absent_for_sec=(bctx.now - since).total_seconds() if since else None,
            min_travel_sec=travel,
            headway_sec=headway,
            listed=listed,
        )
    )
    bctx.cycles.append(
        RouteCycle(
            station_id=station_id,
            route_name=route_name,
            route_id=route_id,
            n_vehicles=n,
            eta1_sec=first.eta_sec if first else None,
            loc1=first.location_no if first else None,
            sta_order=order,
            turn_seq=turn,
            flag=arrival.flag if arrival else "",
            in_service=in_service,
            absent_since=since.isoformat(timespec="seconds") if since else None,
            headway_sec=headway,
            a_status=a_status,
            b_status=verdict.status,
            b_min_sec=verdict.min_sec,
            b_expected_sec=verdict.expected_sec,
            b_overdue=verdict.overdue,
        )
    )
    return verdict


def judge_station(
    station_id: str,
    routes: set[str],
    items: list[dict],
    state: BoardState,
    stall_seconds: float,
    alive: set[tuple[str, str, str]],
    observations: list[Observation],
    bctx: BContext | None = None,
) -> dict[tuple[str, str], RouteState]:
    """응답 하나를 판정해 (정류장, 노선) -> RouteState 로 만든다.

    bctx 가 있으면 같은 응답을 B안으로도 판정해 route_cycles 에 남긴다.
    """
    judged: dict[tuple[str, str], RouteState] = {}
    seen: set[str] = set()

    for arrival in parse_arrivals(items):
        if arrival.route_name not in routes:
            continue
        seen.add(arrival.route_name)

        for vehicle in arrival.vehicles:
            state.stalls.observe(station_id, arrival.route_id, vehicle)
            alive.add((station_id, arrival.route_id, vehicle.veh_id))

        first, second = judge_arrival(
            arrival,
            stalled=state.stalls.counts_for(station_id, arrival.route_id),
            stall_seconds=stall_seconds,
        )
        route_state = RouteState(
            dest_name=arrival.dest_name,
            status=first.status,
            eta_sec=first.eta_sec,
            second_eta_sec=second.eta_sec if second else None,
            second_status=second.status if second else None,
            estimated_wait=first.estimated,
            at_standing=first.at_standing,
        )
        if bctx is not None:
            verdict = record_cycle(station_id, arrival.route_name, arrival,
                                   first.status.value, state, bctx)
            route_state = with_absence(route_state, verdict)
        judged[(station_id, arrival.route_name)] = route_state

        for slot, (vehicle, verdict) in enumerate(
            zip(arrival.vehicles, (first, second)), start=1
        ):
            if verdict is None:
                continue
            observations.append(
                Observation(
                    station_id=station_id,
                    route_id=arrival.route_id,
                    route_name=arrival.route_name,
                    slot=slot,
                    veh_id=vehicle.veh_id,
                    plate_no=vehicle.plate_no,
                    flag=arrival.flag,
                    sta_order=arrival.sta_order,
                    turn_seq=arrival.turn_seq,
                    location_no=vehicle.location_no,
                    position=arrival.position_of(vehicle),
                    state_cd=vehicle.state_cd,
                    predict_sec=vehicle.predict_sec,
                    predict_min=vehicle.predict_min,
                    status=verdict.status.value,
                    estimated=verdict.estimated,
                    reason=verdict.reason,
                )
            )

    # 응답에 아예 안 나온 노선도 '차 없음'으로 남긴다
    if bctx is not None:
        for route in sorted(routes - seen):
            record_cycle(station_id, route, None, Status.NO_BUS.value, state, bctx)
    return judged


def fetch_and_judge(
    client: GbisClient,
    wanted: dict[str, set[str]],
    state: BoardState,
    stall_seconds: float,
    log_db: ObservationLog | None = None,
    bctx: BContext | None = None,
) -> tuple[dict[tuple[str, str], RouteState], dict[str, str]]:
    """정류장마다 딱 한 번 호출한다. 실패는 정류장별로 격리한다."""
    judged: dict[tuple[str, str], RouteState] = {}
    errors: dict[str, str] = {}
    alive: set[tuple[str, str, str]] = set()
    observations: list[Observation] = []

    for station_id, routes in wanted.items():
        try:
            response = client.arrivals(station_id)
        except (GbisError, httpx.HTTPError) as exc:
            # 네트워크 타임아웃은 GbisError 가 아니다. 같이 잡지 않으면
            # 한 정류장이 느릴 때 사이클 전체가 날아가 화면이 빈다.
            log.warning("%s 조회 실패: %s", station_id, _describe(exc))
            errors[station_id] = _describe(exc)
            continue

        items = [
            i
            for i in as_list(find_key(response.body, "busArrivalList"))
            if isinstance(i, dict)
        ]
        judged.update(
            judge_station(
                station_id, routes, items, state, stall_seconds, alive,
                observations, bctx,
            )
        )

    # 조회에 성공한 사이클에서만 정리한다. 전부 실패했으면 이력을 날리지 않는다.
    if not errors:
        state.stalls.forget_missing(alive)

    if log_db is not None:
        log_db.write(observations)
        if bctx is not None:
            log_db.write_cycles(bctx.cycles)

    return judged, errors


def board_for_preset(
    preset: Preset,
    judged: dict[tuple[str, str], RouteState],
    errors: dict[str, str],
) -> Board:
    """판정 결과에서 이 프리셋이 보여줄 줄만 뽑는다.

    설정에 적힌 정류장·노선 순서를 그대로 화면 순서로 쓴다.
    응답에 없는 노선도 빈 줄로 남긴다 — 화면에서 줄이 사라지면 안 된다.
    """
    entries: list[BoardEntry] = []
    for stop in preset.stops:
        for route in stop.routes:
            found = judged.get((stop.station_id, route))
            entries.append(
                BoardEntry(
                    stop_name=stop.name,
                    station_id=stop.station_id,
                    route_name=route,
                    dest_name=found.dest_name if found else "",
                    status=found.status if found else Status.NO_BUS,
                    eta_sec=found.eta_sec if found else None,
                    second_eta_sec=found.second_eta_sec if found else None,
                    second_status=found.second_status if found else None,
                    estimated_wait=found.estimated_wait if found else False,
                    at_standing=found.at_standing if found else False,
                    wait_min_sec=found.wait_min_sec if found else None,
                    wait_expected_sec=found.wait_expected_sec if found else None,
                    wait_overdue=found.wait_overdue if found else False,
                )
            )

    mine = [
        f"{stop.name}: {errors[stop.station_id]}"
        for stop in preset.stops
        if stop.station_id in errors
    ]
    return Board(
        entries=tuple(entries),
        updated_at=_now_iso(),
        error="; ".join(mine) if mine else None,
    )


def off_hours_board(preset: Preset) -> Board:
    """운행시간 밖에 내보낼 보드.

    항상 켜두는 화면이라 빈 화면을 띄울 수 없다. API 는 호출하지 않고
    설정에 있는 노선을 '운행종료'로 채워 목록만 유지한다.
    """
    return Board(
        entries=tuple(
            BoardEntry(
                stop_name=stop.name,
                station_id=stop.station_id,
                route_name=route,
                dest_name="",
                status=Status.ENDED,
                eta_sec=None,
                second_eta_sec=None,
                second_status=None,
            )
            for stop in preset.stops
            for route in stop.routes
        ),
        updated_at=_now_iso(),
    )


def resolve_presets(
    names: set[str], available: tuple[Preset, ...], default: Preset
) -> list[Preset]:
    """이름으로 프리셋을 찾는다. 사라진 이름은 조용히 버린다 —
    편집 중에 지워진 프리셋 때문에 사이클이 죽으면 안 된다."""
    by_name = {p.name: p for p in available}
    found = [by_name[n] for n in names if n in by_name]
    if not any(p.name == default.name for p in found):
        found.append(default)
    return found


def run_cycle(
    client: GbisClient,
    config: BoardConfig,
    state: BoardState,
    log_db: ObservationLog | None = None,
    store: PresetStore | None = None,
    route_info: RouteInfoCache | None = None,
    learned: dict[tuple[str, str], int] | None = None,
) -> int:
    """한 사이클. 폴링한 정류장 수를 돌려준다 (다음 주기 계산용)."""
    presets = store.all() if store else config.presets
    default = next((p for p in presets if p.is_default), presets[0])
    state.default_preset = default.name
    active = resolve_presets(state.active_presets(), presets, default)
    wanted = wanted_routes(active)

    bctx = BContext(
        now=datetime.now().astimezone(), route_info=route_info, learned=learned or {}
    )
    judged, errors = fetch_and_judge(
        client, wanted, state, config.stall_seconds, log_db, bctx
    )
    shown = {**bctx.warnings, **errors}   # 조회 실패가 설정 경고보다 우선
    for preset in active:
        state.set(board_for_preset(preset, judged, shown), preset.name)

    # 사이클마다 한 줄. 이게 없어서 잘못된 프리셋으로 이틀을 조용히 날렸다
    # (docs/incident-2026-09-24.md).
    unseen = sum(1 for c in bctx.cycles if c.b_status == "unseen")
    missing = sum(1 for c in bctx.cycles if c.b_status == MISSING)
    log.info(
        "사이클: 정류장 %d곳 · 노선 %d개 · 차 없음 %d%s%s",
        len(wanted), len(bctx.cycles), unseen,
        f" · 노선 누락 {missing}" if missing else "",
        f" · 실패 {len(errors)}" if errors else "",
    )
    return len(wanted)


async def run_poller(
    settings: Settings,
    config: BoardConfig,
    state: BoardState,
    store: PresetStore | None = None,
) -> None:
    """종료될 때까지 주기적으로 보드를 갱신한다."""
    client = GbisClient(
        settings.service_key,
        timeout=settings.timeout,
        quota_path=settings.quota_path,
    )
    state.stalls.ratio_threshold = config.stall_ratio
    log_db = ObservationLog(
        settings.observations_path if config.log_observations else None
    )
    if log_db.enabled:
        log.info("관측 로그: %s", settings.observations_path)

    # B안 준비 — 노선 정보(하루 1회), 재시작 전 '차 없음' 복구, N 학습값
    route_info = RouteInfoCache(
        settings.routeinfo_path, fetch=lambda rid: client.route_info(rid).body
    )
    state.absences.restore(log_db.open_absences())
    learned = log_db.learned_min_travel()
    learned_at = datetime.now()
    if learned:
        log.info("B안 최소시간 학습값: %s", {f"{k[1]}번": v for k, v in learned.items()})

    # 라즈베리파이는 RTC 가 없어 부팅 직후 시계가 틀리다. 그대로 두면
    # 운행시간 판정·관측 ts·쿼터 날짜가 한꺼번에 어긋난다 (clock.py 참고).
    if not await wait_for_sync():
        log.warning(
            "시계 동기화를 확인하지 못했다 — 현재 시각(%s)을 그대로 믿고 진행한다",
            datetime.now().isoformat(timespec="seconds"),
        )

    window_start = parse_hhmm(config.window_start)
    window_end = parse_hhmm(config.window_end)
    peak_start = parse_hhmm(config.peak_start)
    peak_end = parse_hhmm(config.peak_end)
    sleeping = False
    stations = len(config.default_preset.stops)
    # 임시 주기가 남은 호출로 감당되는지 보려고 센다. 세는 건 client 가 한다
    quota = QuotaCounter(settings.quota_path, limit=config.daily_budget)
    boosting = False

    while True:
        now = datetime.now().time()

        if not in_window(now, window_start, window_end):
            # 첫차 전·막차 후. 빈 응답을 받으려고 쿼터를 쓸 이유가 없다.
            if not sleeping:
                log.info(
                    "운행시간 밖 (%s~%s) — 조회 중지",
                    config.window_start, config.window_end,
                )
                for preset in (store.all() if store else config.presets):
                    state.set(off_hours_board(preset), preset.name)
                sleeping = True
            await asyncio.sleep(60)
            continue

        if sleeping:
            log.info("운행시간 진입 — 조회 재개")
            sleeping = False

        try:
            # httpx 동기 호출이라 이벤트 루프를 막지 않게 스레드로 뺀다
            if (datetime.now() - learned_at).total_seconds() > 3600:
                learned = log_db.learned_min_travel()
                learned_at = datetime.now()
            stations = await asyncio.to_thread(
                run_cycle, client, config, state, log_db, store, route_info, learned
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 폴러는 어떤 이유로도 멈추면 안 된다
            log.exception("폴링 실패")
            state.set_error(str(exc))

        peak_sec, far_sec = config.intervals_for(stations)
        interval = peak_sec if in_window(now, peak_start, peak_end) else far_sec

        boost = config.boost_interval(
            datetime.now(), stations, quota.today("arrivals")
        )
        if boost is not None and boost < interval:
            if not boosting:
                log.info(
                    "임시 주기 %d초 (%s 까지, 평소 %d초)",
                    boost, config.boost_until.isoformat(timespec="minutes"), interval,
                )
                boosting = True
            interval = boost
        elif boosting:
            log.info("임시 주기 끝 — 평소 주기 %d초로", interval)
            boosting = False

        await asyncio.sleep(interval)
