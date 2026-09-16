"""설정 로드.

두 곳에서 읽는다:
  .env        서비스키 (git 에 올리지 않는다)
  config.yaml 정류장·노선·폴링 파라미터

config.yaml 에 staOrder·turnSeq·첫차/막차를 두지 않는 것은 의도적이다.
앞의 둘은 arrivals 응답에 매번 들어오고, 뒤는 route-info 캐시에서 온다.
같은 값을 두 곳에 두면 한쪽이 먼저 낡는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# src/trueeta/config.py -> src/trueeta -> src -> 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def route_key(value: Any) -> str:
    """노선번호 비교용 정규화.

    API 는 숫자 노선을 int(22)로, 가지 노선을 str("57A")로 준다.
    설정 파일은 전부 문자열이므로 비교 전에 맞춰준다.
    """
    return str(value).strip()


@dataclass(frozen=True)
class Settings:
    service_key: str
    timeout: float
    fixture_dir: Path
    var_dir: Path

    @property
    def quota_path(self) -> Path:
        return self.var_dir / "quota.json"


@dataclass(frozen=True)
class Stop:
    station_id: str
    name: str
    routes: tuple[str, ...]
    mobile_no: str = ""
    note: str = ""

    def wants(self, route_name: Any) -> bool:
        return route_key(route_name) in self.routes


@dataclass(frozen=True)
class BoardConfig:
    stops: tuple[Stop, ...]
    vehicles_per_route: int = 2
    countdown: bool = True
    refresh_sec: int = 5
    window_start: str = "05:50"
    window_end: str = "00:10"
    interval_far_sec: int = 60
    interval_near_sec: int = 20
    near_threshold_sec: int = 180
    stall_count: int = 3

    @property
    def station_ids(self) -> tuple[str, ...]:
        return tuple(s.station_id for s in self.stops)

    @property
    def all_routes(self) -> frozenset[str]:
        return frozenset(r for s in self.stops for r in s.routes)


def load_settings(*, require_key: bool = True) -> Settings:
    """`.env` 를 읽어 Settings 를 만든다.

    require_key=False 는 --dry-run 처럼 실제 호출이 없는 경로용.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    key = os.environ.get("SERVICE_KEY", "").strip()
    if require_key and not key:
        raise SystemExit(
            "SERVICE_KEY 가 비어 있습니다.\n"
            "  cp .env.example .env 후 공공데이터포털의 '디코딩' 키를 넣으세요."
        )

    return Settings(
        service_key=key,
        timeout=float(os.environ.get("TRUEETA_TIMEOUT", "10")),
        fixture_dir=PROJECT_ROOT / "tests" / "fixtures",
        var_dir=PROJECT_ROOT / "var",
    )


def load_board_config(path: Path | None = None) -> BoardConfig:
    raw = yaml.safe_load((path or PROJECT_ROOT / "config.yaml").read_text("utf-8"))

    stops = []
    for entry in raw.get("stops") or []:
        stops.append(
            Stop(
                # API 가 int 로 돌려주지만 URL 파라미터로 나가므로 문자열로 고정
                station_id=str(entry["station_id"]),
                name=entry["name"],
                routes=tuple(route_key(r) for r in entry.get("routes") or []),
                mobile_no=str(entry.get("mobile_no", "")),
                note=entry.get("note", ""),
            )
        )
    if not stops:
        raise SystemExit("config.yaml 에 stops 가 비어 있습니다.")

    display = raw.get("display") or {}
    polling = raw.get("polling") or {}
    window = polling.get("service_window") or {}
    interval = polling.get("interval_sec") or {}
    judge = raw.get("judge") or {}

    return BoardConfig(
        stops=tuple(stops),
        vehicles_per_route=display.get("vehicles_per_route", 2),
        countdown=display.get("countdown", True),
        refresh_sec=display.get("refresh_sec", 5),
        window_start=str(window.get("start", "05:50")),
        window_end=str(window.get("end", "00:10")),
        interval_far_sec=interval.get("far", 60),
        interval_near_sec=interval.get("near", 20),
        near_threshold_sec=polling.get("near_threshold_sec", 180),
        stall_count=judge.get("stall_count", 3),
    )
