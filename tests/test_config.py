"""설정 로드 + 실제 fixture 와의 연결 검증.

여기서만 실응답 fixture 를 쓴다. 네트워크는 타지 않는다.
"""

import glob
import json

import pytest

from trueeta.config import load_board_config, route_key

FIXTURE = "tests/fixtures/arrivals_228001059_*.json"


@pytest.fixture(scope="module")
def cfg():
    return load_board_config()


def test_config_has_the_two_real_stops(cfg):
    assert cfg.station_ids == ("228001059", "228003549")
    assert cfg.all_routes == {"22", "25", "59"}


def test_station_id_is_a_string(cfg):
    """URL 파라미터로 나가므로 int 로 두면 안 된다."""
    assert all(isinstance(s.station_id, str) for s in cfg.stops)


def test_route_key_absorbs_mixed_api_types():
    """API 는 숫자 노선을 int, 가지 노선을 str 로 준다."""
    assert route_key(22) == "22"
    assert route_key("57A") == "57A"
    assert route_key(" 25 ") == "25"


def test_stop_matches_int_route_name_from_api(cfg):
    doe = cfg.stops[0]
    assert doe.wants(22) and doe.wants(59)
    assert not doe.wants(25)
    assert not doe.wants("57A")


def test_config_filters_the_real_arrivals_response(cfg):
    """실제 응답에 설정을 걸면 우리 노선만 남는가."""
    paths = sorted(glob.glob(FIXTURE))
    if not paths:
        pytest.skip("arrivals fixture 없음 - probe.py 를 먼저 돌리세요")

    items = json.load(open(paths[-1], encoding="utf-8"))["response"]["msgBody"]["busArrivalList"]
    stop = cfg.stops[0]
    ours = [i for i in items if stop.wants(i["routeName"])]

    assert {route_key(i["routeName"]) for i in ours} == {"22", "59"}
    assert len(items) > len(ours)  # 다른 노선도 섞여 있어야 필터가 의미 있다
