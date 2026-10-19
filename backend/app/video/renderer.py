"""Short social videos (Reels / TikTok / Shorts / square) rendered with Pillow + FFmpeg.

No browser and no paid API: each scene is drawn with the same Arabic-aware text engine as the images,
animated frame by frame (fade/slide-in, drifting light, progress bar, pulsing CTA) and encoded to H.264/AAC.
FFmpeg comes from the imageio-ffmpeg wheel, so `pip install` is enough on Windows too.
"""
import hashlib
import logging
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter

from app.designs.pillow_renderer import DARK, INK, Block, _Canvas, _font, _gradient, _rgb, _shape
from app.video import broll, voice

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

    def __init__(self, path: str, geo: dict, seconds: float):
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
        if self.reader is not None:
            shot = self.reader.next()
            return shot if shot is not None else Image.new("RGB", (self.sw, self.sh), (0, 0, 0))
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

    def render(self, t: float, enters: bool):
        img = self.body.copy()
        img.paste(self._screen(t), (self.pad + self.bezel, self.pad + self.bezel), self.mask)
        x, y, iw, ih = self.island
        ImageDraw.Draw(img).rounded_rectangle((x, y, x + iw, y + ih), ih // 2, fill=(8, 8, 10, 255))
        dy = int(math.sin(t * 2 * math.pi / 3.2) * 8)  # gentle float
        if enters:
            dy += int((1 - _ease_back(t / 0.7)) * self.ph * 0.6)
        return img, (self.px - self.pad, self.py - self.pad + dy)

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()


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

        bg = _gradient(w, h, primary, dark, diagonal=False)
        shade = bg.convert("RGBA")  # brand-tinted darkening over footage keeps the text readable
        shade.putalpha(165)
        glow = self._glow(int(w * 0.9), secondary)
        brand = self._layer(w, Block(req.brand_name, 44, alpha=0.85), req.rtl, margin) if req.brand_name else None

        durs, narration, scene_words = self._timeline(req)
        media = [(req.media[i] if i < len(req.media) else "") or "" for i in range(len(req.scenes))]
        geo = _phone_layout(w, h, margin, req.rtl)
        # per scene: (text centre x, text centre y, text width, text max height); scenes with app media leave room
        boxes = [(geo["text"] if m else (w // 2, h // 2, w - 2 * margin, int(h * 0.6))) for m in media]
        sizes_k = [max(0.62, min(1.0, b[2] / (w - 2 * margin))) for b in boxes]  # narrow text column -> smaller type
        layers = []
        for i, (text, dur) in enumerate(zip(req.scenes, durs)):
            is_cta = i == len(req.scenes) - 1 and text == req.cta and bool(req.cta)
            tw, th = boxes[i][2], boxes[i][3]
            k = sizes_k[i]
            block = (Block(text, int(76 * k), True, ink, line_height=1.25, pill=secondary, pad=(34, 80), align="center")
                     if is_cta else Block(text, int((112 if i == 0 else 84) * k), True, white, line_height=1.3,
                                          align="center"))
            layers.append((self._layer(tw + 2 * margin, block, req.rtl, margin, max_h=th), dur, is_cta))

        total = sum(d for _, d, _ in layers)
        # synced captions: per scene, word groups timed on the scene's own clock (None = static scene text)
        groups: list[list | None] = []
        captions, at = [], 0.0
        for i, (text, (_, dur, is_cta)) in enumerate(zip(req.scenes, layers)):
            g = None
            if scene_words[i] and req.captions and not is_cta:
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
                for idx, (layer, dur, is_cta) in enumerate(layers):
                    path = self._footage(req, idx, w, h, dur)
                    reader = _ClipReader(path, w, h, dur) if path else None
                    phone = _Phone(media[idx], geo, dur) if media[idx] else None
                    enters = phone is not None and not (idx > 0 and media[idx - 1])  # slides in unless already shown
                    cx, cy, tw, _ = boxes[idx]
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
                            if groups[idx]:
                                lay, enter_t, quick = self._caption_at(groups[idx], t, cache, idx == 0, secondary,
                                                                       tw + 2 * margin, margin, req.rtl, sizes_k[idx])
                            frame = self._frame(base, glow, brand, lay, t, dur, (start + t) / total, is_cta,
                                                secondary, w, h, margin, last=idx == len(layers) - 1,
                                                enter_t=enter_t, quick=quick, center=(cx, cy),
                                                glow2=glow2 if reader is None else None,
                                                phone=phone.render(t, enters) if phone else None)
                            if prev_last is not None and t < TRANSITION:  # zoom-through crossfade between scenes
                                a = _ease_out(t / TRANSITION)
                                zoom = 1 + 0.08 * a
                                big = prev_last.resize((int(w * zoom), int(h * zoom)), Image.BILINEAR)
                                ox, oy = (big.width - w) // 2, (big.height - h) // 2
                                frame = Image.blend(big.crop((ox, oy, ox + w, oy + h)), frame, a)
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

    def _caption_at(self, groups, t, cache, first, secondary, w, margin, rtl, size_k=1.0):
        """(layer, seconds since this group appeared, quick) at scene time t; the spoken word is highlighted."""
        gi = max([i for i, (a, _, _) in enumerate(groups) if t >= a] or [0])
        a, _, ws = groups[gi]
        active = max([j for j, (s, _, _) in enumerate(ws) if t >= s] or [-1])
        if (gi, active) not in cache:
            cache[(gi, active)] = self._caption_layer([x[2] for x in ws], active, int((112 if first else 84) * size_k),
                                                      secondary, w, margin, rtl)
        return cache[(gi, active)], (t if gi == 0 else t - a), gi > 0

    @staticmethod
    def _caption_layer(words: list[str], active: int, size: int, hi, w: int, margin: int, rtl: bool) -> Image.Image:
        """One line of words; the active one sits on a brand-colour pill. Words are shaped one by one so the
        highlight lands on the right word in RTL text."""
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        shaped = [_shape(x, rtl) for x in words]
        font = _font(size, True)
        for _ in range(2):  # second pass only when the line is too wide
            widths = [probe.textlength(sx, font=font, **kw) for sx, kw in shaped]
            gap = probe.textlength(" ", font=font) * 1.3
            total = sum(widths) + gap * (len(words) - 1)
            if total <= w - 2 * margin:
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
            d.text((left, 20 + hh / 2), sx, font=font, anchor="lm",
                   fill=(*_rgb(INK, INK), 255) if j == active else (255, 255, 255, 255), **kw)
            x = x - ww - gap if rtl else x + ww + gap
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        shadow.putalpha(img.getchannel("A").point(lambda v: int(v * 0.5)).filter(ImageFilter.GaussianBlur(10)))
        img = Image.alpha_composite(shadow, img)
        box = img.getbbox()
        return img.crop(box) if box else img

    # ---------- drawing ----------
    @staticmethod
    def _layer(w: int, block: Block, rtl: bool, margin: int, max_h: int | None = None) -> Image.Image:
        """Text block on a transparent layer, cropped to its content; shrinks to fit max_h."""
        probe = Image.new("RGBA", (w, 10))
        c = _Canvas(probe, rtl, margin, w - margin)
        scale = 1.0
        while max_h and c.height(block, scale) > max_h and scale > 0.4:
            scale *= 0.92
        hh = max(1, c.height(block, scale)) + 20
        img = Image.new("RGBA", (w, hh + 40), (0, 0, 0, 0))
        _Canvas(img, rtl, margin, w - margin).paint(block, 20, scale)
        if block.pill is None:  # soft shadow keeps text readable over bright footage
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
        gt = progress * 2 * math.pi
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
        enter = _ease_out(enter_t / (0.18 if quick else 0.55))  # later caption groups pop in quickly
        leave = 1.0 if last else min(1.0, max(0.0, (dur - t) / 0.35))
        alpha = enter * leave
        dy = int((1 - enter) * (25 if quick else 70))
        lay = layer
        if is_cta:  # gentle pulse
            s = 1 + 0.035 * math.sin(max(0.0, t - 0.55) * 2 * math.pi / 1.2)
            lay = layer.resize((max(1, int(layer.width * s)), max(1, int(layer.height * s))), Image.BILINEAR)
        lay = self._fade(lay, alpha)
        cx, cy = center or (w // 2, h // 2)
        x, y = cx - lay.width // 2, cy - lay.height // 2 + dy
        frame.paste(lay, (x, y), lay)
        # progress bar
        bar_y = h - margin // 2
        d = ImageDraw.Draw(frame, "RGBA")
        d.rounded_rectangle((margin, bar_y, w - margin, bar_y + 8), radius=4, fill=(255, 255, 255, 60))
        filled = int((w - 2 * margin) * progress)
        if filled > 0:  # grows from the right (RTL)
            d.rounded_rectangle((w - margin - filled, bar_y, w - margin, bar_y + 8), radius=4, fill=(*secondary, 230))
        return frame.convert("RGB")
