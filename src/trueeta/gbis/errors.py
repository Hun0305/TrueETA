"""GBIS 호출 실패 종류.

두 층에서 에러가 온다:
  - 포털(data.go.kr) 층: 키가 안 먹을 때. GBIS 까지 도달하지도 못한다.  -> GbisAuthError
  - GBIS 층: resultCode 로 온다.                                      -> GbisError
"""

from __future__ import annotations

# 매뉴얼의 에러코드
RESULT_MESSAGES = {
    "0": "정상",
    "1": "시스템 에러",
    "2": "필수 파라미터 없음",
    "4": "결과 없음",
}


class GbisError(Exception):
    """GBIS 가 resultCode 로 실패를 알린 경우."""

    def __init__(self, result_code: str, message: str = "", raw: str = "") -> None:
        self.result_code = result_code
        self.message = message or RESULT_MESSAGES.get(result_code, "알 수 없는 오류")
        self.raw = raw
        super().__init__(f"GBIS resultCode={result_code} ({self.message})")


class GbisAuthError(Exception):
    """공공데이터포털이 서비스키를 거부한 경우.

    resultCode 가 아예 없고 OpenAPI_ServiceResponse 형태로 온다.
    """

    HINT = (
        "확인할 것:\n"
        "  1) .env 의 SERVICE_KEY 가 '디코딩' 키인가? (인코딩 키를 쓰면 이중 인코딩으로 실패)\n"
        "  2) 해당 API 활용신청이 승인됐는가?\n"
        "  3) 승인 직후라면 반영까지 최대 1시간 걸린다"
    )

    def __init__(self, reason: str, raw: str = "") -> None:
        self.reason = reason
        self.raw = raw
        super().__init__(f"서비스키 거부됨: {reason}\n{self.HINT}")
