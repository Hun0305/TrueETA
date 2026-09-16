"""응답 봉투 해석.

GBIS v2 는 format=json 을 붙여도 서비스에 따라 XML 로 돌아올 수 있고,
서비스키가 거부되면 GBIS 가 아니라 포털이 자기 형식의 XML 을 돌려준다.
그래서 "형식을 신뢰하지 않고 본문을 보고 판단한다".

HTTP 를 타지 않는 순수 함수만 둔다. 실제 응답 없이 테스트할 수 있어야 하기 때문.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from trueeta.gbis.errors import GbisAuthError, GbisError

#: 포털이 키를 거부할 때 등장하는 키들
_PORTAL_ERROR_KEYS = ("returnAuthMsg", "returnReasonCode", "errMsg")


def _strip_ns(tag: str) -> str:
    """'{http://...}resultCode' -> 'resultCode'"""
    return tag.rpartition("}")[2]


def _element_to_obj(el: ET.Element) -> Any:
    children = list(el)
    if not children:
        text = (el.text or "").strip()
        return text

    out: dict[str, Any] = {}
    for child in children:
        tag = _strip_ns(child.tag)
        value = _element_to_obj(child)
        if tag in out:
            # 같은 태그가 반복되면 리스트로 승격 (busArrivalList 같은 항목들)
            if isinstance(out[tag], list):
                out[tag].append(value)
            else:
                out[tag] = [out[tag], value]
        else:
            out[tag] = value
    return out


def xml_to_dict(text: str) -> dict[str, Any]:
    root = ET.fromstring(text)
    return {_strip_ns(root.tag): _element_to_obj(root)}


def parse_body(text: str) -> tuple[dict[str, Any], bool]:
    """본문을 dict 로. (파싱결과, XML이었는지) 를 돌려준다."""
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped), False
    if stripped.startswith("<"):
        try:
            return xml_to_dict(stripped), True
        except ET.ParseError as exc:
            raise GbisError("?", f"XML 파싱 실패: {exc}", raw=text[:500]) from exc
    raise GbisError("?", "JSON 도 XML 도 아닌 응답", raw=text[:500])


def find_key(obj: Any, key: str) -> Any:
    """중첩 깊이를 모르므로 재귀로 찾는다. 없으면 None."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = find_key(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_key(value, key)
            if found is not None:
                return found
    return None


def as_list(value: Any) -> list[Any]:
    """항목이 1개면 dict, 여러 개면 list 로 오는 XML 관행을 흡수한다."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def check_result(body: dict[str, Any], raw: str = "") -> str:
    """resultCode 를 확인하고 돌려준다.

    '0' 정상, '4' 결과 없음(막차 후 등 정상 상황)은 통과시키고
    나머지는 예외. 포털 레벨 거부는 GbisAuthError 로 구분한다.
    """
    for key in _PORTAL_ERROR_KEYS:
        reason = find_key(body, key)
        if reason:
            raise GbisAuthError(str(reason), raw=raw[:500])

    code = find_key(body, "resultCode")
    if code is None:
        raise GbisError("?", "resultCode 를 찾지 못했다", raw=raw[:500])

    code = str(code).strip()
    if code in ("0", "4"):
        return code

    message = find_key(body, "resultMessage") or ""
    raise GbisError(code, str(message), raw=raw[:500])
