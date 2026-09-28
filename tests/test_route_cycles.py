"""route_cycles — 차가 없어도 한 줄. A·B 판정을 나란히 남긴다.

observations 는 차가 있을 때만 행이 생겨 '차 없음'을 기록 못 한다.
B안을 평가하려면 이 표가 필요하다. 모든 경로는 tmp_path — 실제 var/ 를 건드리지 않는다.
"""

import glob
import json
from datetime import datetime, timedelta

import pytest

from trueeta.poller import BContext, judge_station
from trueeta.state import AbsenceTracker, BoardState
from trueeta.storage import ObservationLog, RouteCycle


def row(**kw) -> RouteCycle:
    base = dict(station_id="S", route_name="25", route_id="R", n_vehicles=0,
                eta1_sec=None, loc1=None, sta_order=27, turn_seq=23, flag="PASS",
                in_service=True, absent_since=None, headway_sec=900,
                a_status="no_bus", b_status="unseen", b_min_sec=180,
                b_expected_sec=600, b_overdue=False)
    base.update(kw)
    return RouteCycle(**base)


def iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


# --- 기록 ---------------------------------------------------------------


def test_absent_route_still_gets_a_row(tmp_path):
    db = ObservationLog(tmp_path / "o.db")
    assert db.write_cycles([row(n_vehicles=0)]) == 1
    n = db._conn.execute("SELECT n_vehicles, a_status, b_status FROM route_cycles").fetchone()
    assert n == (0, "no_bus", "unseen")


def test_same_cycle_twice_is_replaced_not_duplicated(tmp_path):
    db = ObservationLog(tmp_path / "o.db")
    db.write_cycles([row()], ts="2026-09-29T12:00:00+09:00")
    db.write_cycles([row(n_vehicles=1)], ts="2026-09-29T12:00:00+09:00")
    assert db._conn.execute("SELECT COUNT(*) FROM route_cycles").fetchone()[0] == 1


# --- N 학습 -------------------------------------------------------------


def test_learns_minimum_from_absent_to_present_transitions(tmp_path):
    """'차 없음' 다음 첫 등장의 ETA 가 N. 회차점 바로 다음(loc≥경계−2)만 쓴다."""
    db = ObservationLog(tmp_path / "o.db")
    t = datetime.now() - timedelta(hours=3)
    etas = [160, 175, 190]
    for i, eta in enumerate(etas):
        base = t + timedelta(minutes=30 * i)
        db.write_cycles([row(n_vehicles=0)], ts=iso(base))
        db.write_cycles([row(n_vehicles=1, eta1_sec=eta, loc1=3)], ts=iso(base + timedelta(minutes=2)))
    assert db.learned_min_travel() == {("S", "25"): 175}


def test_first_seen_far_along_is_not_used(tmp_path):
    """10분 주기면 이미 한참 온 뒤(loc 1)에 처음 잡힌다. 그건 N 이 아니다."""
    db = ObservationLog(tmp_path / "o.db")
    t = datetime.now() - timedelta(hours=3)
    for i in range(3):
        base = t + timedelta(minutes=30 * i)
        db.write_cycles([row(n_vehicles=0)], ts=iso(base))
        db.write_cycles([row(n_vehicles=1, eta1_sec=40, loc1=1)], ts=iso(base + timedelta(minutes=10)))
    assert db.learned_min_travel() == {}


def test_too_few_samples_learn_nothing(tmp_path):
    db = ObservationLog(tmp_path / "o.db")
    t = datetime.now() - timedelta(hours=1)
    db.write_cycles([row(n_vehicles=0)], ts=iso(t))
    db.write_cycles([row(n_vehicles=1, eta1_sec=170, loc1=3)], ts=iso(t + timedelta(minutes=2)))
    assert db.learned_min_travel() == {}


# --- 재시작 복구 --------------------------------------------------------


def test_open_absence_is_restored_after_restart(tmp_path):
    db = ObservationLog(tmp_path / "o.db")
    since = iso(datetime.now() - timedelta(minutes=8))
    db.write_cycles([row(n_vehicles=0, absent_since=since)], ts=iso(datetime.now() - timedelta(minutes=2)))
    assert db.open_absences() == {("S", "25"): since}


def test_stale_absence_is_not_restored(tmp_path):
    """꺼져 있던 동안의 '차 없음'을 이어 붙이면 M 이 엉터리가 된다."""
    db = ObservationLog(tmp_path / "o.db")
    old = datetime.now() - timedelta(hours=5)
    db.write_cycles([row(n_vehicles=0, absent_since=iso(old))], ts=iso(old))
    assert db.open_absences() == {}


# --- 차 없음 추적 -------------------------------------------------------


def test_absence_starts_when_vehicle_disappears_and_clears_when_it_returns():
    tr = AbsenceTracker()
    t0 = datetime(2026, 9, 29, 12, 0).astimezone()
    assert tr.update(("S", "25"), 1, t0) is None
    assert tr.update(("S", "25"), 0, t0 + timedelta(minutes=2)) == t0 + timedelta(minutes=2)
    assert tr.update(("S", "25"), 0, t0 + timedelta(minutes=4)) == t0 + timedelta(minutes=2)
    assert tr.update(("S", "25"), 1, t0 + timedelta(minutes=6)) is None


# --- 폴러 통합: 실제 응답 ----------------------------------------------


def _items(station_id):
    paths = sorted(glob.glob(f"tests/fixtures/arrivals_{station_id}_*.json"))
    if not paths:
        pytest.skip("fixture 없음")
    return json.load(open(paths[-1], encoding="utf-8"))["response"]["msgBody"]["busArrivalList"]


def test_real_response_writes_a_row_for_every_wanted_route():
    """도담마을 fixture: 22번은 차 없음, 59번은 차 있음. 없는 노선도 한 줄."""
    bctx = BContext(now=datetime.now().astimezone())
    judge_station("228001059", {"22", "59", "999"}, _items("228001059"),
                  BoardState(), 240, set(), [], bctx)
    got = {c.route_name: c for c in bctx.cycles}

    assert set(got) == {"22", "59", "999"}
    assert got["22"].n_vehicles == 0 and got["22"].b_status == "unseen"
    assert got["22"].sta_order == 31 and got["22"].turn_seq == 20
    assert got["59"].n_vehicles > 0 and got["59"].b_status == "running"
    assert got["999"].n_vehicles == 0 and got["999"].a_status == "no_bus"


def test_a_and_b_are_recorded_side_by_side():
    """A안이 no_bus 라 해도 B안은 unseen + 최소 N 을 낸다."""
    bctx = BContext(now=datetime.now().astimezone())
    judge_station("228001059", {"22"}, _items("228001059"),
                  BoardState(), 240, set(), [], bctx)
    c = bctx.cycles[0]
    assert c.a_status == "no_bus"
    assert c.b_status == "unseen" and c.b_min_sec and c.b_min_sec > 0


def test_learned_minimum_overrides_the_default():
    bctx = BContext(now=datetime.now().astimezone(), learned={("228001059", "22"): 431})
    judge_station("228001059", {"22"}, _items("228001059"),
                  BoardState(), 240, set(), [], bctx)
    assert bctx.cycles[0].b_min_sec == 431
