"""운행시간 창 판정.

첫차 전·막차 후에는 조회하지 않는다. 개발계정 1,000건/일이라
빈 응답을 받으려고 쿼터를 쓸 이유가 없다.

창이 자정을 넘어간다 (05:50 ~ 00:10). 그래서 단순 비교로는 안 된다.
"""

from __future__ import annotations

from datetime import time


def parse_hhmm(text: str) -> time:
    hour, _, minute = text.strip().partition(":")
    return time(int(hour) % 24, int(minute or 0))


def in_window(now: time, start: time, end: time) -> bool:
    """now 가 [start, end) 안인가. end < start 면 자정을 넘는 창으로 본다."""
    if start <= end:
        return start <= now < end
    # 자정을 넘는 창: 05:50~24:00 또는 00:00~00:10
    return now >= start or now < end
