"""B안 판정 — "차 없음"을 읽는다. 순수 함수라 네트워크 없이 전 경우를 만든다.

전제(9/24·9/28 실측): GBIS 는 회차지를 출발한 차만 보여준다.
회차점 위치의 차는 374행 중 0건. 회차대기는 '차 없음'으로 나타난다.
"""

from trueeta.judge_absence import (
    ENDED, MISSING, OFF, RUNNING, UNSEEN, RouteSnapshot,
    default_min_travel, edge_distance, judge_absence,
)


def snap(**kw) -> RouteSnapshot:
    base = dict(n_vehicles=0, eta1_sec=None, flag="PASS", in_service=True,
                absent_for_sec=300, min_travel_sec=180, headway_sec=900)
    base.update(kw)
    return RouteSnapshot(**base)


def test_stop_flag_is_ended():
    assert judge_absence(snap(flag="STOP")).status == ENDED


def test_vehicle_present_is_running_with_its_eta():
    v = judge_absence(snap(n_vehicles=1, eta1_sec=240))
    assert v.status == RUNNING and v.eta_sec == 240


def test_no_vehicle_outside_route_hours_is_off_not_waiting():
    """막차 뒤의 '차 없음'을 회차대기로 부르면 안 된다."""
    assert judge_absence(snap(in_service=False)).status == OFF


def test_unknown_service_hours_counts_as_in_service():
    """노선 정보를 못 받아도 폴러가 service_window 안에서만 돌므로 운행 중으로 본다."""
    assert judge_absence(snap(in_service=None)).status == UNSEEN


def test_unseen_gives_minimum_and_expected():
    """배차 15분, 차 안 보인 지 5분 -> 보통 10분 뒤. 최소는 주행시간 3분."""
    v = judge_absence(snap(absent_for_sec=300, headway_sec=900, min_travel_sec=180))
    assert v.status == UNSEEN
    assert v.min_sec == 180
    assert v.expected_sec == 600
    assert not v.overdue


def test_expected_never_below_minimum():
    """배차간격이 거의 다 됐어도 회차지에서 여기까지 오는 시간은 걸린다."""
    v = judge_absence(snap(absent_for_sec=850, headway_sec=900, min_travel_sec=180))
    assert v.expected_sec == 180


def test_overdue_when_gap_exceeds_headway():
    """25번 실측에 40분 공백이 있었다. 그때는 '배차간격 초과'로 표시한다."""
    v = judge_absence(snap(absent_for_sec=2400, headway_sec=900, min_travel_sec=180))
    assert v.overdue and v.expected_sec == 180


def test_unseen_without_headway_still_gives_minimum():
    v = judge_absence(snap(headway_sec=None))
    assert v.status == UNSEEN and v.min_sec == 180 and v.expected_sec is None


def test_unseen_without_absence_start_still_gives_minimum():
    v = judge_absence(snap(absent_for_sec=None))
    assert v.min_sec == 180 and v.expected_sec is None


# --- 첫 등장 위치 (실측과 대조) -----------------------------------------


def test_edge_distance_matches_the_observed_cutoff():
    """관측된 최대 위치가 정확히 경계−1 이었다: 22번 10, 25번 3, 59번 20."""
    assert edge_distance(31, 20) == 11   # 22번 — 회차점 뒤
    assert edge_distance(27, 23) == 4    # 25번 — 회차점 뒤
    assert edge_distance(22, 28) == 21   # 59번 — 회차점이 앞이라 기점 기준


def test_default_minimum_is_close_to_measured():
    """실측 N: 25번 157~190초, 22번 424~448초."""
    assert 120 <= default_min_travel(27, 23) <= 200     # 25번
    assert 400 <= default_min_travel(31, 20) <= 600     # 22번


def test_default_minimum_without_order_info():
    assert default_min_travel(None, None) > 0


def test_route_missing_from_response_is_not_a_turnaround_wait():
    """GBIS 는 차가 없는 노선도 목록에 넣는다 (fixture 5개 전부).
    노선이 아예 없으면 설정 오류다. 이걸 '회차지 대기' 로 읽으면 가짜 ID 로도
    영원히 회차대기라고 말하게 된다."""
    v = judge_absence(snap(listed=False))
    assert v.status == MISSING
    assert v.min_sec is None and v.expected_sec is None
