"""Video studio: describe a video in plain words -> Claude writes the script -> the local renderer makes it ->
export it in several formats and qualities.

Independent of projects/campaigns, so it needs no database tables: each video is a folder in storage
(`studio/<id>/`) holding meta.json, the master MP4, the subtitles and any exports made from the master.
Rendering runs on one background thread (it is CPU-bound; two at once would only slow both down).
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

from app.ai.provider import LLMProvider, LLMRequest
from app.config import settings
from app.storage.local import LocalStorage
from app.video import broll, voice
from app.video.renderer import SIZES, VideoRenderer, VideoRequest, ffmpeg_exe

log = logging.getLogger("studio")
PROMPT = Path(__file__).resolve().parents[1] / "ai" / "prompts" / "studio.v1.md"
LENGTHS = {"short": 4, "medium": 6, "long": 10}  # scene counts for ~15 / ~30 / ~60 seconds
MAX_SCENES = 12

# short side of the frame in pixels, H.264 / VP9 quality (lower = better), GIF width, audio bitrate
QUALITIES = {
    "1080p": {"label": "عالية 1080p", "short": 1080, "crf": 20, "vp9": 31, "gif": 540, "audio": "192k"},
    "720p": {"label": "متوسطة 720p", "short": 720, "crf": 23, "vp9": 34, "gif": 400, "audio": "128k"},
    "480p": {"label": "صغيرة 480p", "short": 480, "crf": 27, "vp9": 38, "gif": 300, "audio": "96k"},
}
FORMATS = {
    "mp4": "MP4 (H.264) — لكل المنصات",
    "webm": "WebM (VP9) — للمواقع",
    "mov": "MOV — لبرامج المونتاج",
    "gif": "GIF متحرك (بدون صوت)",
    "mp3": "MP3 — الصوت فقط",
    "srt": "SRT — ملف الترجمة",
}
MIME = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "gif": "image/gif",
        "mp3": "audio/mpeg", "srt": "application/x-subrip"}


class StudioError(RuntimeError):
    pass


class StudioScript(BaseModel):
    title: str = Field(description="Short name for the video, max 50 characters")
    brand_name: str = Field(default="", description="Brand or product name if the user gave one, else empty")
    scenes: list[str] = Field(description="Narration lines, one per scene, max 90 characters each")
    broll: list[str] = Field(description="English stock-footage search term per scene, same order and count")
    cta: str = Field(description="Short call to action, max 30 characters")


def write_script(llm: LLMProvider, prompt: str, aspect: str, length: str) -> dict:
    count = LENGTHS.get(length, LENGTHS["medium"])
    fmt = {"9:16": "vertical (Reels / TikTok / Shorts)", "16:9": "landscape (YouTube)"}.get(aspect, "square feed")
    req = LLMRequest(instructions=PROMPT.read_text(encoding="utf-8"), stable_context="",
                     user_message=f"<request>\nscene_count: {count}\nformat: {fmt}\ndescription: {prompt}\n</request>",
                     output_model=StudioScript)
    try:
        data = StudioScript.model_validate(llm.generate_structured(req).data)
    except Exception as exc:  # noqa: BLE001 - no key / network / bad output: the user can still write scenes by hand
        raise StudioError(f"تعذّر كتابة السيناريو بالذكاء الاصطناعي ({exc}). اكتب المشاهد بنفسك أو تحقق من مفتاح Anthropic") from exc
    scenes = [s.strip() for s in data.scenes if s.strip()][:MAX_SCENES]
    terms = [b.strip() for b in data.broll][: len(scenes)]
    return {"title": data.title.strip()[:80], "brand_name": data.brand_name.strip(), "scenes": scenes,
            "broll": terms + [""] * (len(scenes) - len(terms)), "cta": data.cta.strip()}


class VideoSpec(BaseModel):
    title: str = Field(default="فيديو", max_length=120)
    scenes: list[str] = Field(min_length=1, max_length=MAX_SCENES)
    broll: list[str] = Field(default_factory=list, max_length=MAX_SCENES)
    cta: str = Field(default="", max_length=80)
    brand_name: str = Field(default="", max_length=80)
    aspect: str = "9:16"
    voice: str = ""  # empty = no narration
    captions: bool = True
    footage: bool = True
    primary_color: str = "#0F766E"
    secondary_color: str = "#F59E0B"


class Studio:
    def __init__(self, storage: LocalStorage | None = None):
        self.storage = storage or LocalStorage()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="studio")
        self._active: set[str] = set()
        self._lock = threading.Lock()

    # ---------- storage ----------
    def _dir(self, vid: str) -> Path:
        if not vid.isalnum():
            raise LookupError("video not found")
        return self.storage._path(f"studio/{vid}")

    def _save(self, meta: dict) -> None:
        """Atomic write: the UI polls meta.json while the render thread updates progress."""
        path = self._dir(meta["id"]) / "meta.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(5):  # Windows refuses the swap while a reader has the file open
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)

    def get(self, vid: str) -> dict:
        try:
            meta = json.loads((self._dir(vid) / "meta.json").read_text(encoding="utf-8"))
        except OSError as exc:
            raise LookupError("video not found") from exc
        if meta["status"] in ("queued", "rendering") and vid not in self._active:  # server restarted mid-render
            meta.update(status="failed", error="توقف الإنشاء لأن الخادم أُعيد تشغيله. أعد المحاولة")
            self._save(meta)
        return meta

    def list(self) -> list[dict]:
        root = self.storage._path("studio")
        out = []
        for d in root.iterdir() if root.is_dir() else []:
            try:
                out.append(self.get(d.name))
            except (LookupError, ValueError):
                continue
        return sorted(out, key=lambda m: m["created_at"], reverse=True)

    def delete(self, vid: str) -> None:
        if vid in self._active:
            raise StudioError("لا يمكن حذف فيديو أثناء إنشائه")
        self.get(vid)
        shutil.rmtree(self._dir(vid), ignore_errors=True)

    def master(self, vid: str) -> Path:
        meta = self.get(vid)
        path = self._dir(vid) / "master.mp4"
        if meta["status"] != "ready" or not path.is_file():
            raise StudioError("الفيديو غير جاهز بعد")
        return path

    # ---------- rendering ----------
    def create(self, spec: VideoSpec, run_async: bool = True) -> dict:
        if spec.aspect not in SIZES:
            raise StudioError(f"مقاس غير مدعوم: {spec.aspect}")
        if spec.voice and spec.voice not in _voices()["voices"]:
            raise StudioError("هذا الصوت غير متاح الآن (تحقق من إعدادات الصوت أو شغّل start.bat) أو اختر «بدون صوت»")
        scenes = [s.strip() for s in spec.scenes if s.strip()]
        if not scenes:
            raise StudioError("اكتب مشهدًا واحدًا على الأقل")
        vid = uuid.uuid4().hex
        meta = {"id": vid, "title": spec.title.strip() or "فيديو", "status": "queued", "progress": 0.0, "error": None,
                "created_at": time.time(), "spec": spec.model_dump() | {"scenes": scenes}, "width": None, "height": None,
                "duration_ms": None}
        self._save(meta)
        with self._lock:
            self._active.add(vid)
        if run_async:
            self._pool.submit(self._render, vid)
        else:
            self._render(vid)
        return self.get(vid)

    def _render(self, vid: str) -> None:
        meta = self.get(vid)
        spec = VideoSpec.model_validate(meta["spec"])
        try:
            meta.update(status="rendering")
            self._save(meta)
            last = [0.0]

            def progress(p: float) -> None:
                if p - last[0] >= 0.03:
                    last[0] = p
                    meta["progress"] = round(min(p, 0.99), 3)
                    self._save(meta)

            cta = spec.cta.strip()
            scenes = spec.scenes + ([cta] if cta and spec.scenes[-1] != cta else [])
            terms = [b.strip() for b in spec.broll if b.strip()] if spec.footage and broll.enabled() else []
            res = VideoRenderer().render(VideoRequest(
                scenes=scenes, cta=cta, brand_name=spec.brand_name, primary_color=spec.primary_color,
                secondary_color=spec.secondary_color, aspect=spec.aspect, music_seed=vid, voice=spec.voice,
                broll=terms, captions=spec.captions), on_progress=progress)
            self.storage.put(f"studio/{vid}/master.mp4", res.mp4)
            self.storage.put(f"studio/{vid}/captions.srt", res.srt().encode("utf-8"))
            meta.update(status="ready", progress=1.0, width=res.width, height=res.height, duration_ms=res.duration_ms)
        except Exception as exc:  # noqa: BLE001 - shown to the user on the video card
            log.exception("studio render %s failed", vid)
            meta.update(status="failed", error=str(exc)[:500])
        finally:
            with self._lock:
                self._active.discard(vid)
            self._save(meta)

    # ---------- export ----------
    def export(self, vid: str, fmt: str, quality: str) -> Path:
        """The master converted to `fmt` at `quality`; made once, then served from disk."""
        if fmt not in FORMATS or quality not in QUALITIES:
            raise StudioError("صيغة أو جودة غير مدعومة")
        master = self.master(vid)
        if fmt == "srt":
            return self._dir(vid) / "captions.srt"
        q = QUALITIES[quality]
        out = self._dir(vid) / "exports" / f"{quality}.{fmt}"
        if out.is_file():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        meta = self.get(vid)
        # the short side of the frame becomes the quality's size (never upscaled); -2 keeps the other side even
        w, h, short = meta["width"], meta["height"], q["short"]
        scale = "null" if short >= min(w, h) else (f"scale={short}:-2" if w <= h else f"scale=-2:{short}")
        if fmt == "mp4" and scale == "null":
            return master  # already H.264/AAC at full size: re-encoding would only lose quality
        args = {
            "mp4": ["-vf", scale, "-c:v", "libx264", "-preset", "medium", "-crf", str(q["crf"]), "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", q["audio"], "-movflags", "+faststart"],
            "mov": ["-vf", scale, "-c:v", "libx264", "-preset", "medium", "-crf", str(q["crf"] - 2), "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-b:a", q["audio"]],
            "webm": ["-vf", scale, "-c:v", "libvpx-vp9", "-crf", str(q["vp9"]), "-b:v", "0", "-deadline", "realtime",
                     "-cpu-used", "8", "-row-mt", "1", "-c:a", "libopus", "-b:a", "128k"],
            "gif": ["-an", "-filter_complex", f"fps=12,scale={q['gif']}:-1:flags=lanczos,split[a][b];"
                    "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"],
            "mp3": ["-vn", "-c:a", "libmp3lame", "-b:a", q["audio"]],
        }[fmt]
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp) / out.name
            proc = subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(master), *args, str(part)],
                                  capture_output=True)
            if proc.returncode != 0 or not part.is_file():
                raise StudioError("فشل التصدير: " + proc.stderr.decode("utf-8", "replace")[-300:])
            shutil.move(str(part), out)
        return out


def _voices() -> dict:
    """Voice choices as 'provider:voice'. ElevenLabs (your chosen voice) first when configured, then the
    Microsoft Arabic voices through Azure (if keyed) or the free Edge service."""
    choices = {}
    if settings.elevenlabs_api_key and settings.elevenlabs_voice_id:
        choices[f"elevenlabs:{settings.elevenlabs_voice_id}"] = "ElevenLabs — صوتك (أعلى جودة)"
    ms = "azure" if settings.azure_speech_key and settings.azure_speech_region else ("edge" if voice._edge_available() else None)
    if ms and (settings.voice_provider or "auto").lower() != "off":
        tag = "Azure" if ms == "azure" else "مجاني"
        choices |= {f"{ms}:{k}": f"{v} — {tag}" for k, v in voice.VOICES.items()}
    current = voice.current_voice()
    return {"voices": choices, "default_voice": current if current in choices else next(iter(choices), ""),
            "voice_engine": voice.provider()}


def options() -> dict:
    return {
        "aspects": {"9:16": "رأسي 9:16 — ريلز / تيك توك / شورتس", "16:9": "أفقي 16:9 — يوتيوب",
                    "1:1": "مربع 1:1 — منشور", "4:5": "4:5 — منشور إنستغرام"},
        "lengths": {"short": "قصير (~15 ثانية)", "medium": "متوسط (~30 ثانية)", "long": "طويل (~60 ثانية)"},
        **_voices(),
        "footage": broll.provider_name(),
        "ai_ready": settings.ai_provider == "fake" or bool(settings.anthropic_api_key),
        "formats": FORMATS,
        "qualities": {k: v["label"] for k, v in QUALITIES.items()},
    }


_studio: Studio | None = None


def get_studio() -> Studio:
    global _studio
    if _studio is None:
        _studio = Studio()
    return _studio
