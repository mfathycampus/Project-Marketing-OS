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
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.provider import LLMProvider, LLMRequest
from app.config import settings
from app.media.library import describe_for_ai, get_library
from app.storage.local import LocalStorage
from app.video import broll, transcribe, voice
from app.video.themes import THEMES
from app.video.renderer import SIZES, VideoRenderer, VideoRequest, ffmpeg_exe, parse_stat

log = logging.getLogger("studio")
PROMPTS = Path(__file__).resolve().parents[1] / "ai" / "prompts"
PROMPT = PROMPTS / "studio.v6.md"
UGC_PROMPT = PROMPTS / "ugc.v1.md"
TALK_PROMPT = PROMPTS / "ugc_script.v1.md"
UGC_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}
UGC_MAX_BYTES = 1024 * 1024 * 1024  # 1 GB
UGC_MAX_SECONDS = 600
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
    media: list[str] = Field(default_factory=list, description=(
        "Per scene, same order and count as scenes: the id of an item from <media_library> that shows what the "
        "scene talks about (it appears inside a phone mockup), or an empty string"))
    stats: list[str] = Field(default_factory=list, description=(
        "Per scene, same order and count as scenes: an animated statistic 'value|label' (e.g. '5|ساعات توفرها أسبوعيًا')"
        " using ONLY numbers the user gave, or an empty string"))
    compare: list[str] = Field(default_factory=list, description=(
        "Per scene, same order and count as scenes: a before/after contrast 'before|after' (a few words each) for at "
        "most ONE scene, or an empty string"))
    hooks: list[str] = Field(default_factory=list, description=(
        "Two ALTERNATIVE first scenes (hooks) for A/B testing, each from a different angle than scenes[0] "
        "(e.g. a question, a surprising number the user gave, a pain); same language, 3-7 words, *one marked word*"))


def write_script(llm: LLMProvider, prompt: str, aspect: str, length: str) -> dict:
    count = LENGTHS.get(length, LENGTHS["medium"])
    fmt = {"9:16": "vertical (Reels / TikTok / Shorts)", "16:9": "landscape (YouTube)"}.get(aspect, "square feed")
    library = get_library().list()
    lib_txt = f"\n<media_library>\n{describe_for_ai(library)}\n</media_library>" if library else ""
    req = LLMRequest(instructions=PROMPT.read_text(encoding="utf-8"), stable_context=_lessons_context(),
                     user_message=f"<request>\nscene_count: {count}\nformat: {fmt}\ndescription: {prompt}\n</request>{lib_txt}",
                     output_model=StudioScript)
    try:
        data = StudioScript.model_validate(llm.generate_structured(req).data)
    except Exception as exc:  # noqa: BLE001 - no key / network / bad output: the user can still write scenes by hand
        raise StudioError(f"تعذّر كتابة السيناريو بالذكاء الاصطناعي ({exc}). اكتب المشاهد بنفسك أو تحقق من مفتاح Anthropic") from exc
    scenes = [s.strip() for s in data.scenes if s.strip()][:MAX_SCENES]
    terms = [b.strip() for b in data.broll][: len(scenes)]
    known = {m["id"] for m in library}
    media = [(m.strip() if m.strip() in known else "") for m in data.media][: len(scenes)]
    return {"title": data.title.strip()[:80], "brand_name": data.brand_name.strip(), "scenes": scenes,
            "broll": terms + [""] * (len(scenes) - len(terms)), "cta": data.cta.strip(),
            "media": media + [""] * (len(scenes) - len(media)),
            "stats": ([x.strip() for x in data.stats] + [""] * len(scenes))[: len(scenes)],
            "compare": ([x.strip() for x in data.compare] + [""] * len(scenes))[: len(scenes)],
            "hooks": [x.strip() for x in data.hooks if x.strip() and x.strip() != (scenes[0] if scenes else "")][:2]}


