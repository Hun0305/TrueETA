#!/usr/bin/env python3
"""관측 로그 분석 — judge 임계값을 데이터로 정하기 위한 도구.

`var/observations.db` 에 폴러가 남긴 기록을 읽는다. API 는 호출하지 않는다.

    python scripts/analyze.py              # 전체 요약
    python scripts/analyze.py --days 3     # 최근 3일
    python scripts/analyze.py --route 25   # 노선 하나만

답하려는 질문:
  1. 정상 주행일 때 ETA 감소율은 실제로 얼마인가  -> judge.stall_ratio
  2. 회차지에 선 차는 얼마나 오래 서 있는가        -> judge.stall_seconds
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "var" / "observations.db"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[index]


def summarize(values: list[float], unit: str = "") -> str:
    if not values:
        return "  (표본 없음)"
    return (
        f"  표본 {len(values):5d}   "
        f"최소 {min(values):6.2f}{unit}  "
        f"p25 {percentile(values, 0.25):6.2f}{unit}  "
        f"중앙 {percentile(values, 0.50):6.2f}{unit}  "
        f"p75 {percentile(values, 0.75):6.2f}{unit}  "
        f"최대 {max(values):6.2f}{unit}"
    )


def load_rows(conn: sqlite3.Connection, days: int | None, route: str | None):
    where, params = [], []
    if days:
        since = (datetime.now().astimezone() - timedelta(days=days)).isoformat()
        where.append("ts >= ?")
        params.append(since)
    if route:
        where.append("route_name = ?")
        params.append(route)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return conn.execute(
        f"""SELECT ts, station_id, route_id, route_name, veh_id, location_no,
                   position, turn_seq, state_cd, predict_sec, predict_min, status
            FROM observations {clause} ORDER BY veh_id, ts""",
        params,
    ).fetchall()


def eta_of(predict_sec, predict_min) -> int | None:
    if predict_sec is not None:
        return predict_sec
    if predict_min is not None:
        return predict_min * 60
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, help="최근 N일만")
    parser.add_argument("--route", help="노선번호 (예: 25)")
    parser.add_argument("--db", type=Path, default=DB)
    args = parser.parse_args()

    if not args.db.exists():
        print(f"{args.db} 가 없습니다. 폴러를 돌려 로그를 쌓으세요.", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    rows = load_rows(conn, args.days, args.route)
    if not rows:
        print("해당 조건의 기록이 없습니다.")
        return 0

    print(f"기록 {len(rows)}행  ({rows[0][0][:16]} ~ {rows[-1][0][:16]})\n")

    # --- 상태 분포 ------------------------------------------------------
    status_count: dict[str, int] = defaultdict(int)
    for r in rows:
        status_count[r[11]] += 1
    print("판정 분포")
    for status, count in sorted(status_count.items(), key=lambda x: -x[1]):
        print(f"  {status:10s} {count:6d}  ({count / len(rows) * 100:.1f}%)")

    # --- ETA 감소율 -----------------------------------------------------
    # 같은 차량의 연속 관측 쌍에서 (ΔETA / Δt) 를 구한다.
    by_vehicle: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_vehicle[(r[1], r[2], r[4])].append(r)

    moving, standing = [], []
    dwell: dict[tuple, float] = defaultdict(float)

    for key, seq in by_vehicle.items():
        for prev, cur in zip(seq, seq[1:]):
            t0 = datetime.fromisoformat(prev[0])
            t1 = datetime.fromisoformat(cur[0])
            elapsed = (t1 - t0).total_seconds()
            if not (0 < elapsed <= 1800):  # 재시작 등으로 벌어진 구간은 버린다
                continue
            eta0, eta1 = eta_of(prev[9], prev[10]), eta_of(cur[9], cur[10])
            if eta0 is None or eta1 is None:
                continue
            ratio = (eta0 - eta1) / elapsed

            at_stand = cur[6] is not None and cur[6] in (1, cur[7])
            (standing if at_stand else moving).append(ratio)
            if at_stand:
                dwell[key] += elapsed

    print("\nETA 감소율 = (이전 ETA - 현재 ETA) / 경과 시간")
    print("  1.0 이면 시간 흐른 만큼 ETA 도 줄었다는 뜻. 0 이면 제자리.")
    print("\n [대기지점이 아닌 곳] — 정상 주행의 기준선")
    print(summarize(moving))
    print("\n [기점·회차점에 있을 때] — 여기가 낮게 나와야 구분이 된다")
    print(summarize(standing))

    if moving and standing:
        # 두 분포 사이의 경계를 고른다. 정상 주행의 하한을 그대로 쓰면
        # 정상 차량의 절반이 걸린다.
        moving_low = percentile(moving, 0.10)   # 정상 주행이 느려지는 하한
        standing_high = percentile(standing, 0.90)  # 서 있는 차가 보이는 상한
        if standing_high < moving_low:
            print(f"\n  -> stall_ratio 후보: {(moving_low + standing_high) / 2:.2f}")
            print(f"     (회차지 상위 {standing_high:.2f} 와 정상 하위 {moving_low:.2f} 사이)")
        else:
            print(f"\n  ! 두 분포가 겹친다 (회차지 p90={standing_high:.2f} >= "
                  f"정상 p10={moving_low:.2f}).")
            print("     감소율만으로는 못 가른다. 위치·stateCd 신호에 더 기대야 한다.")

    # --- 대기지점 체류 시간 ---------------------------------------------
    if dwell:
        times = [v for v in dwell.values() if v > 0]
        print("\n기점·회차점 체류 시간 (차량당 누적)")
        print(summarize([t / 60 for t in times], "분"))
        if len(times) < 10:
            print(f"\n  ! 표본이 {len(times)}대뿐이라 임계값을 정하기엔 이르다. "
                  "며칠 더 모을 것.")
        else:
            print(f"\n  -> stall_seconds 후보: {percentile(times, 0.25):.0f}초 "
                  "(체류의 하위 25%. 이보다 오래면 대기로 확정)")
    else:
        print("\n기점·회차점에 머문 차량 기록이 아직 없습니다.")
        print("  회차대기가 자주 생기는 25번을 낮 시간대에 며칠 더 모아야 합니다.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
