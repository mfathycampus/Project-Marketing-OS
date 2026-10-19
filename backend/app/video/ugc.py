"""'Your face' videos (UGC style): you record yourself talking, the system edits it like a social-media editor.

1. Speech -> words with timings (faster-whisper, local).
2. Jump cuts: pauses and dead air between phrases are cut out; every cut alternates a slight punch-in zoom.
3. Synced captions (spoken word highlighted), a hook title for the first seconds.
4. Overlays planned by Claude on the transcript: your app screens in a phone / browser window, animated numbers,
   punchy text, while your voice keeps going.
5. Voice clean-up (EQ + compression), ducked music, loudness for social, end card with logo / link / offer.
"""
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image, ImageFilter

from app.designs.pillow_renderer import Block, _rgb
from app.video import themes
from app.video.renderer import (FPS, MASTER, SIZES, VOICE_CHAIN, VideoError, VideoRenderer, VideoRequest, VideoResult,
                                _Browser, _browser_layout, _ClipReader, _ease_out, _media_size, _Phone, _phone_layout,
                                _stack, ffmpeg_exe, load_logo, parse_emphasis, parse_stat)

log = logging.getLogger(__name__)
MAX_PAUSE = 0.32  # longer pauses between words are cut out
PAD_IN, PAD_OUT = 0.07, 0.12  # keep a little air around each phrase
END_CARD_S = 2.6
HOOK_TITLE_S = 2.6


@dataclass
class UgcRequest:
    video: str
    words: list = field(default_factory=list)  # [(start, end, word)] on the ORIGINAL timeline
    overlays: list = field(default_factory=list)  # [{"start": s, "end": s, "kind": media|stat|text, "value": ..., "focus": (x,y)|None}]
    hook_text: str = ""
    cta: str = ""
    brand_name: str = ""
    primary_color: str = "#0F766E"
    secondary_color: str = "#F59E0B"
    logo: str = ""
    style: str = "modern"
    music: str = "auto"
    music_seed: str = ""
    end_url: str = ""
    end_offer: str = ""
    captions: bool = True
    jump_cuts: bool = True
    punch_zoom: bool = True
    aspect: str = "9:16"
    rtl: bool = True


def probe(path: str) -> tuple[float, bool]:
    """(duration in seconds, has audio)."""
    info = subprocess.run([ffmpeg_exe(), "-v", "info", "-i", path, "-f", "null", "-t", "0.05", "-"],
                          capture_output=True).stderr.decode("utf-8", "replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if not m:
        raise VideoError("تعذّر قراءة الفيديو المرفوع")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]), "Audio:" in info


def keep_segments(words: list, duration: float, jump_cuts: bool = True) -> list[tuple[float, float]]:
    """Parts of the original video to keep: the spoken phrases, without the long pauses between them."""
    if not words or not jump_cuts:
        return [(0.0, duration)]
    segs = []
    s, e = max(0.0, words[0][0] - PAD_IN), words[0][1] + PAD_OUT
    for a, b, _ in words[1:]:
        if a - PAD_IN > e + (MAX_PAUSE - PAD_IN - PAD_OUT):
            segs.append((s, min(e, duration)))
            s = max(e, a - PAD_IN)
        e = max(e, b + PAD_OUT)
    segs.append((s, min(e, duration)))
    return [(a, b) for a, b in segs if b - a > 0.05]


def remap(t: float, segs: list[tuple[float, float]]) -> float:
    """Original time -> time in the cut video."""
    acc = 0.0
    for a, b in segs:
        if t < a:
            return acc
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return acc


def _cut(src: str, segs: list, w: int, h: int, tmp: Path) -> tuple[Path, Path]:
    """Cut + join the kept parts; video scaled/cropped to the frame, speech as WAV."""
    parts, labels = [], []
    for i, (a, b) in enumerate(segs):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}];"
                     f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(segs)}:v=1:a=1[vc][ac];" \
        f"[vc]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS},setsar=1[v]"
    script = tmp / "cut.txt"
    script.write_text(graph, encoding="utf-8")
    video, speech = tmp / "cut.mp4", tmp / "speech.wav"
    proc = subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", src, "-filter_complex_script", str(script),
                           "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-an", str(video),
                           "-map", "[ac]", "-ac", "1", "-ar", "44100", str(speech)], capture_output=True)
    if proc.returncode != 0:
        raise VideoError("فشل قص الفيديو: " + proc.stderr.decode("utf-8", "replace")[-300:])
    return video, speech


def _zoom(frame: Image.Image, k: float, w: int, h: int) -> Image.Image:
    """Punch-in around the upper middle (where faces usually are)."""
    if k <= 1.001:
        return frame
    cw, ch = int(w / k), int(h / k)
    x0, y0 = (w - cw) // 2, int((h - ch) * 0.4)
    return frame.crop((x0, y0, x0 + cw, y0 + ch)).resize((w, h), Image.BILINEAR)


