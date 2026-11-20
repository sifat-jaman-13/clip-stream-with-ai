"""Adapters for self-hosted TryPost, Postiz and Mixpost publishing.

The adapters intentionally accept the same clip/title/caption/target shape as
the Zernio publisher. They do not create social accounts: those accounts must
already be connected in the selected self-hosted service.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class AlternativePublisherError(Exception):
    def __init__(self, message: str, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body[:500]


def _base_url(raw: str, default: str) -> str:
    value = (raw or default).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("publisher base_url must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("publisher base_url must not contain credentials or query parameters")
    return value


def _request(method: str, url: str, *, headers: dict[str, str], timeout: int = 600, **kwargs):
    try:
        response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        raise AlternativePublisherError(f"publisher network error: {exc}") from exc
    if not 200 <= response.status_code < 300:
        raise AlternativePublisherError(
            f"publisher {method} {url} → HTTP {response.status_code}",
            status_code=response.status_code,
            body=response.text,
        )
    try:
        return response.json()
    except ValueError:
        return {}


def _iso_date_time(value: str | None, timezone: str) -> tuple[str, str]:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.now().astimezone()
    return stamp.date().isoformat(), stamp.strftime("%H:%M")


def _postiz_settings(platform: str, title: str) -> dict[str, Any]:
    kind = {"instagram": "instagram", "youtube": "youtube", "tiktok": "tiktok"}.get(platform, platform)
    settings: dict[str, Any] = {"__type": kind}
    if platform == "instagram":
        settings["post_type"] = "reel"
    elif platform == "youtube":
        settings.update({"title": title, "type": "short"})
    elif platform == "tiktok":
        settings["privacy_level"] = "PUBLIC_TO_EVERYONE"
    return settings


def publish_postiz(*, cfg: dict, clip_path: str, title: str, caption: str,
                  platform_targets: list[dict], schedule_mode: str,
                  scheduled_for: str | None, timezone: str) -> dict:
    base = _base_url(cfg.get("base_url"), "http://postiz:4007/public/v1")
    token = str(cfg.get("api_key") or "").strip()
    if not token:
        raise ValueError("Postiz API key is not configured")
    headers = {"Authorization": token}
    with open(clip_path, "rb") as media:
        upload = _request("POST", f"{base}/upload", headers=headers,
                          files={"file": (Path(clip_path).name, media, "video/mp4")})
    media_item = {"id": upload.get("id"), "path": upload.get("path")}
    if not media_item["id"] or not media_item["path"]:
        raise AlternativePublisherError("Postiz upload response did not contain id and path")
    posts = []
    for target in platform_targets:
        integration = str(target.get("accountId") or "").strip()
        platform = str(target.get("platform") or "").lower()
        if not integration:
            raise ValueError(f"Postiz integration ID missing for {platform}")
        posts.append({
            "integration": {"id": integration},
            "value": [{"content": caption, "image": [media_item]}],
            "settings": _postiz_settings(platform, title),
        })
    when = scheduled_for or datetime.now().astimezone().isoformat()
    payload = {"type": "now" if schedule_mode == "now" else "schedule",
               "date": when, "shortLink": False, "tags": [], "posts": posts}
    result = _request("POST", f"{base}/posts", headers={**headers, "Content-Type": "application/json"}, json=payload)
    return {"post_id": result.get("id") or result.get("groupId") or result.get("group_id"),
            "scheduled_for": scheduled_for, "status": "published" if schedule_mode == "now" else "scheduled",
            "provider": "postiz"}


def publish_mixpost(*, cfg: dict, clip_path: str, title: str, caption: str,
                   platform_targets: list[dict], schedule_mode: str,
                   scheduled_for: str | None, timezone: str) -> dict:
    base = _base_url(cfg.get("base_url"), "http://mixpost/mixpost/api")
    workspace = str(cfg.get("workspace_id") or "").strip()
    token = str(cfg.get("api_key") or "").strip()
    if not workspace or not token:
        raise ValueError("Mixpost workspace ID and access token are required")
    headers = {"Authorization": f"Bearer {token}"}
    root = f"{base}/{workspace}"
    with open(clip_path, "rb") as media:
        upload = _request("POST", f"{root}/media", headers=headers,
                          files={"file": (Path(clip_path).name, media, "video/mp4")})
    media_id = upload.get("id") or upload.get("uuid")
    if not media_id:
        raise AlternativePublisherError("Mixpost upload response did not contain a media ID")
    account_ids = []
    for target in platform_targets:
        try:
            account_ids.append(int(target.get("accountId")))
        except (TypeError, ValueError) as exc:
            raise ValueError("Mixpost account IDs must be numeric") from exc
    date, clock = _iso_date_time(scheduled_for, timezone)
    options = {"youtube": {"title": title, "status": "public"},
               "instagram": {"type": "reel"}}
    versions = [{"account_id": 0, "is_original": True,
                 "content": [{"body": caption, "media": [media_id]}],
                 "options": options}]
    payload = {"date": date, "time": clock, "timezone": timezone,
               "schedule": schedule_mode != "now", "schedule_now": schedule_mode == "now",
               "accounts": account_ids, "versions": versions}
    result = _request("POST", f"{root}/posts", headers={**headers, "Content-Type": "application/json"}, json=payload)
    return {"post_id": result.get("uuid") or result.get("id"),
            "scheduled_for": scheduled_for, "status": "published" if schedule_mode == "now" else "scheduled",
            "provider": "mixpost"}


def publish_trypost(*, cfg: dict, clip_path: str, title: str, caption: str,
                    platform_targets: list[dict], schedule_mode: str,
                    scheduled_for: str | None, timezone: str) -> dict:
    """Upload and schedule a video through TryPost's self-hosted REST API.

    TryPost binds API keys to a workspace. The account IDs entered in ClippyMe
    therefore need to be the UUIDs shown by TryPost's Channels page.
    """
    base = _base_url(cfg.get("base_url"), "http://trypost:8000").rstrip("/")
    token = str(cfg.get("api_key") or "").strip()
    if not token:
        raise ValueError("TryPost API key is not configured")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    with open(clip_path, "rb") as media:
        upload = _request("POST", f"{base}/api/uploads", headers=headers,
                          files={"media": (Path(clip_path).name, media, "video/mp4")})
    upload_data = upload.get("data") if isinstance(upload.get("data"), dict) else upload
    upload_token = upload_data.get("upload_token")
    if not upload_token:
        raise AlternativePublisherError("TryPost upload response did not contain upload_token")

    if not platform_targets:
        raise ValueError("TryPost requires at least one channel/account ID")
    content_types = {"instagram": "instagram_reel", "tiktok": "tiktok_video", "youtube": "youtube_short"}
    post_ids = []
    for target in platform_targets:
        account_id = str(target.get("accountId") or "").strip()
        platform = str(target.get("platform") or "").lower()
        if not account_id:
            raise ValueError(f"TryPost channel ID missing for {platform}")
        payload: dict[str, Any] = {
            "status": "publishing" if schedule_mode == "now" else "scheduled",
            "content": caption or title,
            "social_account_id": account_id,
            "content_type": content_types.get(platform, f"{platform}_post"),
            "media": [{"upload_token": upload_token}],
        }
        if schedule_mode != "now":
            if not scheduled_for:
                raise ValueError("TryPost scheduled posts require scheduled_for")
            payload["scheduled_at"] = scheduled_for
        result = _request("POST", f"{base}/api/posts", headers={**headers, "Content-Type": "application/json"}, json=payload)
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        if data.get("id"):
            post_ids.append(data["id"])
    return {"post_id": post_ids[0] if len(post_ids) == 1 else post_ids,
            "scheduled_for": scheduled_for,
            "status": "published" if schedule_mode == "now" else "scheduled",
            "provider": "trypost"}


def publish_alternative(*, provider: str, cfg: dict, **kwargs) -> dict:
    if provider == "postiz":
        return publish_postiz(cfg=cfg.get("postiz") or {}, **kwargs)
    if provider == "mixpost":
        return publish_mixpost(cfg=cfg.get("mixpost") or {}, **kwargs)
    if provider == "trypost":
        return publish_trypost(cfg=cfg.get("trypost") or {}, **kwargs)
    raise ValueError(f"unsupported publisher: {provider}")
