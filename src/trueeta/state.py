"""최신 보드 상태 + 차량 정체 이력.

폴러가 쓰고 웹서버가 읽는다. 같은 프로세스 안이라 메모리 공유로 끝난다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trueeta.models import Board, Vehicle


@dataclass
class _Observation:
    location_no: int | None
    eta_sec: int | None
    stalls: int  # 연속으로 '안 움직인' 횟수


class StallTracker:
    """같은 차량이 위치·ETA 그대로인 횟수를 센다.

    API 가 flag=WAIT 을 빠뜨렸을 때의 보조 신호.
    회차지에 서 있는 차는 위치가 안 변하고 ETA 도 안 줄어든다.
    """

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str, str], _Observation] = {}

    def observe(self, station_id: str, route_id: str, vehicle: Vehicle) -> int:
        key = (station_id, route_id, vehicle.veh_id)
        previous = self._seen.get(key)

        if previous is None:
            self._seen[key] = _Observation(vehicle.location_no, vehicle.eta_sec, 0)
            return 0

        same_place = previous.location_no == vehicle.location_no
        # ETA 가 줄어들지 않았으면 움직이지 않은 것으로 본다
        not_closer = (
            previous.eta_sec is not None
            and vehicle.eta_sec is not None
            and vehicle.eta_sec >= previous.eta_sec
        )
        stalls = previous.stalls + 1 if (same_place and not_closer) else 0

        self._seen[key] = _Observation(vehicle.location_no, vehicle.eta_sec, stalls)
        return stalls

    def counts_for(self, station_id: str, route_id: str) -> dict[str, int]:
        """judge 에 넘길 {vehId: 정체횟수} 맵."""
        return {
            veh_id: obs.stalls
            for (sid, rid, veh_id), obs in self._seen.items()
            if sid == station_id and rid == route_id
        }

    def forget_missing(self, alive: set[tuple[str, str, str]]) -> None:
        """더 이상 안 보이는 차량은 이력에서 지운다 (메모리 누수 방지)."""
        for key in list(self._seen):
            if key not in alive:
                del self._seen[key]


class BoardState:
    def __init__(self) -> None:
        self._board = Board(entries=(), updated_at=_now(), error="아직 조회 전")
        self.stalls = StallTracker()

    def get(self) -> Board:
        return self._board

    def set(self, board: Board) -> None:
        self._board = board

    def set_error(self, message: str) -> None:
        """조회에 실패해도 직전 화면은 유지하고 에러만 얹는다."""
        self._board = Board(
            entries=self._board.entries, updated_at=self._board.updated_at, error=message
        )


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