class VideoSpec(BaseModel):
    title: str = Field(default="فيديو", max_length=120)
    scenes: list[str] = Field(min_length=1, max_length=MAX_SCENES)
    broll: list[str] = Field(default_factory=list, max_length=MAX_SCENES)
    media: list[str] = Field(default_factory=list, max_length=MAX_SCENES)  # media-library id per scene ('' = none)
    stats: list[str] = Field(default_factory=list, max_length=MAX_SCENES)  # "value|label" per scene ('' = none)
    logo: str = ""  # media-library image id of the brand logo
    compare: list[str] = Field(default_factory=list, max_length=MAX_SCENES)  # "before|after" per scene ('' = none)
    style: str = "modern"  # ready-made look (app/video/themes.py)
    end_url: str = Field(default="", max_length=80)  # link on the final card, e.g. maeen.app
    end_offer: str = Field(default="", max_length=80)  # optional offer on the final card
    music: str = "auto"  # auto | none | generated | media-library audio id
    hooks: list[str] = Field(default_factory=list, max_length=3)  # alternative first scenes -> one extra video each
    vary_backgrounds: bool = True  # a different background per scene
    cta: str = Field(default="", max_length=80)
    brand_name: str = Field(default="", max_length=80)
    aspect: str = "9:16"
    voice: str = ""  # empty = no narration
    captions: bool = True
    footage: bool = True
    primary_color: str = "#0F766E"
    secondary_color: str = "#F59E0B"


# ---------- "your face" videos ----------
class TalkScript(BaseModel):
    title: str = Field(description="Short name, max 50 characters")
    lines: list[str] = Field(description="The script, one short spoken sentence per line, in order")
    tips: list[str] = Field(default_factory=list, description="Up to three short recording tips")


def write_talk_script(llm: LLMProvider, prompt: str, seconds: int = 30) -> dict:
    """A teleprompter script for the user to read on camera."""
    req = LLMRequest(instructions=TALK_PROMPT.read_text(encoding="utf-8"), stable_context=_lessons_context(),
                     user_message=f"<request>\nseconds: {seconds}\ntopic: {prompt}\n</request>", output_model=TalkScript)
    try:
        data = TalkScript.model_validate(llm.generate_structured(req).data)
    except Exception as exc:  # noqa: BLE001
        raise StudioError(f"تعذّر كتابة النص بالذكاء الاصطناعي ({exc}). تحقق من مفتاح Anthropic") from exc
    return {"title": data.title.strip()[:80], "lines": [x.strip() for x in data.lines if x.strip()][:30],
            "tips": [x.strip() for x in data.tips if x.strip()][:3]}


class UgcOverlayOut(BaseModel):
    start_word: int = Field(description="Index of the first word in <transcript> the overlay covers")
    end_word: int = Field(description="Index of the last word it covers (inclusive)")
    kind: Literal["media", "stat", "text"]
    value: str = Field(description="media: a <media_library> id; stat: 'number|label'; text: 2-5 words")


class UgcPlanOut(BaseModel):
    title: str = Field(description="Short name for the video, max 50 characters")
    hook_text: str = Field(description="Title over the speaker in the first seconds, 3-7 words, one *marked* word")
    cta: str = Field(description="Short call to action, max 30 characters")
    overlays: list[UgcOverlayOut] = Field(default_factory=list)


def clean_plan(raw: dict, words: list, media_ids: set[str]) -> dict:
    """Claude's plan -> safe overlays in seconds on the original timeline (bad / overlapping ones dropped)."""
    out, last_end = [], 2.0  # keep the hook's seconds free
    for o in sorted(raw.get("overlays") or [], key=lambda o: o["start_word"]):
        a, b = max(0, int(o["start_word"])), min(len(words) - 2, int(o["end_word"]))  # the last word stays clear
        if b < a:
            continue
        kind, value = o["kind"], str(o["value"]).strip()
        if kind == "media" and value not in media_ids or kind == "stat" and not parse_stat(value) or not value:
            continue
        start, end = words[a][0], words[b][1]
        if start < last_end + 0.4 or end - start < 1.0:
            continue
        out.append({"start": round(start, 3), "end": round(min(end, start + 6.0), 3), "kind": kind, "value": value[:80]})
        last_end = out[-1]["end"]
    return {"title": str(raw.get("title") or "").strip()[:80], "hook_text": str(raw.get("hook_text") or "").strip()[:80],
            "cta": str(raw.get("cta") or "").strip()[:40], "overlays": out}


