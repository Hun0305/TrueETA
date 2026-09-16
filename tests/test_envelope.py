"""봉투 해석 테스트. 네트워크도 쿼터도 쓰지 않는다.

실제 응답을 받기 전이라 샘플은 매뉴얼에 적힌 구조를 손으로 적은 것이다.
프로브를 돌려 tests/fixtures/ 가 채워지면 그 파일로 검증을 옮긴다.
"""

import pytest

from trueeta.gbis.envelope import as_list, check_result, find_key, parse_body, xml_to_dict
from trueeta.gbis.errors import GbisAuthError, GbisError

JSON_OK = """
{"response": {
  "msgHeader": {"resultCode": 0, "resultMessage": "정상적으로 처리되었습니다."},
  "msgBody": {"busArrivalList": [
    {"routeId": "200000078", "routeName": "22", "staOrder": 14, "turnSeq": 38,
     "flag": "RUN", "predictTimeSec1": 320, "vehId1": "1234"}
  ]}
}}
"""

XML_OK = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <msgHeader><resultCode>0</resultCode><resultMessage>정상</resultMessage></msgHeader>
  <msgBody>
    <busArrivalList><routeId>200000078</routeId><routeName>22</routeName></busArrivalList>
    <busArrivalList><routeId>200000079</routeId><routeName>25</routeName></busArrivalList>
  </msgBody>
</response>
"""

XML_SINGLE = """<response>
  <msgHeader><resultCode>0</resultCode></msgHeader>
  <msgBody><busArrivalList><routeId>200000078</routeId></busArrivalList></msgBody>
</response>
"""

XML_EMPTY = """<response>
  <msgHeader><resultCode>4</resultCode><resultMessage>결과가 없습니다</resultMessage></msgHeader>
  <msgBody></msgBody>
</response>
"""

XML_SYSTEM_ERROR = """<response>
  <msgHeader><resultCode>1</resultCode><resultMessage>시스템 에러</resultMessage></msgHeader>
</response>
"""

XML_PORTAL_AUTH = """<OpenAPI_ServiceResponse>
  <cmmMsgHeader>
    <errMsg>SERVICE ERROR</errMsg>
    <returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
    <returnReasonCode>30</returnReasonCode>
  </cmmMsgHeader>
</OpenAPI_ServiceResponse>
"""


def test_json_body_is_detected_without_content_type():
    body, was_xml = parse_body(JSON_OK)
    assert was_xml is False
    assert check_result(body) == "0"
    assert find_key(body, "routeName") == "22"


def test_xml_body_is_detected_and_converted():
    body, was_xml = parse_body(XML_OK)
    assert was_xml is True
    assert check_result(body) == "0"


def test_repeated_xml_tags_become_a_list():
    body = xml_to_dict(XML_OK)
    items = body["response"]["msgBody"]["busArrivalList"]
    assert [i["routeName"] for i in items] == ["22", "25"]


def test_single_xml_item_is_not_a_list_but_as_list_fixes_it():
    body = xml_to_dict(XML_SINGLE)
    item = body["response"]["msgBody"]["busArrivalList"]
    assert isinstance(item, dict)
    assert len(as_list(item)) == 1


def test_result_code_4_is_not_an_error():
    """막차 후에는 결과가 없는 게 정상이다."""
    body, _ = parse_body(XML_EMPTY)
    assert check_result(body) == "4"


def test_system_error_raises():
    body, _ = parse_body(XML_SYSTEM_ERROR)
    with pytest.raises(GbisError) as exc:
        check_result(body)
    assert exc.value.result_code == "1"


def test_portal_key_rejection_is_a_separate_error():
    body, _ = parse_body(XML_PORTAL_AUTH)
    with pytest.raises(GbisAuthError) as exc:
        check_result(body)
    assert "NOT_REGISTERED" in exc.value.reason


def test_garbage_body_raises_rather_than_returning_nothing():
    with pytest.raises(GbisError):
        parse_body("서비스 점검중입니다")


def test_as_list_handles_none():
    assert as_list(None) == []
