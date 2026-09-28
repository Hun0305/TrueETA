"""로그를 파일로도 남긴다.

## 왜

라즈베리파이 OS 는 SD 카드 수명을 아끼려고 journald 를 **메모리에만** 둔다
(`/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf` 의
`Storage=volatile`). 저널은 `/run/log/journal` 에 있고 재부팅하면 통째로 사라진다.

그래서 9/24 사고 조사 때 "어제 무슨 일이 있었나" 를 저널로 볼 수 없었다.
시스템 설정을 바꾸지 않고(= sudo 없이, SD 쓰기를 전역으로 늘리지 않고)
**우리 서비스 로그만** 파일로 남긴다.

## 무엇을

    var/logs/trueeta.log   앱 로그 + uvicorn 오류.  자정마다 교체, 30일 보관
    var/logs/access.log    화면이 부르는 HTTP 접속.  자정마다 교체, 7일 보관

접속 로그를 따로 둔 이유: 화면이 15초마다 /api/board 를 불러 하루 5,700줄이 넘는다.
한 파일에 섞으면 폴러 로그가 묻힌다. 필요할 때만 보면 되므로 보관도 짧게 한다.

## 서비스키

httpx 로거는 WARNING 으로 막아뒀지만(9/29 journald 에 키가 290줄 샌 뒤),
파일은 오래 남으므로 한 겹 더 막는다. 모든 핸들러가 `serviceKey=...` 를 가린다.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

log = logging.getLogger("trueeta.logfile")

APP_LOG = "trueeta.log"
ACCESS_LOG = "access.log"
APP_KEEP_DAYS = 30
ACCESS_KEEP_DAYS = 7

FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
FILE_DATEFMT = "%Y-%m-%d %H:%M:%S"

_KEY = re.compile(r"(serviceKey=)[^&\s\"']+", re.IGNORECASE)
_MARK = "_trueeta_file_handler"


class RedactServiceKey(logging.Filter):
    """메시지에 서비스키가 섞여 있으면 가린다. 로그는 절대 막지 않는다."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:
            return True
        if "serviceKey=" in text or "servicekey=" in text.lower():
            record.msg = _KEY.sub(r"\1***", text)
            record.args = ()
        return True


def redact(text: str) -> str:
    return _KEY.sub(r"\1***", text)


def _handler(path: Path, keep_days: int) -> TimedRotatingFileHandler:
    h = TimedRotatingFileHandler(
        path, when="midnight", backupCount=keep_days, encoding="utf-8", delay=True
    )
    h.setFormatter(logging.Formatter(FILE_FORMAT, FILE_DATEFMT))
    h.addFilter(RedactServiceKey())
    setattr(h, _MARK, True)
    return h


def _already(logger: logging.Logger) -> bool:
    return any(getattr(h, _MARK, False) for h in logger.handlers)


def attach(var_dir: Path) -> Path | None:
    """파일 핸들러를 붙인다. 여러 번 불러도 한 번만 붙는다.

    디렉터리를 못 만들면 경고만 남기고 None — 로그 파일 때문에 전광판이 죽으면 안 된다.
    """
    logs = var_dir / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("로그 디렉터리를 만들지 못했다: %s (파일 로그 없이 계속)", logs)
        return None

    app = _handler(logs / APP_LOG, APP_KEEP_DAYS)
    access = _handler(logs / ACCESS_LOG, ACCESS_KEEP_DAYS)

    root = logging.getLogger()
    if not _already(root):
        root.addHandler(app)

    # uvicorn 은 자기 로거에 핸들러를 달고 propagate 를 끈다. 루트로 안 올라오므로
    # 오류 로그는 앱 파일에, 접속 로그는 따로 직접 붙인다.
    uv = logging.getLogger("uvicorn")
    if not _already(uv):
        uv.addHandler(app)
    uv_access = logging.getLogger("uvicorn.access")
    if not _already(uv_access):
        uv_access.addHandler(access)
    # uvicorn 기본 설정은 이미 False 지만, 실행 방식에 따라 켜져 있으면 접속 로그가
    # 'uvicorn' 로거로 올라가 앱 로그 파일에 섞인다. 명시적으로 끊는다.
    uv_access.propagate = False

    return logs / APP_LOG
