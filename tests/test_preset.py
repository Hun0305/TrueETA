"""프리셋 — 비용이 '저장한 개수' 가 아니라 '보고 있는 개수' 에 비례하는가."""

import pytest
import yaml

from trueeta.config import Preset, load_board_config
from trueeta.state import BoardState


# --- 설정 로딩 -----------------------------------------------------------


def _write(tmp_path, data) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


BASE = {
    "display": {}, "polling": {}, "judge": {},
}
STOP = {"station_id": 228001059, "name": "도담마을", "routes": ["22", "59"]}


def test_old_config_without_presets_still_works(tmp_path):
    """`stops:` 만 있는 기존 설정을 고치지 않아도 동작해야 한다."""
    cfg = load_board_config(_write(tmp_path, {**BASE, "stops": [STOP]}))
    assert len(cfg.presets) == 1
    assert cfg.default_preset.is_default
    assert cfg.stops[0].station_id == "228001059"   # 하위호환 속성


def test_first_preset_becomes_default_when_unmarked(tmp_path):
    """키오스크가 띄울 것이 반드시 하나는 있어야 한다."""
    cfg = load_board_config(_write(tmp_path, {**BASE, "presets": [
        {"name": "가", "stops": [STOP]},
        {"name": "나", "stops": [STOP]},
    ]}))
    assert cfg.default_preset.name == "가"


def test_explicit_default_wins(tmp_path):
    cfg = load_board_config(_write(tmp_path, {**BASE, "presets": [
        {"name": "가", "stops": [STOP]},
        {"name": "나", "stops": [STOP], "default": True},
    ]}))
    assert cfg.default_preset.name == "나"


def test_duplicate_names_are_rejected(tmp_path):
    with pytest.raises(SystemExit):
        load_board_config(_write(tmp_path, {**BASE, "presets": [
            {"name": "같음", "stops": [STOP]},
            {"name": "같음", "stops": [STOP]},
        ]}))


def test_unknown_preset_falls_back_to_default(tmp_path):
    """URL 에 오타가 나도 화면이 비면 안 된다."""
    cfg = load_board_config(_write(tmp_path, {**BASE, "stops": [STOP]}))
    assert cfg.preset("없는이름") is cfg.default_preset
    assert cfg.preset(None) is cfg.default_preset


# --- 구독 ----------------------------------------------------------------


def test_default_preset_is_always_polled():
    """아무도 안 봐도 기본은 돈다 — 여기서 빠지면 관측 수집이 멈춘다."""
    state = BoardState(default_preset="집앞")
    assert state.active_presets(now=0) == {"집앞"}


def test_viewing_a_preset_activates_it():
    state = BoardState(default_preset="집앞")
    state.touch("출근", now=0)
    assert state.active_presets(now=30) == {"집앞", "출근"}


def test_subscription_expires_when_the_screen_stops_asking():
    """브라우저를 닫아도 서버는 모른다. 하트비트가 끊기면 뺀다."""
    state = BoardState(default_preset="집앞")
    state.touch("출근", now=0)
    assert state.active_presets(ttl=60, now=61) == {"집앞"}


def test_boards_are_kept_per_preset():
    from trueeta.models import Board
    state = BoardState(default_preset="집앞")
    state.set(Board(entries=(), updated_at="t", error="A"), "집앞")
    state.set(Board(entries=(), updated_at="t", error="B"), "출근")
    assert state.get("집앞").error == "A"
    assert state.get("출근").error == "B"


# --- 예산 ----------------------------------------------------------------


def test_interval_stretches_as_stations_grow():
    """정류장이 늘면 주기가 길어져야 한도 안에 남는다."""
    cfg = load_board_config()
    previous = 0
    for n in (1, 2, 3, 5, 8):
        peak, far = cfg.intervals_for(n)
        assert peak >= previous
        previous = peak


def test_every_station_count_stays_under_the_quota():
    cfg = load_board_config()
    for n in range(1, 13):
        peak, _ = cfg.intervals_for(n)
        scale = peak / cfg.interval_peak_sec
        assert cfg.daily_calls_for(n, scale) <= cfg.daily_budget, n


def test_two_stations_keep_the_tuned_intervals():
    """현재 설정에서는 동작이 바뀌지 않아야 한다 (관측 데이터 연속성)."""
    cfg = load_board_config()
    assert cfg.intervals_for(2) == (cfg.interval_peak_sec, cfg.interval_far_sec)


# --- 회귀 방지 -----------------------------------------------------------


def test_startup_log_format_matches_its_arguments():
    """포맷 자리수와 인자 수가 어긋나면 로그가 통째로 안 찍힌다.

    실제로 한 번 놓쳤다 — logging 은 예외를 삼키고 "Message: ..." 만 남긴다.
    """
    import inspect

    from trueeta import api

    source = inspect.getsource(api.lifespan)
    start = source.index('"프리셋 %d개')
    call = source[start : source.index(")", source.index("daily_calls"))]
    fmt = call.split('",')[0]
    placeholders = fmt.count("%d") + fmt.count("%s")
    args = len([l for l in call.split("\n")[1:] if l.strip().endswith(",")])
    assert placeholders == args, f"자리 {placeholders}개 vs 인자 {args}개"
