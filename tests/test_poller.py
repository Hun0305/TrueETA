"""보드 조립 — 설정 + 실응답 -> 화면에 나갈 줄."""

import glob
import json

import pytest

from trueeta.config import Preset, Stop, load_board_config
from trueeta.models import Status
from trueeta.poller import board_for_preset, judge_station, wanted_routes
from trueeta.state import BoardState, StallTracker
from trueeta.models import Vehicle


def _items(station_id: str) -> list[dict]:
    paths = sorted(glob.glob("tests/fixtures/arrivals_%s_*.json" % station_id))
    if not paths:
        pytest.skip("fixture 없음: %s" % station_id)
    return json.load(open(paths[-1], encoding="utf-8"))["response"]["msgBody"]["busArrivalList"]


def _judged(stop, items, state=None):
    """정류장 하나를 판정해 (정류장, 노선) -> RouteState 맵으로."""
    return judge_station(
        stop.station_id, set(stop.routes), items,
        state or BoardState(), 240, set(), [],
    )


def test_only_configured_routes_survive():
    cfg = load_board_config()
    stop = cfg.stops[0]  # 도담마을 — 22, 59
    judged = _judged(stop, _items(stop.station_id))
    assert {route for _, route in judged} == {"22", "59"}


def test_configured_route_missing_from_response_still_gets_a_row():
    """노선이 응답에서 빠져도 화면에서 줄이 사라지면 안 된다."""
    cfg = load_board_config()
    board = board_for_preset(cfg.default_preset, judged={}, errors={})
    assert {e.route_name for e in board.entries} == {"22", "25", "59"}
    assert all(e.status is Status.NO_BUS for e in board.entries)


def test_board_keeps_config_order():
    cfg = load_board_config()
    board = board_for_preset(cfg.default_preset, {}, {})
    assert [e.route_name for e in board.entries] == ["22", "59", "25"]


def test_entry_is_json_serializable():
    cfg = load_board_config()
    stop = cfg.stops[0]
    board = board_for_preset(cfg.default_preset, _judged(stop, _items(stop.station_id)), {})
    json.dumps(board.as_dict())  # 던지면 실패


def test_error_on_one_station_does_not_blank_the_others():
    """한 정류장이 실패해도 나머지 줄은 살아 있어야 한다."""
    cfg = load_board_config()
    preset = cfg.default_preset
    failing = preset.stops[1].station_id
    board = board_for_preset(preset, {}, {failing: "ReadTimeout"})
    assert board.error is not None and "ReadTimeout" in board.error
    assert len(board.entries) == 3    # 줄은 그대로 남는다


# --- 프리셋: 정류장 중복 제거 -------------------------------------------


def test_shared_station_is_requested_once():
    """프리셋 둘이 같은 정류장을 봐도 호출은 한 번이어야 한다."""
    cfg = load_board_config()
    a = cfg.default_preset
    b = Preset(name="겹침", stops=(a.stops[0],))
    wanted = wanted_routes([a, b])
    assert len(wanted) == 2                      # 정류장은 2곳뿐
    assert wanted[a.stops[0].station_id] == {"22", "59"}


def test_union_of_routes_at_a_shared_station():
    """같은 정류장을 다른 노선으로 볼 때 합집합을 조회한다."""
    stop_a = Stop(station_id="S1", name="정류장", routes=("22",))
    stop_b = Stop(station_id="S1", name="정류장", routes=("59",))
    wanted = wanted_routes([Preset("A", (stop_a,)), Preset("B", (stop_b,))])
    assert wanted == {"S1": {"22", "59"}}


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
