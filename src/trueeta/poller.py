"""주기 조회 -> 판정 -> 보드 갱신.

FastAPI lifespan 에서 asyncio 태스크로 돈다. 정류장 1곳당 호출 1건.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from trueeta.config import BoardConfig, Settings, Stop
from trueeta.window import in_window, parse_hhmm
from trueeta.gbis import GbisClient, GbisError
from trueeta.gbis.envelope import as_list, find_key
from trueeta.gbis.parser import parse_arrivals
from trueeta.judge import judge_arrival
from trueeta.models import Board, BoardEntry, Status
from trueeta.state import BoardState

log = logging.getLogger("trueeta.poller")


def _entries_for_stop(
    stop: Stop,
    items: list[dict],
    state: BoardState,
    stall_threshold: int,
    alive: set[tuple[str, str, str]] | None = None,
) -> list[BoardEntry]:
    entries: list[BoardEntry] = []

    for arrival in parse_arrivals(items):
        if not stop.wants(arrival.route_name):
            continue

        for vehicle in arrival.vehicles:
            state.stalls.observe(stop.station_id, arrival.route_id, vehicle)
            if alive is not None:
                alive.add((stop.station_id, arrival.route_id, vehicle.veh_id))

        first, second = judge_arrival(
            arrival,
            stalled=state.stalls.counts_for(stop.station_id, arrival.route_id),
            stall_threshold=stall_threshold,
        )
        entries.append(
            BoardEntry(
                stop_name=stop.name,
                station_id=stop.station_id,
                route_name=arrival.route_name,
                dest_name=arrival.dest_name,
                status=first.status,
                eta_sec=first.eta_sec,
                second_eta_sec=second.eta_sec if second else None,
                second_status=second.status if second else None,
                estimated_wait=first.estimated,
            )
        )

    # 설정에 있는데 응답에 없는 노선도 빈 줄로 남긴다 (화면에서 사라지지 않게)
    seen = {e.route_name for e in entries}
    for route in stop.routes:
        if route not in seen:
            entries.append(
                BoardEntry(
                    stop_name=stop.name,
                    station_id=stop.station_id,
                    route_name=route,
                    dest_name="",
                    status=Status.NO_BUS,
                    eta_sec=None,
                    second_eta_sec=None,
                    second_status=None,
                )
            )
    return entries


def build_board(
    client: GbisClient, config: BoardConfig, state: BoardState
) -> Board:
    """한 사이클. 정류장마다 한 번씩 호출한다."""
    entries: list[BoardEntry] = []
    errors: list[str] = []
    # 이번 사이클에 실제로 보인 차량들. 안 보이는 차는 이력에서 지운다.
    alive: set[tuple[str, str, str]] = set()

    for stop in config.stops:
        try:
            response = client.arrivals(stop.station_id)
        except GbisError as exc:
            log.warning("%s 조회 실패: %s", stop.name, exc)
            errors.append(f"{stop.name}: {exc}")
            continue

        items = [
            i for i in as_list(find_key(response.body, "busArrivalList")) if isinstance(i, dict)
        ]
        entries.extend(
            _entries_for_stop(stop, items, state, config.stall_count, alive)
        )

    # 조회에 성공한 사이클에서만 정리한다. 전부 실패했으면 이력을 날리지 않는다.
    if not errors:
        state.stalls.forget_missing(alive)

    # 설정 파일의 정류장·노선 순서를 그대로 화면 순서로 쓴다
    order = {
        (s.name, r): i
        for i, (s, r) in enumerate((s, r) for s in config.stops for r in s.routes)
    }
    entries.sort(key=lambda e: order.get((e.stop_name, e.route_name), 999))

    return Board(
        entries=tuple(entries),
        updated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        error="; ".join(errors) if errors else None,
    )


def off_hours_board(config: BoardConfig) -> Board:
    """운행시간 밖에 내보낼 보드.

    항상 켜두는 화면이라 빈 화면을 띄울 수 없다. API 는 호출하지 않고
    설정에 있는 노선을 '운행종료'로 채워 목록만 유지한다.
    """
    entries = tuple(
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
        for stop in config.stops
        for route in stop.routes
    )
    return Board(
        entries=entries,
        updated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


async def run_poller(
    settings: Settings, config: BoardConfig, state: BoardState
) -> None:
    """종료될 때까지 주기적으로 보드를 갱신한다."""
    client = GbisClient(
        settings.service_key,
        timeout=settings.timeout,
        quota_path=settings.quota_path,
    )
    interval = config.interval_far_sec
    window_start = parse_hhmm(config.window_start)
    window_end = parse_hhmm(config.window_end)
    sleeping = False

    while True:
        if not in_window(datetime.now().time(), window_start, window_end):
            # 첫차 전·막차 후. 빈 응답을 받으려고 쿼터를 쓸 이유가 없다.
            if not sleeping:
                log.info("운행시간 밖 (%s~%s) — 조회 중지",
                         config.window_start, config.window_end)
                state.set(off_hours_board(config))
                sleeping = True
            await asyncio.sleep(60)
            continue

        if sleeping:
            log.info("운행시간 진입 — 조회 재개")
            sleeping = False

        try:
            # httpx 동기 호출이라 이벤트 루프를 막지 않게 스레드로 뺀다
            board = await asyncio.to_thread(build_board, client, config, state)
            state.set(board)
            log.info(
                "보드 갱신: %d줄%s", len(board.entries),
                f" (에러: {board.error})" if board.error else "",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 폴러는 어떤 이유로도 멈추면 안 된다
            log.exception("폴링 실패")
            state.set_error(str(exc))

        await asyncio.sleep(interval)
