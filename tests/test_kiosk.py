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


# --- 잠깐 나가기 (Ctrl+Alt+K) ----------------------------------------------

TOGGLE = ROOT / "scripts" / "kiosk-toggle.sh"


def test_toggle_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(TOGGLE)]).returncode == 0


def test_keybind_runs_the_toggle():
    import xml.dom.minidom as dom

    doc = dom.parse(str(ROOT / "scripts" / "labwc-rc.xml"))
    binds = {k.getAttribute("key"): k for k in doc.getElementsByTagName("keybind")}
    assert "C-A-k" in binds
    action = binds["C-A-k"].getElementsByTagName("action")[0]
    assert action.getAttribute("command").endswith("scripts/kiosk-toggle.sh")


def test_keybind_does_not_shadow_system_shortcuts():
    """Ctrl+Alt+T(터미널) 등 시스템 단축키와 겹치면 안 된다."""
    import re

    system = Path("/etc/xdg/labwc/rc.xml")
    if not system.exists():
        return
    taken = set(re.findall(r'keybind key="([^"]+)"', system.read_text()))
    assert "C-A-k" not in taken


def test_pause_lives_in_runtime_dir_so_reboot_restores_kiosk():
    """잠깐 나가기는 재부팅하면 풀려야 한다 — 전원만 꽂으면 전광판으로."""
    for f in (SCRIPT, TOGGLE):
        text = f.read_text(encoding="utf-8")
        assert 'PAUSE="${XDG_RUNTIME_DIR' in text or 'PAUSE="$RUNTIME/' in text
        assert "/run/user/" in text


def test_loop_stops_on_pause_as_well_as_disable():
    assert 'while [ ! -e "$DISABLE" ] && [ ! -e "$PAUSE" ]' in SCRIPT.read_text(encoding="utf-8")


def test_chromium_does_not_inherit_the_lock():
    """물려주면 루프를 꺼도 Chromium 이 잠금을 쥐고 있어 새 루프가 물러났다."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "flock -n 9" in text
    assert '"$URL" > /dev/null 2>&1 9>&-' in text


def test_toggle_kills_only_the_kiosk_main_process():
    """pkill -f 'chromium.*trueeta-kiosk' 는 그 문자열이 든 다른 셸까지 죽였다."""
    code = "\n".join(
        l for l in TOGGLE.read_text(encoding="utf-8").splitlines()
        if not l.lstrip().startswith("#")          # 주석엔 겪은 일로 적혀 있다
    )
    assert "pkill -f" not in code
    assert "pgrep -x chromium" in code and "--type=" in code