def plan_ugc(llm: LLMProvider, words: list, notes: str, cta: str) -> dict:
    library = [m for m in get_library().list() if m["kind"] != "audio"]
    transcript = " ".join(f"[{i}]{w}" for i, (_, _, w) in enumerate(words))
    lib_txt = f"\n<media_library>\n{describe_for_ai(library)}\n</media_library>" if library else ""
    req = LLMRequest(instructions=UGC_PROMPT.read_text(encoding="utf-8"), stable_context=_lessons_context(),
                     user_message=f"<request>\nabout: {notes or '-'}\ncta: {cta or '-'}\n</request>\n"
                                  f"<transcript>\n{transcript}\n</transcript>{lib_txt}", output_model=UgcPlanOut)
    data = UgcPlanOut.model_validate(llm.generate_structured(req).data)
    return clean_plan(data.model_dump(), words, {m["id"] for m in library})


def _lessons_context() -> str:
    """What worked before (the learning loop), when the user has recorded results."""
    try:
        from app.insights.lessons import lessons_text

        return lessons_text(None)
    except Exception:  # noqa: BLE001 - lessons are a bonus, never a reason to fail
        return ""


class UgcSpec(BaseModel):
    title: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=1000)  # what the video is about (helps Claude pick overlays)
    language: str = "ar"  # speech language for transcription ("" = detect)
    aspect: str = "9:16"
    style: str = "modern"
    logo: str = ""
    music: str = "auto"
    end_url: str = Field(default="", max_length=80)
    end_offer: str = Field(default="", max_length=80)
    cta: str = Field(default="", max_length=80)
    brand_name: str = Field(default="", max_length=80)
    captions: bool = True
    jump_cuts: bool = True
    punch_zoom: bool = True
    smart_edit: bool = True  # Claude plans the hook title + overlays
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
        variants = [None] + [h.strip() for h in spec.hooks if h.strip() and h.strip() != scenes[0]]
        group = uuid.uuid4().hex if len(variants) > 1 else None
        first = None
        for k, hook in enumerate(variants):
            v_scenes, v_spec = list(scenes), spec.model_dump()
            if hook:  # same video, different opening: the hook scene loses the original's number/contrast
                v_scenes[0] = hook
                for key in ("stats", "compare"):
                    if v_spec.get(key):
                        v_spec[key] = [""] + list(v_spec[key][1:])
            label = "ABC"[k] if group else None
            vid = uuid.uuid4().hex
            title = spec.title.strip() or "فيديو"
            meta = {"id": vid, "title": f"{title} ({label})" if label else title, "status": "queued", "progress": 0.0,
                    "error": None, "created_at": time.time() + k * 0.001, "group": group, "variant": label,
                    "spec": v_spec | {"scenes": v_scenes, "hooks": []}, "width": None, "height": None, "duration_ms": None}
            self._save(meta)
            with self._lock:
                self._active.add(vid)
            if run_async:
                self._pool.submit(self._render, vid)
            else:
                self._render(vid)
            first = first or vid
        return self.get(first)

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
                broll=terms, captions=spec.captions,
                media=[get_library().resolve(m) or "" for m in spec.media], stats=spec.stats,
                logo=get_library().resolve(spec.logo) or "", style=spec.style, compare=spec.compare,
                media_focus=[get_library().focus_of(m) for m in spec.media], end_url=spec.end_url,
                end_offer=spec.end_offer, vary_backgrounds=spec.vary_backgrounds,
                music=spec.music if spec.music in ("auto", "none", "generated") else (get_library().resolve(spec.music) or "auto")),
                on_progress=progress)
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

    def create_ugc(self, spec: UgcSpec, filename: str, fileobj, llm: LLMProvider | None = None,
                   run_async: bool = True) -> dict:
        """Store the uploaded talking video and start the edit in the background."""
        ext = Path(filename or "").suffix.lower()
        if ext not in UGC_EXT:
            raise StudioError("ارفع فيديو (MP4 أو MOV أو WebM)")
        if spec.aspect not in SIZES:
            raise StudioError(f"مقاس غير مدعوم: {spec.aspect}")
        vid = uuid.uuid4().hex
        src = self._dir(vid) / f"source{ext}"
        src.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(src, "wb") as out:
            while chunk := fileobj.read(1024 * 1024):
                size += len(chunk)
                if size > UGC_MAX_BYTES:
                    out.close()
                    shutil.rmtree(src.parent, ignore_errors=True)
                    raise StudioError("الفيديو أكبر من 1 جيجابايت")
                out.write(chunk)
        meta = {"id": vid, "kind": "ugc", "title": spec.title.strip() or "فيديو بوجهي", "status": "queued",
                "progress": 0.0, "stage": "", "error": None, "created_at": time.time(), "group": None, "variant": None,
                "source": src.name, "spec": spec.model_dump(), "plan": None, "width": None, "height": None,
                "duration_ms": None}
        self._save(meta)
        with self._lock:
            self._active.add(vid)
        if run_async:
            self._pool.submit(self._render_ugc, vid, llm)
        else:
            self._render_ugc(vid, llm)
        return self.get(vid)

    def _render_ugc(self, vid: str, llm: LLMProvider | None) -> None:
        from app.video.ugc import UgcRequest, probe, render_ugc

        meta = self.get(vid)
        spec = UgcSpec.model_validate(meta["spec"])
        src = self._dir(vid) / meta["source"]
        last = [0.0]

        def progress(p: float, stage: str | None = None) -> None:
            if stage or p - last[0] >= 0.03:
                last[0] = p
                meta["progress"] = round(min(p, 0.99), 3)
                if stage:
                    meta["stage"] = stage
                self._save(meta)

        try:
            meta.update(status="rendering")
            duration, has_audio = probe(str(src))
            if not has_audio:
                raise StudioError("الفيديو المرفوع بلا صوت")
            if duration > UGC_MAX_SECONDS:
                raise StudioError("الفيديو أطول من 10 دقائق: قصّه أولًا (الأفضل 30–90 ثانية)")
            progress(0.01, "تفريغ الكلام")
            words = transcribe.transcribe(str(src), spec.language or None, on_progress=lambda p: progress(0.01 + p * 0.3))
            if not words:
                raise StudioError("لم يُسمع كلام في الفيديو. تأكد من الصوت ولغة الكلام")
            (self._dir(vid) / "words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
            plan = {"title": "", "hook_text": "", "cta": "", "overlays": []}
            if spec.smart_edit and llm is not None:
                progress(0.32, "Claude يخطط المونتاج")
                try:
                    plan = plan_ugc(llm, words, spec.notes, spec.cta)
                except Exception as exc:  # noqa: BLE001 - the plain edit (cuts + captions) is still worth making
                    log.warning("ugc plan failed for %s: %s", vid, exc)
                    meta["warning"] = "تعذّر تخطيط المونتاج بالذكاء الاصطناعي، فأُنشئ الفيديو بالقص والترجمة فقط"
            meta["plan"] = plan
            meta["transcript"] = " ".join(w for _, _, w in words)[:4000]
            if not spec.title.strip() and plan["title"]:
                meta["title"] = plan["title"]
            lib = get_library()
            overlays = []
            for o in plan["overlays"]:
                if o["kind"] == "media":
                    path = lib.resolve(o["value"])
                    if not path:
                        continue
                    o = {**o, "path": path, "focus": lib.focus_of(o["value"])}
                overlays.append(o)
            progress(0.35, "المونتاج")
            res = render_ugc(UgcRequest(
                video=str(src), words=words, overlays=overlays, hook_text=plan["hook_text"],
                cta=spec.cta.strip() or plan["cta"], brand_name=spec.brand_name, primary_color=spec.primary_color,
                secondary_color=spec.secondary_color, logo=lib.resolve(spec.logo) or "", style=spec.style,
                music=spec.music if spec.music in ("auto", "none", "generated") else (lib.resolve(spec.music) or "auto"),
                music_seed=vid, end_url=spec.end_url, end_offer=spec.end_offer, captions=spec.captions,
                jump_cuts=spec.jump_cuts, punch_zoom=spec.punch_zoom, aspect=spec.aspect,
                rtl=(spec.language or "ar") == "ar"), on_progress=lambda p: progress(0.35 + p * 0.64))
            self.storage.put(f"studio/{vid}/master.mp4", res.mp4)
            self.storage.put(f"studio/{vid}/captions.srt", res.srt().encode("utf-8"))
            meta.update(status="ready", progress=1.0, stage="", width=res.width, height=res.height,
                        duration_ms=res.duration_ms, source_seconds=round(duration, 1))
        except Exception as exc:  # noqa: BLE001 - shown to the user on the video card
            log.exception("ugc render %s failed", vid)
            meta.update(status="failed", error=str(exc)[:500])
        finally:
            with self._lock:
                self._active.discard(vid)
            self._save(meta)

    # ---------- results (the learning loop) ----------
    def set_metrics(self, vid: str, values: dict) -> dict:
        """Numbers the video got after you posted it (typed in from the platform's insights)."""
        from app.insights.lessons import score

        meta = self.get(vid)
        if meta["status"] != "ready":
            raise StudioError("الفيديو غير جاهز بعد")
        m = {k: v for k, v in values.items() if v is not None}
        meta["metrics"] = m | {"score": score(m)} if m else None
        self._save(meta)
        return meta

    def lessons(self, llm: LLMProvider, session) -> dict:
        from app.insights import lessons

        rows = []
        for v in self.list():
            m = v.get("metrics")
            if not m:
                continue
            spec = v["spec"]
            hook = (v.get("plan") or {}).get("hook_text") if v.get("kind") == "ugc" else (spec.get("scenes") or [""])[0]
            rows.append({"title": v["title"], "type": "talking-head" if v.get("kind") == "ugc" else "generated video",
                         "ab_group": v.get("group"), "variant": v.get("variant"), "hook": hook, "style": spec.get("style"),
                         "seconds": round((v.get("duration_ms") or 0) / 1000, 1), "aspect": spec.get("aspect"), **m})
        data = lessons.analyse(llm, rows)
        return lessons.out(lessons.save(session, None, data, len(rows)))

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
        "styles": {k: v["label"] for k, v in THEMES.items()},
        "music": {"auto": "تلقائي (مقطعك إن وُجد، وإلا موسيقى مولّدة)", "generated": "موسيقى مولّدة", "none": "بدون موسيقى",
                  **{m["id"]: f"🎵 {m['description'] or m['filename']}" for m in get_library().list() if m["kind"] == "audio"}},
        "ai_ready": settings.ai_provider == "fake" or bool(settings.anthropic_api_key),
        "transcribe": transcribe.available(),
        "formats": FORMATS,
        "qualities": {k: v["label"] for k, v in QUALITIES.items()},
    }


_studio: Studio | None = None


def get_studio() -> Studio:
    global _studio
    if _studio is None:
        _studio = Studio()
    return _studio
