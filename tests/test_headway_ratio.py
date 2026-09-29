"""배차 보정 학습과 '보통 M분' 노선별 표시.

9/29 저녁 1분 주기 결과: 22번은 공칭 배차(25분)보다 늘 3분쯤 일찍 와서 예상 M 이
체계적으로 늦었고(절대오차 4.1분), 25번은 들쭉날쭉했다(6.9분). 그래서
  - 배차간격을 실측 비율로 보정한다
  - 검증을 통과한 노선만 '보통 M분' 을 보여준다 (기본: 없음)
모든 경로는 tmp_path — 실제 var/ 를 건드리지 않는다.
"""

from datetime import datetime, timedelta

from trueeta.config import load_board_config
from trueeta.judge_absence import BVerdict
from trueeta.models import Status
from trueeta.poller import BContext, RouteState, record_cycle, with_absence
from trueeta.state import BoardState
from trueeta.storage import (
    HEADWAY_RATIO_MAX, ObservationLog, RouteCycle, headway_ratios,
)

T0 = datetime(2026, 9, 29, 20, 0).astimezone()


def iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


def episode(start, absent_min, eta_sec, headway=1500):
    """차가 보이다가 사라지고, absent_min 분 뒤 다시 보인다 (그때 ETA eta_sec).

    1분 주기 사이클 열을 (ts, n, eta, since, headway, status) 로 만든다.
    """
    rows = [(iso(start), 1, 60, None, headway, "running")]
    since = start + timedelta(minutes=1)
    for m in range(1, absent_min + 1):
        rows.append((iso(start + timedelta(minutes=m)), 0, None, iso(since), headway, "unseen"))
    rows.append((iso(start + timedelta(minutes=absent_min + 1)), 1, eta_sec, None, headway, "running"))
    return rows


# --- 구간 → 비율 --------------------------------------------------------


def test_ratio_is_actual_wait_over_nominal_headway():
    # 1분째에 차 없음 시작, 17분째에 첫 등장(ETA 360초) → 실제 대기 16분 + 6분 = 22분
    seq = episode(T0, 16, 360, headway=1500)
    (ratio,) = headway_ratios(seq)
    assert abs(ratio - (22 * 60) / 1500) < 0.01


def test_episode_must_start_from_a_seen_vehicle():
    """재시작 직후 복구한 구간은 시작 시각이 부정확하다. 차가 보이다 사라진 것만."""
    seq = episode(T0, 15, 360)[1:]      # 앞의 '차 보임' 사이클을 뺀다
    assert headway_ratios(seq) == []


def test_episode_cut_by_last_bus_is_dropped():
    seq = episode(T0, 15, 360)
    ts, n, eta, since, h, _ = seq[8]
    seq[8] = (ts, n, eta, since, h, "off")
    assert headway_ratios(seq) == []


def test_episode_across_a_long_gap_is_dropped():
    """운행창 밖·재시작으로 사이클이 끊기면 그 사이 무슨 일이 있었는지 모른다."""
    seq = episode(T0, 3, 360)
    last = seq[-1]
    seq[-1] = (iso(T0 + timedelta(hours=7)),) + last[1:]
    assert headway_ratios(seq) == []


def test_learned_ratio_is_median_and_needs_enough_episodes(tmp_path):
    db = ObservationLog(tmp_path / "o.db")
    start = datetime.now().astimezone().replace(microsecond=0) - timedelta(hours=6)
    # 22분 대기 5번 + 이상치 하나(59분 → 비율 2.4)
    for i, absent in enumerate([16, 16, 16, 16, 16, 53]):
        for ts, n, eta, since, h, st in episode(start + timedelta(minutes=70 * i), absent, 360):
            db.write_cycles([RouteCycle(
                station_id="S", route_name="22", route_id="R", n_vehicles=n,
                eta1_sec=eta, loc1=None, sta_order=31, turn_seq=20, flag="PASS",
                in_service=True, absent_since=since, headway_sec=h,
                a_status="no_bus" if n == 0 else "running", b_status=st,
                b_min_sec=366, b_expected_sec=None, b_overdue=False,
            )], ts=ts)
    got = db.learned_headway_ratio()
    assert abs(got[("S", "22")] - 0.88) < 0.01           # 이상치에 안 끌려간다
    assert db.learned_headway_ratio(min_samples=7) == {}


def test_ratio_is_clamped():
    seq = episode(T0, 80, 360, headway=1500)      # 86분 / 25분 = 3.4
    assert headway_ratios(seq)[0] > HEADWAY_RATIO_MAX  # 원값은 그대로, 자르는 건 학습 단계


# --- 판정에 적용 --------------------------------------------------------


class _Info:
    def in_service(self, *a, **k):
        return True

    def headway_sec(self, now):
        return 1500


class _Cache:
    def get(self, route_id):
        return _Info()


class _Arrival:
    route_id = "R"
    sta_order = 31
    turn_seq = 20
    flag = "PASS"
    vehicles = ()


def test_ratio_shortens_the_expected_wait_but_records_nominal():
    state = BoardState()
    key = ("S", "22")
    state.absences.update(key, 1, T0)
    now = T0 + timedelta(minutes=5)
    state.absences.update(key, 0, now)

    plain = BContext(now=now, route_info=_Cache(), learned={key: 366})
    fixed = BContext(now=now, route_info=_Cache(), learned={key: 366},
                     headway_ratio={key: 0.88})
    v_plain = record_cycle("S", "22", _Arrival(), "no_bus", state, plain)
    v_fixed = record_cycle("S", "22", _Arrival(), "no_bus", state, fixed)

    assert v_plain.expected_sec == 1500
    assert v_fixed.expected_sec == 1320                  # 1500 × 0.88
    # 기록에는 공칭값 — 보정값을 남기면 다음 학습이 또 보정한다
    assert fixed.cycles[0].headway_sec == 1500


# --- 화면 표시 ----------------------------------------------------------


def _state():
    return RouteState(dest_name="", status=Status.NO_BUS, eta_sec=None,
                      second_eta_sec=None, second_status=None,
                      estimated_wait=False, at_standing=False)


def test_hidden_expected_keeps_only_the_minimum():
    v = BVerdict("unseen", min_sec=366, expected_sec=900, overdue=True)
    got = with_absence(_state(), v, show_expected=False)
    assert got.wait_min_sec == 366
    assert got.wait_expected_sec is None
    assert got.wait_overdue is False       # 배차 넘김도 배차간격 추정이라 같이 숨긴다


def test_shown_expected_passes_through():
    v = BVerdict("unseen", min_sec=366, expected_sec=900)
    assert with_absence(_state(), v, show_expected=True).wait_expected_sec == 900


def test_config_hides_expected_everywhere_for_now():
    """9/29 검증에서 22·25 모두 불합격. 합격한 노선만 설정에 넣는다."""
    assert load_board_config().show_expected == frozenset()
