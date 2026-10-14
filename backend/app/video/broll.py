"""Background footage from Pexels (free for commercial use, no attribution required).

One search term per scene (Claude writes them in English, which Pexels matches best). The chosen file is
downloaded once and cached; any failure (no key, no match, network) returns None and the scene falls back to
the brand gradient, so footage can never break a video.
"""
import hashlib
import logging
from pathlib import Path

import httpx

from app.config import settings
from app.storage.local import LocalStorage

API = "https://api.pexels.com/videos/search"
log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(settings.pexels_api_key)


def _pick_file(video: dict, want_w: int, want_h: int) -> dict | None:
    """Smallest mp4 that still covers the target size (else the largest available)."""
    portrait = want_h >= want_w
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("link")
             and f.get("width") and f.get("height") and (f["height"] >= f["width"]) == portrait]
    if not files:
        return None
    covering = [f for f in files if f["width"] >= want_w * 0.66 and f["height"] >= want_h * 0.66]
    return min(covering, key=lambda f: f["width"] * f["height"]) if covering else max(
        files, key=lambda f: f["width"] * f["height"])


def search(query: str, want_w: int, want_h: int, min_seconds: float, seed: str,
           http: httpx.Client | None = None) -> dict | None:
    """Pick one video file for the query: stable for the same seed, long enough for the scene."""
    client = http or httpx.Client(timeout=30)
    r = client.get(API, headers={"Authorization": settings.pexels_api_key}, params={
        "query": query, "orientation": "portrait" if want_h >= want_w else "square", "size": "medium", "per_page": 15})
    if r.status_code in (401, 403):
        raise RuntimeError("PEXELS_API_KEY غير صحيح (401/403)")
    r.raise_for_status()
    candidates = []
    for v in r.json().get("videos", []):
        f = _pick_file(v, want_w, want_h)
        if f and (v.get("duration") or 0) >= min(min_seconds, 6):
            candidates.append({"video_id": v["id"], "file_id": f.get("id"), "link": f["link"],
                               "page": v.get("url", ""), "author": (v.get("user") or {}).get("name", "")})
    if not candidates:
        return None
    pool = candidates[:5]
    return pool[int(hashlib.sha256(f"{seed}|{query}".encode()).hexdigest(), 16) % len(pool)]


def fetch(query: str, want_w: int, want_h: int, min_seconds: float, seed: str,
          http: httpx.Client | None = None, storage: LocalStorage | None = None) -> Path | None:
    """Local path of a cached clip for this scene, or None (scene keeps the gradient background)."""
    if not enabled() or not query.strip():
        return None
    storage = storage or LocalStorage()
    try:
        hit = search(query.strip(), want_w, want_h, min_seconds, seed, http)
        if hit is None:
            log.info("pexels: no footage for %r", query)
            return None
        key = f"broll/pexels-{hit['video_id']}-{hit['file_id']}.mp4"
        path = storage._path(key)
        if not path.exists():
            client = http or httpx.Client(timeout=120, follow_redirects=True)
            r = client.get(hit["link"], follow_redirects=True)
            r.raise_for_status()
            storage.put(key, r.content)
        return path
    except Exception as exc:  # noqa: BLE001 - footage is optional
        log.warning("pexels footage skipped for %r: %s", query, exc)
        return None
