"""YouTube uploads-feed poller (long-form only) + channel-id resolution."""
from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("clippyme")

FEED_URL = "https://www.youtube.com/feeds/videos.xml?playlist_id={playlist}"
USER_AGENT = "Mozilla/5.0 (compatible; ClippyMe-LiveMonitor/1.0)"
MAX_FEED_BYTES = 2 * 1024 * 1024
CHANNEL_RESOLVE_TIMEOUT = 15
LIVE_PAGE_TIMEOUT = 20

_UC_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_PLAYLIST_RE = re.compile(r"^UULF[A-Za-z0-9_-]{22}$")
_VIDEO_ID_XML_RE = re.compile(r"<yt:videoId>([^<]+)</yt:videoId>")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_FEED_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def uploads_playlist_id(channel_id: str) -> str:
    """UC… channel id -> its long-form uploads playlist id (UULF…)."""
    cid = (channel_id or "").strip()
    if not _UC_RE.fullmatch(cid):
        raise ValueError(f"expected a canonical UC channel id, got {channel_id!r}")
    return "UULF" + cid[2:]


def feed_url(playlist_id: str) -> str:
    playlist = (playlist_id or "").strip()
    if not _PLAYLIST_RE.fullmatch(playlist):
        raise ValueError(f"invalid uploads playlist id: {playlist_id!r}")
    return FEED_URL.format(playlist=playlist)


def parse_feed(xml) -> list:
    """Return ``[{id, url}]`` in feed order from a small Atom document."""
    text = xml.decode("utf-8", "replace") if isinstance(xml, (bytes, bytearray)) else (xml or "")
    out = []
    for match in _VIDEO_ID_XML_RE.finditer(text):
        video_id = match.group(1).strip()
        if _VIDEO_ID_RE.fullmatch(video_id):
            out.append({"id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}"})
    return out


def _validate_feed_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "www.youtube.com":
        raise ValueError("feed URL must use https://www.youtube.com")
    if parsed.path != "/feeds/videos.xml":
        raise ValueError("unexpected YouTube feed path")
    playlist_values = parse_qs(parsed.query, strict_parsing=True).get("playlist_id", [])
    if len(playlist_values) != 1 or not _PLAYLIST_RE.fullmatch(playlist_values[0]):
        raise ValueError("invalid playlist_id in feed URL")
    return parsed.geturl()


def fetch_feed(url: str, timeout: float = 15.0) -> bytes:
    """Fetch a validated YouTube feed without redirects or unbounded reads."""
    safe_url = _validate_feed_url(url)
    req = urllib.request.Request(safe_url, headers={"User-Agent": USER_AGENT})
    try:
        with _FEED_OPENER.open(req, timeout=timeout) as response:  # nosec B310: exact HTTPS host/path validated above
            data = response.read(MAX_FEED_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"YouTube feed HTTP {exc.code}") from exc
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("YouTube feed exceeded size cap")
    return data


def resolve_channel_id(channel_input: str) -> str:
    """Resolve an @handle / channel URL / UC id to a canonical UC id.

    The yt-dlp metadata lookup is explicitly bounded: without socket/retry
    limits a broken resolver or half-open connection could pin a monitor worker
    forever and prevent clean shutdown.
    """
    channel = (channel_input or "").strip()
    if _UC_RE.fullmatch(channel):
        return channel
    lowered = channel.lower()
    if lowered.startswith("http://"):
        raise ValueError("YouTube channel URLs must use HTTPS")
    if lowered.startswith(("youtube.com/", "www.youtube.com/")):
        url = f"https://{channel}"
    elif lowered.startswith("https://"):
        url = channel
    else:
        url = f"https://www.youtube.com/{channel.lstrip('/')}"
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid YouTube channel URL port") from exc
    path = parsed.path.rstrip("/")
    allowed_path = (
        path.startswith("/@")
        or path.startswith("/channel/UC")
        or path.startswith("/c/")
        or path.startswith("/user/")
    )
    if (
        parsed.scheme != "https"
        or host not in {"youtube.com", "www.youtube.com"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not allowed_path
    ):
        raise ValueError("YouTube channel must use an official channel/handle URL")
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "noplaylist": True,
        "socket_timeout": CHANNEL_RESOLVE_TIMEOUT,
        "retries": 2,
        "extractor_retries": 2,
        "cachedir": False,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False, process=False) or {}
    channel_id = info.get("channel_id") or info.get("uploader_id") or info.get("id")
    if not channel_id or not _UC_RE.fullmatch(str(channel_id)):
        raise ValueError(f"could not resolve YouTube channel id from {channel_input!r}")
    return str(channel_id)


def live_page_url(channel_id: str) -> str:
    """Return the canonical live tab URL for a resolved channel id."""
    cid = (channel_id or "").strip()
    if not _UC_RE.fullmatch(cid):
        raise ValueError(f"expected a canonical UC channel id, got {channel_id!r}")
    return f"https://www.youtube.com/channel/{cid}/live"


