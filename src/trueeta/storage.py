"""관측 로그 (SQLite).

판정 임계값을 정하려면 근거가 필요한데 지금은 하나도 없다.
매 폴링에서 본 차량을 그대로 남긴다 — API 호출은 1콜도 늘지 않는다.
이미 받은 응답을 저장만 하는 것이다.

여기 쌓인 걸로 답할 것:
  - 정상 주행일 때 ETA 는 실제로 초당 몇 초씩 줄어드는가
  - 회차지에 선 차는 얼마나 오래 서 있는가
  - judge.stall_ratio / stall_seconds 를 얼마로 둬야 하는가
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger("trueeta.storage")

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,   -- ISO8601, 로컬 타임존
    station_id   TEXT    NOT NULL,
    route_id     TEXT    NOT NULL,
    route_name   TEXT    NOT NULL,
    slot         INTEGER NOT NULL,   -- 1호차 / 2호차
    veh_id       TEXT,
    plate_no     TEXT,
    flag         TEXT,               -- RUN/PASS/STOP/WAIT
    sta_order    INTEGER,            -- 우리 정류장의 노선 내 순번
    turn_seq     INTEGER,            -- 회차점 순번
    location_no  INTEGER,            -- 몇 정거장 전
    position     INTEGER,            -- sta_order - location_no (차량 위치 순번)
    state_cd     INTEGER,            -- 0 통과 · 1 도착 · 2 출발
    predict_sec  INTEGER,
    predict_min  INTEGER,
    status       TEXT,               -- 판정 결과
    estimated    INTEGER,            -- 회차대기가 추정이면 1
    reason       TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_veh ON observations (veh_id, ts);
CREATE INDEX IF NOT EXISTS idx_obs_ts  ON observations (ts);
"""


@dataclass(frozen=True)
class Observation:
    station_id: str
    route_id: str
    route_name: str
    slot: int
    veh_id: str
    plate_no: str
    flag: str
    sta_order: int | None
    turn_seq: int | None
    location_no: int | None
    position: int | None
    state_cd: int | None
    predict_sec: int | None
    predict_min: int | None
    status: str
    estimated: bool
    reason: str


class ObservationLog:
    """없으면 조용히 비활성. 로그 때문에 전광판이 죽으면 안 된다."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        except (sqlite3.Error, OSError):
            # mkdir 실패는 OSError 라 sqlite3.Error 로는 안 잡힌다.
            # 로그 때문에 전광판이 죽으면 안 되므로 조용히 비활성화한다.
            log.exception("관측 로그를 열지 못했다 — 로그 없이 계속한다")
            self._conn = None

    @property
    def enabled(self) -> bool:
        return self._conn is not None

    def write(self, rows: list[Observation], *, ts: str | None = None) -> int:
        if self._conn is None or not rows:
            return 0

        stamp = ts or datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            self._conn.executemany(
                """INSERT INTO observations (
                       ts, station_id, route_id, route_name, slot, veh_id, plate_no,
                       flag, sta_order, turn_seq, location_no, position, state_cd,
                       predict_sec, predict_min, status, estimated, reason
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        stamp, r.station_id, r.route_id, r.route_name, r.slot,
                        r.veh_id, r.plate_no, r.flag, r.sta_order, r.turn_seq,
                        r.location_no, r.position, r.state_cd, r.predict_sec,
                        r.predict_min, r.status, int(r.estimated), r.reason,
                    )
                    for r in rows
                ],
            )
            self._conn.commit()
            return len(rows)
        except sqlite3.Error:
            # 기록 실패가 화면을 멈추게 해서는 안 된다
            log.exception("관측 로그 기록 실패")
            return 0

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
