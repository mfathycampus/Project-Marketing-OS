"""Short social videos (Reels / TikTok / Shorts / square) rendered with Pillow + FFmpeg.

No browser and no paid API: each scene is drawn with the same Arabic-aware text engine as the images,
animated frame by frame (fade/slide-in, drifting light, progress bar, pulsing CTA) and encoded to H.264/AAC.
FFmpeg comes from the imageio-ffmpeg wheel, so `pip install` is enough on Windows too.
"""
import hashlib
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.designs.pillow_renderer import DARK, INK, Block, _Canvas, _gradient, _rgb
from app.video import broll, voice

FPS = 30
VOICE_LEAD = 0.3  # narration starts shortly after the scene text begins to appear
MUSIC_DIR = Path(__file__).parent / "music"
AUDIO_EXT = (".mp3", ".m4a", ".aac", ".wav", ".ogg")
SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
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
    voice: str = ""  # Azure voice name (e.g. ar-SA-HamedNeural); empty = no narration
    broll: list[str] = field(default_factory=list)  # Pexels search term per scene; empty = brand gradient


@dataclass
class VideoResult:
    mp4: bytes
    width: int
    height: int
    duration_ms: int


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

    def render(self, req: VideoRequest) -> VideoResult:
        w, h = SIZES.get(req.aspect, SIZES["9:16"])
        primary, secondary = _rgb(req.primary_color, "#0F766E"), _rgb(req.secondary_color, "#F59E0B")
        ink, dark, white = _rgb(INK, INK), _rgb(DARK, DARK), (255, 255, 255)
        margin = 90

        bg = _gradient(w, h, primary, dark, diagonal=False)
        shade = bg.convert("RGBA")  # brand-tinted darkening over footage keeps the text readable
        shade.putalpha(165)
        glow = self._glow(int(w * 0.9), secondary)
        brand = self._layer(w, Block(req.brand_name, 44, alpha=0.85), req.rtl, margin) if req.brand_name else None

        clips = self._voice_clips(req)  # [(wav, seconds)] per scene, empty when no voice
        layers = []
        for i, text in enumerate(req.scenes):
            is_cta = i == len(req.scenes) - 1 and text == req.cta and bool(req.cta)
            block = (Block(text, 76, True, ink, line_height=1.25, pill=secondary, pad=(34, 80), align="center")
                     if is_cta else Block(text, 112 if i == 0 else 84, True, white, line_height=1.3,
                                          align="center"))
            dur = _scene_seconds(text)
            if clips:  # the scene lasts as long as its narration (+ lead-in and a breath)
                dur = max(dur, VOICE_LEAD + clips[i][1] + 0.45)
            layers.append((self._layer(w, block, req.rtl, margin, max_h=int(h * 0.6)), dur, is_cta))

        total = sum(d for _, d, _ in layers)
        n_frames = int(total * FPS)
        cmd = [ffmpeg_exe(), "-v", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-"]
        music = pick_music(req.music_seed or "|".join(req.scenes))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.mp4"
            fades = f"afade=t=in:d=0.6,afade=t=out:st={max(0.0, total - 1.2):.2f}:d=1.2"
            if music:
                cmd += ["-stream_loop", "-1", "-i", str(music)]
            if clips:
                starts, at = [], 0.0
                for _, d, _ in layers:
                    starts.append(at + VOICE_LEAD)
                    at += d
                track = Path(tmp) / "voice.wav"
                track.write_bytes(voice.build_track([(st, c[0]) for st, c in zip(starts, clips)], total))
                cmd += ["-i", str(track)]
            if music and clips:  # music ducked under the narration
                graph = f"[1:a]{fades},volume=0.18[m];[2:a]apad[v];[m][v]amix=inputs=2:duration=longest:normalize=0[a]"
            elif music:
                graph = f"[1:a]{fades},volume=0.8[a]"
            elif clips:
                graph = "[1:a]apad[a]"
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
                            frame = self._frame(base, glow, brand, layer, t, dur, (start + t) / total, is_cta,
                                                secondary, w, h, margin, last=idx == len(layers) - 1)
                            proc.stdin.write(frame.tobytes())
                            frame_no += 1
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
        return VideoResult(mp4=data, width=w, height=h, duration_ms=int(total * 1000))

    @staticmethod
    def _footage(req: VideoRequest, idx: int, w: int, h: int, dur: float) -> Path | None:
        if not req.broll:
            return None
        query = req.broll[idx] if idx < len(req.broll) else req.broll[-1]
        return broll.fetch(query, w, h, dur, req.music_seed or "|".join(req.scenes))

    @staticmethod
    def _voice_clips(req: VideoRequest) -> list[tuple[bytes, float]]:
        if not req.voice:
            return []
        out = []
        for text in req.scenes:
            data = voice.synthesize(text, req.voice)
            out.append((data, voice.wav_seconds(data)))
        return out

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

    def _frame(self, bg, glow, brand, layer, t, dur, progress, is_cta, secondary, w, h, margin, last):
        frame = bg.copy().convert("RGBA")
        # drifting light (continuous across scenes)
        gt = progress * 2 * math.pi
        gx = int(w * 0.5 + w * 0.35 * math.sin(gt) - glow.width / 2)
        gy = int(h * 0.45 + h * 0.25 * math.cos(gt * 0.7) - glow.height / 2)
        frame.paste(glow, (gx, gy), glow)  # paste (not alpha_composite) tolerates off-canvas positions
        if brand is not None:
            frame.alpha_composite(brand, (w - margin - brand.width, margin) if brand.width < w else (0, margin))
        # scene text: slide up + fade in, fade out at the end (the last scene stays)
        enter = _ease_out(t / 0.55)
        leave = 1.0 if last else min(1.0, max(0.0, (dur - t) / 0.35))
        alpha = enter * leave
        dy = int((1 - enter) * 70)
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
