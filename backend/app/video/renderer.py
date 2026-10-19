"""Short social videos (Reels / TikTok / Shorts / square) rendered with Pillow + FFmpeg.

No browser and no paid API: each scene is drawn with the same Arabic-aware text engine as the images,
animated frame by frame (fade/slide-in, drifting light, progress bar, pulsing CTA) and encoded to H.264/AAC.
FFmpeg comes from the imageio-ffmpeg wheel, so `pip install` is enough on Windows too.
"""
import hashlib
import io
import re
import logging
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter

from app.designs.pillow_renderer import DARK, INK, Block, _Canvas, _font, _gradient, _rgb, _shape
from app.video import broll, themes, voice

log = logging.getLogger(__name__)
FPS = 30
VOICE_LEAD = 0.3  # narration starts shortly after the scene text begins to appear
MUSIC_DIR = Path(__file__).parent / "music"
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".wav", ".ogg")
SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}
CAPTION_WORDS = 4  # max words on screen at once in synced captions
# Voice "mastering": cut rumble, clear mud, add presence, even out the level (broadcast-style compression)
VOICE_CHAIN = ("highpass=f=80,equalizer=f=250:t=q:w=1.2:g=-2,equalizer=f=3500:t=q:w=1.5:g=2.5,"
               "acompressor=threshold=-20dB:ratio=3:attack=5:release=120:makeup=2,aresample=44100")
# Final mix at -14 LUFS, the loudness Instagram / TikTok / YouTube normalise to (so it is not quieter than others)
MASTER = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100"
PLATFORM_ASPECT = {"linkedin": "1:1"}  # everything else: vertical 9:16 (Reels / TikTok / Shorts / FB Reels)


class VideoError(RuntimeError):
    pass


@dataclass
class VideoRequest:
    scenes: list[str]
    cta: str = ""
    brand_name: str = ""
    primary_color: str = "#0F766E"
    secondary_color: str = "#F59E0B"
    aspect: str = "9:16"
    rtl: bool = True
    music_seed: str = ""
    voice: str = ""  # "provider:voice" (e.g. edge:ar-EG-SalmaNeural, elevenlabs:<id>); empty = no narration
    broll: list[str] = field(default_factory=list)  # Pexels search term per scene; empty = brand gradient
    captions: bool = True  # with narration: words appear in short groups synced to the voice, spoken word highlighted
    media: list[str] = field(default_factory=list)  # per scene: path of an app screenshot/recording ('' = none)
    stats: list[str] = field(default_factory=list)  # per scene: "value|label" (e.g. "5|ساعات توفرها") -> animated stat
    logo: str = ""  # path of the brand logo (PNG with transparency is best); replaces the brand name, big on the CTA
    style: str = "modern"  # ready-made look: modern | calm | energetic | corporate (see themes.py)
    compare: list[str] = field(default_factory=list)  # per scene: "before|after" -> animated before/after cards
    media_focus: list = field(default_factory=list)  # per scene: (x, y) in 0..1 = the spot to point/tap/zoom at, or None


@dataclass
class VideoResult:
    mp4: bytes
    width: int
    height: int
    duration_ms: int
    captions: list[tuple[float, float, str]] = field(default_factory=list)  # (start_s, end_s, text) for SRT

    def srt(self) -> str:
        return "".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(self.captions, 1))


def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def ffmpeg_exe() -> str:
    exe = os.environ.get("FFMPEG_BINARY")
    if exe and Path(exe).is_file():
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - fall back to a system ffmpeg
        found = shutil.which("ffmpeg")
        if found:
            return found
    raise VideoError("FFmpeg غير متوفر. نفّذ: python -m pip install imageio-ffmpeg (أو شغّل start.bat) ثم أعد المحاولة")


def scenes_for(headline: str, description: str, price: str, cta: str, scripted: list[str] | None) -> list[str]:
    """Claude's scripted scenes when present; otherwise built from the post's design fields."""
    scenes = [s.strip() for s in (scripted or []) if s and s.strip()][:6]
    if not scenes:
        scenes = [x for x in (headline, description, price) if x and x.strip()]
    if cta and (not scenes or scenes[-1] != cta):
        scenes.append(cta)
    return scenes or ["…"]


def pick_music(seed: str) -> Path | None:
    tracks = sorted(p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in AUDIO_EXT) if MUSIC_DIR.is_dir() else []
    if not tracks:
        return None
    return tracks[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(tracks)]


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _sentence(text: str) -> str:
    """End each scene with punctuation so a one-take narration pauses naturally between scenes."""
    text = text.strip()
    return text if text[-1:] in ".!?؟!…،:;" else text + "."


def _scene_seconds(text: str) -> float:
    return max(2.6, min(5.0, 1.8 + 0.05 * len(text)))


TRANSITION = 0.3  # seconds of zoom-through crossfade at each scene change
EMPH = re.compile(r"\*([^*]+)\*")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")
_PUNCT = ".,!?؟،:;…«»\"'()"


def _bare(word: str) -> str:
    return word.strip(_PUNCT)


def parse_emphasis(text: str) -> tuple[str, set[str]]:
    """'وفّر *ساعات* أسبوعيًا' -> ('وفّر ساعات أسبوعيًا', {'ساعات'}). Marked words are drawn in the accent colour."""
    words = {_bare(x) for span in EMPH.findall(text) for x in span.split()}
    return text.replace("*", "").strip(), {x for x in words if x}


def parse_stat(spec: str):
    """'+80%|أسرع في التوثيق' -> (prefix '+', 80.0, decimals 0, suffix '%', label, arabic_digits) or None."""
    if not spec or "|" not in spec:
        value, label = (spec or "").strip(), ""
    else:
        value, label = (x.strip() for x in spec.split("|", 1))
    m = re.search(r"\d+(?:[.,٫]\d+)?", value.translate(_AR_DIGITS))
    if not value or not m:
        return None
    num = m.group(0).replace(",", ".")
    return (value.translate(_AR_DIGITS)[: m.start()], float(num), len(num.split(".")[1]) if "." in num else 0,
            value.translate(_AR_DIGITS)[m.end():], label, any(c in "٠١٢٣٤٥٦٧٨٩" for c in value))


def _stat_text(stat, p: float) -> str:
    prefix, target, decimals, suffix, _, arabic = stat
    v = target * p
    txt = f"{v:.{decimals}f}" if decimals else f"{int(round(v)):,}" if target >= 10000 else f"{int(round(v))}"
    if arabic:
        txt = txt.translate(str.maketrans("0123456789.,", "٠١٢٣٤٥٦٧٨٩٫٬"))
    return f"{prefix}{txt}{suffix}"


