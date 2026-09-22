"""프리셋 저장소 — 화면에서 편집한 내용의 원본이다."""

import pytest

from trueeta.config import Preset, Stop, load_board_config
from trueeta.presets import PresetStore


def _stop(sid="S1", routes=("22", "59")) -> Stop:
    return Stop(station_id=sid, name="정류장", routes=routes, mobile_no="12345")


def store(tmp_path) -> PresetStore:
    return PresetStore(tmp_path / "presets.db")


def test_seed_only_runs_when_empty(tmp_path):
    """설정 파일을 고쳐도 DB 를 덮어쓰면 안 된다 — 편집 내용이 날아간다."""
    s = store(tmp_path)
    assert s.is_empty()
    assert s.seed((Preset("집앞", (_stop(),), is_default=True),)) == 1
    assert s.seed((Preset("다른것", (_stop(),)),)) == 0   # 두 번째는 무시
    assert [p.name for p in s.all()] == ["집앞"]


def test_roundtrip_preserves_stops_and_routes(tmp_path):
    s = store(tmp_path)
    s.save(Preset("출근", (_stop("S1", ("22",)), _stop("S2", ("25", "59"))), True))
    got = s.all()[0]
    assert got.name == "출근" and got.is_default
    assert [(x.station_id, list(x.routes)) for x in got.stops] == [
        ("S1", ["22"]), ("S2", ["25", "59"]),
    ]


def test_routes_at_the_same_station_are_grouped(tmp_path):
    """저장은 (정류장, 노선) 행 단위지만 읽을 때는 정류장으로 묶인다."""
    s = store(tmp_path)
    s.save(Preset("집앞", (_stop("S1", ("22", "59")),)))
    assert len(s.all()[0].stops) == 1
    assert s.all()[0].stops[0].routes == ("22", "59")


def test_stop_order_is_kept(tmp_path):
    s = store(tmp_path)
    s.save(Preset("순서", (_stop("B", ("1",)), _stop("A", ("2",)))))
    assert [x.station_id for x in s.all()[0].stops] == ["B", "A"]


def test_saving_again_replaces_not_appends(tmp_path):
    s = store(tmp_path)
    s.save(Preset("집앞", (_stop("S1", ("22",)),)))
    s.save(Preset("집앞", (_stop("S2", ("25",)),)))
    stops = s.all()[0].stops
    assert len(stops) == 1 and stops[0].station_id == "S2"


def test_dest_name_is_stored_for_direction(tmp_path):
    """방면은 고를 때 본 값을 그대로 보관한다 (나중에 바뀌었는지 비교용)."""
    s = store(tmp_path)
    s.save(Preset("집앞", (_stop("S1", ("22",)),)),
           dests={("S1", "22"): "미금역"})
    assert "미금역" in s.all()[0].stops[0].note


def test_only_one_default(tmp_path):
    s = store(tmp_path)
    s.save(Preset("가", (_stop(),), is_default=True))
    s.save(Preset("나", (_stop(),), is_default=True))
    assert [p.name for p in s.all() if p.is_default] == ["나"]


def test_default_preset_cannot_be_deleted(tmp_path):
    """지우면 키오스크가 띄울 것이 없어진다."""
    s = store(tmp_path)
    s.save(Preset("집앞", (_stop(),), is_default=True))
    s.save(Preset("출근", (_stop(),)))
    assert s.delete("집앞") is False
    assert s.delete("출근") is True
    assert s.delete("없는것") is False


def test_set_default_moves_the_flag(tmp_path):
    s = store(tmp_path)
    s.save(Preset("가", (_stop(),), is_default=True))
    s.save(Preset("나", (_stop(),)))
    assert s.set_default("나") is True
    assert [p.name for p in s.all() if p.is_default] == ["나"]
    assert s.set_default("없는것") is False


def test_seeds_the_real_config(tmp_path):
    """실제 config.yaml 이 그대로 들어가는지."""
    s = store(tmp_path)
    cfg = load_board_config()
    s.seed(cfg.presets)
    assert [p.name for p in s.all()] == [p.name for p in cfg.presets]
    assert s.all()[0].station_ids == cfg.presets[0].station_ids
