"""방어선 자체의 테스트 — 테스트 세션에서 load_settings() 는 실제 var/ 를 가리키면 안 된다."""

from trueeta.config import PROJECT_ROOT, load_settings


def test_settings_point_to_a_sandbox_during_tests():
    var = load_settings(require_key=False).var_dir
    assert var != PROJECT_ROOT / "var"
    assert "trueeta-test-var-" in str(var)


def test_every_data_file_lives_in_the_sandbox():
    s = load_settings(require_key=False)
    for path in (s.presets_path, s.observations_path, s.quota_path, s.routeinfo_path):
        assert PROJECT_ROOT / "var" not in path.parents, path
