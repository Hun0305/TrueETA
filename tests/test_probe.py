"""프로브의 항목 추출. 응답 키 이름을 못 맞혔을 때도 표가 나와야 한다."""

import probe


def test_known_key_is_used():
    body = {"response": {"msgBody": {"busArrivalList": [{"routeName": "22"}]}}}
    assert probe.extract_items(body, "arrivals") == [{"routeName": "22"}]


def test_single_item_is_wrapped_in_a_list():
    body = {"response": {"msgBody": {"busArrivalList": {"routeName": "22"}}}}
    assert probe.extract_items(body, "arrivals") == [{"routeName": "22"}]


def test_unknown_key_falls_back_to_first_list_under_msg_body():
    """키 이름을 틀려도 빈손으로 끝나지 않게."""
    body = {"response": {"msgBody": {"someUnexpectedName": [{"stationId": "1"}]}}}
    assert probe.extract_items(body, "station-list") == [{"stationId": "1"}]


def test_empty_body_gives_no_items():
    body = {"response": {"msgHeader": {"resultCode": "4"}, "msgBody": ""}}
    assert probe.extract_items(body, "arrivals") == []
