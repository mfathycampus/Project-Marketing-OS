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
        layers = []
        for i, (text, dur) in enumerate(zip(req.scenes, durs)):
            is_cta = i == len(req.scenes) - 1 and text == req.cta and bool(req.cta)
            block = (Block(text, 76, True, ink, line_height=1.25, pill=secondary, pad=(34, 80), align="center")
                     if is_cta else Block(text, 112 if i == 0 else 84, True, white, line_height=1.3,
                                          align="center"))
            layers.append((self._layer(w, block, req.rtl, margin, max_h=int(h * 0.6)), dur, is_cta))

        total = sum(d for _, d, _ in layers)
        # synced captions: per scene, word groups timed on the scene's own clock (None = static scene text)
        groups: list[list | None] = []
        captions, at = [], 0.0
        for i, (text, (_, dur, is_cta)) in enumerate(zip(req.scenes, layers)):
            g = None
            if scene_words[i] and req.captions and not is_cta:
                g = self._word_groups(scene_words[i], w - 2 * margin, 112 if i == 0 else 84, req.rtl) or None
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
                for idx, (layer, dur, is_cta) in enumerate(layers):
                    path = self._footage(req, idx, w, h, dur)
                    reader = _ClipReader(path, w, h, dur) if path else None
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
                                                                       w, margin, req.rtl)
                            frame = self._frame(base, glow, brand, lay, t, dur, (start + t) / total, is_cta,
                                                secondary, w, h, margin, last=idx == len(layers) - 1,
                                                enter_t=enter_t, quick=quick)
                            proc.stdin.write(frame.tobytes())
                            frame_no += 1
                            if on_progress and frame_no % FPS == 0:
                                on_progress(frame_no / n_frames)
                    finally:
                        if reader is not None:
                            reader.close()
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

    def _caption_at(self, groups, t, cache, first, secondary, w, margin, rtl):
        """(layer, seconds since this group appeared, quick) at scene time t; the spoken word is highlighted."""
        gi = max([i for i, (a, _, _) in enumerate(groups) if t >= a] or [0])
        a, _, ws = groups[gi]
        active = max([j for j, (s, _, _) in enumerate(ws) if t >= s] or [-1])
        if (gi, active) not in cache:
            cache[(gi, active)] = self._caption_layer([x[2] for x in ws], active, 112 if first else 84, secondary,
                                                      w, margin, rtl)
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
               enter_t=None, quick=False):
        frame = bg.copy().convert("RGBA")
        # drifting light (continuous across scenes)
        gt = progress * 2 * math.pi
        gx = int(w * 0.5 + w * 0.35 * math.sin(gt) - glow.width / 2)
        gy = int(h * 0.45 + h * 0.25 * math.cos(gt * 0.7) - glow.height / 2)
        frame.paste(glow, (gx, gy), glow)  # paste (not alpha_composite) tolerates off-canvas positions
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
        x, y = (w - lay.width) // 2, (h - lay.height) // 2 + dy
        frame.paste(lay, (x, y), lay)
        # progress bar
        bar_y = h - margin // 2
        d = ImageDraw.Draw(frame, "RGBA")
        d.rounded_rectangle((margin, bar_y, w - margin, bar_y + 8), radius=4, fill=(255, 255, 255, 60))
        filled = int((w - 2 * margin) * progress)
        if filled > 0:  # grows from the right (RTL)
            d.rounded_rectangle((w - margin - filled, bar_y, w - margin, bar_y + 8), radius=4, fill=(*secondary, 230))
        return frame.convert("RGB")
