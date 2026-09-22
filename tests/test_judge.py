"""회차대기 판정 — 이 프로젝트의 핵심 로직.

순수 함수라 네트워크 없이 전 경우를 만들어 볼 수 있다.
실제 우리 정류장 값(staOrder/turnSeq)을 그대로 써서 현실성을 유지한다.
"""

from datetime import time

import pytest

from trueeta.judge import at_standing_point, judge_arrival, judge_vehicle
from trueeta.models import Arrival, Status, Vehicle
from trueeta.window import in_window, parse_hhmm


def vehicle(**kw) -> Vehicle:
    base = dict(
        veh_id="V1", plate_no="경기78아1234", predict_sec=300,
        predict_min=5, location_no=4, state_cd=0, station_nm="어딘가",
    )
    base.update(kw)
    return Vehicle(**base)


def arrival(*vehicles, flag="PASS", sta_order=27, turn_seq=23) -> Arrival:
    """기본값은 대일초교후문의 25번 (회차점이 4정거장 전)."""
    return Arrival(
        station_id="228003549", route_id="241428003", route_name="25",
        dest_name="미금역", sta_order=sta_order, turn_seq=turn_seq,
        flag=flag, vehicles=vehicles,
    )


# --- 판정 순서 -----------------------------------------------------------


def test_stop_flag_means_service_ended():
    verdict = judge_vehicle(arrival(vehicle(), flag="STOP"), vehicle())
    assert verdict.status is Status.ENDED


def test_no_vehicle_means_no_bus():
    assert judge_vehicle(arrival(), None).status is Status.NO_BUS


def test_flag_wait_is_confirmed_not_estimated():
    """API 가 WAIT 을 주면 추정이 아니다."""
    verdict = judge_vehicle(arrival(vehicle(), flag="WAIT"), vehicle())
    assert verdict.status is Status.WAITING
    assert verdict.estimated is False


def test_running_bus_is_running():
    """회차점이 아닌 위치에서 움직이는 차."""
    verdict = judge_vehicle(arrival(), vehicle(location_no=10, state_cd=2))
    assert verdict.status is Status.RUNNING


# --- 위치 기반 추정 ------------------------------------------------------


def test_bus_sitting_at_turn_point_is_estimated_wait():
    """25번의 핵심 사례.

    회차점이 4정거장 전이라, 거기 서 있는 차가 '4정거장 전 = 곧 도착'처럼 보인다.
    이게 버스를 놓치게 만든 상황이다.
    """
    at_turn = vehicle(location_no=4, state_cd=1)  # 27 - 4 = 23 = turnSeq
    verdict = judge_vehicle(arrival(at_turn), at_turn)
    assert verdict.status is Status.WAITING
    assert verdict.estimated is True


def test_bus_at_origin_is_also_a_standing_point():
    at_origin = vehicle(location_no=26, state_cd=1)  # 27 - 26 = 1 = 기점
    assert at_standing_point(arrival(at_origin), at_origin)


def test_passing_through_turn_point_is_not_a_wait():
    """회차점 위치라도 '교차로 통과' 중이면 대기가 아니다."""
    passing = vehicle(location_no=4, state_cd=0)
    verdict = judge_vehicle(arrival(passing), passing)
    assert verdict.status is Status.RUNNING


def test_long_stall_at_turn_point_becomes_a_wait():
    """stateCd 를 못 믿을 때의 보조 신호. 횟수가 아니라 초로 센다."""
    passing = vehicle(location_no=4, state_cd=0)
    verdict = judge_vehicle(
        arrival(passing), passing, stalled_sec=300, stall_seconds=240
    )
    assert verdict.status is Status.WAITING
    assert verdict.estimated is True


def test_short_stall_at_turn_point_is_not_yet_confirmed():
    passing = vehicle(location_no=4, state_cd=0)
    verdict = judge_vehicle(
        arrival(passing), passing, stalled_sec=120, stall_seconds=240
    )
    assert verdict.status is Status.RUNNING


def test_passing_turn_point_still_warns_the_screen():
    """확정은 못 해도 '여기 회차지다' 는 조회 1회로 즉시 알려야 한다.

    나가기 직전에 보는 화면이라 3회 관측을 기다릴 수 없다.
    """
    passing = vehicle(location_no=4, state_cd=0)
    verdict = judge_vehicle(arrival(passing), passing)
    assert verdict.status is Status.RUNNING
    assert verdict.at_standing is True


def test_normal_running_bus_does_not_warn():
    moving = vehicle(location_no=10, state_cd=2)
    assert judge_vehicle(arrival(moving), moving).at_standing is False


def test_59_has_no_turn_point_ahead_of_our_stop():
    """59번은 staOrder(22) < turnSeq(28) 이라 회차점이 우리 뒤에 있다.

    이 방향에서 대기 지점은 기점(1)뿐이다.
    """
    our_59 = lambda v: Arrival(
        station_id="228001059", route_id="241423008", route_name="59",
        dest_name="수지구청", sta_order=22, turn_seq=28, flag="PASS", vehicles=(v,),
    )
    at_origin = vehicle(location_no=21, state_cd=1)   # 22 - 21 = 1
    mid_route = vehicle(location_no=9, state_cd=1)    # 22 - 9 = 13, 대기지점 아님
    assert at_standing_point(our_59(at_origin), at_origin)
    assert not at_standing_point(our_59(mid_route), mid_route)


def test_missing_sta_order_disables_position_judgment():
    """순번을 모르면 추정하지 않는다. 틀린 회차대기 표시가 더 나쁘다."""
    v = vehicle(location_no=4, state_cd=1)
    assert not at_standing_point(arrival(v, sta_order=-1), v)


# --- 두 대 동시 판정 -----------------------------------------------------


def test_second_vehicle_is_judged_independently():
    waiting = vehicle(veh_id="V1", location_no=4, state_cd=1)
    running = vehicle(veh_id="V2", location_no=12, state_cd=2, predict_sec=900)
    first, second = judge_arrival(arrival(waiting, running))
    assert first.status is Status.WAITING
    assert second.status is Status.RUNNING


def test_single_vehicle_gives_no_second():
    first, second = judge_arrival(arrival(vehicle()))
    assert second is None


# --- 운행시간 창 ---------------------------------------------------------


@pytest.mark.parametrize("hhmm,expected", [
    ("05:49", False), ("05:50", True), ("12:00", True),
    ("23:59", True), ("00:05", True), ("00:10", False), ("03:00", False),
])
def test_service_window_crosses_midnight(hhmm, expected):
    hour, minute = (int(x) for x in hhmm.split(":"))
    assert in_window(time(hour, minute), parse_hhmm("05:50"), parse_hhmm("00:10")) is expected
