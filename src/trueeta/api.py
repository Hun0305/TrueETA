"""FastAPI 앱. 폴러를 lifespan 에서 띄우고 /api/board 와 화면을 서빙한다.

폴러와 웹서버가 한 프로세스라 상태는 메모리로 공유한다.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from trueeta import logfile
from trueeta.config import (
    BoardConfig, Preset, Stop, load_board_config, load_settings, valid_station_id,
)
from trueeta.gbis import GbisClient, GbisError
from trueeta.presets import PresetStore
from trueeta.search import StationSearch
from trueeta.poller import run_poller
from trueeta.state import BoardState

import asyncio

log = logging.getLogger("trueeta")

WEB_DIR = Path(__file__).parent / "web"

state = BoardState()
_config: BoardConfig | None = None
_store: PresetStore | None = None
_search: StationSearch | None = None


def _setup_logging(var_dir: Path | None = None) -> Path | None:
    """uvicorn 의 --log-level 은 자기 로거만 건드린다.
    폴러가 뭘 하고 있는지 보이려면 우리 로거에도 핸들러를 달아야 한다.

    var_dir 를 주면 파일로도 남긴다 (logfile.py — journald 가 메모리에만 있어
    재부팅하면 사라지기 때문). 앱 로그 파일 경로를 돌려준다.
    """
    if not logging.getLogger("trueeta").handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    # httpx 는 INFO 에서 요청 URL 을 통째로 남기는데, 서비스키가 쿼리스트링에
    # 들어 있다. basicConfig(INFO) 가 이걸 같이 열어서 journald 에 키가 290줄
    # 새어 나갔다 (2026-09-29 발견). 우리 로그만 INFO 로 두고 이쪽은 막는다.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # 그래도 새면 가린다 — journald 로 가는 스트림에도 한 겹
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, logfile.RedactServiceKey) for f in handler.filters):
            handler.addFilter(logfile.RedactServiceKey())

    return logfile.attach(var_dir) if var_dir is not None else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _config
    settings = load_settings()
    log_path = _setup_logging(settings.var_dir)
    if log_path:
        log.info("로그 파일: %s (journald 는 재부팅하면 사라진다)", log_path)
    config = load_board_config()
    _config = config

    global _store, _search
    _store = PresetStore(settings.presets_path)
    _store.seed(config.presets)          # DB 가 비었을 때만 옮겨 담는다
    state.default_preset = _store.all()[0].name
    for preset in _store.all():
        if preset.is_default:
            state.default_preset = preset.name
    _search = StationSearch(
        GbisClient(settings.service_key, timeout=settings.timeout,
                   quota_path=settings.quota_path)
    )
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
    task = asyncio.create_task(run_poller(settings, config, state, _store))
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
    known = {p.name for p in (_store.all() if _store else ())}
    name = preset if preset in known else state.default_preset
    state.touch(name)
    payload = state.get(name).as_dict()
    payload["preset"] = name
    return JSONResponse(payload)


@app.get("/api/presets")
def get_presets() -> JSONResponse:
    """화면이 프리셋 목록을 그릴 때 쓴다."""
    presets = _store.all() if _store else (_config.presets if _config else ())
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
            for p in presets
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


# --- 검색 (프리셋 편집용) --------------------------------------------
#
# 검색 한도는 도착정보와 별개다 (오퍼레이션마다 1,000건/일).
# 그래도 StationSearch 가 캐시를 들고 있고, 화면은 검색 버튼·엔터에서만 부른다.


def _need_search() -> StationSearch:
    if _search is None:
        raise HTTPException(503, "검색이 아직 준비되지 않았습니다")
    return _search


@app.get("/api/search/stations")
def search_stations(keyword: str = Query(min_length=2)) -> JSONResponse:
    """이름·번호로 정류장을 찾는다.

    같은 이름이 둘 나오면 양방향이다 (`ambiguous`). 표지판 번호(`mobile_no`)로
    현장에서 대조할 수 있고, 방향은 노선 목록에서 확정한다.
    """
    try:
        return JSONResponse({"stations": _need_search().by_keyword(keyword)})
    except GbisError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/search/around")
def search_around(x: float, y: float) -> JSONResponse:
    """좌표 반경 500m. 폰에서 '내 주변' 을 찾을 때."""
    try:
        return JSONResponse({"stations": _need_search().around(x, y)})
    except GbisError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/search/routes")
def search_routes(station_id: str) -> JSONResponse:
    """정류장을 지나는 노선. **방면(dest_name)이 여기서 나온다.**"""
    try:
        return JSONResponse({"routes": _need_search().routes_at(station_id)})
    except GbisError as exc:
        raise HTTPException(502, str(exc)) from exc


# --- 프리셋 편집 ------------------------------------------------------


def _need_store() -> PresetStore:
    if _store is None:
        raise HTTPException(503, "저장소가 아직 준비되지 않았습니다")
    return _store


@app.put("/api/presets/{name}")
def save_preset(name: str, payload: dict = Body(...)) -> JSONResponse:
    """프리셋 하나를 통째로 덮어쓴다.

    payload: {"stops": [{"station_id", "name", "mobile_no",
                         "routes": [{"name", "dest_name"}]}]}
    """
    store = _need_store()
    stops, dests = [], {}
    for entry in payload.get("stops") or []:
        station_id = str(entry.get("station_id") or "").strip()
        routes = entry.get("routes") or []
        if not station_id or not routes:
            continue
        if not valid_station_id(station_id):
            # 9/24 사고 때 'S4' 같은 값이 기본 프리셋에 들어가 이틀을 날렸다
            raise HTTPException(400, f"정류소 ID 형식이 아닙니다: {station_id}")
        names = []
        for route in routes:
            route_name = str(route.get("name") if isinstance(route, dict) else route).strip()
            if not route_name:
                continue
            names.append(route_name)
            if isinstance(route, dict):
                dests[(station_id, route_name)] = str(route.get("dest_name") or "")
        if not names:
            continue
        stops.append(Stop(
            station_id=station_id,
            name=str(entry.get("name") or station_id),
            routes=tuple(names),
            mobile_no=str(entry.get("mobile_no") or ""),
        ))

    if not stops:
        raise HTTPException(400, "정류장과 노선을 하나 이상 골라야 합니다")

    existing = {p.name: p for p in store.all()}
    was_default = existing[name].is_default if name in existing else False
    store.save(Preset(name=name, stops=tuple(stops), is_default=was_default), dests=dests)
    return JSONResponse({"saved": name, "stops": len(stops)})


@app.delete("/api/presets/{name}")
def delete_preset(name: str) -> JSONResponse:
    """기본 프리셋은 지울 수 없다 — 키오스크가 띄울 것이 없어진다."""
    if not _need_store().delete(name):
        raise HTTPException(400, "기본 프리셋이거나 존재하지 않습니다")
    return JSONResponse({"deleted": name})


@app.post("/api/presets/{name}/default")
def make_default(name: str) -> JSONResponse:
    store = _need_store()
    if not store.set_default(name):
        raise HTTPException(404, "그런 프리셋이 없습니다")
    state.default_preset = name
    return JSONResponse({"default": name})


@app.get("/edit")
def editor() -> FileResponse:
    return FileResponse(WEB_DIR / "edit.html")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
