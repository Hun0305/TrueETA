"""python -m trueeta 로 전광판 서버를 띄운다.

포트는 TRUEETA_PORT 로 바꾼다 (기본 8099).
8080 은 이 라즈베리파이의 다른 서버가 쓰고 있어서 피했다.

TRUEETA_RELOAD=1 로 띄우면 .py 수정 시 자동 재시작된다 (개발용, systemd 서비스에서는 끈다).
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

SRC_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    reload = os.environ.get("TRUEETA_RELOAD") == "1"
    uvicorn.run(
        "trueeta.api:app",
        host=os.environ.get("TRUEETA_HOST", "0.0.0.0"),
        port=int(os.environ.get("TRUEETA_PORT", "8099")),
        log_level="info",
        reload=reload,
        # 감시 범위를 src/ 로 좁힌다. 기본값은 현재 디렉터리 전체라
        # .venv 의 수천 개 .py 까지 0.25 초마다 stat 하게 된다.
        reload_dirs=[str(SRC_DIR)] if reload else None,
    )


if __name__ == "__main__":
    main()