def youtube_video_id(source: str) -> str | None:
    """Extract a canonical video ID from a YouTube URL or bare ID.

    Channel handles/URLs return None and continue through resolve_channel_id.
    Only official HTTPS YouTube hosts are accepted for video URLs.
    """
    raw = str(source or "").strip()
    if not raw:
        return None
    if _VIDEO_ID_RE.fullmatch(raw):
        return raw

    lowered = raw.lower()
    if lowered.startswith("http://"):
        raise ValueError("YouTube video URLs must use HTTPS")
    if lowered.startswith((
        "youtube.com/", "www.youtube.com/", "m.youtube.com/",
        "youtu.be/", "www.youtu.be/",
    )):
        url = f"https://{raw}"
    elif lowered.startswith("https://"):
        url = raw
    else:
        return None

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com",
                    "youtu.be", "www.youtu.be"}:
        return None
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid YouTube video URL port") from exc
    if (parsed.scheme != "https" or parsed.username is not None
            or parsed.password is not None or port not in (None, 443)):
        raise ValueError("YouTube video URLs must use an official HTTPS host")

    path = parsed.path.rstrip("/")
    video_id = None
    if host in {"youtu.be", "www.youtu.be"}:
        parts = path.strip("/").split("/")
        if len(parts) != 1:
            raise ValueError("invalid YouTube short video URL")
        video_id = parts[0]
    elif path == "/watch":
        values = parse_qs(parsed.query, keep_blank_values=True).get("v", [])
        if len(values) != 1:
            raise ValueError("YouTube watch URL must contain exactly one video ID")
        video_id = values[0]
    else:
        match = re.fullmatch(r"/(?:live|embed|shorts|v)/([^/]+)", path)
        if match:
            video_id = match.group(1)

    if video_id is None:
        return None
    if not _VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError("invalid YouTube video ID")
    return video_id


def _live_start(info: dict):
    """Read the best available UTC start time from yt-dlp metadata."""
    from datetime import datetime, timezone

    for key in ("live_start_time", "release_timestamp", "timestamp"):
        value = info.get(key)
        if value is None:
            continue
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
    return None


def _live_media_url(info: dict):
    """Pick the highest available direct HLS/media URL from metadata."""
    if info.get("url"):
        return info["url"]
    formats = [item for item in (info.get("formats") or []) if item.get("url")]
    hls = [item for item in formats
           if "m3u8" in str(item.get("protocol") or "").lower()]
    candidates = hls or formats
    if not candidates:
        return None
    best = max(candidates, key=lambda item: (
        float(item.get("height") or 0),
        float(item.get("tbr") or 0),
    ))
    return best.get("url")


def resolve_live_stream(channel_id: str):
    """Return ``(is_live, stream_url, started_at)`` for a YouTube channel.

    The stream URL is deliberately resolved on every state check: YouTube HLS
    URLs expire while a broadcast is running.  The live tab is used instead of
    the uploads feed, so scheduled/upcoming broadcasts are not treated as live.
    """
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": LIVE_PAGE_TIMEOUT,
        "retries": 1,
        "extractor_retries": 1,
        "cachedir": False,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(live_page_url(channel_id), download=False) or {}

    # A channel's /live tab can resolve as a playlist when there is no active
    # broadcast.  Inspect flat entries as a fallback, then resolve the active
    # video once more to obtain a fresh media URL.
    if info.get("_type") == "playlist":
        entries = [entry for entry in (info.get("entries") or []) if entry]
        live_entry = next((entry for entry in entries
                           if entry.get("is_live") or entry.get("live_status") == "is_live"), None)
        if live_entry and live_entry.get("id"):
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={live_entry['id']}",
                    download=False,
                ) or {}

    if not info.get("is_live") and info.get("live_status") != "is_live":
        return False, None, None
    media_url = _live_media_url(info)
    if not media_url:
        raise RuntimeError("YouTube live broadcast has no playable media URL")
    return True, media_url, _live_start(info)


def resolve_live_video(video_id: str):
    """Return live state and a fresh HLS URL for one exact video ID.

    This is used when a creator supplies a watch/live URL instead of a channel
    URL. Scheduled and ended videos stay offline; an active broadcast returns
    a playable media URL.
    """
    vid = str(video_id or "").strip()
    if not _VIDEO_ID_RE.fullmatch(vid):
        raise ValueError("invalid YouTube video ID")

    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": LIVE_PAGE_TIMEOUT,
        "retries": 1,
        "extractor_retries": 1,
        "cachedir": False,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={vid}", download=False,
        ) or {}

    if not info.get("is_live") and info.get("live_status") != "is_live":
        return False, None, None
    media_url = _live_media_url(info)
    if not media_url:
        raise RuntimeError("YouTube live broadcast has no playable media URL")
    return True, media_url, _live_start(info)
