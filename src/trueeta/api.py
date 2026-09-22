"""FastAPI 앱. 폴러를 lifespan 에서 띄우고 /api/board 와 화면을 서빙한다.

폴러와 웹서버가 한 프로세스라 상태는 메모리로 공유한다.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse

from trueeta.config import BoardConfig, load_board_config, load_settings
from trueeta.poller import run_poller
from trueeta.state import BoardState

import asyncio

log = logging.getLogger("trueeta")

WEB_DIR = Path(__file__).parent / "web"

state = BoardState()
_config: BoardConfig | None = None


def _setup_logging() -> None:
    """uvicorn 의 --log-level 은 자기 로거만 건드린다.
    폴러가 뭘 하고 있는지 보이려면 우리 로거에도 핸들러를 달아야 한다."""
    if not logging.getLogger("trueeta").handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    global _config
    settings = load_settings()
    config = load_board_config()
    _config = config
    state.default_preset = config.default_preset.name
    log.info(
        "프리셋 %d개 (기본 '%s'), 정류장 %d곳, %s~%s 는 %d초 · 그 외 %d초 (하루 약 %d콜)",
        len(config.presets),
        config.default_preset.name,
        len(config.stops),
        config.peak_start,
        config.peak_end,
        config.interval_peak_sec,
        config.interval_far_sec,
        config.daily_calls,
    )
    task = asyncio.create_task(run_poller(settings, config, state))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="TrueETA", lifespan=lifespan)


@app.get("/api/board")
def get_board(preset: str | None = Query(default=None)) -> JSONResponse:
    """이 호출이 곧 '이 프리셋을 보고 있다' 는 하트비트다.

    화면이 15초마다 부르므로, 끊기면 폴러가 그 프리셋을 대상에서 뺀다.
    """
    name = _config.preset(preset).name if _config else (preset or state.default_preset)
    state.touch(name)
    payload = state.get(name).as_dict()
    payload["preset"] = name
    return JSONResponse(payload)


@app.get("/api/presets")
def get_presets() -> JSONResponse:
    """화면이 프리셋 목록을 그릴 때 쓴다."""
    if _config is None:
        return JSONResponse({"presets": []})
    return JSONResponse({
        "presets": [
            {
                "name": p.name,
                "default": p.is_default,
                "stops": [
                    {"name": s.name, "station_id": s.station_id, "routes": list(s.routes)}
                    for s in p.stops
                ],
            }
            for p in _config.presets
        ]
    })


@app.get("/api/health")
def health() -> dict:
    board = state.get()
    return {
        "ok": board.error is None,
        "entries": len(board.entries),
        "updated_at": board.updated_at,
        "error": board.error,
        "active_presets": sorted(state.active_presets()),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
