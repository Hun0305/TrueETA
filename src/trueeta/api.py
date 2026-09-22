"""FastAPI 앱. 폴러를 lifespan 에서 띄우고 /api/board 와 화면을 서빙한다.

폴러와 웹서버가 한 프로세스라 상태는 메모리로 공유한다.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from trueeta.config import load_board_config, load_settings
from trueeta.poller import run_poller
from trueeta.state import BoardState

import asyncio

log = logging.getLogger("trueeta")

WEB_DIR = Path(__file__).parent / "web"

state = BoardState()


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
    settings = load_settings()
    config = load_board_config()
    log.info(
        "폴링 시작: 정류장 %d곳, %s~%s 는 %d초 · 그 외 %d초 (하루 약 %d콜)",
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
def get_board() -> JSONResponse:
    return JSONResponse(state.get().as_dict())


@app.get("/api/health")
def health() -> dict:
    board = state.get()
    return {
        "ok": board.error is None,
        "entries": len(board.entries),
        "updated_at": board.updated_at,
        "error": board.error,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
