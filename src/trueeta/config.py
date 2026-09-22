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

from trueeta.window import parse_hhmm, span_seconds

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

    @property
    def observations_path(self) -> Path:
        return self.var_dir / "observations.db"


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
class Preset:
    """한 화면에 띄울 정류장·노선 묶음.

    기본 프리셋(is_default)은 키오스크가 상시 띄우는 것이라 항상 폴링된다.
    나머지는 누가 열어볼 때만 폴링한다 — 저장은 공짜고 표시가 비용이다.
    """

    name: str
    stops: tuple[Stop, ...]
    is_default: bool = False

    @property
    def station_ids(self) -> tuple[str, ...]:
        return tuple(s.station_id for s in self.stops)


@dataclass(frozen=True)
class BoardConfig:
    presets: tuple[Preset, ...]
    vehicles_per_route: int = 2
    countdown: bool = True
    refresh_sec: int = 5
    window_start: str = "05:50"
    window_end: str = "00:10"
    peak_start: str = "08:00"
    peak_end: str = "18:00"
    interval_peak_sec: int = 120
    interval_far_sec: int = 60
    interval_near_sec: int = 20
    near_threshold_sec: int = 180
    stall_ratio: float = 0.3
    stall_seconds: float = 240.0
    log_observations: bool = True
    daily_budget: int = 1000

    @property
    def default_preset(self) -> Preset:
        for preset in self.presets:
            if preset.is_default:
                return preset
        return self.presets[0]

    def preset(self, name: str | None) -> Preset:
        """이름으로 찾는다. 없으면 기본 프리셋 — 화면이 비면 안 된다."""
        if name:
            for preset in self.presets:
                if preset.name == name:
                    return preset
        return self.default_preset

    @property
    def stops(self) -> tuple[Stop, ...]:
        """하위호환. 프리셋을 안 쓰는 코드는 기본 프리셋을 본다."""
        return self.default_preset.stops

    @property
    def station_ids(self) -> tuple[str, ...]:
        return self.default_preset.station_ids

    @property
    def all_routes(self) -> frozenset[str]:
        return frozenset(r for s in self.stops for r in s.routes)

    @property
    def daily_calls(self) -> int:
        """하루 예상 호출 건수. 개발계정 한도(1,000건)와 대볼 값이다.

        피크 창이 운행시간 창 안에 있다고 본다 (08:00~18:00 ⊂ 05:50~00:10).
        """
        return self.daily_calls_for(len(self.stops))

    def daily_calls_for(self, stations: int, scale: float = 1.0) -> int:
        total = span_seconds(parse_hhmm(self.window_start), parse_hhmm(self.window_end))
        peak = span_seconds(parse_hhmm(self.peak_start), parse_hhmm(self.peak_end))
        peak_int = max(1, round(self.interval_peak_sec * scale))
        far_int = max(1, round(self.interval_far_sec * scale))
        cycles = peak // peak_int + (total - peak) // far_int
        return stations * cycles

    def intervals_for(self, stations: int) -> tuple[int, int]:
        """활성 정류장 수에 맞춘 (피크, 그 외) 주기.

        프리셋을 여러 개 띄우면 정류장이 늘어 예산을 넘는다. 설정된 주기를
        기준으로 두고 한도에 맞을 때까지 같은 비율로 늘린다 — 피크와
        그 외의 비율(현재 1:5)은 손으로 맞춘 값이라 유지한다.
        """
        if stations <= 0:
            return self.interval_peak_sec, self.interval_far_sec

        base = self.daily_calls_for(stations)
        scale = max(1.0, base / self.daily_budget) if self.daily_budget else 1.0
        # 반올림 때문에 한도를 살짝 넘을 수 있으니 확인 후 조금씩 늘린다
        for _ in range(20):
            if self.daily_calls_for(stations, scale) <= self.daily_budget:
                break
            scale *= 1.05
        return (
            max(1, round(self.interval_peak_sec * scale)),
            max(1, round(self.interval_far_sec * scale)),
        )


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


def _load_stops(entries: list[dict]) -> tuple[Stop, ...]:
    return tuple(
        Stop(
            # API 가 int 로 돌려주지만 URL 파라미터로 나가므로 문자열로 고정
            station_id=str(entry["station_id"]),
            name=entry["name"],
            routes=tuple(route_key(r) for r in entry.get("routes") or []),
            mobile_no=str(entry.get("mobile_no", "")),
            note=entry.get("note", ""),
        )
        for entry in entries or []
    )


def _load_presets(raw: dict) -> tuple[Preset, ...]:
    """`presets:` 를 읽는다. 없으면 기존 `stops:` 를 기본 프리셋 하나로 감싼다.

    설정을 안 고쳐도 동작이 그대로이게 하려는 것이다.
    """
    entries = raw.get("presets")
    if not entries:
        stops = _load_stops(raw.get("stops"))
        if not stops:
            raise SystemExit("config.yaml 에 presets 도 stops 도 없습니다.")
        return (Preset(name="기본", stops=stops, is_default=True),)

    presets = []
    for entry in entries:
        stops = _load_stops(entry.get("stops"))
        if not stops:
            raise SystemExit(f"프리셋 '{entry.get('name')}' 에 stops 가 없습니다.")
        presets.append(
            Preset(
                name=str(entry["name"]),
                stops=stops,
                is_default=bool(entry.get("default", False)),
            )
        )

    names = [p.name for p in presets]
    if len(names) != len(set(names)):
        raise SystemExit(f"프리셋 이름이 겹칩니다: {names}")
    # 기본이 지정되지 않았으면 첫 번째를 기본으로. 키오스크가 띄울 것이 있어야 한다.
    if not any(p.is_default for p in presets):
        presets[0] = Preset(presets[0].name, presets[0].stops, is_default=True)
    return tuple(presets)


def load_board_config(path: Path | None = None) -> BoardConfig:
    raw = yaml.safe_load((path or PROJECT_ROOT / "config.yaml").read_text("utf-8"))

    presets = _load_presets(raw)

    display = raw.get("display") or {}
    polling = raw.get("polling") or {}
    window = polling.get("service_window") or {}
    peak = polling.get("peak_window") or {}
    interval = polling.get("interval_sec") or {}
    judge = raw.get("judge") or {}

    return BoardConfig(
        presets=presets,
        vehicles_per_route=display.get("vehicles_per_route", 2),
        countdown=display.get("countdown", True),
        refresh_sec=display.get("refresh_sec", 5),
        window_start=str(window.get("start", "05:50")),
        window_end=str(window.get("end", "00:10")),
        peak_start=str(peak.get("start", "08:00")),
        peak_end=str(peak.get("end", "18:00")),
        interval_peak_sec=interval.get("peak", 120),
        interval_far_sec=interval.get("far", 60),
        interval_near_sec=interval.get("near", 20),
        near_threshold_sec=polling.get("near_threshold_sec", 180),
        stall_ratio=float(judge.get("stall_ratio", 0.3)),
        stall_seconds=float(judge.get("stall_seconds", 240)),
        log_observations=bool(judge.get("log_observations", True)),
        daily_budget=int(polling.get("daily_budget", 1000)),
    )
