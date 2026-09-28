"""analyze.py --compare — B안 예측을 실제 도착과 맞춰보는 계산이 맞는가."""

import sqlite3
from datetime import datetime, timedelta

import analyze
from trueeta.storage import ObservationLog, RouteCycle


def cyc(n, **kw):
    base = dict(station_id="S", route_name="25", route_id="R", n_vehicles=n,
                eta1_sec=None, loc1=None, sta_order=27, turn_seq=23, flag="PASS",
                in_service=True, absent_since=None, headway_sec=900,
                a_status="running" if n else "no_bus",
                b_status="running" if n else "unseen",
                b_min_sec=None if n else 150, b_expected_sec=None, b_overdue=False)
    base.update(kw)
    return RouteCycle(**base)


def iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


def _scenario(db):
    """25번: 차 보임 -> 2분 뒤 사라짐 -> 4분 뒤 다시 보임(3정거장 전, ETA 170초).

    B안은 사라진 순간 '예상 900초' (배차 15분), 2분 뒤 '예상 780초' 라고 했다.
    실제 대기: 첫 unseen 기준 240+170 = 410초, 둘째 기준 120+170 = 290초.
    오차는 둘 다 -490초 (실제가 8분 넘게 빨랐다).
    """
    log = ObservationLog(db)
    t = datetime.now() - timedelta(hours=1)
    gone = iso(t + timedelta(minutes=2))
    log.write_cycles([cyc(1, eta1_sec=60, loc1=1)], ts=iso(t))
    log.write_cycles([cyc(0, absent_since=gone, b_expected_sec=900)], ts=gone)
    log.write_cycles([cyc(0, absent_since=gone, b_expected_sec=780)], ts=iso(t + timedelta(minutes=4)))
    log.write_cycles([cyc(1, eta1_sec=170, loc1=3)], ts=iso(t + timedelta(minutes=6)))


def test_matches_predictions_with_actual_arrivals(tmp_path):
    db = tmp_path / "o.db"
    _scenario(db)
    r = analyze.compare(db)["routes"][("S", "25")]
    assert r["matched"] == 2
    assert r["m_bias"] == -490
    assert r["under_min"] == 0


def test_reports_that_a_never_fired(tmp_path):
    db = tmp_path / "o.db"
    _scenario(db)
    assert analyze.compare(db)["routes"][("S", "25")]["a_waiting"] == 0


def test_absence_shorter_than_headway_is_not_counted_as_over(tmp_path):
    db = tmp_path / "o.db"
    _scenario(db)
    r = analyze.compare(db)["routes"][("S", "25")]
    assert r["gaps"] == 1 and r["gap_over_headway"] == 0


def test_arrival_faster_than_minimum_is_flagged(tmp_path):
    """최소 N 보다 빨리 오면 N 이 너무 크다는 뜻이다."""
    db = tmp_path / "o.db"
    log = ObservationLog(db)
    t = datetime.now() - timedelta(hours=1)
    log.write_cycles([cyc(0, absent_since=iso(t), b_min_sec=600, b_expected_sec=900)], ts=iso(t))
    log.write_cycles([cyc(1, eta1_sec=60, loc1=3)], ts=iso(t + timedelta(minutes=2)))
    assert analyze.compare(db)["routes"][("S", "25")]["under_min"] == 1


def test_off_in_between_is_not_matched(tmp_path):
    """막차로 끊긴 뒤 다음 날 첫차를 '실제 도착' 으로 맞추면 안 된다."""
    db = tmp_path / "o.db"
    log = ObservationLog(db)
    t = datetime.now() - timedelta(hours=2)
    log.write_cycles([cyc(0, b_expected_sec=900)], ts=iso(t))
    log.write_cycles([cyc(0, b_status="off", b_min_sec=None)], ts=iso(t + timedelta(minutes=10)))
    log.write_cycles([cyc(1, eta1_sec=100, loc1=3)], ts=iso(t + timedelta(minutes=20)))
    assert analyze.compare(db)["routes"][("S", "25")]["matched"] == 0


def test_without_route_cycles_table(tmp_path):
    db = tmp_path / "o.db"
    sqlite3.connect(db).execute("CREATE TABLE observations (id INTEGER)").connection.commit()
    assert analyze.compare(db) == {}
