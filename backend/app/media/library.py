"""Media library: screenshots and screen recordings of your own product, used inside videos (phone mockup).

File based like the studio (no tables): media/<id>/ holds meta.json, the original file and a poster image.
The description is what Claude reads to decide which scene shows which media, so it should say what is on screen.
"""
import io
import json
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from PIL import Image

from app.storage.local import LocalStorage

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".ogg"}
MAX_BYTES = 300 * 1024 * 1024


class MediaError(ValueError):
    pass


class MediaLibrary:
    def __init__(self, storage: LocalStorage | None = None):
        self.storage = storage or LocalStorage()

    def _dir(self, mid: str) -> Path:
        if not mid.isalnum():
            raise LookupError("media not found")
        return self.storage._path(f"media/{mid}")

    def _save(self, meta: dict) -> None:
        d = self._dir(meta["id"])
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- read ----------
    def get(self, mid: str) -> dict:
        try:
            return json.loads((self._dir(mid) / "meta.json").read_text(encoding="utf-8"))
        except OSError as exc:
            raise LookupError("media not found") from exc

    def list(self) -> list[dict]:
        root = self.storage._path("media")
        out = []
        for d in root.iterdir() if root.is_dir() else []:
            try:
                out.append(self.get(d.name))
            except (LookupError, ValueError):
                continue
        return sorted(out, key=lambda m: m["created_at"], reverse=True)

    def file(self, mid: str) -> Path:
        meta = self.get(mid)
        return self._dir(mid) / meta["file"]

    def poster(self, mid: str) -> Path:
        self.get(mid)
        return self._dir(mid) / "poster.jpg"

    def resolve(self, mid: str | None) -> str | None:
        """Path of a media id (for the renderer), None when empty or unknown."""
        if not mid:
            return None
        try:
            path = self.file(mid)
        except LookupError:
            return None
        return str(path) if path.is_file() else None

    # ---------- write ----------
    def add(self, filename: str, data: bytes, description: str = "") -> dict:
        ext = Path(filename or "").suffix.lower()
        if ext not in IMAGE_EXT | VIDEO_EXT | AUDIO_EXT:
            raise MediaError("نوع الملف غير مدعوم: ارفع صورة (PNG/JPG/WEBP) أو فيديو (MP4/MOV/WEBM) أو موسيقى (MP3/M4A/WAV)")
        if not data:
            raise MediaError("الملف فارغ")
        if len(data) > MAX_BYTES:
            raise MediaError("الملف أكبر من 300 ميجابايت")
        mid = uuid.uuid4().hex
        d = self._dir(mid)
        d.mkdir(parents=True, exist_ok=True)
        kind = "image" if ext in IMAGE_EXT else ("audio" if ext in AUDIO_EXT else "video")
        src = d / f"source{ext}"
        src.write_bytes(data)
        try:
            if kind == "image":
                with Image.open(io.BytesIO(data)) as im:
                    im = im.convert("RGB")
                    width, height = im.size
                    im.thumbnail((540, 1200))
                    im.save(d / "poster.jpg", "JPEG", quality=85)
                duration_ms = None
            elif kind == "audio":
                width, height, duration_ms = 0, 0, _probe_audio(src)
            else:
                width, height, duration_ms = _probe_video(src, d / "poster.jpg")
        except Exception as exc:  # noqa: BLE001 - unreadable upload
            for p in d.iterdir():
                p.unlink()
            d.rmdir()
            raise MediaError(f"تعذّر قراءة الملف: {exc}") from exc
        meta = {"id": mid, "kind": kind, "file": src.name, "filename": Path(filename).name[:200],
                "description": description.strip()[:300], "width": width, "height": height,
                "duration_ms": duration_ms, "created_at": time.time()}
        self._save(meta)
        return meta

    def update(self, mid: str, description: str | None = None, focus=None, clear_focus: bool = False) -> dict:
        """focus = (x, y) in 0..1 of the image/video: where the video points, taps and zooms."""
        meta = self.get(mid)
        if description is not None:
            meta["description"] = description.strip()[:300]
        if clear_focus:
            meta["focus"] = None
        elif focus is not None:
            x, y = (float(v) for v in focus)
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise MediaError("نقطة التركيز يجب أن تكون داخل الصورة")
            meta["focus"] = [round(x, 4), round(y, 4)]
        self._save(meta)
        return meta

    def focus_of(self, mid: str | None):
        if not mid:
            return None
        try:
            f = self.get(mid).get("focus")
        except LookupError:
            return None
        return tuple(f) if f else None

    def delete(self, mid: str) -> None:
        d = self._dir(mid)
        self.get(mid)
        for p in d.iterdir():
            p.unlink()
        d.rmdir()


def _probe_video(src: Path, poster: Path) -> tuple[int, int, int | None]:
    """Size as displayed (phone recordings carry a rotation flag; FFmpeg applies it when decoding) + duration."""
    from app.video.renderer import ffmpeg_exe

    proc = subprocess.run([ffmpeg_exe(), "-v", "info", "-y", "-i", str(src), "-frames:v", "1", "-vf",
                           "scale='min(540,iw)':-2", str(poster)], capture_output=True)
    if proc.returncode != 0 or not poster.is_file():
        raise MediaError(proc.stderr.decode("utf-8", "replace")[-200:])
    info = proc.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
    duration = int((int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])) * 1000) if m else None
    with tempfile.TemporaryDirectory() as tmp:  # full-size first frame -> true displayed size
        full = Path(tmp) / "f.png"
        subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(src), "-frames:v", "1", str(full)],
                       capture_output=True)
        with Image.open(full) as im:
            width, height = im.size
    return width, height, duration


def _probe_audio(src: Path) -> int:
    from app.video.renderer import ffmpeg_exe

    proc = subprocess.run([ffmpeg_exe(), "-v", "info", "-i", str(src), "-f", "null", "-t", "0.1", "-"], capture_output=True)
    info = proc.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if not m or "Audio:" not in info:
        raise MediaError("لا يحتوي الملف على صوت مقروء")
    return int((int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])) * 1000)


def describe_for_ai(items: list[dict], limit: int = 30) -> str:
    """Compact list Claude sees: id, kind, shape and the user's description (screens only, not music)."""
    lines = []
    items = [m for m in items if m["kind"] != "audio"]
    for m in items[:limit]:
        shape = "tall" if m["height"] > m["width"] * 1.2 else ("wide" if m["width"] > m["height"] * 1.2 else "square")
        dur = f", {m['duration_ms'] / 1000:.0f}s" if m.get("duration_ms") else ""
        lines.append(f"- {m['id']} | {m['kind']} ({shape}{dur}) | {m['description'] or m['filename']}")
    return "\n".join(lines)


_lib: MediaLibrary | None = None


def get_library() -> MediaLibrary:
    global _lib
    if _lib is None:
        _lib = MediaLibrary()
    return _lib
