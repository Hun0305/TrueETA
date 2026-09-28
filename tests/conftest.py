"""테스트 공통 설정.

1. `pip install -e .` 전에도 돌도록 경로를 잡는다.
2. **실제 var/ 를 절대 못 건드리게 한다.**

2026-09-24 에 test_api.py 픽스처가 lifespan 을 돌려 실제 var/presets.db 를
테스트 데이터로 덮어썼고, 기본 프리셋이 존재하지 않는 정류장으로 바뀌어
관측 로그를 이틀 날렸다 (docs/incident-2026-09-24.md). 그때는 픽스처만 고쳤고
구조적인 방어선이 없었다. 이제 두 겹으로 막는다:

  - 세션이 시작하는 순간 TRUEETA_VAR_DIR 을 임시 디렉터리로 돌린다.
    load_settings() 를 거치는 모든 경로(lifespan 포함)가 거기로 간다
  - 그래도 실제 var/presets.db 가 바뀌었으면 세션을 실패시킨다
"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src", ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

# trueeta 를 import 하기 전에 정해야 한다
_SANDBOX = tempfile.mkdtemp(prefix="trueeta-test-var-")
os.environ["TRUEETA_VAR_DIR"] = _SANDBOX

# 실제 데이터 중 테스트가 바꿀 이유가 전혀 없는 것 (서비스가 쓰는 관측 로그는 제외)
_GUARDED = [ROOT / "var" / "presets.db"]


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


_BEFORE = {p: _digest(p) for p in _GUARDED}


def pytest_sessionfinish(session, exitstatus):
    changed = [str(p) for p in _GUARDED if _digest(p) != _BEFORE[p]]
    if changed:
        sys.stderr.write(
            "\n\n!!! 테스트가 실제 데이터를 바꿨다: %s\n"
            "!!! tmp_path 를 쓰지 않는 테스트가 있다. docs/incident-2026-09-24.md 참고\n\n"
            % ", ".join(changed)
        )
        session.exitstatus = 1
