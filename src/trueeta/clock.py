"""시계 동기화 대기.

라즈베리파이에는 RTC 가 없다. 전원을 끊으면 시계가 멈추고, 부팅 직후에는
마지막으로 알던 시각(또는 엉뚱한 값)으로 시작해 NTP 가 고칠 때까지 그대로다.

2026-09-24 에 실제로 물렸다 — 20:41 에 부팅했는데 시계가 01:00 을 가리켜서
폴러가 "운행시간(05:50~00:10) 밖" 으로 판단하고 잠들었다. NTP 가 20:42 에
고쳐준 뒤에야 깨어났다 (docs/incident-2026-09-24.md).

시계가 틀리면 세 가지가 같이 틀어진다:

  - 운행시간 판정  -> 조회를 통째로 건너뛴다
  - 관측 로그의 ts -> 분석에서 경과 시간이 엉킨다
  - quota.json     -> 엉뚱한 날짜에 기록돼 한도 계산이 어긋난다

그래서 폴링을 시작하기 전에 동기화를 기다린다. 다만 **무한정 기다리지는
않는다.** 네트워크가 영영 안 붙는 상황에서 화면이 영원히 비는 것보다,
틀릴 수 있는 시계로라도 도는 편이 낫다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

log = logging.getLogger("trueeta.clock")

#: systemd-timesyncd 가 동기화에 성공하면 만드는 파일.
#: time-sync.target 이 이걸 기준으로 삼는다.
SYNC_MARKER = Path("/run/systemd/timesync/synchronized")

DEFAULT_TIMEOUT_SEC = 120.0
DEFAULT_INTERVAL_SEC = 2.0


def is_synced(marker: Path = SYNC_MARKER) -> bool:
    """시계가 NTP 로 맞춰졌는가."""
    try:
        return marker.exists()
    except OSError:
        # 마커를 볼 수 없는 환경(컨테이너 등)에서는 판단을 포기한다
        return False


async def wait_for_sync(
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    interval: float = DEFAULT_INTERVAL_SEC,
    marker: Path = SYNC_MARKER,
) -> bool:
    """시계가 맞춰질 때까지 기다린다.

    맞춰졌으면 True. 시간 안에 못 맞추면 False 를 돌려주되 **막지는 않는다** —
    호출한 쪽이 경고를 남기고 그대로 진행하게 한다.
    """
    if is_synced(marker):
        return True

    log.info("시계 동기화를 기다린다 (최대 %.0f초)", timeout)
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        await asyncio.sleep(interval)
        if is_synced(marker):
            log.info("시계 동기화됨 (%.0f초 걸림)", time.monotonic() - started)
            return True
    return False
