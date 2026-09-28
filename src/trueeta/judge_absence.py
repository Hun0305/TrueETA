"""회차대기 판정 B안 — "차 없음"을 읽는다.

A안(judge.py)은 회차점·기점 **위치에 서 있는 차**를 찾는다. 그런데 GBIS 는
회차지를 **출발한 차만** 도착정보에 넣는다. 9/24·9/28 관측 374행에서 회차점
위치의 차는 한 번도 안 잡혔다. A안의 위치 규칙은 이 정류장들에서 발동할 수 없다.

회차대기의 실제 모습은 **"운행 중인데 차가 안 보임"** 이다. B안은 그걸 읽는다.

  - 타는 사람에겐 회차지 대기와 배차 공백이 같은 것이다 — 둘 다 "다음 차가
    아직 출발 안 했다". 그래서 구분하지 않는다
  - 차가 안 보여도 **최소 N분**은 확실하다: 회차지를 떠나 여기까지 오는 시간
  - **보통 M분**은 배차간격으로 추정한다: 마지막 차가 지나간 뒤 H 가 흐르면 온다

    M = max(N,  H − 차가 안 보인 지 경과한 시간)

A안과 나란히 돌며 둘 다 route_cycles 에 기록된다. 화면은 아직 A안이다.
자세히는 docs/judge-absence-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass

RUNNING = "running"  # 차가 오는 중
UNSEEN = "unseen"  # 운행 중인데 차가 안 보임 = 회차지 대기(또는 배차 공백)
OFF = "off"  # 이 노선의 운행시간 밖
ENDED = "ended"  # flag=STOP

#: N 을 학습하기 전 기본값. 실측 22번 약 43초/정거장, 25번 약 57초/정거장.
SEC_PER_STOP = 50
DEFAULT_MIN_TRAVEL_SEC = 180


@dataclass(frozen=True)
class RouteSnapshot:
    """한 사이클, 한 정류장의 한 노선. B안 판정의 입력 전부."""

    n_vehicles: int
    eta1_sec: int | None
    flag: str
    in_service: bool | None  # 노선별 운행시간 안인가. None = 노선 정보 없음
    absent_for_sec: float | None  # 차가 안 보인 지 몇 초. 모르면 None
    min_travel_sec: int  # N
    headway_sec: int | None  # H. 모르면 None


@dataclass(frozen=True)
class BVerdict:
    status: str
    eta_sec: int | None = None  # RUNNING 일 때 1호차 ETA
    min_sec: int | None = None  # UNSEEN 일 때 N
    expected_sec: int | None = None  # UNSEEN 일 때 M. 배차간격을 모르면 None
    overdue: bool = False  # 배차간격을 넘겨서도 안 옴
    reason: str = ""


def edge_distance(sta_order: int | None, turn_seq: int | None) -> int | None:
    """차가 처음 보이기 시작하는 곳까지의 정거장 수.

    우리 정류장이 회차점 뒤면 회차점까지, 앞이면 기점까지.
    GBIS 는 그 지점을 **떠난** 차부터 보여주므로, 실제 첫 등장은 이보다 1 작다.
    """
    if not sta_order or sta_order < 0:
        return None
    if turn_seq and 0 < turn_seq < sta_order:
        return sta_order - turn_seq
    return sta_order - 1


def default_min_travel(sta_order: int | None, turn_seq: int | None) -> int:
    """학습값이 없을 때의 N — 첫 등장 위치 × 정거장당 초."""
    edge = edge_distance(sta_order, turn_seq)
    if not edge or edge <= 1:
        return DEFAULT_MIN_TRAVEL_SEC
    return (edge - 1) * SEC_PER_STOP


def judge_absence(snap: RouteSnapshot) -> BVerdict:
    if snap.flag == "STOP":
        return BVerdict(ENDED, reason="flag=STOP")

    if snap.n_vehicles > 0:
        return BVerdict(RUNNING, eta_sec=snap.eta1_sec, reason="차 보임")

    # 노선 정보를 못 받았으면 운행 중으로 본다. 폴러 자체가 service_window
    # 밖에서는 돌지 않으므로 여기까지 왔다면 대체로 운행시간이다.
    if snap.in_service is False:
        return BVerdict(OFF, reason="노선 운행시간 밖")

    n = snap.min_travel_sec
    h = snap.headway_sec
    elapsed = snap.absent_for_sec

    if h is None or elapsed is None:
        return BVerdict(
            UNSEEN, min_sec=n,
            reason="차 없음 — " + ("배차간격 모름" if h is None else "안 보인 시점 모름"),
        )

    remaining = h - elapsed
    overdue = remaining < 0
    return BVerdict(
        UNSEEN,
        min_sec=n,
        expected_sec=max(n, int(remaining)),
        overdue=overdue,
        reason=f"차 없음 {int(elapsed)}초째 / 배차 {h}초" + (" — 배차간격 초과" if overdue else ""),
    )
