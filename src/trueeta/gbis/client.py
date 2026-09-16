"""경기도 버스 API v2 HTTP 클라이언트.

1차에서는 재시도를 하지 않는다. 실패를 그대로 드러내는 편이
원인 파악에 낫고, 쿼터도 아낀다. 재시도는 폴러를 붙일 때 그 층에서 한다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from trueeta.gbis.envelope import check_result, parse_body
from trueeta.gbis.errors import GbisError
from trueeta.gbis.operations import OPERATIONS
from trueeta.quota import WARN_AT, QuotaCounter

BASE_URL = "http://apis.data.go.kr/6410000"

_MASK = "PUT_DECODED_SERVICE_KEY_HERE"


@dataclass
class GbisResponse:
    op: str
    url: str  # 서비스키를 가린 표시용 URL
    body: dict[str, Any]
    raw_text: str
    was_xml: bool
    result_code: str

    @property
    def empty(self) -> bool:
        """resultCode=4. 막차 후처럼 정상적으로 결과가 없는 상태."""
        return self.result_code == "4"


class GbisClient:
    def __init__(
        self,
        service_key: str,
        *,
        timeout: float = 10.0,
        quota_path: Path | None = None,
    ) -> None:
        self._key = service_key
        self._timeout = timeout
        self._quota = QuotaCounter(quota_path) if quota_path else None

    # --- URL 조립 -------------------------------------------------------

    def _endpoint(self, op: str, path: str | None) -> str:
        if path:
            return f"{BASE_URL}/{path.strip('/')}"
        try:
            return f"{BASE_URL}/{OPERATIONS[op].path}"
        except KeyError:
            raise SystemExit(f"모르는 op: {op} (가능: {', '.join(OPERATIONS)})") from None

    def _params(self, extra: dict[str, Any]) -> dict[str, Any]:
        # serviceKey 는 '디코딩' 키여야 한다. httpx 가 여기서 한 번 인코딩한다.
        return {"serviceKey": self._key, "format": "json", **extra}

    def display_url(self, op: str, params: dict[str, Any], path: str | None = None) -> str:
        """로그·--dry-run 용. 서비스키는 가린다."""
        masked = self._params(params) | {"serviceKey": _MASK}
        request = httpx.Request("GET", self._endpoint(op, path), params=masked)
        return str(request.url)

    # --- 호출 -----------------------------------------------------------

    def call(
        self, op: str, params: dict[str, Any], *, path: str | None = None
    ) -> GbisResponse:
        url = self._endpoint(op, path)
        display = self.display_url(op, params, path)

        if self._quota is not None:
            used = self._quota.bump()
            note = "  <-- 한도 임박!" if used >= WARN_AT else ""
            print(f"[quota] 오늘 {used}/{self._quota.limit}건{note}", file=sys.stderr)

        response = httpx.get(url, params=self._params(params), timeout=self._timeout)
        text = response.text

        try:
            body, was_xml = parse_body(text)
        except GbisError:
            if response.status_code >= 400:
                raise GbisError(
                    str(response.status_code),
                    f"HTTP {response.status_code}",
                    raw=text[:500],
                ) from None
            raise

        result_code = check_result(body, raw=text)
        return GbisResponse(
            op=op,
            url=display,
            body=body,
            raw_text=text,
            was_xml=was_xml,
            result_code=result_code,
        )

    # --- 편의 메서드 (2차에서 폴러가 쓸 것들) ---------------------------

    def station_list(self, keyword: str) -> GbisResponse:
        return self.call("station-list", {"keyword": keyword})

    def station_around(self, x: float, y: float) -> GbisResponse:
        return self.call("station-around", {"x": x, "y": y})

    def arrivals(self, station_id: str) -> GbisResponse:
        return self.call("arrivals", {"stationId": station_id})

    def route_info(self, route_id: str) -> GbisResponse:
        return self.call("route-info", {"routeId": route_id})

    def route_stations(self, route_id: str) -> GbisResponse:
        return self.call("route-stations", {"routeId": route_id})
