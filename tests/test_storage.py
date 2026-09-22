"""관측 로그 — 기록이 화면을 멈추게 하면 안 된다."""

import sqlite3

from trueeta.storage import Observation, ObservationLog


def _obs(**kw) -> Observation:
    base = dict(
        station_id="228003549", route_id="241428003", route_name="25", slot=1,
        veh_id="V1", plate_no="경기78아1234", flag="PASS", sta_order=27,
        turn_seq=23, location_no=4, position=23, state_cd=1,
        predict_sec=245, predict_min=4, status="waiting", estimated=True,
        reason="위치=대기지점 + 정류소도착",
    )
    base.update(kw)
    return Observation(**base)


def test_writes_rows(tmp_path):
    log = ObservationLog(tmp_path / "obs.db")
    assert log.enabled
    assert log.write([_obs(), _obs(slot=2, veh_id="V2")]) == 2

    rows = sqlite3.connect(tmp_path / "obs.db").execute(
        "SELECT route_name, position, turn_seq, status, estimated FROM observations"
    ).fetchall()
    assert rows == [("25", 23, 23, "waiting", 1), ("25", 23, 23, "waiting", 1)]


def test_disabled_when_path_is_none():
    """로그를 꺼도 폴러는 그대로 돈다."""
    log = ObservationLog(None)
    assert not log.enabled
    assert log.write([_obs()]) == 0


def test_write_failure_does_not_raise(tmp_path):
    """DB 가 깨져도 전광판은 계속 떠야 한다."""
    log = ObservationLog(tmp_path / "obs.db")
    log._conn.execute("DROP TABLE observations")
    log._conn.commit()
    assert log.write([_obs()]) == 0   # 예외가 아니라 0 을 돌려준다


def test_unwritable_path_disables_quietly(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("파일이라 디렉터리를 못 만든다")
    log = ObservationLog(blocker / "sub" / "obs.db")
    assert not log.enabled
    assert log.write([_obs()]) == 0
