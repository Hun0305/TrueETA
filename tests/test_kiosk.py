"""키오스크 스크립트 — Chromium 은 띄우지 않고 구성만 확인한다.

실제 화면 검증은 docs/kiosk.md 에 캡처로 남겼다.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "kiosk.sh"


def test_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_launches_natively_on_wayland():
    """기본값(X11)이면 'Missing X server' 로 바로 죽었다."""
    assert "--ozone-platform=wayland" in SCRIPT.read_text(encoding="utf-8")


def test_prefs_are_seeded_before_every_launch():
    text = SCRIPT.read_text(encoding="utf-8")
    loop = text[text.index("while [ ! -e \"$DISABLE\" ]"):]
    assert loop.index("seed_prefs") < loop.index("chromium \\")


def test_seed_prefs_disables_translate_and_marks_clean_exit(tmp_path):
    """번역 팝업이 갱신 시각을 가렸다. 플래그·meta 로는 안 막혔고 프로필 설정이 먹었다.

    기존 설정은 보존해야 한다 (Chromium 이 쓴 다른 값을 날리면 안 된다).
    """
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir(parents=True)
    prefs.write_text(json.dumps({
        "profile": {"exit_type": "Crashed", "exited_cleanly": False, "name": "x"},
        "keep_me": 1,
    }))
    fn = subprocess.run(
        ["sed", "-n", "/^seed_prefs()/,/^}/p", str(SCRIPT)],
        capture_output=True, text=True, check=True,
    ).stdout
    subprocess.run(
        ["bash", "-c", f'PROFILE="{tmp_path}"; LOG=/dev/null\n{fn}\nseed_prefs'],
        check=True,
    )
    got = json.loads(prefs.read_text())
    assert got["translate"]["enabled"] is False
    assert got["translate_blocked_languages"] == ["ko"]
    assert got["profile"]["exit_type"] == "Normal"
    assert got["profile"]["exited_cleanly"] is True
    assert got["profile"]["name"] == "x" and got["keep_me"] == 1


def test_seed_prefs_creates_the_profile_on_first_boot(tmp_path):
    fn = subprocess.run(["sed", "-n", "/^seed_prefs()/,/^}/p", str(SCRIPT)],
                        capture_output=True, text=True, check=True).stdout
    subprocess.run(["bash", "-c", f'PROFILE="{tmp_path}/new"; LOG=/dev/null\n{fn}\nseed_prefs'],
                   check=True)
    got = json.loads((tmp_path / "new" / "Default" / "Preferences").read_text())
    assert got["translate"]["enabled"] is False


def test_pages_also_declare_notranslate():
    """프로필 설정이 주 방어선이고, 폰에서 열 때를 위해 페이지에도 선언해 둔다."""
    for name in ("index.html", "edit.html"):
        html = (ROOT / "src" / "trueeta" / "web" / name).read_text(encoding="utf-8")
        assert 'content="notranslate"' in html and 'translate="no"' in html


def test_autostart_copy_points_to_the_script():
    line = [l for l in (ROOT / "scripts" / "labwc-autostart").read_text().splitlines()
            if l and not l.startswith("#")]
    assert line == ["/home/hun/TrueETA/scripts/kiosk.sh &"]
