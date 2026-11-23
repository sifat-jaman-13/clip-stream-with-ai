"""Regression tests for direct YouTube live-video monitoring sources."""
import sys
from datetime import timezone
from types import SimpleNamespace

import pytest

from clippyme.core.errors import ValidationError
from clippyme.integrations import youtube_feed as yf
from clippyme.monitoring.live_monitor import YoutubeStrategy, validate_monitor_config


VIDEO_ID = "sDfXq1QOz9k"
CHANNEL_ID = "UCzPpbeK8ANcNKg5aoMB0miw"


@pytest.mark.parametrize(
    "source",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/live/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}",
        VIDEO_ID,
    ],
)
def test_youtube_video_id_accepts_direct_video_sources(source):
    assert yf.youtube_video_id(source) == VIDEO_ID


def test_youtube_video_id_leaves_channel_urls_for_channel_resolver():
    assert yf.youtube_video_id("https://www.youtube.com/@dylan.page.") is None
    assert yf.youtube_video_id("@dylan.page.") is None


@pytest.mark.parametrize(
    "source",
    [
        f"http://www.youtube.com/watch?v={VIDEO_ID}",
        "https://www.youtube.com/watch?v=invalid",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&v=abcdefghijk",
    ],
)
def test_youtube_video_id_rejects_unsafe_or_ambiguous_video_urls(source):
    with pytest.raises(ValueError):
        yf.youtube_video_id(source)


def test_resolve_live_video_returns_fresh_muxed_media_url(monkeypatch):
    captured = {}

    class FakeYDL:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return {
                "id": VIDEO_ID,
                "is_live": True,
                "live_status": "is_live",
                "live_start_time": 1_700_000_000,
                "formats": [{
                    "url": "https://video.example/live.m3u8",
                    "protocol": "m3u8_native",
                    "height": 720,
                    "vcodec": "avc1",
                    "acodec": "mp4a",
                }],
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYDL))
    live, media_url, started = yf.resolve_live_video(VIDEO_ID)

    assert live is True
    assert media_url == "https://video.example/live.m3u8"
    assert started.tzinfo == timezone.utc
    assert captured["url"] == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert captured["kwargs"] == {"download": False}


def test_direct_video_strategy_does_not_try_to_resolve_a_channel(monkeypatch):
    monkeypatch.setattr(yf, "youtube_video_id", lambda source: VIDEO_ID)
    monkeypatch.setattr(
        yf, "resolve_channel_id",
        lambda source: (_ for _ in ()).throw(AssertionError("channel lookup must not run")),
    )
    expected = (True, "https://video.example/live.m3u8", None)
    monkeypatch.setattr(yf, "resolve_live_video", lambda video_id: expected)

    strategy = YoutubeStrategy(f"https://www.youtube.com/watch?v={VIDEO_ID}")
    strategy.resolve()

    assert strategy.get_live_state() == expected


def test_channel_strategy_keeps_channel_live_tab_detection(monkeypatch):
    monkeypatch.setattr(yf, "youtube_video_id", lambda source: None)
    monkeypatch.setattr(yf, "resolve_channel_id", lambda source: CHANNEL_ID)
    expected = (True, "https://video.example/channel-live.m3u8", None)
    monkeypatch.setattr(yf, "resolve_live_stream", lambda channel_id: expected)

    strategy = YoutubeStrategy("https://www.youtube.com/@dylan.page.")
    strategy.resolve()

    assert strategy.get_live_state() == expected


def test_monitor_config_accepts_live_video_but_rejects_video_as_vod():
    config = {
        "platform": "youtube",
        "mode": "live",
        "channel": f"https://www.youtube.com/watch?v={VIDEO_ID}",
        "platforms": [{"platform": "instagram", "accountId": "test-account"}],
    }
    assert validate_monitor_config(config)["channel"] == config["channel"]

    with pytest.raises(ValidationError, match="VOD monitoring requires a channel"):
        validate_monitor_config({**config, "mode": "vod"})
