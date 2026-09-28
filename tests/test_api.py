"""API 계층 — 네트워크·쿼터 0. 검색과 저장소를 가짜로 바꿔 끼운다."""

import pytest
from fastapi.testclient import TestClient

from trueeta import api as api_mod
from trueeta.config import Preset, Stop
from trueeta.presets import PresetStore


class FakeSearch:
    def __init__(self):
        self.calls = []

    def by_keyword(self, keyword):
        self.calls.append(("kw", keyword))
        return [
            {"station_id": "228001207", "name": "도담마을", "mobile_no": "29273",
             "region": "용인", "x": 1, "y": 2, "ambiguous": True},
            {"station_id": "228001059", "name": "도담마을", "mobile_no": "29272",
             "region": "용인", "x": 1, "y": 2, "ambiguous": True},
        ]

    def around(self, x, y):
        self.calls.append(("xy", x, y))
        return []

    def routes_at(self, station_id):
        self.calls.append(("rt", station_id))
        return [{"route_id": "1", "route_name": "22", "route_type": "마을버스",
                 "dest_name": "미금역", "sta_order": "31"}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = PresetStore(tmp_path / "p.db")
    store.seed((Preset("집앞", (Stop("S1", "정류장", ("22",)),), is_default=True),))
    monkeypatch.setattr(api_mod, "_store", store)
    monkeypatch.setattr(api_mod, "_search", FakeSearch())
    api_mod.state.default_preset = "집앞"
    api_mod.state._boards.clear()
    api_mod.state._last_seen.clear()
    # TestClient 를 with 로 열면 lifespan 이 돌아 폴러가 뜨고 실호출이 나간다.
    # 컨텍스트 없이 쓰면 lifespan 을 건너뛴다.
    return TestClient(api_mod.app)


# --- 검색 ---------------------------------------------------------------


def test_search_marks_both_directions(client):
    body = client.get("/api/search/stations", params={"keyword": "도담마을"}).json()
    assert len(body["stations"]) == 2
    assert all(s["ambiguous"] for s in body["stations"])
    assert {s["mobile_no"] for s in body["stations"]} == {"29272", "29273"}


def test_routes_endpoint_returns_destination(client):
    body = client.get("/api/search/routes", params={"station_id": "900000001"}).json()
    assert body["routes"][0]["dest_name"] == "미금역"


def test_short_keyword_is_rejected_before_spending_quota(client):
    """한 글자 검색으로 쿼터를 낭비하지 않는다."""
    assert client.get("/api/search/stations", params={"keyword": "도"}).status_code == 422
    assert api_mod._search.calls == []


# --- 프리셋 편집 --------------------------------------------------------


def test_save_and_read_back(client):
    payload = {"stops": [{
        "station_id": "228001059", "name": "도담마을", "mobile_no": "29272",
        "routes": [{"name": "22", "dest_name": "미금역"},
                   {"name": "59", "dest_name": "수지구청"}],
    }]}
    assert client.put("/api/presets/출근", json=payload).json()["stops"] == 1

    presets = {p["name"]: p for p in client.get("/api/presets").json()["presets"]}
    assert presets["출근"]["stops"][0]["routes"] == ["22", "59"]


def test_empty_selection_is_rejected(client):
    assert client.put("/api/presets/빈것", json={"stops": []}).status_code == 400
    assert client.put(
        "/api/presets/빈것",
        json={"stops": [{"station_id": "900000009", "name": "x", "routes": []}]},
    ).status_code == 400


def test_default_preset_cannot_be_deleted(client):
    """지우면 키오스크가 띄울 것이 없어진다."""
    assert client.delete("/api/presets/집앞").status_code == 400


def test_delete_removes_a_normal_preset(client):
    client.put("/api/presets/임시", json={"stops": [{
        "station_id": "900000002", "name": "x", "routes": [{"name": "1", "dest_name": ""}]}]})
    assert client.delete("/api/presets/임시").status_code == 200
    names = [p["name"] for p in client.get("/api/presets").json()["presets"]]
    assert "임시" not in names


def test_saving_keeps_the_default_flag(client):
    """편집해도 기본 여부가 뒤바뀌면 안 된다."""
    client.put("/api/presets/집앞", json={"stops": [{
        "station_id": "900000003", "name": "y", "routes": [{"name": "7", "dest_name": ""}]}]})
    presets = {p["name"]: p for p in client.get("/api/presets").json()["presets"]}
    assert presets["집앞"]["default"] is True


def test_changing_default(client):
    client.put("/api/presets/출근", json={"stops": [{
        "station_id": "900000004", "name": "z", "routes": [{"name": "9", "dest_name": ""}]}]})
    assert client.post("/api/presets/출근/default").status_code == 200
    presets = {p["name"]: p for p in client.get("/api/presets").json()["presets"]}
    assert presets["출근"]["default"] is True and presets["집앞"]["default"] is False


# --- 보드 ---------------------------------------------------------------


def test_unknown_preset_falls_back_to_default(client):
    """URL 오타로 화면이 비면 안 된다."""
    body = client.get("/api/board", params={"preset": "없는것"}).json()
    assert body["preset"] == "집앞"


def test_board_request_is_a_subscription_heartbeat(client):
    client.put("/api/presets/출근2", json={"stops": [{
        "station_id": "900000005", "name": "w", "routes": [{"name": "3", "dest_name": ""}]}]})
    client.get("/api/board", params={"preset": "출근2"})
    assert "출근2" in client.get("/api/health").json()["active_presets"]


def test_service_key_never_reaches_the_logs(caplog):
    """httpx 는 INFO 에서 요청 URL(=서비스키 포함)을 남긴다. 막혀 있어야 한다.

    2026-09-29 에 journald 에서 키가 290줄 발견됐다.
    """
    import logging

    api_mod._setup_logging()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_non_numeric_station_id_is_rejected(client):
    """9/24 사고 때 'S4' 가 기본 프리셋에 들어가 이틀을 날렸다. 입구에서 막는다."""
    r = client.put("/api/presets/잘못", json={"stops": [{
        "station_id": "S4", "name": "z", "routes": [{"name": "9", "dest_name": ""}]}]})
    assert r.status_code == 400
    names = [p["name"] for p in client.get("/api/presets").json()["presets"]]
    assert "잘못" not in names