def load_logo(path: str, max_w: int, max_h: int) -> Image.Image | None:
    """Logo fitted into max_w x max_h; logos without transparency get rounded corners so they look like a badge."""
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA")
    except Exception:  # noqa: BLE001 - a broken logo must not break the video
        return None
    im.thumbnail((max_w, max_h), Image.LANCZOS)
    if im.getchannel("A").getextrema()[0] == 255:  # fully opaque
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), int(min(im.size) * 0.18), fill=255)
        im.putalpha(mask)
    shadow = Image.new("RGBA", (im.width + 40, im.height + 40), (0, 0, 0, 0))
    a = Image.new("L", shadow.size, 0)
    a.paste(im.getchannel("A").point(lambda v: int(v * 0.45)), (20, 26))
    shadow.putalpha(a.filter(ImageFilter.GaussianBlur(10)))
    shadow.alpha_composite(im, (20, 20))
    return shadow


def _stack(top: Image.Image, bottom: Image.Image, gap: int) -> Image.Image:
    out = Image.new("RGBA", (max(top.width, bottom.width), top.height + gap + bottom.height), (0, 0, 0, 0))
    out.alpha_composite(top, ((out.width - top.width) // 2, 0))
    out.alpha_composite(bottom, ((out.width - bottom.width) // 2, top.height + gap))
    return out


def _mix(a, b, k: float):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


def _ease_back(t: float) -> float:
    """Ease-out with a small overshoot (spring feel) for the phone entrance."""
    t = max(0.0, min(1.0, t))
    c = 1.4
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def _phone_layout(w: int, h: int, margin: int, rtl: bool) -> dict:
    """Where the phone mockup and the text go when a scene shows app media.
    Tall frames: text on top, phone below. Square/wide frames: phone on one side, text on the other."""
    if h >= w * 1.5:  # 9:16
        pw = int(w * 0.56)
        ph = int(pw * 2.05)
        px, py = (w - pw) // 2, h - ph - 110
        text = (w // 2, (margin + 40 + py) // 2, w - 2 * margin, py - margin - 60)
    elif h > w:  # 4:5
        pw = int(w * 0.40)
        ph = int(pw * 2.05)
        px, py = (w - pw) // 2, h - ph - 60
        text = (w // 2, (margin + py) // 2 + 10, w - 2 * margin, py - margin - 30)
    else:  # 1:1 and 16:9: side by side (RTL reads right to left, so the text sits on the right)
        ph = int(h * 0.84)
        pw = int(ph / 2.05)
        py = (h - ph) // 2 + 10
        px = int(w * 0.09) if rtl else w - int(w * 0.09) - pw
        x0, x1 = (px + pw + 70, w - margin) if rtl else (margin, px - 70)
        text = ((x0 + x1) // 2, h // 2, x1 - x0, h - 2 * margin)
    return {"phone": (px, py, pw, ph), "text": text}


class _Phone:
    """App screenshot or screen recording inside a phone mockup. Screenshots taller than the screen scroll
    slowly (like someone browsing the app); others zoom in gently. Recordings play inside the screen."""

    def __init__(self, path: str, geo: dict, seconds: float, focus=None):
        self.focus = tuple(focus) if focus else None
        self.px, self.py, self.pw, self.ph = geo["phone"]
        bezel = max(10, self.pw // 34)
        self.sw, self.sh = self.pw - 2 * bezel, self.ph - 2 * bezel
        self.bezel, self.seconds = bezel, seconds
        r_out, r_in = int(self.pw * 0.14), int(self.pw * 0.12)
        pad = 60  # room for the shadow
        self.pad = pad
        body = Image.new("RGBA", (self.pw + 2 * pad, self.ph + 2 * pad), (0, 0, 0, 0))
        sh = Image.new("L", body.size, 0)
        ImageDraw.Draw(sh).rounded_rectangle((pad + 10, pad + 30, pad + self.pw - 10, pad + self.ph + 10), r_out, fill=150)
        body.putalpha(sh.filter(ImageFilter.GaussianBlur(28)))
        d = ImageDraw.Draw(body)
        d.rounded_rectangle((pad, pad, pad + self.pw, pad + self.ph), r_out, fill=(18, 20, 24, 255),
                            outline=(70, 74, 82, 255), width=3)
        self.body = body
        self.mask = Image.new("L", (self.sw, self.sh), 0)
        ImageDraw.Draw(self.mask).rounded_rectangle((0, 0, self.sw - 1, self.sh - 1), r_in, fill=255)
        iw = int(self.sw * 0.30)  # dynamic-island pill
        self.island = (pad + bezel + (self.sw - iw) // 2, pad + bezel + int(self.sh * 0.018), iw, int(self.sw * 0.075))
        self.reader, self.still = None, None
        if Path(path).suffix.lower() in (".mp4", ".mov", ".m4v", ".webm", ".mkv"):
            self.reader = _ClipReader(Path(path), self.sw, self.sh, seconds)
        else:
            with Image.open(path) as im:
                im = im.convert("RGB")
                k = self.sw / im.width
                if im.height * k < self.sh:  # short/wide image: cover the screen
                    k = self.sh / im.height
                self.still = im.resize((max(self.sw, int(im.width * k)), max(self.sh, int(im.height * k))), Image.LANCZOS)

    def _screen(self, t: float) -> Image.Image:
        self.tap = None
        if self.reader is not None:
            shot = self.reader.next()
            return shot if shot is not None else Image.new("RGB", (self.sw, self.sh), (0, 0, 0))
        if self.focus:
            return self._focused(t)
        im, p = self.still, max(0.0, min(1.0, (t - 0.4) / max(0.1, self.seconds - 0.8)))
        p = p * p * (3 - 2 * p)  # smoothstep
        x0 = (im.width - self.sw) // 2
        if im.height - self.sh > self.sh * 0.15:  # tall screenshot: scroll down through it
            y0 = int((im.height - self.sh) * p * 0.85)
            return im.crop((x0, y0, x0 + self.sw, y0 + self.sh))
        z = 1 + 0.08 * p  # otherwise a slow push-in
        cw, ch = int(self.sw / z), int(self.sh / z)
        cx, cy = im.width // 2, im.height // 2
        return im.crop((cx - cw // 2, cy - ch // 2, cx - cw // 2 + cw, cy - ch // 2 + ch)).resize((self.sw, self.sh), Image.BILINEAR)

    def _focused(self, t: float) -> Image.Image:
        """Scroll so the focus point comes into view, tap it, then push in around it."""
        im = self.still
        fx, fy = self.focus
        y_target = max(0, min(im.height - self.sh, int(fy * im.height - self.sh / 2)))
        y0 = int(y_target * _smooth((t - 0.3) / 0.8))
        crop = im.crop(((im.width - self.sw) // 2, y0, (im.width - self.sw) // 2 + self.sw, y0 + self.sh))
        tx, ty = fx * im.width - (im.width - self.sw) / 2, fy * im.height - y0
        z = 1 + 0.14 * _smooth((t - TAP_AT - 0.4) / max(0.5, self.seconds - TAP_AT - 0.8))
        if z > 1.001:  # zoom around the tapped point
            cw, ch = self.sw / z, self.sh / z
            x0 = min(max(tx - cw / 2, 0), self.sw - cw)
            yy = min(max(ty - ch / 2, 0), self.sh - ch)
            crop = crop.crop((int(x0), int(yy), int(x0 + cw), int(yy + ch))).resize((self.sw, self.sh), Image.BILINEAR)
            tx, ty = (tx - x0) * z, (ty - yy) * z
        self.tap = (tx, ty)
        return crop

    def render(self, t: float, enters: bool):
        img = self.body.copy()
        img.paste(self._screen(t), (self.pad + self.bezel, self.pad + self.bezel), self.mask)
        x, y, iw, ih = self.island
        ImageDraw.Draw(img).rounded_rectangle((x, y, x + iw, y + ih), ih // 2, fill=(8, 8, 10, 255))
        if self.tap and t >= TAP_AT - 0.35:  # finger tap: a dot presses, then a ring spreads out
            _tap_marks(img, self.pad + self.bezel + self.tap[0], self.pad + self.bezel + self.tap[1], t - TAP_AT,
                       int(self.sw * 0.075))
        dy = int(math.sin(t * 2 * math.pi / 3.2) * 8)  # gentle float
        if enters:
            dy += int((1 - _ease_back(t / 0.7)) * self.ph * 0.6)
        return img, (self.px - self.pad, self.py - self.pad + dy)

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()


TAP_AT = 1.35  # seconds into a scene when the pointer clicks / the finger taps the focus point
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


def _smooth(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _tap_marks(img: Image.Image, x: float, y: float, dt: float, r: int) -> None:
    """Touch/click feedback at (x, y): a soft dot that presses down, then an expanding ring."""
    d = ImageDraw.Draw(img, "RGBA")
    if -0.35 <= dt <= 0.35:
        k = 1 - 0.25 * _smooth((dt + 0.35) / 0.35) if dt < 0 else 0.75 + 0.25 * _smooth(dt / 0.35)
        rr = r * k
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(255, 255, 255, 150), outline=(20, 20, 24, 90), width=3)
    if 0 <= dt <= 0.7:
        p = dt / 0.7
        rr = r * (1 + 2.2 * p)
        d.ellipse((x - rr, y - rr, x + rr, y + rr), outline=(255, 255, 255, int(230 * (1 - p))), width=max(3, r // 6))


def _media_size(path: str) -> tuple[int, int] | None:
    """Displayed size of an image or video (rotation applied), None if unreadable."""
    try:
        if Path(path).suffix.lower() in VIDEO_EXT:
            png = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", path, "-frames:v", "1", "-f", "image2pipe",
                                  "-c:v", "png", "-"], capture_output=True, timeout=60).stdout
            with Image.open(io.BytesIO(png)) as im:
                return im.size
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return None


def _browser_layout(w: int, h: int, margin: int, ar: float) -> dict:
    """Desktop window for wide screenshots/recordings: text above, the window large below."""
    ar = max(1.2, min(2.2, ar))
    bar = int(min(w, h) * 0.05)
    if h > w:
        bw = w - 80
        bh = int(bw / ar) + bar
        py = (h - bh) // 2 + int(h * 0.1)
        text = (w // 2, (margin + 30 + py) // 2, w - 2 * margin, py - margin - 70)
    else:
        band = int(h * 0.24)
        avail = h - band - margin - 30
        bw = min(w - 2 * margin, int((avail - bar) * ar))
        bh = int(bw / ar) + bar
        py = band + 10 + (avail - bh) // 2
        text = (w // 2, (margin + band) // 2 + 10, w - 2 * margin, band - margin // 2)
    return {"phone": ((w - bw) // 2, py, bw, bh), "bar": bar, "text": text}


class _Browser:
    """Wide screenshot / recording inside a desktop browser window. With a focus point, a mouse pointer glides to
    it, clicks, and the camera zooms into that part of the screen; without one, a slow push-in."""

    def __init__(self, path: str, geo: dict, seconds: float, focus=None):
        self.px, self.py, self.bw, self.bh = geo["phone"]
        self.bar, self.seconds = geo["bar"], seconds
        self.focus = tuple(focus) if focus else None
        self.sw, self.sh = self.bw, self.bh - self.bar
        pad, r = 50, 18
        self.pad = pad
        body = Image.new("RGBA", (self.bw + 2 * pad, self.bh + 2 * pad), (0, 0, 0, 0))
        sh = Image.new("L", body.size, 0)
        ImageDraw.Draw(sh).rounded_rectangle((pad + 6, pad + 22, pad + self.bw - 6, pad + self.bh + 8), r, fill=140)
        body.putalpha(sh.filter(ImageFilter.GaussianBlur(24)))
        d = ImageDraw.Draw(body)
        d.rounded_rectangle((pad, pad, pad + self.bw, pad + self.bh), r, fill=(235, 238, 242, 255))
        cy, dot = pad + self.bar / 2, self.bar * 0.13
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            cx = pad + self.bar * 0.55 + i * self.bar * 0.42
            d.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=c)
        d.rounded_rectangle((pad + self.bw * 0.22, cy - self.bar * 0.26, pad + self.bw * 0.78, cy + self.bar * 0.26),
                            int(self.bar * 0.26), fill=(255, 255, 255, 255))
        self.body = body
        self.mask = Image.new("L", (self.sw, self.sh), 0)
        md = ImageDraw.Draw(self.mask)
        md.rounded_rectangle((0, 0, self.sw - 1, self.sh - 1), r, fill=255)
        md.rectangle((0, 0, self.sw, r), fill=255)  # square top corners under the title bar
        self.cursor = None
        self.reader, self.still = None, None
        if Path(path).suffix.lower() in VIDEO_EXT:
            self.reader = _ClipReader(Path(path), self.sw, self.sh, seconds)
        else:
            from app.video.themes import cursor

            with Image.open(path) as im:
                im = im.convert("RGB")
                q = max(1.0, min(1.8, im.width / self.sw))  # keep detail for the zoom
                tw, th = int(self.sw * q), int(self.sh * q)
                k = max(tw / im.width, th / im.height)
                big = im.resize((max(tw, int(im.width * k)), max(th, int(im.height * k))), Image.LANCZOS)
                x0, y0 = (big.width - tw) // 2, (big.height - th) // 2
                self.still = big.crop((x0, y0, x0 + tw, y0 + th))
            if self.focus:
                self.cursor = cursor(max(26, self.sw // 22))

    def _screen(self, t: float):
        if self.reader is not None:
            shot = self.reader.next()
            return (shot if shot is not None else Image.new("RGB", (self.sw, self.sh), (0, 0, 0))), None
        W, H = self.still.size
        if self.focus:
            p = _smooth((t - TAP_AT - 0.15) / max(0.6, self.seconds - TAP_AT - 0.6))
            z, (fx, fy) = 1 + 0.6 * p, self.focus
            cx, cy = W * (0.5 + (fx - 0.5) * p), H * (0.5 + (fy - 0.5) * p)
        else:
            z, fx, fy = 1 + 0.1 * _smooth(t / max(0.5, self.seconds)), 0.5, 0.5
            cx, cy = W / 2, H / 2
        cw, ch = W / z, H / z
        x0, y0 = min(max(cx - cw / 2, 0), W - cw), min(max(cy - ch / 2, 0), H - ch)
        frame = self.still.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((self.sw, self.sh), Image.BILINEAR)
        target = ((fx * W - x0) / cw * self.sw, (fy * H - y0) / ch * self.sh)
        return frame, target

    def render(self, t: float, enters: bool):
        img = self.body.copy()
        screen, target = self._screen(t)
        ox, oy = self.pad, self.pad + self.bar
        img.paste(screen, (ox, oy), self.mask)
        if self.cursor is not None and target is not None:
            start = (self.sw * 0.92, self.sh * 1.08)
            k = _smooth((t - 0.25) / (TAP_AT - 0.35))
            x = start[0] + (target[0] - start[0]) * k
            y = start[1] + (target[1] - start[1]) * k
            if t >= TAP_AT - 0.05:
                _tap_marks(img, ox + x, oy + y, t - TAP_AT, int(self.sw * 0.035))
            cur = self.cursor
            if abs(t - TAP_AT) < 0.12:  # pressed
                cur = cur.resize((int(cur.width * 0.88), int(cur.height * 0.88)), Image.BILINEAR)
            img.alpha_composite(cur, (int(min(max(ox + x, 0), img.width - cur.width)),
                                      int(min(max(oy + y, 0), img.height - cur.height))))
        dy = int(math.sin(t * 2 * math.pi / 3.6) * 5)
        if enters:
            dy += int((1 - _ease_back(t / 0.7)) * self.bh * 0.7)
        return img, (self.px - self.pad, self.py - self.pad + dy)

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()


def _transition(kind: str, prev: Image.Image, cur: Image.Image, x: float, w: int, h: int, rtl: bool) -> Image.Image:
    """Blend the previous scene's last frame into the new scene; x goes 0 -> 1 over the transition."""
    a = _ease_out(x)
    if kind == "fade":
        return Image.blend(prev, cur, _smooth(x))
    if kind == "slide":  # the new scene pushes the old one out (towards reading direction)
        off = int((1 - a) * w) * (-1 if rtl else 1)
        out = Image.new("RGB", (w, h))
        out.paste(prev, (off + (w if rtl else -w), 0))
        out.paste(cur, (off, 0))
        return out
    if kind == "punch":  # white flash + the new frame settling from a zoom
        z = 1 + 0.12 * (1 - a)
        big = cur.resize((int(w * z), int(h * z)), Image.BILINEAR)
        ox, oy = (big.width - w) // 2, (big.height - h) // 2
        frame = big.crop((ox, oy, ox + w, oy + h))
        return Image.blend(Image.new("RGB", (w, h), (255, 255, 255)), frame, min(1.0, 0.35 + a))
    zoom = 1 + 0.08 * a  # "zoom": zoom-through crossfade
    big = prev.resize((int(w * zoom), int(h * zoom)), Image.BILINEAR)
    ox, oy = (big.width - w) // 2, (big.height - h) // 2
    return Image.blend(big.crop((ox, oy, ox + w, oy + h)), cur, a)


def parse_compare(spec: str):
    """'تفريغ يدوي 3 ساعات|مستند جاهز في دقيقة' -> (before, after) or None."""
    if not spec or "|" not in spec:
        return None
    a, b = (x.strip() for x in spec.split("|", 1))
    return (a, b) if a and b else None


class _ClipReader:
    """Decodes a footage file into frames of exactly w x h at FPS (cover-crop, looped if too short)."""

    def __init__(self, path: Path, w: int, h: int, seconds: float):
        self.size = (w, h)
        self.nbytes = w * h * 3
        self.proc = subprocess.Popen(
            [ffmpeg_exe(), "-v", "error", "-stream_loop", "-1", "-i", str(path), "-t", f"{seconds + 0.5:.3f}", "-an",
             "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS}",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.last: Image.Image | None = None

    def next(self) -> Image.Image | None:
        data = self.proc.stdout.read(self.nbytes)
        if len(data) == self.nbytes:
            self.last = Image.frombytes("RGB", self.size, data)
        return self.last  # clip ended early: hold the last frame (None if it never produced one)

    def close(self) -> None:
        try:
            self.proc.stdout.close()
        finally:
            self.proc.kill()
            self.proc.wait()


class VideoRenderer:
    name = "video"

    def render(self, req: VideoRequest, on_progress: Callable[[float], None] | None = None) -> VideoResult:
        w, h = SIZES.get(req.aspect, SIZES["9:16"])
        primary, secondary = _rgb(req.primary_color, "#0F766E"), _rgb(req.secondary_color, "#F59E0B")
        ink, dark, white = _rgb(INK, INK), _rgb(DARK, DARK), (255, 255, 255)
        margin = 90

        look = themes.build(req.style, w, h, primary, secondary, dark)
        bg = look["bg"]
        shade = _gradient(w, h, primary, dark, diagonal=False).convert("RGBA")  # darkens footage so text stays readable
        shade.putalpha(165)
        glow = self._glow(int(w * 0.9), secondary)
        parsed = [parse_emphasis(t) for t in req.scenes]
        emph = [e for _, e in parsed]
        req = replace(req, scenes=[t for t, _ in parsed], cta=parse_emphasis(req.cta)[0])  # voice reads clean text
        stats = [parse_stat(req.stats[i]) if i < len(req.stats) else None for i in range(len(req.scenes))]
        logo_small = load_logo(req.logo, int(w * 0.32), int(min(w, h) * 0.085)) if req.logo else None
        logo_big = load_logo(req.logo, int(w * 0.5), int(min(w, h) * 0.2)) if req.logo else None
        self.look = look
        brand = logo_small or (self._layer(w, Block(req.brand_name, 44, look["fg"], alpha=0.85), req.rtl, margin)
                               if req.brand_name else None)

        durs, narration, scene_words = self._timeline(req)
        n = len(req.scenes)
        compare = [parse_compare(req.compare[i]) if i < len(req.compare) else None for i in range(n)]
        media = [((req.media[i] if i < len(req.media) else "") or "") if not (stats[i] or compare[i]) else ""
                 for i in range(n)]  # a stat / comparison scene is about the number or the contrast, not the app
        focus = [(req.media_focus[i] if i < len(req.media_focus) else None) for i in range(n)]
        footage = [self._footage(req, i, w, h, durs[i]) for i in range(n)]
        looks = [themes.on_footage(look) if footage[i] else look for i in range(n)]
        # device per scene: wide media -> browser window, tall -> phone; its layout decides where the text goes
        devices, geos = [], []
        for m in media:
            size = _media_size(m) if m else None
            kind = "browser" if size and size[0] >= size[1] * 1.05 else ("phone" if m else None)
            devices.append(kind)
            geos.append(_browser_layout(w, h, margin, size[0] / size[1]) if kind == "browser"
                        else _phone_layout(w, h, margin, req.rtl) if kind else None)
        # per scene: (text centre x, text centre y, text width, text max height); scenes with app media leave room
        boxes = [(g["text"] if g else (w // 2, h // 2, w - 2 * margin, int(h * 0.6))) for g in geos]
        sizes_k = [max(0.62, min(1.0, b[2] / (w - 2 * margin))) * look["text_k"] for b in boxes]
        layers = []
        for i, (text, dur) in enumerate(zip(req.scenes, durs)):
            is_cta = i == len(req.scenes) - 1 and text == req.cta and bool(req.cta)
            tw, th = boxes[i][2], boxes[i][3]
            k, lk = sizes_k[i], looks[i]
            block = (Block(text, int(76 * k), True, lk["pill_fg"], line_height=1.25, pill=lk["pill"], pad=(34, 80),
                           align="center")
                     if is_cta else Block(text, int((112 if i == 0 else 84) * k), True, lk["fg"], line_height=1.3,
                                          align="center"))
            if compare[i] and not is_cta:
                lay = Image.new("RGBA", (1, 1), (0, 0, 0, 0))  # drawn per frame
            elif emph[i] and not is_cta:
                lay = self._rich_layer(text, emph[i], int((112 if i == 0 else 84) * k), lk, tw, margin, req.rtl, th)
            else:
                lay = self._layer(tw + 2 * margin, block, req.rtl, margin, max_h=th, shadow=lk["shadow"])
            if is_cta and logo_big is not None:
                lay = _stack(logo_big, lay, 50)
            layers.append((lay, dur, is_cta))

        total = sum(d for _, d, _ in layers)
        # synced captions: per scene, word groups timed on the scene's own clock (None = static scene text)
        groups: list[list | None] = []
        captions, at = [], 0.0
        for i, (text, (_, dur, is_cta)) in enumerate(zip(req.scenes, layers)):
            g = None
            if scene_words[i] and req.captions and not is_cta and not stats[i] and not compare[i]:
                g = self._word_groups(scene_words[i], boxes[i][2], int((112 if i == 0 else 84) * sizes_k[i]), req.rtl) or None
            if g:
                captions += [(at + a, at + min(b, dur), " ".join(x[2] for x in ws)) for a, b, ws in g]
            else:
                captions.append((at, at + dur, text))
            groups.append(g)
            at += dur
        n_frames = int(total * FPS)
        cmd = [ffmpeg_exe(), "-v", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-"]
        music = pick_music(req.music_seed or "|".join(req.scenes))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.mp4"
            fades = f"afade=t=in:d=0.6,afade=t=out:st={max(0.0, total - 1.2):.2f}:d=1.2"
            if music:
                cmd += ["-stream_loop", "-1", "-i", str(music)]
            if narration:
                track = Path(tmp) / "voice.wav"
                track.write_bytes(voice.build_track(narration, total))
                cmd += ["-i", str(track)]
            if music and narration:  # music ducks automatically whenever the voice speaks
                graph = (f"[1:a]{fades},volume=0.45,aresample=44100[m];[2:a]{VOICE_CHAIN},apad,asplit=2[v][sc];"
                         "[m][sc]sidechaincompress=threshold=0.02:ratio=12:attack=15:release=450:makeup=1[md];"
                         f"[md][v]amix=inputs=2:duration=longest:normalize=0,{MASTER}[a]")
            elif music:
                graph = f"[1:a]{fades},volume=0.8[a]"
            elif narration:
                graph = f"[1:a]{VOICE_CHAIN},apad,{MASTER}[a]"
            else:
                cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
                graph = "[1:a]anull[a]"
            cmd += ["-filter_complex", graph, "-map", "0:v", "-map", "[a]", "-t", f"{total:.3f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", str(out)]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                start = 0.0
                frame_no = 0
                prev_last = None  # last frame of the previous scene, for the transition
                glow2 = self._glow(int(w * 0.7), _mix(primary, white, 0.35))
                kind, tr = look["transition"]
                for idx, (layer, dur, is_cta) in enumerate(layers):
                    path = footage[idx]
                    reader = _ClipReader(path, w, h, dur) if path else None
                    dev_cls = {"phone": _Phone, "browser": _Browser}.get(devices[idx])
                    phone = dev_cls(media[idx], geos[idx], dur, focus[idx]) if dev_cls else None
                    # the device slides in unless the same kind was already on screen
                    enters = phone is not None and not (idx > 0 and devices[idx - 1] == devices[idx])
                    cx, cy, tw, _ = boxes[idx]
                    lk = looks[idx]
                    self.look = lk
                    cache: dict = {}
                    try:
                        for f in range(int(round(dur * FPS))):
                            if frame_no >= n_frames:
                                break
                            t = f / FPS
                            base = bg
                            if reader is not None:
                                shot = reader.next()
                                if shot is not None:
                                    base = Image.alpha_composite(shot.convert("RGBA"), shade)
                            lay, enter_t, quick = layer, t, False
                            if stats[idx]:
                                lay = self._stat_layer(stats[idx], t, cache, lk["pill"], w, h, req.rtl)
                            elif compare[idx] and not is_cta:
                                lay = self._compare_layer(compare[idx], t, dur, cache, w, h, margin, req.rtl)
                                enter_t = 10.0  # the cards animate themselves
                            elif groups[idx]:
                                lay, enter_t, quick = self._caption_at(groups[idx], t, cache, idx == 0, lk["pill"],
                                                                       tw + 2 * margin, margin, req.rtl, sizes_k[idx],
                                                                       emph[idx])
                            frame = self._frame(base, glow if lk["glows"] else None, brand, lay, t, dur,
                                                (start + t) / total, is_cta, lk["pill"], w, h, margin,
                                                last=idx == len(layers) - 1, enter_t=enter_t, quick=quick,
                                                center=(cx, cy),
                                                glow2=glow2 if reader is None and lk["glows"] >= 2 else None,
                                                phone=phone.render(t, enters) if phone else None)
                            if prev_last is not None and t < tr:
                                frame = _transition(kind, prev_last, frame, t / tr, w, h, req.rtl)
                            proc.stdin.write(frame.tobytes())
                            last_frame = frame
                            frame_no += 1
                            if on_progress and frame_no % FPS == 0:
                                on_progress(frame_no / n_frames)
                    finally:
                        if reader is not None:
                            reader.close()
                        if phone is not None:
                            phone.close()
                    prev_last = last_frame
                    start += dur
                proc.stdin.close()
            except BrokenPipeError:
                pass
            err = proc.stderr.read().decode("utf-8", "replace")
            if proc.wait() != 0 or not out.exists():
                raise VideoError(f"فشل ترميز الفيديو: {err.strip()[-400:]}")
            data = out.read_bytes()
        return VideoResult(mp4=data, width=w, height=h, duration_ms=int(total * 1000), captions=captions)

    @staticmethod
    def _footage(req: VideoRequest, idx: int, w: int, h: int, dur: float) -> Path | None:
        if not req.broll:
            return None
        query = req.broll[idx] if idx < len(req.broll) else req.broll[-1]
        return broll.fetch(query, w, h, dur, req.music_seed or "|".join(req.scenes))

    @staticmethod
    def _timeline(req: VideoRequest):
        """(scene durations, narration clips [(start_s, wav)], per-scene timed words on the scene's clock).

        With Edge (real word timings) the whole script is read in ONE take, like a voice artist would: the
        intonation flows across scenes and each scene cut follows the moment its first word is spoken.
        Otherwise each scene gets its own clip and lasts as long as it (+ lead-in and a breath)."""
        n = len(req.scenes)
        silent = [_scene_seconds(t) for t in req.scenes], [], [None] * n
        if not req.voice:
            return silent
        try:
            return VideoRenderer._narrated(req)
        except voice.VoiceError as exc:
            if not req.voice.startswith("edge:"):
                raise  # a configured paid/official provider failing must be visible
            log.warning("free edge-tts voice unavailable, video rendered without narration: %s", exc)
            return silent

    @staticmethod
    def _narrated(req: VideoRequest):
        n = len(req.scenes)
        if req.voice.startswith("edge:") and n > 1:
            script = " ".join(_sentence(t) for t in req.scenes)
            wav = voice.synthesize(script, req.voice)
            secs = voice.wav_seconds(wav)
            times, per, at = voice.word_times(script, req.voice, secs), [], 0
            for text in req.scenes:
                toks = text.split()
                per.append([(a, b, tok) for (a, b, _), tok in zip(times[at:at + len(toks)], toks)])
                at += len(toks)
            starts = [0.0]
            for k in range(1, n):  # text appears just before its first word is heard
                first = per[k][0][0] if per[k] else secs
                starts.append(max(starts[-1] + 0.8, VOICE_LEAD + first - 0.12))
            end = max(VOICE_LEAD + secs + 0.9, starts[-1] + 2.2)
            durs = [b - a for a, b in zip(starts, starts[1:] + [end])]
            words = [[(VOICE_LEAD + a - s, VOICE_LEAD + b - s, tok) for a, b, tok in ws] for ws, s in zip(per, starts)]
            return durs, [(VOICE_LEAD, wav)], words
        durs, clips, words, at = [], [], [], 0.0
        for text in req.scenes:
            wav = voice.synthesize(text, req.voice)
            secs = voice.wav_seconds(wav)
            dur = max(_scene_seconds(text), VOICE_LEAD + secs + 0.45)
            clips.append((at + VOICE_LEAD, wav))
            words.append([(VOICE_LEAD + a, VOICE_LEAD + b, tok) for a, b, tok in voice.word_times(text, req.voice, secs)])
            durs.append(dur)
            at += dur
        return durs, clips, words

    # ---------- synced captions ----------
    @staticmethod
    def _word_groups(words, max_w: int, size: int, rtl: bool):
        """Split timed words into on-screen groups of <= CAPTION_WORDS that fit one line: [(start, end, words)]."""
        font = _font(size, True)
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

        def width(ws):
            shaped, kw = _shape(" ".join(x[2] for x in ws), rtl)
            return probe.textlength(shaped, font=font, **kw)

        out, cur = [], []
        for wd in words:
            if cur and (len(cur) >= CAPTION_WORDS or width(cur + [wd]) > max_w):
                out.append(cur)
                cur = []
            cur.append(wd)
        if cur:
            out.append(cur)
        return [(g[0][0], out[i + 1][0][0] if i + 1 < len(out) else g[-1][1], g) for i, g in enumerate(out)]

    def _caption_at(self, groups, t, cache, first, secondary, w, margin, rtl, size_k=1.0, emph=None):
        """(layer, seconds since this group appeared, quick) at scene time t; the spoken word is highlighted."""
        gi = max([i for i, (a, _, _) in enumerate(groups) if t >= a] or [0])
        a, _, ws = groups[gi]
        active = max([j for j, (s, _, _) in enumerate(ws) if t >= s] or [-1])
        if (gi, active) not in cache:
            cache[(gi, active)] = self._caption_layer([x[2] for x in ws], active, int((112 if first else 84) * size_k),
                                                      secondary, w, margin, rtl, emph=emph)
        return cache[(gi, active)], (t if gi == 0 else t - a), gi > 0

    def _caption_layer(self, words: list[str], active: int, size: int, hi, w: int, margin: int, rtl: bool,
                       emph: set[str] | None = None, shrink: bool = True, look: dict | None = None) -> Image.Image:
        """One line of words; the active one sits on a brand-colour pill. Words are shaped one by one so the
        highlight lands on the right word in RTL text."""
        lk = look or getattr(self, "look", None) or themes.build("modern", 2, 2, (0, 0, 0), hi, (0, 0, 0))
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        shaped = [_shape(x, rtl) for x in words]
        font = _font(size, True)
        for _ in range(2):  # second pass only when the line is too wide
            widths = [probe.textlength(sx, font=font, **kw) for sx, kw in shaped]
            gap = probe.textlength(" ", font=font) * 1.3
            total = sum(widths) + gap * (len(words) - 1)
            if total <= w - 2 * margin or not shrink:
                break
            font = _font(max(12, int(font.size * (w - 2 * margin) / total)), True)
        hh = int(font.size * 1.5)
        img = Image.new("RGBA", (w, hh + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        x = (w + total) / 2 if rtl else (w - total) / 2  # RTL: the first word starts on the right
        for j, ((sx, kw), ww) in enumerate(zip(shaped, widths)):
            left = x - ww if rtl else x
            if j == active:
                d.rounded_rectangle((left - 16, 16, left + ww + 16, hh + 24), radius=20, fill=(*hi, 240))
            colour = (*lk["pill_fg"], 255) if j == active else (
                (*lk["emph"], 255) if emph and _bare(words[j]) in emph else (*lk["fg"], 255))
            d.text((left, 20 + hh / 2), sx, font=font, anchor="lm", fill=colour, **kw)
            x = x - ww - gap if rtl else x + ww + gap
        if lk["shadow"]:
            shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            shadow.putalpha(img.getchannel("A").point(lambda v: int(v * 0.5)).filter(ImageFilter.GaussianBlur(10)))
            img = Image.alpha_composite(shadow, img)
        box = img.getbbox()
        return img.crop(box) if box else img

    def _rich_layer(self, text: str, emph: set[str], size: int, look: dict, tw: int, margin: int, rtl: bool,
                    max_h: int) -> Image.Image:
        """Static scene text with the marked words in the accent colour (wrapped, centred, shrunk to fit)."""
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        words = text.split()
        for _ in range(8):
            font = _font(size, True)
            gap = probe.textlength(" ", font=font) * 1.3
            lines, cur, cur_w = [], [], 0.0
            for wd in words:
                sx, kw = _shape(wd, rtl)
                ww = probe.textlength(sx, font=font, **kw)
                if cur and cur_w + gap + ww > tw:
                    lines.append(cur)
                    cur, cur_w = [], 0.0
                cur_w = cur_w + (gap if cur else 0) + ww
                cur.append(wd)
            if cur:
                lines.append(cur)
            imgs = [self._caption_layer(ln, -1, size, look["pill"], tw + 2 * margin, margin, rtl, emph=emph,
                                        shrink=False, look=look) for ln in lines]
            gap = -30 if look["shadow"] else int(size * 0.18)  # shadowed lines carry their own padding
            height = sum(im.height for im in imgs) + gap * (len(imgs) - 1)
            if height <= max_h or size < 40:
                break
            size = int(size * 0.9)
        out = imgs[0]
        for im in imgs[1:]:
            out = _stack(out, im, gap)
        return out

    def _compare_layer(self, spec, t: float, dur: float, cache: dict, w: int, h: int, margin: int, rtl: bool):
        """Before/after: the 'before' card slides in, then the 'after' card pops in and the 'before' one dims."""
        lk = self.look
        if "cmp" not in cache:
            stacked = h > w
            cw = w - 2 * margin if stacked else (w - 2 * margin - 60) // 2
            ch = int(h * 0.21) if stacked else int(h * 0.56)
            light = lk["fg"] != themes.WHITE  # light theme
            before_bg, before_fg = ((229, 232, 236), themes.INK_DARK) if light else ((48, 52, 60), (236, 238, 242))
            labels = ("قبل", "بعد") if rtl else ("Before", "After")
            cards = []
            for text, label, bgc, fgc, icon in (
                    (spec[0], labels[0], before_bg, before_fg, themes.icon_cross(int(min(cw, ch) * 0.26), (226, 72, 72), (255, 255, 255))),
                    (spec[1], labels[1], lk["pill"], lk["pill_fg"], themes.icon_check(int(min(cw, ch) * 0.26), lk["pill_fg"], lk["pill"]))):
                card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                ImageDraw.Draw(card).rounded_rectangle((0, 0, cw - 1, ch - 1), int(min(cw, ch) * 0.12), fill=(*bgc, 240))
                head = self._layer(cw, Block(label, int(min(cw, ch) * 0.13), True, fgc, line_height=1.2), rtl, 40, shadow=False)
                body = self._layer(cw, Block(text, int(min(w, h) * 0.055), True, fgc, line_height=1.35, align="center"),
                                   rtl, 50, max_h=int(ch - icon.height - head.height - 60), shadow=False)
                top = Image.new("RGBA", (cw - 80, max(icon.height, head.height)), (0, 0, 0, 0))
                ix = top.width - icon.width if rtl else 0
                top.alpha_composite(icon, (ix, (top.height - icon.height) // 2))
                hx = ix - head.width - 20 if rtl else icon.width + 20
                top.alpha_composite(head, (max(0, hx), (top.height - head.height) // 2))
                card.alpha_composite(top, (40, 30))
                card.alpha_composite(body, ((cw - body.width) // 2, 30 + top.height + max(10, (ch - 30 - top.height - body.height) // 2 - 10)))
                cards.append(card)
            gap = 60
            size = (cw, 2 * ch + gap) if stacked else (2 * cw + gap, ch)
            pos = [(0, 0), (0, ch + gap)] if stacked else ([(cw + gap, 0), (0, 0)] if rtl else [(0, 0), (cw + gap, 0)])
            cache["cmp"] = (cards, size, pos)
        cards, size, pos = cache["cmp"]
        out = Image.new("RGBA", size, (0, 0, 0, 0))
        t_after = min(max(1.0, dur * 0.42), max(0.6, dur - 0.9))
        a1 = _ease_out(t / 0.5)
        b = max(0.0, (t - t_after) / 0.45)
        dim = 1 - 0.35 * _smooth((t - t_after) / 0.4)
        c0 = self._fade(cards[0], a1 * dim)
        out.alpha_composite(c0, (pos[0][0], pos[0][1] + int((1 - a1) * 40)))
        if b > 0:
            sc = 0.85 + 0.15 * _ease_back(min(1.0, b))
            c1 = cards[1].resize((max(1, int(cards[1].width * sc)), max(1, int(cards[1].height * sc))), Image.BILINEAR)
            c1 = self._fade(c1, min(1.0, b * 1.5))
            out.alpha_composite(c1, (pos[1][0] + (cards[1].width - c1.width) // 2, pos[1][1] + (cards[1].height - c1.height) // 2))
        return out

    def _stat_layer(self, stat, t: float, cache: dict, hi, w: int, h: int, rtl: bool) -> Image.Image:
        """Animated stat: the number counts up inside a filling ring, the label sits underneath."""
        p = _ease_out(max(0.0, t - 0.15) / 1.3)
        pop = 0.75 + 0.25 * _ease_back(t / 0.5)  # spring-in
        key = ("stat", _stat_text(stat, p), round(p, 2), round(pop, 2))
        if key in cache:
            return cache[key]
        side = int(min(w, h) * 0.62)
        size = int(side * 0.30)
        img = Image.new("RGBA", (side + 40, side + 40), (0, 0, 0, 0))
        d = ImageDraw.Draw(img, "RGBA")
        box = (20 + 14, 20 + 14, 20 + side - 14, 20 + side - 14)
        fg = self.look["fg"] if getattr(self, "look", None) else (255, 255, 255)
        d.ellipse(box, outline=(*fg, 55), width=22)
        if p > 0:
            d.arc(box, -90, -90 + 360 * p, fill=(*hi, 255), width=22)
        txt = _stat_text(stat, p)
        font = _font(size, True)
        while d.textlength(txt, font=font) > side * 0.78 and size > 30:
            size = int(size * 0.9)
            font = _font(size, True)
        sx, kw = _shape(txt, rtl)
        d.text((img.width / 2, img.height / 2), sx, font=font, anchor="mm", fill=(*fg, 255), **kw)
        if stat[4]:  # label
            label = self._layer(w, Block(stat[4], int(min(w, h) * 0.06), True, fg, line_height=1.3,
                                         align="center"), rtl, 90, max_h=int(h * 0.2),
                                shadow=self.look["shadow"] if getattr(self, "look", None) else True)
            img = _stack(img, label, 30)
        if pop < 0.999:
            img = img.resize((max(1, int(img.width * pop)), max(1, int(img.height * pop))), Image.BILINEAR)
        cache[key] = img
        return img

    # ---------- drawing ----------
    @staticmethod
    def _layer(w: int, block: Block, rtl: bool, margin: int, max_h: int | None = None, shadow: bool = True) -> Image.Image:
        """Text block on a transparent layer, cropped to its content; shrinks to fit max_h."""
        probe = Image.new("RGBA", (w, 10))
        c = _Canvas(probe, rtl, margin, w - margin)
        scale = 1.0
        while max_h and c.height(block, scale) > max_h and scale > 0.4:
            scale *= 0.92
        hh = max(1, c.height(block, scale)) + 20
        img = Image.new("RGBA", (w, hh + 40), (0, 0, 0, 0))
        _Canvas(img, rtl, margin, w - margin).paint(block, 20, scale)
        if block.pill is None and shadow:  # soft shadow keeps text readable over bright footage
            shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            shadow.putalpha(img.getchannel("A").point(lambda v: int(v * 0.55)).filter(ImageFilter.GaussianBlur(10)))
            img = Image.alpha_composite(shadow, img)
        box = img.getbbox()
        return img.crop(box) if box else img

    @staticmethod
    def _glow(size: int, color) -> Image.Image:
        g = Image.new("L", (size, size), 0)
        ImageDraw.Draw(g).ellipse((size * 0.2, size * 0.2, size * 0.8, size * 0.8), fill=90)
        g = g.filter(ImageFilter.GaussianBlur(size // 8))
        layer = Image.new("RGBA", (size, size), (*color, 0))
        layer.putalpha(g)
        return layer

    @staticmethod
    def _fade(layer: Image.Image, alpha: float) -> Image.Image:
        if alpha >= 0.999:
            return layer
        a = layer.getchannel("A").point(lambda v: int(v * alpha))
        out = layer.copy()
        out.putalpha(a)
        return out

    def _frame(self, bg, glow, brand, layer, t, dur, progress, is_cta, secondary, w, h, margin, last,
               enter_t=None, quick=False, center=None, glow2=None, phone=None):
        frame = bg.copy().convert("RGBA")
        # drifting lights (continuous across scenes)
        lk = getattr(self, "look", None) or {"glow_speed": 1.0, "enter": (0.55, 70, False), "bar_track": (255, 255, 255, 60)}
        gt = progress * 2 * math.pi * lk["glow_speed"]
        if glow is not None:
            gx = int(w * 0.5 + w * 0.35 * math.sin(gt) - glow.width / 2)
            gy = int(h * 0.45 + h * 0.25 * math.cos(gt * 0.7) - glow.height / 2)
            frame.paste(glow, (gx, gy), glow)  # paste (not alpha_composite) tolerates off-canvas positions
        if glow2 is not None:
            gx2 = int(w * 0.5 + w * 0.4 * math.cos(gt * 1.3 + 1.7) - glow2.width / 2)
            gy2 = int(h * 0.55 + h * 0.3 * math.sin(gt * 0.9 + 0.6) - glow2.height / 2)
            frame.paste(glow2, (gx2, gy2), glow2)
        if phone is not None:
            img, (px, py) = phone
            frame.paste(img, (px, py), img)
        if brand is not None:
            frame.alpha_composite(brand, (w - margin - brand.width, margin) if brand.width < w else (0, margin))
        # scene text: slide up + fade in, fade out at the end (the last scene stays)
        enter_t = t if enter_t is None else enter_t
        e_s, e_dy, e_pop = lk["enter"]
        enter = _ease_out(enter_t / (0.18 if quick else e_s))  # later caption groups pop in quickly
        leave = 1.0 if last else min(1.0, max(0.0, (dur - t) / 0.35))
        alpha = enter * leave
        dy = int((1 - enter) * (25 if quick else e_dy))
        lay = layer
        if e_pop and not quick and enter_t < e_s:  # energetic: text springs in
            sc = 0.7 + 0.3 * _ease_back(enter_t / e_s)
            lay = layer.resize((max(1, int(layer.width * sc)), max(1, int(layer.height * sc))), Image.BILINEAR)
        if is_cta:  # gentle pulse
            s = 1 + 0.035 * math.sin(max(0.0, t - 0.55) * 2 * math.pi / 1.2)
            lay = lay.resize((max(1, int(lay.width * s)), max(1, int(lay.height * s))), Image.BILINEAR)
        lay = self._fade(lay, alpha)
        cx, cy = center or (w // 2, h // 2)
        x, y = cx - lay.width // 2, cy - lay.height // 2 + dy
        frame.paste(lay, (x, y), lay)
        # progress bar
        bar_y = h - margin // 2
        d = ImageDraw.Draw(frame, "RGBA")
        d.rounded_rectangle((margin, bar_y, w - margin, bar_y + 8), radius=4, fill=lk["bar_track"])
        filled = int((w - 2 * margin) * progress)
        if filled > 0:  # grows from the right (RTL)
            d.rounded_rectangle((w - margin - filled, bar_y, w - margin, bar_y + 8), radius=4, fill=(*secondary, 230))
        return frame.convert("RGB")
