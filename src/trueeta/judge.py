"""회차대기 판정.

이 프로젝트의 존재 이유. I/O 가 없는 순수 함수만 둔다 —
임계값을 계속 고칠 부분이라 fixture 만으로 반복 테스트가 돼야 한다.

## 왜 시간표와 비교하지 않는가

마을버스는 정시 운행이 아니라 배차간격 운행이라 "몇 시 몇 분 도착"이라는
데이터가 존재하지 않는다. GBIS·TAGO 모두 첫차/막차와 배차간격만 준다
(고속·시외버스는 정시 운행이라 시간표 API 가 있다).

그래서 비교 대상은 시간표가 아니라 **ETA 가 줄어드는 속도**다.
감소율 추적은 state.StallTracker 가 하고 여기는 결과 초만 받는다.

## 신호 세 가지

1. `flag = WAIT`      API 확정. 가장 강하지만 마을버스에서는 잘 안 온다
2. **위치**            staOrder - locationNo 가 기점(1)이나 회차점(turnSeq)
                       -> 조회 1회로 즉시 알 수 있다. 나가기 직전에 보는
                          화면이라 이 즉시성이 중요하다
3. 정체 누적 시간      ETA 가 오래 안 줄었다 -> 확신을 높인다

2번만으로 회차대기를 확정하지는 않는다. 지나가는 중일 수도 있기 때문이다.
대신 `at_standing` 으로 화면에 넘겨 "이 숫자는 못 믿는다"를 표시하게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from trueeta.models import Arrival, Status, Vehicle

#: stateCd — 0 교차로통과 · 1 정류소도착 · 2 정류소출발
STATE_ARRIVED = 1


@dataclass(frozen=True)
class Verdict:
    status: Status
    eta_sec: int | None
    estimated: bool = False  # 회차대기가 추정인지 (API 가 WAIT 을 안 줬을 때)
    at_standing: bool = False  # 차가 기점/회차점에 있는가 (ETA 를 믿기 어렵다)
    reason: str = ""  # 로그·튜닝용


def at_standing_point(arrival: Arrival, vehicle: Vehicle) -> bool:
    """차량이 기점 또는 회차점에 있는가.

    위치 순번 = staOrder - locationNo 가 1(기점) 또는 turnSeq(회차점)이면
    그 차는 대기 지점에 있다. 정류장마다 이 값이 다르게 나타난다:

        22 (staOrder 31, turnSeq 20)  회차점 대기 -> locationNo 11
        25 (staOrder 27, turnSeq 23)  회차점 대기 -> locationNo 4
        59 (staOrder 22, turnSeq 28)  회차점이 뒤 -> 기점 대기만 해당

    25 번은 회차점이 4정거장 전이라 거기 선 차가 "4분"으로 뜬다.
    이게 버스를 놓치게 만든 그 상황이다.
    """
    if arrival.sta_order < 0 or arrival.turn_seq < 0:
        return False  # 순번 정보가 없으면 판정 불가

    position = arrival.position_of(vehicle)
    if position is None:
        return False
    return position == 1 or position == arrival.turn_seq


def judge_vehicle(
    arrival: Arrival,
    vehicle: Vehicle | None,
    *,
    stalled_sec: float = 0.0,
    stall_seconds: float = 240.0,
) -> Verdict:
    """차량 한 대의 상태를 판정한다.

    stalled_sec 는 ETA 가 연속으로 안 줄어든 누적 시간(초).
    이력 추적은 state.StallTracker 가 한다.
    """
    if arrival.flag == "STOP":
        return Verdict(Status.ENDED, None, reason="flag=STOP")

    if vehicle is None or not vehicle.has_eta:
        return Verdict(Status.NO_BUS, None, reason="도착정보 없음")

    eta = vehicle.eta_sec
    standing = at_standing_point(arrival, vehicle)

    if arrival.flag == "WAIT":
        return Verdict(
            Status.WAITING, eta, at_standing=standing, reason="flag=WAIT"
        )

    if standing:
        # 대기 지점에 '도착' 상태로 서 있으면 대기로 확정한다
        if vehicle.state_cd == STATE_ARRIVED:
            return Verdict(
                Status.WAITING, eta, estimated=True, at_standing=True,
                reason="위치=대기지점 + 정류소도착",
            )
        # ETA 가 오래 안 줄었으면 서 있는 것이다
        if stalled_sec >= stall_seconds:
            return Verdict(
                Status.WAITING, eta, estimated=True, at_standing=True,
                reason=f"위치=대기지점 + {int(stalled_sec)}초 정체",
            )
        # 지나가는 중일 수도 있다. 확정하지 않되 화면에는 알린다 —
        # 조회 1회로 줄 수 있는 유일한 즉시 신호다.
        return Verdict(
            Status.RUNNING, eta, at_standing=True, reason="대기지점 통과 중(미확정)"
        )

    # 대기 지점이 아닌데도 오래 안 줄면 정체(사고·정체·고장)다
    if stalled_sec >= stall_seconds:
        return Verdict(
            Status.RUNNING, eta, at_standing=False,
            reason=f"{int(stalled_sec)}초 정체 (대기지점 아님)",
        )

    return Verdict(Status.RUNNING, eta, reason="운행중")


def judge_arrival(
    arrival: Arrival,
    *,
    stalled: dict[str, float] | None = None,
    stall_seconds: float = 240.0,
) -> tuple[Verdict, Verdict | None]:
    """1호차·2호차를 각각 판정한다. 2호차가 없으면 두 번째는 None."""
    stalled = stalled or {}

    def judge_slot(index: int) -> Verdict:
        vehicle = arrival.vehicles[index]
        return judge_vehicle(
            arrival,
            vehicle,
            stalled_sec=stalled.get(vehicle.veh_id, 0.0),
            stall_seconds=stall_seconds,
        )

    if not arrival.vehicles:
        return judge_vehicle(arrival, None), None

    first = judge_slot(0)
    if len(arrival.vehicles) < 2:
        return first, None
    return first, judge_slot(1)
