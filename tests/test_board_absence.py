"""B안을 화면에 — '—' 대신 '회차지 대기 · 최소 N분 · 보통 M분'.

그리고 임시 주기(boost) — 정해진 시각까지만 짧게 조회하되, 오늘 남은
호출로 감당이 안 되면 스스로 늘린다.
"""

import glob
import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from trueeta.config import BOOST_RESERVE, load_board_config
from trueeta.judge_absence import BVerdict
from trueeta.models import Status
from trueeta.poller import BContext, RouteState, board_for_preset, judge_station, with_absence
from trueeta.state import BoardState


def _items(station_id):
    paths = sorted(glob.glob(f"tests/fixtures/arrivals_{station_id}_*.json"))
    if not paths:
        pytest.skip("fixture 없음")
    return json.load(open(paths[-1], encoding="utf-8"))["response"]["msgBody"]["busArrivalList"]


def _state(**kw) -> RouteState:
    base = dict(dest_name="미금역", status=Status.NO_BUS, eta_sec=None,
                second_eta_sec=None, second_status=None,
                estimated_wait=False, at_standing=False)
    base.update(kw)
    return RouteState(**base)


# --- 덧씌우기 규칙 ------------------------------------------------------


def test_unseen_fills_the_wait_fields():
    got = with_absence(_state(), BVerdict("unseen", min_sec=180, expected_sec=600))
    assert (got.wait_min_sec, got.wait_expected_sec, got.wait_overdue) == (180, 600, False)
    assert got.status == Status.NO_BUS


def test_overdue_is_carried():
    got = with_absence(_state(), BVerdict("unseen", min_sec=180, expected_sec=180, overdue=True))
    assert got.wait_overdue


def test_running_vehicle_is_left_alone():
    """차가 보이면 ETA 가 더 정확하다. B안이 뭐라 하든 덮지 않는다."""
    s = _state(status=Status.RUNNING, eta_sec=300)
    assert with_absence(s, BVerdict("unseen", min_sec=180)) == s


def test_api_confirmed_wait_is_left_alone():
    """flag=WAIT 로 API 가 확정해 준 회차대기는 채운 배지 그대로."""
    s = _state(status=Status.WAITING)
    assert with_absence(s, BVerdict("unseen", min_sec=180)) == s


def test_route_off_hours_becomes_ended():
    """노선별 막차가 지났다. 전체 운행창(00:10)보다 이른 노선이 있다."""
    assert with_absence(_state(), BVerdict("off")).status == Status.ENDED


def test_missing_route_shows_no_wait():
    """응답에 노선이 없으면 설정 오류다. 회차지 대기라고 말하면 안 된다."""
    s = _state()
    assert with_absence(s, BVerdict("missing")) == s


# --- 폴러 통합: 실제 응답 ----------------------------------------------


def test_real_response_puts_wait_on_the_empty_route():
    """도담마을 fixture: 22번은 차 없음 → 회차지 대기, 59번은 ETA 그대로."""
    bctx = BContext(now=datetime.now().astimezone())
    judged = judge_station("228001059", {"22", "59"}, _items("228001059"),
                           BoardState(), 240, set(), [], bctx)
    assert judged[("228001059", "22")].wait_min_sec > 0
    assert judged[("228001059", "59")].wait_min_sec is None
    assert judged[("228001059", "59")].eta_sec is not None


def test_board_entry_exposes_the_wait_fields():
    cfg = load_board_config()
    preset = cfg.default_preset
    stop = preset.stops[0]
    route = stop.routes[0]
    judged = {(stop.station_id, route): replace(
        _state(), wait_min_sec=180, wait_expected_sec=600, wait_overdue=False)}
    entry = board_for_preset(preset, judged, {}).entries[0].as_dict()
    assert entry["wait_min_sec"] == 180
    assert entry["wait_expected_sec"] == 600
    assert entry["wait_overdue"] is False


def test_route_absent_from_response_has_no_wait_on_board():
    cfg = load_board_config()
    entry = board_for_preset(cfg.default_preset, {}, {}).entries[0].as_dict()
    assert entry["status"] == "no_bus" and entry["wait_min_sec"] is None


# --- 임시 주기 ----------------------------------------------------------


@pytest.fixture
def boosted():
    cfg = load_board_config()
    return replace(cfg, boost_interval_sec=60,
                   boost_until=datetime(2026, 9, 30, 0, 10), daily_budget=1000)


def test_boost_runs_at_its_interval_when_calls_are_plenty(boosted):
    now = datetime(2026, 9, 29, 19, 30)
    # 19:30~24:00 = 270분, 남은 몫 (1000-50-10)/2 = 470사이클 → 60초 가능
    assert boosted.boost_interval(now, 2, used_today=10) == 60


def test_boost_stretches_when_calls_run_short(boosted):
    now = datetime(2026, 9, 29, 19, 30)
    used = 800
    got = boosted.boost_interval(now, 2, used_today=used)
    cycles_left = (1000 - BOOST_RESERVE - used) // 2
    # 자정까지 남은 몫으로 버틴다
    assert got > 60
    assert (datetime(2026, 9, 30) - now).total_seconds() / got <= cycles_left


def test_boost_never_spends_the_reserve(boosted):
    assert boosted.boost_interval(datetime(2026, 9, 29, 23, 0), 2,
                                  used_today=1000 - BOOST_RESERVE) is None


def test_boost_ends_at_until(boosted):
    assert boosted.boost_interval(datetime(2026, 9, 30, 0, 10), 2, 0) is None
    assert boosted.boost_interval(datetime(2026, 9, 30, 0, 5), 2, 0) == 60


def test_boost_is_off_by_default():
    cfg = replace(load_board_config(), boost_interval_sec=0, boost_until=None)
    assert cfg.boost_interval(datetime.now(), 2, 0) is None


def test_whole_evening_fits_the_budget(boosted):
    """19:20 부터 1분씩 돌려도 자정까지 한도 안 — 오늘 실제 계획의 산수."""
    now = datetime(2026, 9, 29, 19, 20)
    used = 10
    while now < datetime(2026, 9, 30):
        step = boosted.boost_interval(now, 2, used)
        assert step is not None
        used += 2
        now += timedelta(seconds=step)
    assert used <= 1000 - BOOST_RESERVE + 2
