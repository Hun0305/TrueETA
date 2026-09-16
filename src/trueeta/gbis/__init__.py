"""경기도 버스 API v2 어댑터.

이 패키지 밖으로는 GBIS 고유의 필드명이 새어나가지 않게 하는 것이 목표다.
(TAGO 를 백업 소스로 붙이게 되면 같은 층에 tago/ 를 하나 더 만든다.)
"""

from trueeta.gbis.client import GbisClient, GbisResponse
from trueeta.gbis.errors import GbisAuthError, GbisError
from trueeta.gbis.operations import OPERATIONS, Operation

__all__ = [
    "OPERATIONS",
    "Operation",
    "GbisClient",
    "GbisResponse",
    "GbisAuthError",
    "GbisError",
]
