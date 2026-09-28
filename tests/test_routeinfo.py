"""노선 정보 캐시 — 첫차·막차·배차간격. 실제 route-info 응답(fixture)으로 검증."""

import glob
import json
from datetime import date, datetime

import pytest

from trueeta.routeinfo import RouteInfo, RouteInfoCache

TUE, SAT = date(2026, 9, 29), date(2026, 10, 3)


def _body(route_id):
    paths = sorted(glob.glob(f"tests/fixtures/route-info_{route_id}_*.json"))
    if not paths:
        pytest.skip("route-info fixture 없음")
    return json.load(open(paths[-1], encoding="utf-8"))


def _info(route_id) -> RouteInfo:
    item = _body(route_id)["response"]["msgBody"]["busRouteInfoItem"]
    return RouteInfo(route_id, item)


def at(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm)


# --- 25번: 회차점 뒤(down) 06:30~23:45, 배차 피크 15 / 비피크 20 ----------


def test_service_hours_use_the_down_side_after_the_turn_point():
    info = _info("241428003")
    assert info.in_service(at(TUE, 6, 0), after_turn=True) is False    # 첫차 전
    assert info.in_service(at(TUE, 12, 0), after_turn=True) is True
    # 막차 23:45 출발 + 회차점->정류장 약 3분 + 여유 10분 = 23:58 까지
    assert info.in_service(at(TUE, 23, 55), after_turn=True, travel_sec=150) is True
    assert info.in_service(at(TUE, 2, 0), after_turn=True) is False


def test_after_last_bus_is_off_not_waiting():
    """00:29 에 막차가 다 끝났는데 '회차지 대기'로 판정했던 회귀 (여유를 일괄 60분으로 뒀었다)."""
    info = _info("241428003")
    assert info.in_service(at(TUE, 0, 29), after_turn=True, travel_sec=150) is False


def test_headway_is_peak_in_rush_hours():
    info = _info("241428003")
    assert info.headway_sec(at(TUE, 8, 0)) == 15 * 60
    assert info.headway_sec(at(TUE, 13, 0)) == 20 * 60


def test_weekend_uses_saturday_values():
    """25번 토요일은 피크 20 / 비피크 25."""
    info = _info("241428003")
    assert info.headway_sec(at(SAT, 8, 0)) == 20 * 60
    assert info.headway_sec(at(SAT, 13, 0)) == 25 * 60


def test_59_uses_the_up_side_before_the_turn_point():
    """59번은 우리 정류장이 회차점 앞 -> up 07:00~23:50."""
    info = _info("241423008")
    assert info.in_service(at(TUE, 6, 45), after_turn=False) is False
    assert info.in_service(at(TUE, 7, 30), after_turn=False) is True


# --- 캐시 --------------------------------------------------------------


class Fetch:
    def __init__(self, body, fail=False):
        self.body, self.fail, self.calls = body, fail, 0

    def __call__(self, route_id):
        self.calls += 1
        if self.fail:
            raise RuntimeError("네트워크")
        return self.body


def test_fetches_once_per_day(tmp_path):
    fetch = Fetch(_body("241428003"))
    cache = RouteInfoCache(tmp_path / "ri.json", fetch=fetch)
    cache.get("241428003", today=TUE)
    cache.get("241428003", today=TUE)
    assert fetch.calls == 1


def test_survives_restart_without_refetching(tmp_path):
    fetch = Fetch(_body("241428003"))
    RouteInfoCache(tmp_path / "ri.json", fetch=fetch).get("241428003", today=TUE)
    again = Fetch(_body("241428003"))
    info = RouteInfoCache(tmp_path / "ri.json", fetch=again).get("241428003", today=TUE)
    assert again.calls == 0 and info is not None


def test_failure_keeps_yesterdays_value(tmp_path):
    """노선 정보 때문에 폴링이 죽거나 B안이 멈추면 안 된다."""
    path = tmp_path / "ri.json"
    RouteInfoCache(path, fetch=Fetch(_body("241428003"))).get("241428003", today=TUE)
    broken = Fetch(None, fail=True)
    info = RouteInfoCache(path, fetch=broken).get("241428003", today=date(2026, 9, 30))
    assert broken.calls == 1 and info is not None


def test_failure_is_not_retried_every_cycle(tmp_path):
    """실패했다고 사이클마다 다시 부르면 쿼터를 태운다. 한 시간에 한 번."""
    broken = Fetch(None, fail=True)
    cache = RouteInfoCache(tmp_path / "ri.json", fetch=broken)
    for _ in range(5):
        assert cache.get("241428003", today=TUE) is None
    assert broken.calls == 1
