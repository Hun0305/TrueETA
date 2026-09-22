"""프리셋 저장소 (SQLite).

관측 로그(storage.py)와 **다른 파일**에 둔다. 관측 로그는 지워도 되는
데이터이고 프리셋은 아니다.

`config.yaml` 의 `presets:` 는 **씨앗**이다. DB 가 비어 있을 때 한 번
옮겨 담고, 그 뒤로는 DB 가 원본이다. 설정 파일을 고쳐도 DB 를 덮어쓰지
않는다 — 화면에서 편집한 내용이 날아가면 안 되기 때문이다.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from trueeta.config import Preset, Stop, route_key

log = logging.getLogger("trueeta.presets")

SCHEMA = """
CREATE TABLE IF NOT EXISTS presets (
    name        TEXT PRIMARY KEY,
    is_default  INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS preset_items (
    preset_name  TEXT NOT NULL REFERENCES presets(name) ON DELETE CASCADE,
    station_id   TEXT NOT NULL,
    station_name TEXT NOT NULL,   -- 표시용 복제. API 가 죽어도 목록은 보여야 한다
    route_name   TEXT NOT NULL,
    dest_name    TEXT DEFAULT '', -- 고를 때 본 방면. 나중에 바뀌었는지 비교용
    mobile_no    TEXT DEFAULT '',
    sort_order   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_preset ON preset_items (preset_name, sort_order);
"""


class PresetStore:
    """프리셋 CRUD. 열지 못하면 비활성이 아니라 예외다 — 화면의 원본이다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # --- 읽기 -----------------------------------------------------------

    def is_empty(self) -> bool:
        return self._conn.execute("SELECT COUNT(*) FROM presets").fetchone()[0] == 0

    def all(self) -> tuple[Preset, ...]:
        rows = self._conn.execute(
            "SELECT name, is_default FROM presets ORDER BY sort_order, name"
        ).fetchall()
        return tuple(self._build(name, bool(flag)) for name, flag in rows)

    def _build(self, name: str, is_default: bool) -> Preset:
        items = self._conn.execute(
            """SELECT station_id, station_name, route_name, dest_name, mobile_no
               FROM preset_items WHERE preset_name = ? ORDER BY sort_order""",
            (name,),
        ).fetchall()

        # 같은 정류장의 노선들을 한 Stop 으로 묶는다. 순서는 처음 나온 순.
        order: list[str] = []
        grouped: dict[str, dict] = {}
        for station_id, station_name, route, dest, mobile in items:
            if station_id not in grouped:
                order.append(station_id)
                grouped[station_id] = {
                    "name": station_name, "routes": [], "mobile": mobile or "",
                    "dests": [],
                }
            grouped[station_id]["routes"].append(route_key(route))
            grouped[station_id]["dests"].append(dest or "")

        stops = tuple(
            Stop(
                station_id=sid,
                name=grouped[sid]["name"],
                routes=tuple(grouped[sid]["routes"]),
                mobile_no=grouped[sid]["mobile"],
                note=" · ".join(d for d in dict.fromkeys(grouped[sid]["dests"]) if d),
            )
            for sid in order
        )
        return Preset(name=name, stops=stops, is_default=is_default)

    # --- 쓰기 -----------------------------------------------------------

    def save(self, preset: Preset, *, dests: dict[tuple[str, str], str] | None = None) -> None:
        """프리셋 하나를 통째로 덮어쓴다. 같은 이름이면 교체."""
        dests = dests or {}
        with self._conn:
            self._conn.execute(
                """INSERT INTO presets (name, is_default, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET is_default = excluded.is_default""",
                (preset.name, int(preset.is_default), _now()),
            )
            self._conn.execute(
                "DELETE FROM preset_items WHERE preset_name = ?", (preset.name,)
            )
            rows = []
            order = 0
            for stop in preset.stops:
                for route in stop.routes:
                    rows.append((
                        preset.name, stop.station_id, stop.name, route,
                        dests.get((stop.station_id, route), ""), stop.mobile_no, order,
                    ))
                    order += 1
            self._conn.executemany(
                """INSERT INTO preset_items (preset_name, station_id, station_name,
                       route_name, dest_name, mobile_no, sort_order)
                   VALUES (?,?,?,?,?,?,?)""",
                rows,
            )
            if preset.is_default:
                self._conn.execute(
                    "UPDATE presets SET is_default = 0 WHERE name != ?", (preset.name,)
                )

    def delete(self, name: str) -> bool:
        """기본 프리셋은 지우지 않는다 — 키오스크가 띄울 것이 없어진다."""
        row = self._conn.execute(
            "SELECT is_default FROM presets WHERE name = ?", (name,)
        ).fetchone()
        if row is None or row[0]:
            return False
        with self._conn:
            self._conn.execute("DELETE FROM preset_items WHERE preset_name = ?", (name,))
            self._conn.execute("DELETE FROM presets WHERE name = ?", (name,))
        return True

    def set_default(self, name: str) -> bool:
        if self._conn.execute(
            "SELECT 1 FROM presets WHERE name = ?", (name,)
        ).fetchone() is None:
            return False
        with self._conn:
            self._conn.execute("UPDATE presets SET is_default = 0")
            self._conn.execute("UPDATE presets SET is_default = 1 WHERE name = ?", (name,))
        return True

    def seed(self, presets: tuple[Preset, ...]) -> int:
        """DB 가 비어 있을 때만 config.yaml 내용을 옮겨 담는다."""
        if not self.is_empty():
            return 0
        for order, preset in enumerate(presets):
            self.save(preset)
            self._conn.execute(
                "UPDATE presets SET sort_order = ? WHERE name = ?", (order, preset.name)
            )
        self._conn.commit()
        log.info("프리셋 %d개를 config.yaml 에서 옮겨 담았다", len(presets))
        return len(presets)

    def close(self) -> None:
        self._conn.close()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
