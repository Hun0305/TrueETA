"""로그 파일 — journald 가 메모리에만 있어 재부팅하면 사라지므로 파일로도 남긴다.

라즈베리파이 OS 기본값: Storage=volatile (/usr/lib/systemd/journald.conf.d/
40-rpi-volatile-storage.conf). 모든 경로는 tmp_path.
"""

import logging

import pytest

from trueeta import logfile

LOGGERS = ("", "uvicorn", "uvicorn.access")


@pytest.fixture(autouse=True)
def clean_handlers():
    """로깅은 전역이다. 테스트가 붙인 파일 핸들러를 매번 떼어낸다."""
    yield
    for name in LOGGERS:
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if getattr(h, logfile._MARK, False):
                h.close()
                lg.removeHandler(h)


def _flush():
    for name in LOGGERS:
        for h in logging.getLogger(name).handlers:
            h.flush()


def test_app_logs_go_to_a_dated_file(tmp_path):
    path = logfile.attach(tmp_path)
    lg = logging.getLogger("trueeta.test")
    lg.setLevel(logging.INFO)
    lg.info("사이클: 정류장 2곳")
    _flush()
    text = path.read_text(encoding="utf-8")
    assert "사이클: 정류장 2곳" in text
    # 파일은 오래 남으므로 날짜까지 찍는다 (journald 출력은 시각만)
    assert text[:4].isdigit() and text[4] == "-"


def test_service_key_is_masked_in_the_file(tmp_path):
    """9/29 journald 에서 키가 290줄 발견됐다. 파일에서는 이중으로 막는다."""
    path = logfile.attach(tmp_path)
    lg = logging.getLogger("trueeta.test")
    lg.setLevel(logging.INFO)
    lg.info("GET http://x/y?serviceKey=%s&stationId=1", "abcdef0123456789")
    _flush()
    text = path.read_text(encoding="utf-8")
    assert "abcdef0123456789" not in text
    assert "serviceKey=***" in text and "stationId=1" in text


def test_access_log_is_separate(tmp_path):
    """화면이 15초마다 부르는 접속 로그가 폴러 로그를 묻지 않게."""
    path = logfile.attach(tmp_path)
    logging.getLogger("uvicorn.access").warning('127.0.0.1 - "GET /api/board" 200')
    _flush()
    access = (tmp_path / "logs" / logfile.ACCESS_LOG).read_text(encoding="utf-8")
    assert "/api/board" in access
    assert not path.exists() or "/api/board" not in path.read_text(encoding="utf-8")


def test_attaching_twice_does_not_duplicate_lines(tmp_path):
    """lifespan 이 다시 돌아도 같은 줄이 두 번 찍히면 안 된다."""
    logfile.attach(tmp_path)
    path = logfile.attach(tmp_path)
    lg = logging.getLogger("trueeta.test")
    lg.setLevel(logging.INFO)
    lg.info("한 번만")
    _flush()
    assert path.read_text(encoding="utf-8").count("한 번만") == 1


def test_rotates_daily_and_keeps_limited_history(tmp_path):
    """SD 카드를 채우면 안 된다."""
    logfile.attach(tmp_path)
    handlers = {h.baseFilename.rsplit("/", 1)[-1]: h
                for name in LOGGERS for h in logging.getLogger(name).handlers
                if getattr(h, logfile._MARK, False)}
    assert handlers[logfile.APP_LOG].when == "MIDNIGHT"
    assert handlers[logfile.APP_LOG].backupCount == logfile.APP_KEEP_DAYS
    assert handlers[logfile.ACCESS_LOG].backupCount == logfile.ACCESS_KEEP_DAYS


def test_unwritable_location_does_not_crash(tmp_path):
    """로그 파일 때문에 전광판이 죽으면 안 된다."""
    blocker = tmp_path / "file"
    blocker.write_text("디렉터리가 아니다")
    assert logfile.attach(blocker) is None


def test_redact_helper():
    assert logfile.redact("a?serviceKey=SECRET&b=1") == "a?serviceKey=***&b=1"
    assert logfile.redact("아무것도 없음") == "아무것도 없음"
