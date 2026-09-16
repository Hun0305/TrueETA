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
    entries = _entries_for_stop(stop, _items(stop.station_id), BoardState(), 3)
    assert {e.route_name for e in entries} == {"22", "59"}


def test_configured_route_missing_from_response_still_gets_a_row():
    """노선이 응답에서 빠져도 화면에서 줄이 사라지면 안 된다."""
    cfg = load_board_config()
    stop = cfg.stops[0]
    entries = _entries_for_stop(stop, [], BoardState(), 3)
    assert {e.route_name for e in entries} == {"22", "59"}
    assert all(e.status is Status.NO_BUS for e in entries)


def test_entry_is_json_serializable():
    cfg = load_board_config()
    stop = cfg.stops[0]
    entries = _entries_for_stop(stop, _items(stop.station_id), BoardState(), 3)
    json.dumps([e.as_dict() for e in entries])  # 던지면 실패


# --- 정체 추적 -----------------------------------------------------------


def _veh(location_no: int, eta: int) -> Vehicle:
    return Vehicle(
        veh_id="V1", plate_no="", predict_sec=eta, predict_min=None,
        location_no=location_no, state_cd=0, station_nm="",
    )


def test_stall_counts_up_when_bus_does_not_move():
    tracker = StallTracker()
    assert tracker.observe("S", "R", _veh(4, 300)) == 0   # 첫 관측
    assert tracker.observe("S", "R", _veh(4, 300)) == 1   # 그대로
    assert tracker.observe("S", "R", _veh(4, 310)) == 2   # ETA 가 오히려 늘어남


def test_stall_resets_when_bus_moves():
    tracker = StallTracker()
    tracker.observe("S", "R", _veh(4, 300))
    tracker.observe("S", "R", _veh(4, 300))
    assert tracker.observe("S", "R", _veh(3, 200)) == 0


def test_tracker_forgets_vehicles_that_disappeared():
    tracker = StallTracker()
    tracker.observe("S", "R", _veh(4, 300))
    tracker.forget_missing(alive=set())
    assert tracker.counts_for("S", "R") == {}
