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

-- 사이클마다 (정류장, 노선) 한 줄. 차가 없어도 남긴다.
--
-- observations 는 차가 있을 때만 행이 생겨서 '차 없음'을 기록하지 못한다.
-- 그런데 GBIS 는 회차지를 출발한 차만 보여주므로, 회차대기는 바로 그
-- '차 없음'으로 나타난다. B안(judge_absence.py)을 평가하려면 이 표가 필요하다.
-- A·B 두 판정을 같은 줄에 나란히 남겨 나중에 비교한다.
CREATE TABLE IF NOT EXISTS route_cycles (
    ts             TEXT    NOT NULL,
    station_id     TEXT    NOT NULL,
    route_name     TEXT    NOT NULL,
    route_id       TEXT,
    n_vehicles     INTEGER NOT NULL,   -- 0 이면 '차 없음'
    eta1_sec       INTEGER,
    loc1           INTEGER,
    sta_order      INTEGER,
    turn_seq       INTEGER,
    flag           TEXT,
    in_service     INTEGER,            -- 노선별 첫차~막차 안인가 (NULL = 노선 정보 없음)
    absent_since   TEXT,               -- '차 없음'이 시작된 시각
    headway_sec    INTEGER,            -- 그 시각의 배차간격
    a_status       TEXT,               -- A안 판정 (judge.py)
    b_status       TEXT,               -- B안 판정 (judge_absence.py)
    b_min_sec      INTEGER,            -- B안의 최소 N
    b_expected_sec INTEGER,            -- B안의 예상 M
    b_overdue      INTEGER,
    PRIMARY KEY (ts, station_id, route_name)
);
CREATE INDEX IF NOT EXISTS idx_cyc_route ON route_cycles (station_id, route_name, ts);
"""


#: 배차 보정 학습 — 이보다 적은 '차 없음' 구간으로는 배우지 않는다.
#: 한 구간 안의 매 사이클 예측은 같은 사건이라 표본은 구간 수다.
HEADWAY_MIN_SAMPLES = 5
#: 비율이 이 밖이면 잘라낸다. 결행·재시작 같은 사고 몇 건에 휘둘리지 않게
HEADWAY_RATIO_MIN = 0.5
HEADWAY_RATIO_MAX = 1.5
#: 사이클 사이가 이보다 벌어지면 (재시작·운행창 밖) 그 구간은 버린다
_MAX_CYCLE_GAP_SEC = 15 * 60
#: 차 없음이 이보다 길면 막차·결행으로 보고 버린다 (analyze.MAX_MATCH_SEC 와 같다)
_MAX_EPISODE_SEC = 90 * 60


def headway_ratios(seq: list[tuple]) -> list[float]:
    """한 (정류장, 노선)의 사이클 열에서 '차 없음' 구간마다 실측 ÷ 공칭 비율.

    seq 항목: (ts, n_vehicles, eta1_sec, absent_since, headway_sec, b_status),
    ts 순서.

    구간은 **차가 보이다가 사라진 사이클**에서 시작하는 것만 쓴다. 재시작
    직후 복구한 구간은 시작 시각이 부정확하다. 끝은 다음에 차가 처음 보인
    사이클이고, 실제 대기 = (그 시각 − 차 없음 시작) + 그때 1호차 ETA.
    B안의 M = H − (지금 − 차 없음 시작) 이 맞히려는 바로 그 값이다.

    분모는 **그 시각의 공칭 배차간격**(기록된 headway_sec)이다. 보정된 값을
    기록하면 다음 학습이 보정값을 또 보정해 값이 흘러간다.
    """
    ratios = []
    for i in range(1, len(seq)):
        ts, n, _eta, since, headway, status = seq[i]
        if not (seq[i - 1][1] > 0 and n == 0 and since and headway and status == "unseen"):
            continue
        start = datetime.fromisoformat(since)
        prev_ts = datetime.fromisoformat(ts)
        for nxt in seq[i + 1:]:
            nxt_ts = datetime.fromisoformat(nxt[0])
            if (nxt_ts - prev_ts).total_seconds() > _MAX_CYCLE_GAP_SEC:
                break
            if nxt[5] in ("off", "ended", "missing"):
                break
            if nxt[1] > 0:
                if nxt[2] is not None:
                    actual = (nxt_ts - start).total_seconds() + nxt[2]
                    if 0 < actual <= _MAX_EPISODE_SEC:
                        ratios.append(actual / headway)
                break
            prev_ts = nxt_ts
    return ratios


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


@dataclass(frozen=True)
class RouteCycle:
    station_id: str
    route_name: str
    route_id: str
    n_vehicles: int
    eta1_sec: int | None
    loc1: int | None
    sta_order: int | None
    turn_seq: int | None
    flag: str
    in_service: bool | None
    absent_since: str | None
    headway_sec: int | None
    a_status: str
    b_status: str
    b_min_sec: int | None
    b_expected_sec: int | None
    b_overdue: bool


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

    def write_cycles(self, rows: list[RouteCycle], *, ts: str | None = None) -> int:
        """사이클 기록. 같은 사이클이 두 번 오면 덮어쓴다 (PRIMARY KEY)."""
        if self._conn is None or not rows:
            return 0
        stamp = ts or datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            self._conn.executemany(
                """INSERT OR REPLACE INTO route_cycles (
                       ts, station_id, route_name, route_id, n_vehicles, eta1_sec, loc1,
                       sta_order, turn_seq, flag, in_service, absent_since, headway_sec,
                       a_status, b_status, b_min_sec, b_expected_sec, b_overdue
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        stamp, r.station_id, r.route_name, r.route_id, r.n_vehicles,
                        r.eta1_sec, r.loc1, r.sta_order, r.turn_seq, r.flag,
                        None if r.in_service is None else int(r.in_service),
                        r.absent_since, r.headway_sec, r.a_status, r.b_status,
                        r.b_min_sec, r.b_expected_sec, int(r.b_overdue),
                    )
                    for r in rows
                ],
            )
            self._conn.commit()
            return len(rows)
        except sqlite3.Error:
            log.exception("사이클 기록 실패")
            return 0

    def learned_min_travel(self, *, days: int = 14, min_samples: int = 3) -> dict[tuple[str, str], int]:
        """B안의 N 을 로그에서 배운다.

        '차 없음' 다음 사이클에 처음 보인 차의 ETA 가 곧 "회차지를 떠나면 최소
        이만큼" 이다. 단 주기가 길면 이미 한참 온 뒤에 처음 잡히므로, 회차점
        바로 다음(경계 −2 이내)에서 잡힌 것만 쓴다. 노선별 중앙값.

        veh_id 는 쓰지 않는다 — 같은 차가 하루에 여러 번 왕복해서 '차량별 첫 등장'
        으로는 첫 운행만 잡힌다.
        """
        if self._conn is None:
            return {}
        try:
            rows = self._conn.execute(
                """SELECT station_id, route_name, eta1_sec, loc1, sta_order, turn_seq
                   FROM (
                     SELECT *, LAG(n_vehicles) OVER (
                         PARTITION BY station_id, route_name ORDER BY ts) AS prev_n
                     FROM route_cycles
                     WHERE ts >= datetime('now', ?)
                   )
                   WHERE prev_n = 0 AND n_vehicles > 0 AND eta1_sec IS NOT NULL""",
                (f"-{days} days",),
            ).fetchall()
        except sqlite3.Error:
            log.exception("N 학습 실패")
            return {}

        from trueeta.judge_absence import edge_distance

        samples: dict[tuple[str, str], list[int]] = {}
        for station, route, eta, loc, order, turn in rows:
            edge = edge_distance(order, turn)
            if edge is None or loc is None or loc < edge - 2:
                continue
            samples.setdefault((station, route), []).append(eta)

        learned = {}
        for key, values in samples.items():
            if len(values) >= min_samples:
                values.sort()
                learned[key] = values[len(values) // 2]
        return learned

    def learned_headway_ratio(
        self, *, days: int = 14, min_samples: int = HEADWAY_MIN_SAMPLES
    ) -> dict[tuple[str, str], float]:
        """B안의 배차간격 H 를 실측으로 보정하는 비율을 배운다.

        노선 정보 API 의 배차간격은 공칭값이다. 9/29 저녁 22번은 공칭 25분인데
        실제로는 늘 3분쯤 일찍 와서 예상 M 이 체계적으로 늦었다.

        절대값이 아니라 **비율**(실측 ÷ 공칭)로 배운다. 저녁 데이터만 있어도
        출근·주말 시간대의 공칭값에 곱해 쓸 수 있다.
        """
        if self._conn is None:
            return {}
        try:
            rows = self._conn.execute(
                """SELECT station_id, route_name, ts, n_vehicles, eta1_sec,
                          absent_since, headway_sec, b_status
                   FROM route_cycles
                   WHERE ts >= datetime('now', ?)
                   ORDER BY station_id, route_name, ts""",
                (f"-{days} days",),
            ).fetchall()
        except sqlite3.Error:
            log.exception("배차 보정 학습 실패")
            return {}

        groups: dict[tuple[str, str], list] = {}
        for r in rows:
            groups.setdefault((r[0], r[1]), []).append(r[2:])

        learned = {}
        for key, seq in groups.items():
            ratios = headway_ratios(seq)
            if len(ratios) >= min_samples:
                ratios.sort()
                ratio = ratios[len(ratios) // 2]
                learned[key] = min(HEADWAY_RATIO_MAX, max(HEADWAY_RATIO_MIN, ratio))
        return learned

    def open_absences(self, *, max_age_min: int = 120) -> dict[tuple[str, str], str]:
        """재시작 직후 '차 없음이 언제부터였나' 를 복구한다.

        마지막 기록이 차 없음이고 너무 오래되지 않았으면 그 absent_since 를 돌려준다.
        이게 없으면 재시작 뒤 첫 차가 올 때까지 B안이 예상 M 을 못 낸다.
        """
        if self._conn is None:
            return {}
        try:
            rows = self._conn.execute(
                """SELECT station_id, route_name, n_vehicles, absent_since, ts
                   FROM route_cycles r
                   WHERE ts = (SELECT MAX(ts) FROM route_cycles
                               WHERE station_id = r.station_id AND route_name = r.route_name)"""
            ).fetchall()
        except sqlite3.Error:
            return {}
        now = datetime.now().astimezone()
        out = {}
        for station, route, n, since, ts in rows:
            if n == 0 and since:
                try:
                    age = (now - datetime.fromisoformat(ts)).total_seconds() / 60
                except ValueError:
                    continue
                if age <= max_age_min:
                    out[(station, route)] = since
        return out

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
