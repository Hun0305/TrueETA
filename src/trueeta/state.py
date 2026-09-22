"""최신 보드 상태 + 차량 정체 이력.

폴러가 쓰고 웹서버가 읽는다. 같은 프로세스 안이라 메모리 공유로 끝난다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from trueeta.models import Board, Vehicle


@dataclass
class _Observation:
    at: float  # time.monotonic(). 주기가 120/600초로 바뀌므로 시각이 필요하다
    location_no: int | None
    eta_sec: int | None
    stalled_sec: float  # 연속으로 '안 줄어든' 누적 시간


class StallTracker:
    """차량이 실제로 다가오고 있는지를 ETA 감소율로 본다.

    핵심은 감소율이다:

        비율 = (이전 ETA - 현재 ETA) / 경과 시간

        정상 주행  0.6 ~ 1.0   시간이 흐른 만큼 ETA 도 줄어든다
        회차대기   0 근처       시계는 가는데 ETA 는 제자리

    이전 구현은 'ETA 가 1초라도 줄면 정상'으로 봤다. 회차지에 서 있어도
    GBIS 가 주행시간을 재계산하며 ETA 를 조금씩 깎으면 절대 안 걸렸다.

    횟수가 아니라 '누적 초'로 센다. 주기가 피크 120초 / 그 외 600초라
    '3회 연속'이 6분일 수도 30분일 수도 있기 때문이다.
    """

    def __init__(self, ratio_threshold: float = 0.3) -> None:
        self._seen: dict[tuple[str, str, str], _Observation] = {}
        self.ratio_threshold = ratio_threshold

    def observe(
        self, station_id: str, route_id: str, vehicle: Vehicle, *, now: float | None = None
    ) -> float:
        """관측을 기록하고 '얼마나 오래 안 줄었는지'를 초로 돌려준다."""
        moment = time.monotonic() if now is None else now
        key = (station_id, route_id, vehicle.veh_id)
        previous = self._seen.get(key)

        if previous is None:
            self._seen[key] = _Observation(moment, vehicle.location_no, vehicle.eta_sec, 0.0)
            return 0.0

        elapsed = moment - previous.at
        if elapsed <= 0:
            return previous.stalled_sec

        stalled = self._stalled_since(previous, vehicle, elapsed)
        self._seen[key] = _Observation(
            moment, vehicle.location_no, vehicle.eta_sec, stalled
        )
        return stalled

    def _stalled_since(
        self, previous: _Observation, vehicle: Vehicle, elapsed: float
    ) -> float:
        # 정류장을 하나라도 지났으면 확실히 움직인 것이다
        if (
            previous.location_no is not None
            and vehicle.location_no is not None
            and vehicle.location_no < previous.location_no
        ):
            return 0.0

        if previous.eta_sec is None or vehicle.eta_sec is None:
            return previous.stalled_sec  # 판단 불가 — 누적을 유지만 한다

        ratio = (previous.eta_sec - vehicle.eta_sec) / elapsed
        if ratio >= self.ratio_threshold:
            return 0.0
        return previous.stalled_sec + elapsed

    def counts_for(self, station_id: str, route_id: str) -> dict[str, float]:
        """judge 에 넘길 {vehId: 정체 누적 초} 맵."""
        return {
            veh_id: obs.stalled_sec
            for (sid, rid, veh_id), obs in self._seen.items()
            if sid == station_id and rid == route_id
        }

    def forget_missing(self, alive: set[tuple[str, str, str]]) -> None:
        """더 이상 안 보이는 차량은 이력에서 지운다 (메모리 누수 방지)."""
        for key in list(self._seen):
            if key not in alive:
                del self._seen[key]


#: 화면이 이 시간 안에 /api/board 를 부르지 않으면 안 보는 것으로 친다.
#: 화면은 15초마다 부르므로 네 번 놓치면 꺼진 것으로 본다.
SUBSCRIPTION_TTL_SEC = 60.0


class BoardState:
    """프리셋별 보드 + 누가 뭘 보고 있는지.

    폴링 비용이 '저장한 프리셋 수' 가 아니라 '지금 보고 있는 프리셋 수' 에
    비례하게 만드는 것이 목적이다.
    """

    def __init__(self, default_preset: str = "기본") -> None:
        self.default_preset = default_preset
        self._boards: dict[str, Board] = {}
        self._last_seen: dict[str, float] = {}
        self.stalls = StallTracker()

    # --- 보드 -----------------------------------------------------------

    def get(self, preset: str | None = None) -> Board:
        name = preset or self.default_preset
        return self._boards.get(
            name, Board(entries=(), updated_at=_now(), error="아직 조회 전")
        )

    def set(self, board: Board, preset: str | None = None) -> None:
        self._boards[preset or self.default_preset] = board

    def set_error(self, message: str, preset: str | None = None) -> None:
        """조회에 실패해도 직전 화면은 유지하고 에러만 얹는다."""
        name = preset or self.default_preset
        previous = self.get(name)
        self._boards[name] = Board(
            entries=previous.entries, updated_at=previous.updated_at, error=message
        )

    # --- 구독 -----------------------------------------------------------

    def touch(self, preset: str, *, now: float | None = None) -> None:
        """누가 이 프리셋을 보고 있다는 신호. /api/board 호출이 곧 하트비트다."""
        self._last_seen[preset] = time.monotonic() if now is None else now

    def active_presets(
        self, *, ttl: float = SUBSCRIPTION_TTL_SEC, now: float | None = None
    ) -> set[str]:
        """지금 폴링해야 할 프리셋.

        기본 프리셋은 아무도 안 봐도 항상 포함한다 — 키오스크가 늘 띄우고 있고,
        여기서 빠지면 관측 로그 수집이 통째로 멈춘다.
        """
        moment = time.monotonic() if now is None else now
        active = {self.default_preset}
        active.update(
            name for name, seen in self._last_seen.items() if moment - seen <= ttl
        )
        return active


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
