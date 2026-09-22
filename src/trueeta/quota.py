"""일일 호출 건수 카운터.

**한도는 오퍼레이션별로 따로다.** 활용신청 화면의 '상세기능' 표에
기능마다 일일 트래픽 1,000 이 각각 적혀 있다. 즉 도착정보 조회를
하루 900건 써도 정류장 검색은 자기 몫 1,000건을 그대로 갖는다.

그래서 오퍼레이션별로 센다. 합산만 하면 실제로는 여유가 있는데도
한도에 닿은 것처럼 보인다.

세기만 하고 막지는 않는다. 한도를 넘기면 GBIS 가 거절해 그날 남은
시간 내내 화면이 에러가 되므로, 주기를 바꿀 때는 테스트가 방어선이다.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

#: 오퍼레이션 하나당 하루 한도 (개발계정)
DAILY_LIMIT = 1000
WARN_AT = 900
_KEEP_DAYS = 30


class QuotaCounter:
    def __init__(self, path: Path, limit: int = DAILY_LIMIT) -> None:
        self.path = path
        self.limit = limit

    # --- 파일 ------------------------------------------------------------

    def _load(self) -> dict[str, dict[str, int]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}

        days: dict[str, dict[str, int]] = {}
        for day, value in raw.items():
            if isinstance(value, dict):
                days[day] = {k: int(v) for k, v in value.items()}
            else:
                # 예전 형식: 날짜마다 합계 하나. 오퍼레이션을 몰랐던 기록이다.
                days[day] = {"(이전)": int(value)}
        return days

    def _save(self, days: dict[str, dict[str, int]]) -> None:
        cutoff = (date.today() - timedelta(days=_KEEP_DAYS)).isoformat()
        kept = {k: v for k, v in days.items() if k >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(kept, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )

    # --- 읽기 ------------------------------------------------------------

    def today(self, op: str | None = None) -> int:
        """op 을 주면 그 오퍼레이션의 오늘 건수, 안 주면 전체 합계."""
        day = self._load().get(date.today().isoformat(), {})
        return day.get(op, 0) if op else sum(day.values())

    def breakdown(self) -> dict[str, int]:
        return dict(self._load().get(date.today().isoformat(), {}))

    # --- 쓰기 ------------------------------------------------------------

    def bump(self, op: str = "(미상)", n: int = 1) -> int:
        """호출 1건을 기록하고 **그 오퍼레이션의** 오늘 누적을 돌려준다."""
        days = self._load()
        today = date.today().isoformat()
        day = days.setdefault(today, {})
        day[op] = day.get(op, 0) + n
        self._save(days)
        return day[op]
