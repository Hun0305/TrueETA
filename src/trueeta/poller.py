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
from dataclasses import dataclass
from datetime import datetime

import httpx

from trueeta.config import BoardConfig, Preset, Settings, Stop
from trueeta.gbis import GbisClient, GbisError
from trueeta.gbis.envelope import as_list, find_key
from trueeta.gbis.parser import parse_arrivals
from trueeta.judge import judge_arrival
from trueeta.models import Board, BoardEntry, Status
from trueeta.state import BoardState
from trueeta.storage import Observation, ObservationLog
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


def wanted_routes(presets: list[Preset]) -> dict[str, set[str]]:
    """활성 프리셋들이 원하는 {정류장: 노선들}. 정류장이 겹치면 합친다."""
    wanted: dict[str, set[str]] = {}
    for preset in presets:
        for stop in preset.stops:
            wanted.setdefault(stop.station_id, set()).update(stop.routes)
    return wanted


def judge_station(
    station_id: str,
    routes: set[str],
    items: list[dict],
    state: BoardState,
    stall_seconds: float,
    alive: set[tuple[str, str, str]],
    observations: list[Observation],
) -> dict[tuple[str, str], RouteState]:
    """응답 하나를 판정해 (정류장, 노선) -> RouteState 로 만든다."""
    judged: dict[tuple[str, str], RouteState] = {}

    for arrival in parse_arrivals(items):
        if arrival.route_name not in routes:
            continue

        for vehicle in arrival.vehicles:
            state.stalls.observe(station_id, arrival.route_id, vehicle)
            alive.add((station_id, arrival.route_id, vehicle.veh_id))

        first, second = judge_arrival(
            arrival,
            stalled=state.stalls.counts_for(station_id, arrival.route_id),
            stall_seconds=stall_seconds,
        )
        judged[(station_id, arrival.route_name)] = RouteState(
            dest_name=arrival.dest_name,
            status=first.status,
            eta_sec=first.eta_sec,
            second_eta_sec=second.eta_sec if second else None,
            second_status=second.status if second else None,
            estimated_wait=first.estimated,
            at_standing=first.at_standing,
        )

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
    return judged


def fetch_and_judge(
    client: GbisClient,
    wanted: dict[str, set[str]],
    state: BoardState,
    stall_seconds: float,
    log_db: ObservationLog | None = None,
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
                station_id, routes, items, state, stall_seconds, alive, observations
            )
        )

    # 조회에 성공한 사이클에서만 정리한다. 전부 실패했으면 이력을 날리지 않는다.
    if not errors:
        state.stalls.forget_missing(alive)

    if log_db is not None:
        log_db.write(observations)

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


def run_cycle(
    client: GbisClient,
    config: BoardConfig,
    state: BoardState,
    log_db: ObservationLog | None = None,
) -> int:
    """한 사이클. 폴링한 정류장 수를 돌려준다 (다음 주기 계산용)."""
    active = [config.preset(name) for name in state.active_presets()]
    wanted = wanted_routes(active)

    judged, errors = fetch_and_judge(
        client, wanted, state, config.stall_seconds, log_db
    )
    for preset in active:
        state.set(board_for_preset(preset, judged, errors), preset.name)
    return len(wanted)


async def run_poller(
    settings: Settings, config: BoardConfig, state: BoardState
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

    window_start = parse_hhmm(config.window_start)
    window_end = parse_hhmm(config.window_end)
    peak_start = parse_hhmm(config.peak_start)
    peak_end = parse_hhmm(config.peak_end)
    sleeping = False
    stations = len(config.default_preset.stops)

    while True:
        now = datetime.now().time()

        if not in_window(now, window_start, window_end):
            # 첫차 전·막차 후. 빈 응답을 받으려고 쿼터를 쓸 이유가 없다.
            if not sleeping:
                log.info(
                    "운행시간 밖 (%s~%s) — 조회 중지",
                    config.window_start, config.window_end,
                )
                for preset in config.presets:
                    state.set(off_hours_board(preset), preset.name)
                sleeping = True
            await asyncio.sleep(60)
            continue

        if sleeping:
            log.info("운행시간 진입 — 조회 재개")
            sleeping = False

        try:
            # httpx 동기 호출이라 이벤트 루프를 막지 않게 스레드로 뺀다
            stations = await asyncio.to_thread(run_cycle, client, config, state, log_db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 폴러는 어떤 이유로도 멈추면 안 된다
            log.exception("폴링 실패")
            state.set_error(str(exc))

        peak_sec, far_sec = config.intervals_for(stations)
        peak = in_window(now, peak_start, peak_end)
        await asyncio.sleep(peak_sec if peak else far_sec)
