"""보드 조립 — 설정 + 실응답 -> 화면에 나갈 줄."""

import glob
import json

import pytest

from trueeta.config import load_board_config
from trueeta.models import Status
from trueeta.poller import _entries_for_stop
from trueeta.state import BoardState, StallTracker
from trueeta.models import Vehicle


def _items(station_id: str) -> list[dict]:
    paths = sorted(glob.glob("tests/fixtures/arrivals_%s_*.json" % station_id))
    if not paths:
        pytest.skip("fixture 없음: %s" % station_id)
    return json.load(open(paths[-1], encoding="utf-8"))["response"]["msgBody"]["busArrivalList"]


def test_only_configured_routes_survive():
    cfg = load_board_config()
    stop = cfg.stops[0]  # 도담마을 — 22, 59
    entries = _entries_for_stop(stop, _items(stop.station_id), BoardState(), 240)
    assert {e.route_name for e in entries} == {"22", "59"}


def test_configured_route_missing_from_response_still_gets_a_row():
    """노선이 응답에서 빠져도 화면에서 줄이 사라지면 안 된다."""
    cfg = load_board_config()
    stop = cfg.stops[0]
    entries = _entries_for_stop(stop, [], BoardState(), 240)
    assert {e.route_name for e in entries} == {"22", "59"}
    assert all(e.status is Status.NO_BUS for e in entries)


def test_entry_is_json_serializable():
    cfg = load_board_config()
    stop = cfg.stops[0]
    entries = _entries_for_stop(stop, _items(stop.station_id), BoardState(), 240)
    json.dumps([e.as_dict() for e in entries])  # 던지면 실패


# --- 정체 추적 -----------------------------------------------------------


def _veh(location_no: int, eta: int) -> Vehicle:
    return Vehicle(
        veh_id="V1", plate_no="", predict_sec=eta, predict_min=None,
        location_no=location_no, state_cd=0, station_nm="",
    )


def test_stall_accumulates_seconds_when_eta_barely_moves():
    """회차지에 선 차: 시계는 가는데 ETA 는 거의 그대로."""
    tracker = StallTracker(ratio_threshold=0.3)
    assert tracker.observe("S", "R", _veh(4, 300), now=0) == 0
    assert tracker.observe("S", "R", _veh(4, 295), now=120) == 120   # 비율 0.04
    assert tracker.observe("S", "R", _veh(4, 290), now=240) == 240


def test_slowly_decreasing_eta_still_counts_as_stalled():
    """이전 구현의 버그: ETA 가 1초라도 줄면 정상으로 봤다.

    회차지에서도 GBIS 가 주행시간을 재계산하며 ETA 를 조금씩 깎기 때문에
    그 기준으로는 정체가 영원히 안 잡혔다.
    """
    tracker = StallTracker(ratio_threshold=0.3)
    tracker.observe("S", "R", _veh(4, 300), now=0)
    assert tracker.observe("S", "R", _veh(4, 280), now=120) == 120  # 비율 0.17


def test_stall_resets_when_bus_actually_approaches():
    tracker = StallTracker(ratio_threshold=0.3)
    tracker.observe("S", "R", _veh(4, 300), now=0)
    tracker.observe("S", "R", _veh(4, 295), now=120)
    # 120초 동안 ETA 가 100초 줄었다 -> 비율 0.83, 정상 주행
    assert tracker.observe("S", "R", _veh(3, 195), now=240) == 0


def test_passing_a_stop_always_resets():
    """정류장을 지났으면 ETA 와 무관하게 움직인 것이다."""
    tracker = StallTracker(ratio_threshold=0.3)
    tracker.observe("S", "R", _veh(4, 300), now=0)
    assert tracker.observe("S", "R", _veh(3, 300), now=120) == 0


def test_tracker_forgets_vehicles_that_disappeared():
    tracker = StallTracker()
    tracker.observe("S", "R", _veh(4, 300), now=0)
    tracker.forget_missing(alive=set())
    assert tracker.counts_for("S", "R") == {}
