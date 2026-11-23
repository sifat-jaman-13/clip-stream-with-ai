"""Regression tests for YouTube live capture fallback behavior."""
import asyncio

from clippyme.monitoring.live_monitor import LiveMonitor


def test_marathon_keeps_confirmed_url_when_recheck_temporarily_fails(tmp_path, monkeypatch):
    monitor = LiveMonitor(
        id="youtube:live-video",
        jobs={},
        job_queue=None,
        output_dir=str(tmp_path),
    )
    monitor.platform = "youtube"
    monitor.cfg = {"prelive_skip_seconds": 0, "segment_seconds": 1800}
    monitor._stop = asyncio.Event()

    async def no_backfill(_started_at):
        return None

    async def no_prelive_wait(_started_at=None):
        return True

    async def provider_temporarily_unknown():
        return None

    captured_urls = []

    async def record_capture_url(url):
        captured_urls.append(url)
        return None

    monkeypatch.setattr(monitor, "_schedule_backfill", no_backfill)
    monkeypatch.setattr(monitor, "_skip_prelive", no_prelive_wait)
    monkeypatch.setattr(monitor, "_live_state", provider_temporarily_unknown)
    monkeypatch.setattr(monitor, "_capture_segment", record_capture_url)

    confirmed_url = "https://video.example/live.m3u8"
    asyncio.run(monitor._marathon(started_at=None, initial_url=confirmed_url))

    assert captured_urls == [confirmed_url]
    assert monitor.state == "draining"
