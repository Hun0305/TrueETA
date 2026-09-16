"""회차대기 판정.

이 프로젝트의 존재 이유. I/O 가 없는 순수 함수만 둔다 —
임계값을 계속 고칠 부분이라 fixture 만으로 반복 테스트가 돼야 한다.

판정 순서 (docs/architecture.md 3장):
  1. flag = STOP            -> 운행종료
  2. 도착정보 없음           -> 오는 차 없음
     (flag 는 노선 단위 상태라 차량 유무를 먼저 봐야 한다. 실응답에서 확인)
  3. flag = WAIT            -> 회차대기 (API 확정)
  4. 차량 위치가 기점/회차점 -> 회차대기 (추정)
  5. 그 외                  -> 운행중
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
    reason: str = ""  # 로그·튜닝용


def at_standing_point(arrival: Arrival, vehicle: Vehicle) -> bool:
    """차량이 기점 또는 회차점에 서 있는가.

    위치 순번 = staOrder - locationNo 가 1(기점) 또는 turnSeq(회차점)이면
    그 차는 대기 지점에 있다. 정류장마다 이 값이 다르게 나타난다 —
    예: 25번은 회차점이 4정거장 전이라 '곧 도착'처럼 보인다.
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
    stalled_count: int = 0,
    stall_threshold: int = 3,
) -> Verdict:
    """차량 한 대의 상태를 판정한다.

    stalled_count 는 같은 vehId 가 몇 회 연속 위치·ETA 그대로였는지.
    이력 추적은 state.py 가 하고 여기는 숫자만 받는다.
    """
    if arrival.flag == "STOP":
        return Verdict(Status.ENDED, None, reason="flag=STOP")

    if vehicle is None or not vehicle.has_eta:
        return Verdict(Status.NO_BUS, None, reason="도착정보 없음")

    eta = vehicle.eta_sec

    if arrival.flag == "WAIT":
        return Verdict(Status.WAITING, eta, reason="flag=WAIT")

    if at_standing_point(arrival, vehicle):
        # 대기 지점에 '도착' 상태로 서 있으면 대기로 본다.
        if vehicle.state_cd == STATE_ARRIVED:
            return Verdict(
                Status.WAITING, eta, estimated=True, reason="위치=대기지점 + 정류소도착"
            )
        # 통과 중일 수도 있으므로, 여러 번 연속 안 움직였을 때만 대기로 본다.
        if stalled_count >= stall_threshold:
            return Verdict(
                Status.WAITING, eta, estimated=True,
                reason=f"위치=대기지점 + {stalled_count}회 정체",
            )

    return Verdict(Status.RUNNING, eta, reason="운행중")


def judge_arrival(
    arrival: Arrival,
    *,
    stalled: dict[str, int] | None = None,
    stall_threshold: int = 3,
) -> tuple[Verdict, Verdict | None]:
    """1호차·2호차를 각각 판정한다. 2호차가 없으면 두 번째는 None."""
    stalled = stalled or {}

    first_vehicle = arrival.vehicles[0] if arrival.vehicles else None
    first = judge_vehicle(
        arrival,
        first_vehicle,
        stalled_count=stalled.get(first_vehicle.veh_id, 0) if first_vehicle else 0,
        stall_threshold=stall_threshold,
    )

    if len(arrival.vehicles) < 2:
        return first, None

    second_vehicle = arrival.vehicles[1]
    second = judge_vehicle(
        arrival,
        second_vehicle,
        stalled_count=stalled.get(second_vehicle.veh_id, 0),
        stall_threshold=stall_threshold,
    )
    return first, second
