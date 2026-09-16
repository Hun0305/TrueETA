"""일일 호출 건수 카운터.

개발계정 한도가 1,000건/일이라 폴러를 붙이기 전에 미리 만들어 둔다.
프로세스가 죽어도 유지되도록 파일에 쓴다.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

DAILY_LIMIT = 1000
WARN_AT = 900
_KEEP_DAYS = 30


class QuotaCounter:
    def __init__(self, path: Path, limit: int = DAILY_LIMIT) -> None:
        self.path = path
        self.limit = limit

    def _load(self) -> dict[str, int]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def today(self) -> int:
        return self._load().get(date.today().isoformat(), 0)

    def bump(self, n: int = 1) -> int:
        """호출 1건을 기록하고 오늘 누적을 돌려준다."""
        data = self._load()
        today = date.today().isoformat()
        data[today] = data.get(today, 0) + n

        cutoff = (date.today() - timedelta(days=_KEEP_DAYS)).isoformat()
        data = {k: v for k, v in data.items() if k >= cutoff}

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return data[today]
