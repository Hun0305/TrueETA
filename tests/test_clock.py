"""시계 동기화 대기 — 부팅 직후 틀린 시각으로 판단하지 않게.

라즈베리파이는 RTC 가 없어 부팅 직후 시계가 틀리다. 2026-09-24 에
실제로 물렸다 (docs/incident-2026-09-24.md).
"""

import asyncio

from trueeta.clock import is_synced, wait_for_sync


def test_marker_present_means_synced(tmp_path):
    marker = tmp_path / "synchronized"
    assert not is_synced(marker)
    marker.touch()
    assert is_synced(marker)


def test_missing_directory_is_not_synced(tmp_path):
    """컨테이너처럼 마커가 없는 환경에서도 죽지 않아야 한다."""
    assert not is_synced(tmp_path / "없는디렉터리" / "synchronized")


def test_returns_immediately_when_already_synced(tmp_path):
    marker = tmp_path / "synchronized"
    marker.touch()
    assert asyncio.run(wait_for_sync(timeout=5, interval=0.01, marker=marker)) is True


def test_waits_until_the_marker_appears(tmp_path):
    marker = tmp_path / "synchronized"

    async def scenario():
        async def sync_soon():
            await asyncio.sleep(0.05)
            marker.touch()

        task = asyncio.create_task(sync_soon())
        result = await wait_for_sync(timeout=2, interval=0.01, marker=marker)
        await task
        return result

    assert asyncio.run(scenario()) is True


def test_gives_up_instead_of_blocking_forever(tmp_path):
    """네트워크가 영영 안 붙어도 화면이 영원히 비면 안 된다.

    False 를 돌려주되 호출한 쪽이 경고만 남기고 그대로 진행한다.
    """
    result = asyncio.run(
        wait_for_sync(timeout=0.05, interval=0.01, marker=tmp_path / "synchronized")
    )
    assert result is False
