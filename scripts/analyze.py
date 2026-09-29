#!/usr/bin/env python3
"""관측 로그 분석 — judge 임계값을 데이터로 정하기 위한 도구.

`var/observations.db` 에 폴러가 남긴 기록을 읽는다. API 는 호출하지 않는다.

    python scripts/analyze.py              # 전체 요약
    python scripts/analyze.py --days 3     # 최근 3일
    python scripts/analyze.py --route 25   # 노선 하나만
    python scripts/analyze.py --compare    # 회차대기 A안·B안 비교 (route_cycles)

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

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "var" / "observations.db"

# 배차 보정은 폴러와 **같은 코드**로 계산해야 여기서 본 숫자가 화면과 맞는다
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
from trueeta.storage import (  # noqa: E402
    HEADWAY_MIN_SAMPLES, HEADWAY_RATIO_MAX, HEADWAY_RATIO_MIN, headway_ratios,
)


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


# --- A안·B안 비교 (route_cycles) ------------------------------------------
#
# B안이 "차 없음, 최소 N · 예상 M" 이라 했을 때 실제로 몇 초 뒤 왔나를 맞춰본다.
#   실제 대기 = (다음에 차가 보인 사이클 시각 - 이 사이클 시각) + 그때 1호차 ETA
# 설계: docs/judge-absence-design.md

#: 이보다 오래 뒤에 차가 보였다면 중간에 꺼졌거나 운행이 끊긴 것으로 보고 버린다
MAX_MATCH_SEC = 90 * 60


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _edge(order, turn):
    if not order:
        return None
    if turn and 0 < turn < order:
        return order - turn
    return order - 1


def compare(db: Path, days: int | None = None, route: str | None = None) -> dict:
    """비교 결과를 dict 로 돌려주고 사람이 읽을 요약을 출력한다."""
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "route_cycles" not in tables:
        print("route_cycles 가 아직 없습니다. 서비스를 재시작해 B안 기록을 시작하세요.")
        return {}

    where, params = [], []
    if days:
        where.append("ts >= ?")
        params.append((datetime.now().astimezone() - timedelta(days=days)).isoformat())
    if route:
        where.append("route_name = ?")
        params.append(route)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""SELECT ts, station_id, route_name, n_vehicles, eta1_sec, loc1, sta_order,
                   turn_seq, absent_since, headway_sec, a_status, b_status,
                   b_min_sec, b_expected_sec, b_overdue
            FROM route_cycles {clause} ORDER BY station_id, route_name, ts""",
        params,
    ).fetchall()
    if not rows:
        print("해당 조건의 route_cycles 기록이 없습니다.")
        return {}

    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        groups[(r[1], r[2])].append(r)

    result: dict = {"routes": {}}
    print(f"route_cycles {len(rows)}행  ({rows[0][0][:16]} ~ {max(r[0] for r in rows)[:16]})")

    for (station, name), seq in groups.items():
        res: dict = {}
        a_count, b_count = defaultdict(int), defaultdict(int)
        for r in seq:
            a_count[r[10]] += 1
            b_count[r[11]] += 1
        res["a"], res["b"] = dict(a_count), dict(b_count)
        res["a_waiting"] = a_count.get("waiting", 0)

        # 0) 배차 보정 비율 — 폴러와 같은 함수 (storage.headway_ratios)
        ratios = headway_ratios([(r[0], r[3], r[4], r[8], r[9], r[11]) for r in seq])
        ratio = None
        if len(ratios) >= HEADWAY_MIN_SAMPLES:
            ratio = min(HEADWAY_RATIO_MAX, max(HEADWAY_RATIO_MIN, _median(ratios)))
        res["h_ratio"], res["h_samples"] = ratio, len(ratios)

        # 1) B안 예측 vs 실제 대기. 보정 비율을 적용했다면 M 이 어땠을지도 같이
        errors, adj_errors, under_min, matched = [], [], 0, 0
        for i, r in enumerate(seq):
            if r[11] != "unseen":
                continue
            t0 = datetime.fromisoformat(r[0])
            for nxt in seq[i + 1:]:
                if nxt[11] in ("off", "ended", "missing"):
                    break
                if nxt[3] > 0 and nxt[4] is not None:
                    actual = (datetime.fromisoformat(nxt[0]) - t0).total_seconds() + nxt[4]
                    if actual <= MAX_MATCH_SEC:
                        matched += 1
                        if r[12] is not None and actual < r[12]:
                            under_min += 1
                        if r[13] is not None:
                            errors.append(actual - r[13])
                        if ratio and r[8] and r[9] and r[12] is not None:
                            elapsed = (t0 - datetime.fromisoformat(r[8])).total_seconds()
                            m_adj = max(r[12], r[9] * ratio - elapsed)
                            adj_errors.append(actual - m_adj)
                    break
        res["matched"] = matched
        res["under_min"] = under_min
        res["m_bias"] = _median(errors)
        res["m_abs_err"] = _median([abs(e) for e in errors])
        res["m_adj_bias"] = _median(adj_errors)
        res["m_adj_abs_err"] = _median([abs(e) for e in adj_errors])

        # 2) '차 없음' 구간 길이 vs 배차간격
        gaps = []
        for i, r in enumerate(seq):
            if r[3] > 0 and i > 0 and seq[i - 1][3] == 0 and seq[i - 1][8] and seq[i - 1][9]:
                gap = (datetime.fromisoformat(r[0]) - datetime.fromisoformat(seq[i - 1][8])).total_seconds()
                gaps.append(gap / seq[i - 1][9])
        res["gap_over_headway"] = sum(1 for g in gaps if g > 1)
        res["gaps"] = len(gaps)

        # 3) N 재학습 — '차 없음 -> 차 있음' 전환의 ETA, 경계 근처에서 잡힌 것만
        firsts = []
        for i, r in enumerate(seq):
            if i and seq[i - 1][3] == 0 and r[3] > 0 and r[4] is not None and r[5] is not None:
                edge = _edge(r[6], r[7])
                if edge and r[5] >= edge - 2:
                    firsts.append(r[4])
        res["n_learned"] = _median(firsts) if len(firsts) >= 3 else None
        res["n_samples"] = len(firsts)
        res["n_current"] = _median([r[12] for r in seq if r[12] is not None])

        result["routes"][(station, name)] = res

        # --- 출력
        print(f"\n=== {name}번 ({station}) — {len(seq)}사이클 ===")
        print("  B안 판정: " + "  ".join(f"{k} {v}" for k, v in sorted(b_count.items(), key=lambda x: -x[1])))
        print(f"  A안 판정: " + "  ".join(f"{k} {v}" for k, v in sorted(a_count.items(), key=lambda x: -x[1])))
        if res["a_waiting"] == 0:
            print("    -> A안 위치 규칙이 한 번도 발동하지 않았다")
        if b_count.get("missing"):
            print(f"    !! 응답에 노선 없음 {b_count['missing']}회 — 정류장·노선 설정 확인")
        if matched:
            print(f"  B안 예측 검증 ({matched}건 — 차 없음 뒤 실제로 온 시각과 대조)")
            if res["m_bias"] is not None:
                print(f"    예상 M 오차: 중앙 {res['m_bias'] / 60:+.1f}분 (＋면 실제가 더 늦음), "
                      f"절대오차 중앙 {res['m_abs_err'] / 60:.1f}분")
            print(f"    최소 N 보다 빨리 온 경우: {under_min}건"
                  + ("  <- N 이 너무 크다" if under_min > matched * 0.1 else ""))
            if res["m_adj_abs_err"] is not None:
                # 같은 데이터로 배우고 같은 데이터로 잰 값이라 실제보다 좋게 나온다
                print(f"    배차 보정 ×{ratio:.2f} 을 적용했다면: 중앙 {res['m_adj_bias'] / 60:+.1f}분, "
                      f"절대오차 중앙 {res['m_adj_abs_err'] / 60:.1f}분  (같은 데이터로 학습 — 낙관적)")
        else:
            print("  B안 예측 검증: 아직 대조할 쌍이 없다 (차 없음 뒤 차가 다시 보인 기록 필요)")
        if gaps:
            print(f"  차 없음 구간이 배차간격을 넘긴 경우: {res['gap_over_headway']}/{len(gaps)}")
        if ratio is not None:
            print(f"  배차 보정 학습: ×{ratio:.2f} (구간 {len(ratios)}개, 실측 ÷ 노선정보 배차간격)")
        else:
            print(f"  배차 보정 학습: 구간 {len(ratios)}개 — {HEADWAY_MIN_SAMPLES}개 이상 필요")
        if res["n_learned"] is not None:
            print(f"  N 재학습: {res['n_learned']}초 (표본 {len(firsts)}) — 현재 사용 중 {res['n_current']}초")
        else:
            print(f"  N 재학습: 표본 {len(firsts)}개 — 3개 이상 필요")

    conn.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, help="최근 N일만")
    parser.add_argument("--route", help="노선번호 (예: 25)")
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--compare", action="store_true",
                        help="회차대기 A안·B안 비교 (route_cycles)")
    args = parser.parse_args()

    if args.compare:
        if not args.db.exists():
            print(f"{args.db} 가 없습니다.", file=sys.stderr)
            return 1
        compare(args.db, args.days, args.route)
        return 0

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
