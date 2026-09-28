"""노선 정보 캐시 — 첫차·막차·배차간격.

B안 판정(judge_absence.py)이 쓴다. "차 없음"이 회차지 대기인지 운행 종료인지
가르려면 **노선별 운행시간**이, "보통 몇 분 뒤 오나"를 내려면 **배차간격**이 필요하다.

`버스노선 조회`(getBusRouteInfoItemv2)를 노선마다 **하루 1회**만 부른다.
노선 3개면 하루 3콜이고, 도착정보와 오퍼레이션이 달라 쿼터 주머니가 따로다.

`var/routeinfo.json` 에 저장해 재시작해도 다시 부르지 않는다.
받기에 실패하면 전날 값을 계속 쓰고, 재시도는 한 시간에 한 번만 한다.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any

from trueeta.gbis.envelope import find_key
from trueeta.window import in_window, parse_hhmm

log = logging.getLogger("trueeta.routeinfo")

#: 막차 시각은 기점(또는 회차점) **출발** 기준이다. 우리 정류장엔 그보다 늦게
#: 닿으므로 '출발점 -> 우리 정류장' 주행시간에 이만큼을 더해 운행 종료로 본다.
#: 처음엔 일괄 60분이었는데, 00:29 에 막차가 다 끝났는데도 '회차지 대기'로
#: 판정했다. 25번은 회차점에서 3정거장이라 5분이면 충분하다.
LAST_BUS_SLACK_MIN = 10

#: 배차간격의 '피크'가 언제인지 API 가 알려주지 않는다. 출퇴근 시간으로 가정한다.
#: route_cycles 의 '차 없음' 구간 길이로 나중에 맞는지 확인할 것.
RUSH_HOURS = ((dtime(7, 0), dtime(9, 0)), (dtime(17, 0), dtime(19, 0)))

RETRY_SEC = 3600


def _int(value: Any) -> int | None:
    try:
        text = str(value).strip()
        return int(text) if text else None
    except (TypeError, ValueError):
        return None


def _day_prefix(day: date) -> str:
    """토요일 sat*, 일요일 sun*, 평일은 접두어 없음. 공휴일은 구분하지 않는다."""
    return {5: "sat", 6: "sun"}.get(day.weekday(), "")


def _key(prefix: str, name: str) -> str:
    # upFirstTime / satUpFirstTime 처럼 접두어가 붙으면 첫 글자가 대문자가 된다
    return prefix + name[0].upper() + name[1:] if prefix else name


@dataclass(frozen=True)
class RouteInfo:
    route_id: str
    raw: dict[str, Any]

    def _get(self, day: date, name: str) -> Any:
        value = self.raw.get(_key(_day_prefix(day), name))
        # 주말 값이 비어 있으면 평일 값으로
        return value if value not in (None, "") else self.raw.get(name)

    def service_hours(
        self, day: date, *, after_turn: bool, travel_sec: int = 0
    ) -> tuple[dtime, dtime] | None:
        """이 방향의 (첫차, 막차 + 주행시간 + 여유).

        우리 정류장이 회차점 뒤(after_turn)면 down*, 앞이면 up* 시각을 쓴다.
        travel_sec 은 출발점에서 우리 정류장까지 걸리는 시간 (B안의 N).
        """
        side = "down" if after_turn else "up"
        first = self._get(day, f"{side}FirstTime")
        last = self._get(day, f"{side}LastTime")
        if not first or not last:
            return None
        try:
            start = parse_hhmm(str(first))
            end = parse_hhmm(str(last))
        except ValueError:
            return None
        margin = -(-travel_sec // 60) + LAST_BUS_SLACK_MIN   # 올림
        minutes = (end.hour * 60 + end.minute + margin) % (24 * 60)
        return start, dtime(minutes // 60, minutes % 60)

    def in_service(
        self, now: datetime, *, after_turn: bool, travel_sec: int = 0
    ) -> bool | None:
        hours = self.service_hours(now.date(), after_turn=after_turn, travel_sec=travel_sec)
        if hours is None:
            return None
        return in_window(now.time(), *hours)

    def headway_sec(self, now: datetime) -> int | None:
        """지금 시각의 배차간격(초). 출퇴근 시간이면 피크 값."""
        rush = any(in_window(now.time(), a, b) for a, b in RUSH_HOURS)
        minutes = _int(self._get(now.date(), "peekAlloc" if rush else "nPeekAlloc"))
        if not minutes:
            minutes = _int(self._get(now.date(), "nPeekAlloc" if rush else "peekAlloc"))
        return minutes * 60 if minutes else None


class RouteInfoCache:
    """노선별 route-info 를 하루 1회 받아 파일로 들고 있는다."""

    def __init__(self, path: Path, fetch=None) -> None:
        """fetch(route_id) -> 응답 body(dict). 테스트에서는 가짜를 넣는다."""
        self.path = path
        self._fetch = fetch
        self._data: dict[str, dict[str, Any]] = self._load()
        self._last_try: dict[str, float] = {}

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            log.exception("노선 정보를 저장하지 못했다")

    def get(self, route_id: str, *, today: date | None = None) -> RouteInfo | None:
        """오늘 받은 값이 없으면 받아온다. 실패하면 예전 값이라도 돌려준다."""
        if not route_id:
            return None
        today = today or date.today()
        entry = self._data.get(route_id)
        fresh = entry is not None and entry.get("fetched") == today.isoformat()

        if not fresh and self._fetch is not None and self._may_try(route_id):
            try:
                body = self._fetch(route_id)
                item = find_key(body, "busRouteInfoItem")
                if isinstance(item, dict):
                    entry = {"fetched": today.isoformat(), "item": item}
                    self._data[route_id] = entry
                    self._save()
                    log.info("노선 정보 갱신: %s", route_id)
            except Exception:  # 노선 정보 때문에 폴링이 죽으면 안 된다
                log.warning("노선 정보 조회 실패: %s (예전 값 사용)", route_id, exc_info=True)

        if entry is None:
            return None
        return RouteInfo(route_id=route_id, raw=entry["item"])

    def _may_try(self, route_id: str) -> bool:
        now = time.monotonic()
        last = self._last_try.get(route_id)
        if last is not None and now - last < RETRY_SEC:
            return False
        self._last_try[route_id] = now
        return True
