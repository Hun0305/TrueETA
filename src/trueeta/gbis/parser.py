"""GBIS 도착정보 응답 -> 도메인 객체.

실응답에서 확인한 것들을 여기서 전부 흡수한다 (docs/phase1-probe.md 5장):
  - 빈 값은 null 이 아니라 빈 문자열 ""
  - 차량이 없으면 predictTimeSec1 · stateCd1 키 자체가 없다
  - routeName 이 숫자 노선은 int, 가지 노선은 str
"""

from __future__ import annotations

from typing import Any

from trueeta.models import Arrival, Vehicle


def _int(value: Any) -> int | None:
    """빈 문자열·None·공백을 None 으로, 나머지를 int 로."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse_vehicle(item: dict[str, Any], slot: int) -> Vehicle | None:
    """1호차(slot=1) 또는 2호차(slot=2)를 꺼낸다. 없으면 None.

    키가 아예 없을 수도, 빈 문자열일 수도 있다. 둘 다 '없음'으로 본다.
    """
    predict_min = _int(item.get(f"predictTime{slot}"))
    predict_sec = _int(item.get(f"predictTimeSec{slot}"))
    veh_id = _str(item.get(f"vehId{slot}"))

    # ETA 도 차량 ID 도 없으면 그 자리는 비어 있는 것
    if predict_min is None and predict_sec is None and not veh_id:
        return None

    return Vehicle(
        veh_id=veh_id,
        plate_no=_str(item.get(f"plateNo{slot}")),
        predict_sec=predict_sec,
        predict_min=predict_min,
        location_no=_int(item.get(f"locationNo{slot}")),
        state_cd=_int(item.get(f"stateCd{slot}")),
        station_nm=_str(item.get(f"stationNm{slot}")),
    )


def parse_arrival(item: dict[str, Any]) -> Arrival:
    vehicles = tuple(
        v for v in (parse_vehicle(item, 1), parse_vehicle(item, 2)) if v is not None
    )
    return Arrival(
        station_id=_str(item.get("stationId")),
        route_id=_str(item.get("routeId")),
        route_name=_str(item.get("routeName")),
        dest_name=_str(item.get("routeDestName")),
        # staOrder / turnSeq 가 없으면 위치 계산이 불가능하다. -1 로 두어
        # judge 가 '정차 지점 판정 불가'로 흘러가게 한다.
        sta_order=_int(item.get("staOrder")) or -1,
        turn_seq=_int(item.get("turnSeq")) or -1,
        flag=_str(item.get("flag")).upper(),
        vehicles=vehicles,
    )


def parse_arrivals(items: list[dict[str, Any]]) -> list[Arrival]:
    return [parse_arrival(i) for i in items]
