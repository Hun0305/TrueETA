"""python -m trueeta 로 전광판 서버를 띄운다.

포트는 TRUEETA_PORT 로 바꾼다 (기본 8099).
8080 은 이 라즈베리파이의 다른 서버가 쓰고 있어서 피했다.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "trueeta.api:app",
        host=os.environ.get("TRUEETA_HOST", "0.0.0.0"),
        port=int(os.environ.get("TRUEETA_PORT", "8099")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
