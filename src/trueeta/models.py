"""도메인 모델.

GBIS 필드명(`predictTimeSec1`, `locationNo1` …)은 여기까지 올라오지 않는다.
변환은 gbis/parser.py 가 맡는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    RUNNING = "running"  # 운행 중, ETA 있음
    WAITING = "waiting"  # 회차대기 — 이 프로젝트의 존재 이유
    NO_BUS = "no_bus"  # 노선은 운행 중인데 지금 오는 차가 없음
    ENDED = "ended"  # flag=STOP, 운행 종료


@dataclass(frozen=True)
class Vehicle:
    """도착 예정 차량 한 대."""

    veh_id: str
    plate_no: str
    predict_sec: int | None  # 초 단위 ETA. 없으면 None
    predict_min: int | None  # 분 단위 ETA (API 가 주는 값)
    location_no: int | None  # 몇 정거장 전에 있는지
    state_cd: int | None  # 0 교차로통과 · 1 정류소도착 · 2 정류소출발
    station_nm: str  # 현재 위치 정류소명 (디버깅용)

    @property
    def has_eta(self) -> bool:
        return self.predict_sec is not None or self.predict_min is not None

    @property
    def eta_sec(self) -> int | None:
        """초 단위가 없으면 분 단위를 환산한다."""
        if self.predict_sec is not None:
            return self.predict_sec
        if self.predict_min is not None:
            return self.predict_min * 60
        return None


@dataclass(frozen=True)
class Arrival:
    """한 정류장에 오는 한 노선의 도착정보."""

    station_id: str
    route_id: str
    route_name: str
    dest_name: str  # 진행방향 종점 — 양방향 정류장에서 방향을 가른다
    sta_order: int  # 이 정류장이 노선의 몇 번째인지
    turn_seq: int  # 회차점 순번
    flag: str  # RUN/PASS 운행중 · STOP 운행종료 · WAIT 회차지대기
    vehicles: tuple[Vehicle, ...]  # 0~2대

    def position_of(self, vehicle: Vehicle) -> int | None:
        """차량이 노선의 몇 번째 정류소에 있는지.

        위치 순번 = staOrder - locationNo
        위치정보 API 없이 도착정보만으로 계산된다.
        """
        if vehicle.location_no is None:
            return None
        return self.sta_order - vehicle.location_no


@dataclass(frozen=True)
class BoardEntry:
    """전광판 한 줄 = 정류장 하나 × 노선 하나."""

    stop_name: str
    station_id: str
    route_name: str
    dest_name: str
    status: Status
    eta_sec: int | None  # 첫 번째 차량
    second_eta_sec: int | None  # 두 번째 차량
    second_status: Status | None
    estimated_wait: bool = False  # 회차대기가 API 확정이 아니라 추정인지
    # 차가 기점/회차점에 있다. 회차대기로 확정되지 않았어도 ETA 를 믿기 어려우므로
    # 화면이 숫자를 흐리게 하고 '회차지' 표식을 단다.
    at_standing: bool = False

    def as_dict(self) -> dict:
        return {
            "stop_name": self.stop_name,
            "station_id": self.station_id,
            "route_name": self.route_name,
            "dest_name": self.dest_name,
            "status": self.status.value,
            "eta_sec": self.eta_sec,
            "second_eta_sec": self.second_eta_sec,
            "second_status": self.second_status.value if self.second_status else None,
            "estimated_wait": self.estimated_wait,
            "at_standing": self.at_standing,
        }


@dataclass(frozen=True)
class Board:
    """화면이 받는 전체 상태."""

    entries: tuple[BoardEntry, ...]
    updated_at: str  # ISO8601
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "entries": [e.as_dict() for e in self.entries],
            "updated_at": self.updated_at,
            "error": self.error,
        }
