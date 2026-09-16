"""파서 — 실응답의 지저분한 부분을 흡수하는지.

샘플은 tests/fixtures 의 실제 응답에서 가져온 모양 그대로다.
"""

import glob
import json

import pytest

from trueeta.gbis.parser import parse_arrival, parse_arrivals, parse_vehicle

# 차량 있음 (실응답 22번)
WITH_BUS = {
    "flag": "PASS", "locationNo1": 1, "locationNo2": "",
    "plateNo1": "경기78아8813", "plateNo2": "",
    "predictTime1": 1, "predictTime2": "",
    "routeDestName": "㈜다우", "routeId": 241423001, "routeName": 22,
    "staOrder": 8, "stationId": 228001027, "stationNm1": "죽전역(마을)",
    "turnSeq": 20, "vehId1": 289001125, "vehId2": "",
    "predictTimeSec1": 47, "stateCd1": 0,
}

# 차량 없음 (실응답 57A) — predictTimeSec1·stateCd1 키가 아예 없다
WITHOUT_BUS = {
    "flag": "PASS", "locationNo1": "", "locationNo2": "",
    "plateNo1": "", "plateNo2": "", "predictTime1": "", "predictTime2": "",
    "routeDestName": "사기막골", "routeId": 241421014, "routeName": "57A",
    "staOrder": 58, "stationId": 228001027, "stationNm1": "",
    "turnSeq": 48, "vehId1": "", "vehId2": "",
}


def test_empty_string_means_no_vehicle():
    """빈 값이 null 이 아니라 빈 문자열로 온다."""
    assert parse_vehicle(WITHOUT_BUS, 1) is None
    assert parse_vehicle(WITHOUT_BUS, 2) is None


def test_missing_keys_do_not_raise():
    """차량이 없으면 predictTimeSec1·stateCd1 키 자체가 없다."""
    assert "predictTimeSec1" not in WITHOUT_BUS
    arrival = parse_arrival(WITHOUT_BUS)
    assert arrival.vehicles == ()


def test_int_route_name_becomes_string():
    """숫자 노선은 int, 가지 노선은 str 로 온다."""
    assert parse_arrival(WITH_BUS).route_name == "22"
    assert parse_arrival(WITHOUT_BUS).route_name == "57A"


def test_vehicle_fields():
    vehicle = parse_vehicle(WITH_BUS, 1)
    assert vehicle.veh_id == "289001125"
    assert vehicle.predict_sec == 47
    assert vehicle.location_no == 1
    assert vehicle.state_cd == 0  # 0 은 유효값이다. None 으로 뭉개면 안 된다


def test_eta_falls_back_to_minutes():
    item = dict(WITH_BUS)
    del item["predictTimeSec1"]
    assert parse_vehicle(item, 1).eta_sec == 60  # predictTime1=1분


def test_position_is_sta_order_minus_location():
    arrival = parse_arrival(WITH_BUS)
    assert arrival.position_of(arrival.vehicles[0]) == 8 - 1


def test_every_real_fixture_parses():
    """네트워크 없이, 받아둔 모든 실응답이 파싱되는지."""
    paths = glob.glob("tests/fixtures/arrivals_*.json")
    if not paths:
        pytest.skip("arrivals fixture 없음")

    for path in paths:
        items = json.load(open(path, encoding="utf-8"))["response"]["msgBody"]["busArrivalList"]
        arrivals = parse_arrivals(items)
        assert len(arrivals) == len(items)
        assert all(a.route_name for a in arrivals)
