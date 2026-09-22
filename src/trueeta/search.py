"""정류장·노선 검색 (프리셋 편집용).

GBIS 정류소 조회 서비스를 감싼다. 1차 때 활용신청해둬서 추가 승인은 필요 없다.

**검색도 쿼터를 먹는다.** 다만 한도는 오퍼레이션별로 따로라
도착정보 조회분을 깎아먹지는 않는다 (각각 1,000건/일).
그래도 같은 질의를 반복할 이유가 없어 캐시를 둔다 — 정류장 목록은
몇 분 사이에 바뀌는 데이터가 아니라 오래 들고 있어도 된다.

## 방향 문제

정류장 하나에 stationId 가 둘이고 **이름이 완전히 같다**.
검색 결과만으로는 구분할 수 없으므로 두 가지를 같이 내보낸다:

  - `mobile_no`  정류장 표지판에 적힌 번호. 현장에서 대조할 수 있다
  - `dest_name`  노선 목록에 붙는 진행방향 종점. 이게 결정적이다

그래서 UI 흐름을 "정류장 검색 -> 노선 선택(방면 표시)" 로 둔다.
방향은 노선을 고르는 단계에서 드러난다.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from trueeta.gbis import GbisClient, GbisError
from trueeta.gbis.envelope import as_list, find_key

log = logging.getLogger("trueeta.search")

#: 정류장·노선 목록은 자주 바뀌지 않는다. 길게 잡아 쿼터를 아낀다.
CACHE_TTL_SEC = 1800.0


class _Cache:
    def __init__(self, ttl: float = CACHE_TTL_SEC) -> None:
        self.ttl = ttl
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, *, now: float | None = None) -> Any | None:
        moment = time.monotonic() if now is None else now
        hit = self._items.get(key)
        if hit is None or moment - hit[0] > self.ttl:
            return None
        return hit[1]

    def put(self, key: str, value: Any, *, now: float | None = None) -> None:
        self._items[key] = (time.monotonic() if now is None else now, value)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


class StationSearch:
    def __init__(self, client: GbisClient, ttl: float = CACHE_TTL_SEC) -> None:
        self._client = client
        self._cache = _Cache(ttl)

    def _items(self, op: str, params: dict[str, Any], key: str) -> list[dict]:
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        response = self._client.call(op, params)
        items = [
            i for i in as_list(find_key(response.body, _ITEM_KEYS[op])) if isinstance(i, dict)
        ]
        self._cache.put(key, items)
        return items

    # --- 정류장 ---------------------------------------------------------

    def by_keyword(self, keyword: str) -> list[dict]:
        """이름·번호로 검색. 같은 이름이 둘 나오면 양방향이다."""
        raw = self._items("station-list", {"keyword": keyword}, f"kw:{keyword}")
        return _stations(raw)

    def around(self, x: float, y: float) -> list[dict]:
        """좌표 반경 500m. 폰 GPS 로 '내 주변' 을 찾을 때."""
        key = f"xy:{round(float(x), 5)},{round(float(y), 5)}"
        raw = self._items("station-around", {"x": x, "y": y}, key)
        return _stations(raw, distance=True)

    # --- 노선 -----------------------------------------------------------

    def routes_at(self, station_id: str) -> list[dict]:
        """그 정류장을 지나는 노선. **방면(dest_name)이 여기서 나온다.**"""
        raw = self._items(
            "station-routes", {"stationId": station_id}, f"rt:{station_id}"
        )
        routes = [
            {
                "route_id": _text(i.get("routeId")),
                "route_name": _text(i.get("routeName")),
                "route_type": _text(i.get("routeTypeName")),
                "dest_name": _text(i.get("routeDestName")),
                "sta_order": _text(i.get("staOrder")),
            }
            for i in raw
        ]
        routes.sort(key=lambda r: (len(r["route_name"]), r["route_name"]))
        return routes


def _stations(raw: list[dict], *, distance: bool = False) -> list[dict]:
    stations = []
    for item in raw:
        entry = {
            "station_id": _text(item.get("stationId")),
            "name": _text(item.get("stationName")),
            "mobile_no": _text(item.get("mobileNo")),
            "region": _text(item.get("regionName")),
            "x": item.get("x"),
            "y": item.get("y"),
        }
        if distance:
            entry["distance"] = item.get("distance")
        stations.append(entry)

    # 이름이 겹치는 것들을 표시해준다 — 양방향이라는 신호다
    counts: dict[str, int] = {}
    for s in stations:
        counts[s["name"]] = counts.get(s["name"], 0) + 1
    for s in stations:
        s["ambiguous"] = counts[s["name"]] > 1

    if distance:
        stations.sort(key=lambda s: float(s.get("distance") or 0))
    return stations


_ITEM_KEYS = {
    "station-list": "busStationList",
    "station-around": "busStationAroundList",
    "station-routes": "busRouteList",
}


__all__ = ["StationSearch", "GbisError", "CACHE_TTL_SEC"]