def _dim(frame: Image.Image, amount: float = 0.55) -> Image.Image:
    """Speaker behind an overlay: soft blur + darken (cheap: blur a small copy)."""
    w, h = frame.size
    small = frame.resize((w // 8, h // 8), Image.BILINEAR).filter(ImageFilter.GaussianBlur(2))
    return Image.blend(small.resize((w, h), Image.BILINEAR), Image.new("RGB", (w, h), (0, 0, 0)), amount)


def render_ugc(req: UgcRequest, on_progress: Callable[[float], None] | None = None) -> VideoResult:
    w, h = SIZES.get(req.aspect, SIZES["9:16"])
    margin = 90
    duration, has_audio = probe(req.video)
    if not has_audio:
        raise VideoError("الفيديو المرفوع بلا صوت")
    primary, secondary = _rgb(req.primary_color, "#0F766E"), _rgb(req.secondary_color, "#F59E0B")
    dark = _rgb("#0b1f1d", "#0b1f1d")
    look = themes.on_footage(themes.build(req.style, w, h, primary, secondary, dark))  # text over a real video
    vr = VideoRenderer()
    vr.look = look
    segs = keep_segments(req.words, duration, req.jump_cuts)
    cut_len = sum(b - a for a, b in segs)
    words = [(remap(a, segs), remap(b, segs), wd) for a, b, wd in req.words]
    overlays = []
    for o in req.overlays:
        s, e = remap(o["start"], segs), remap(o["end"], segs)
        if e - s >= 0.6:
            overlays.append({**o, "start": s, "end": e})
    groups = vr._word_groups(words, w - 2 * margin, 84, req.rtl) if (req.captions and words) else []
    logo_small = load_logo(req.logo, int(w * 0.32), int(min(w, h) * 0.085)) if req.logo else None
    logo_big = load_logo(req.logo, int(w * 0.5), int(min(w, h) * 0.2)) if req.logo else None
    brand = logo_small or (vr._layer(w, Block(req.brand_name, 44, (255, 255, 255), alpha=0.9), req.rtl, margin)
                           if req.brand_name else None)
    hook = None
    if req.hook_text:
        text, emph = parse_emphasis(req.hook_text)
        hook = vr._rich_layer(text, emph, 92, look, w - 2 * margin, margin, req.rtl, int(h * 0.22))
    end_card = None
    if req.cta or req.end_url or req.end_offer or logo_big is not None:
        cta = vr._layer(w, Block(req.cta or "جرّب الآن", 76, True, look["pill_fg"], line_height=1.25, pill=look["pill"],
                                 pad=(34, 80), align="center"), req.rtl, margin)
        card = _stack(logo_big, cta, 50) if logo_big is not None else cta
        end_card = vr._end_card(card, VideoRequest(scenes=["x"], end_url=req.end_url, end_offer=req.end_offer,
                                                   rtl=req.rtl), look, w, h, margin)
    # the hook title gives way to the first overlay (they share the top of the frame)
    hook_end = min([HOOK_TITLE_S] + [o["start"] for o in overlays])
    total = cut_len + (END_CARD_S if end_card is not None else 0.0)
    n_cut = int(cut_len * FPS)
    bounds, acc = [], 0.0
    for a, b in segs:
        acc += b - a
        bounds.append(acc)

    with tempfile.TemporaryDirectory() as tmpd:
        tmp = Path(tmpd)
        video, speech = _cut(req.video, segs, w, h, tmp)
        music = VideoRenderer._music(VideoRequest(scenes=[req.video], music=req.music,
                                                  music_seed=req.music_seed or req.video), total, look["name"], tmp)
        out = tmp / "out.mp4"
        cmd = [ffmpeg_exe(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
               "-r", str(FPS), "-i", "-", "-i", str(speech)]
        if music:
            cmd += ["-stream_loop", "-1", "-i", str(music)]
            fades = f"afade=t=in:d=0.6,afade=t=out:st={max(0.0, total - 1.2):.2f}:d=1.2"
            graph = (f"[2:a]{fades},volume=0.4,aresample=44100[m];[1:a]{VOICE_CHAIN},apad,asplit=2[v][sc];"
                     "[m][sc]sidechaincompress=threshold=0.02:ratio=12:attack=15:release=450:makeup=1[md];"
                     f"[md][v]amix=inputs=2:duration=longest:normalize=0,{MASTER}[a]")
        else:
            graph = f"[1:a]{VOICE_CHAIN},apad,{MASTER}[a]"
        cmd += ["-filter_complex", graph, "-map", "0:v", "-map", "[a]", "-t", f"{total:.3f}", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                "-ar", "44100", "-ac", "2", "-movflags", "+faststart", str(out)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        reader = _ClipReader(video, w, h, cut_len)
        devices: dict = {}
        cache: dict = {}
        try:
            n_total = int(total * FPS)
            for i in range(n_total):
                t = i / FPS
                if i < n_cut:
                    frame = vr_frame = reader.next() or Image.new("RGB", (w, h))
                    seg = next((k for k, b in enumerate(bounds) if t < b), len(bounds) - 1)
                    if req.punch_zoom and req.jump_cuts:
                        frame = _zoom(vr_frame, 1.0 if seg % 2 == 0 else 1.1, w, h)
                    frame = frame.convert("RGBA")
                    ov = next((o for o in overlays if o["start"] <= t < o["end"]), None)
                    cap_y = int(h * 0.70)
                    if ov is not None:
                        frame = _overlay(vr, ov, frame, t, w, h, margin, req, devices, cache, look)
                        if ov["kind"] != "text":  # the device / number takes the middle: captions move to the top
                            cap_y = int(h * 0.15)
                    if hook is not None and t < hook_end:
                        a = _ease_out(t / 0.3) * min(1.0, (hook_end - t) / 0.3)
                        lay = vr._fade(hook, a)
                        frame.paste(lay, ((w - lay.width) // 2, int(h * 0.2) - lay.height // 2), lay)
                    if groups:
                        g = next((k for k, (a, b, _) in enumerate(groups) if a - 0.05 <= t < b + 0.35), None)
                        if g is not None:
                            a, _, ws = groups[g]
                            active = max([j for j, (s, _, _) in enumerate(ws) if t >= s] or [-1])
                            key = ("cap", g, active)
                            if key not in cache:
                                cache[key] = vr._caption_layer([x[2] for x in ws], active, 84, look["pill"], w, margin,
                                                               req.rtl, look=look)
                            lay = cache[key]
                            frame.paste(lay, ((w - lay.width) // 2, cap_y - lay.height // 2), lay)
                else:  # end card on the brand background
                    te = t - cut_len
                    frame = look["bg"].copy().convert("RGBA")
                    a = _ease_out(te / 0.4)
                    sc = 0.85 + 0.15 * a
                    lay = end_card.resize((max(1, int(end_card.width * sc)), max(1, int(end_card.height * sc))),
                                          Image.BILINEAR)
                    lay = vr._fade(lay, a)
                    frame.paste(lay, ((w - lay.width) // 2, (h - lay.height) // 2), lay)
                if brand is not None:
                    frame.alpha_composite(brand, ((w - brand.width) // 2, int(margin * 0.75)))
                proc.stdin.write(frame.convert("RGB").tobytes())
                if on_progress and i % FPS == 0:
                    on_progress(i / max(1, n_total))
            proc.stdin.close()
        except BrokenPipeError:
            pass
        finally:
            reader.close()
            for d in devices.values():
                d.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0 or not out.exists():
            raise VideoError(f"فشل ترميز الفيديو: {err.strip()[-400:]}")
        data = out.read_bytes()
    captions = [(a, b, " ".join(x[2] for x in ws)) for a, b, ws in groups]
    return VideoResult(mp4=data, width=w, height=h, duration_ms=int(total * 1000), captions=captions)


def _overlay(vr, ov, frame, t, w, h, margin, req, devices, cache, look):
    """Draw one overlay over the (dimmed) speaker."""
    tl = t - ov["start"]
    kind = ov["kind"]
    if kind == "text":  # punchy line over the face, no dimming
        key = ("txt", ov["value"])
        if key not in cache:
            text, emph = parse_emphasis(ov["value"])
            cache[key] = vr._rich_layer(text, emph, 96, look, w - 2 * margin, margin, req.rtl, int(h * 0.25))
        lay = vr._fade(cache[key], _ease_out(tl / 0.25))
        frame.paste(lay, ((w - lay.width) // 2, int(h * 0.30) - lay.height // 2), lay)
        return frame
    base = _dim(frame.convert("RGB"), 0.5 * _ease_out(tl / 0.25)).convert("RGBA")
    if kind == "stat":
        stat = parse_stat(ov["value"])
        if stat:
            lay = vr._stat_layer(stat, tl, cache.setdefault(("stat", ov["value"]), {}), look["pill"], w, h, req.rtl)
            base.paste(lay, ((w - lay.width) // 2, int(h * 0.42) - lay.height // 2), lay)
        return base
    if kind == "media" and ov.get("path"):
        key = (ov["path"], ov["start"])
        if key not in devices:
            size = _media_size(ov["path"])
            if size and size[0] >= size[1] * 1.05:
                devices[key] = _Browser(ov["path"], _browser_layout(w, h, margin, size[0] / size[1]),
                                        ov["end"] - ov["start"], ov.get("focus"))
            else:
                devices[key] = _Phone(ov["path"], _phone_layout(w, h, margin, req.rtl), ov["end"] - ov["start"], ov.get("focus"))
            devices[key].highlight = look["pill"]
        img, (px, py) = devices[key].render(tl, True)
        base.paste(img, (px, py), img)
    return base
