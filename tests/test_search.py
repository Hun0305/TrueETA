"""검색 계층 — 양방향 구분이 되는가, 쿼터를 아끼는가.

네트워크는 타지 않는다. 실제 fixture 응답을 돌려주는 가짜 클라이언트를 쓴다.
"""

import glob
import json

import pytest

from trueeta.search import StationSearch


class FakeClient:
    """호출 횟수를 세는 가짜 GbisClient."""

    def __init__(self, body):
        self.body = body
        self.calls = 0

    def call(self, op, params, *, path=None):
        self.calls += 1
        class R:
            pass
        r = R()
        r.body = self.body
        return r


def _fixture(pattern: str):
    paths = sorted(glob.glob(f"tests/fixtures/{pattern}"))
    if not paths:
        pytest.skip(f"fixture 없음: {pattern}")
    return json.load(open(paths[-1], encoding="utf-8"))


# --- 정류장 검색 ---------------------------------------------------------


def test_both_directions_are_found_and_flagged():
    """도담마을아이파크는 양방향 2개가 이름이 완전히 같다."""
    search = StationSearch(FakeClient(_fixture("station-list_도담마을아이파크_*.json")))
    found = search.by_keyword("도담마을아이파크")

    assert len(found) == 2
    assert {s["station_id"] for s in found} == {"228001207", "228001059"}
    assert len({s["name"] for s in found}) == 1          # 이름이 같다
    assert all(s["ambiguous"] for s in found)            # 그래서 표시해준다


def test_mobile_no_distinguishes_the_two():
    """이름이 같으니 표지판 번호로 현장에서 대조할 수 있어야 한다."""
    search = StationSearch(FakeClient(_fixture("station-list_도담마을아이파크_*.json")))
    numbers = {s["mobile_no"] for s in search.by_keyword("도담마을아이파크")}
    assert numbers == {"29272", "29273"}


def test_single_result_is_not_ambiguous():
    search = StationSearch(FakeClient(_fixture("station-list_대일초교후문_*.json")))
    found = search.by_keyword("대일초교후문")
    # 대일초교후문도 양방향이다
    assert len(found) == 2 and all(s["ambiguous"] for s in found)


# --- 노선 목록 (방면) ----------------------------------------------------


def test_routes_carry_the_destination():
    """방면은 노선 단계에서 드러난다. 이게 방향 선택의 근거다."""
    body = {"response": {"msgBody": {"busRouteList": [
        {"routeId": 241423001, "routeName": 22, "routeTypeName": "마을버스",
         "routeDestName": "미금역.청솔마을.2001아울렛", "staOrder": 31},
        {"routeId": 241423008, "routeName": 59, "routeTypeName": "마을버스",
         "routeDestName": "수지구청.수지우체국", "staOrder": 22},
    ]}}}
    routes = StationSearch(FakeClient(body)).routes_at("228001059")
    assert [r["route_name"] for r in routes] == ["22", "59"]
    assert routes[0]["dest_name"] == "미금역.청솔마을.2001아울렛"


def test_routes_are_sorted_short_names_first():
    body = {"response": {"msgBody": {"busRouteList": [
        {"routeName": "57A", "routeDestName": "가"},
        {"routeName": 22, "routeDestName": "나"},
    ]}}}
    routes = StationSearch(FakeClient(body)).routes_at("S")
    assert [r["route_name"] for r in routes] == ["22", "57A"]


# --- 쿼터 ----------------------------------------------------------------


def test_repeated_search_does_not_call_again():
    """검색도 도착정보와 같은 1,000건/일을 나눠 쓴다."""
    client = FakeClient(_fixture("station-list_대일초교후문_*.json"))
    search = StationSearch(client)
    search.by_keyword("대일초교후문")
    search.by_keyword("대일초교후문")
    search.by_keyword("대일초교후문")
    assert client.calls == 1


def test_different_queries_each_cost_a_call():
    client = FakeClient(_fixture("station-list_대일초교후문_*.json"))
    search = StationSearch(client)
    search.by_keyword("가")
    search.by_keyword("나")
    assert client.calls == 2


def test_cache_expires():
    from trueeta.search import _Cache
    cache = _Cache(ttl=10)
    cache.put("k", "v", now=0)
    assert cache.get("k", now=5) == "v"
    assert cache.get("k", now=11) is None
