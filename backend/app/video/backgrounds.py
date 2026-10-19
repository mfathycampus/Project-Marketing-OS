"""Scene backgrounds: each scene gets a different (but on-brand) background so a video does not sit on one gradient.

Every background is drawn once, a little larger than the frame, and drifts slowly (a cheap crop per frame), so it
feels alive without costing render time.
"""
import math
import random

from PIL import Image, ImageDraw, ImageFilter

from app.video.themes import _gradient, _mix

DRIFT = 90  # px of extra canvas on each side for the slow drift
DARK_KINDS = ["gradient", "mesh", "grid", "rays", "split", "dots"]
LIGHT_KINDS = ["light", "light_grid", "light_split", "light_dots"]
PER_THEME = {
    "modern": ["gradient", "mesh", "grid", "split", "dots", "rays"],
    "calm": ["gradient", "mesh", "dots"],
    "energetic": ["rays", "split", "mesh", "grid", "dots"],
    "corporate": LIGHT_KINDS,
}


def plan(theme: str, n: int, vary: bool = True) -> list[str]:
    """Background kind per scene: the theme's signature first (hook), then the others in turn, never twice in a row."""
    kinds = PER_THEME.get(theme, PER_THEME["modern"])
    if not vary:
        return [kinds[0]] * n
    return [kinds[i % len(kinds)] for i in range(n)]


def build(kind: str, w: int, h: int, primary, secondary, dark, seed: int = 0) -> Image.Image:
    W, H = w + 2 * DRIFT, h + 2 * DRIFT
    rnd = random.Random(seed)
    light_base = (247, 248, 250)
    if kind.startswith("light"):
        im = Image.new("RGB", (W, H), light_base)
        d = ImageDraw.Draw(im)
        band = max(10, h // 140)
        d.rectangle((0, DRIFT, W, DRIFT + band), fill=primary)
        d.rectangle((0, H - DRIFT - band, W, H - DRIFT), fill=primary)
        if kind == "light_grid":
            step = w // 9
            for x in range(0, W, step):
                d.line((x, 0, x, H), fill=_mix(light_base, primary, 0.08), width=2)
            for y in range(0, H, step):
                d.line((0, y, W, y), fill=_mix(light_base, primary, 0.08), width=2)
        elif kind == "light_split":
            d.polygon([(0, H * 0.62), (W, H * 0.38), (W, H), (0, H)], fill=_mix(light_base, primary, 0.08))
        elif kind == "light_dots":
            for _ in range(140):
                x, y, r = rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(4, 14)
                d.ellipse((x - r, y - r, x + r, y + r), fill=_mix(light_base, rnd.choice([primary, secondary]), 0.18))
        else:
            d.ellipse((int(W * 0.55), int(-H * 0.12), int(W * 1.25), int(H * 0.28)), fill=_mix(light_base, primary, 0.07))
            d.ellipse((int(-W * 0.3), int(H * 0.72), int(W * 0.45), int(H * 1.15)), fill=_mix(light_base, secondary, 0.10))
        return im
    base = _gradient(W, H, primary, dark, diagonal=False)
    d = ImageDraw.Draw(base, "RGBA")
    if kind == "mesh":  # big soft colour blobs
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for colour, (cx, cy, r) in zip([secondary, _mix(primary, (255, 255, 255), 0.3), primary, secondary],
                                       [(0.2, 0.25, 0.45), (0.85, 0.45, 0.5), (0.3, 0.8, 0.55), (0.8, 0.9, 0.35)]):
            ld.ellipse((W * cx - W * r, H * cy - W * r, W * cx + W * r, H * cy + W * r), fill=(*colour, 110))
        base = Image.alpha_composite(base.convert("RGBA"), layer.filter(ImageFilter.GaussianBlur(W // 7))).convert("RGB")
    elif kind == "grid":  # tech grid fading into the dark
        step = w // 8
        for x in range(0, W, step):
            d.line((x, 0, x, H), fill=(255, 255, 255, 22), width=2)
        for y in range(0, H, step):
            d.line((0, y, W, y), fill=(255, 255, 255, 22), width=2)
        for _ in range(10):  # a few lit cells
            x, y = rnd.randrange(0, W // step) * step, rnd.randrange(0, H // step) * step
            d.rectangle((x, y, x + step, y + step), fill=(*secondary, 26))
    elif kind == "rays":  # light rays from above
        cx, cy = W / 2, -H * 0.15
        for i in range(18):
            a0 = math.pi * (0.15 + 0.7 * i / 18)
            a1 = a0 + math.pi * 0.018
            pts = [(cx, cy), (cx + math.cos(a0) * H * 2, cy + math.sin(a0) * H * 2),
                   (cx + math.cos(a1) * H * 2, cy + math.sin(a1) * H * 2)]
            d.polygon(pts, fill=(255, 255, 255, 16 if i % 2 else 9))
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((W * 0.2, -H * 0.25, W * 0.8, H * 0.2), fill=(*secondary, 90))
        base = Image.alpha_composite(base.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(W // 8))).convert("RGB")
    elif kind == "split":  # diagonal accent band
        d.polygon([(0, H * 0.70), (W, H * 0.42), (W, H * 0.58), (0, H * 0.86)], fill=(*secondary, 58))
        d.polygon([(0, H * 0.88), (W, H * 0.60), (W, H * 0.64), (0, H * 0.92)], fill=(255, 255, 255, 28))
    elif kind == "dots":  # floating particles
        for _ in range(90):
            x, y, r = rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(3, 11)
            colour = rnd.choice([secondary, (255, 255, 255)])
            d.ellipse((x - r, y - r, x + r, y + r), fill=(*colour, rnd.randint(40, 120)))
    return base


def frame_at(big: Image.Image, t: float, w: int, h: int, speed: float = 1.0, vertical: bool = True) -> Image.Image:
    """Slow drift across the oversized background (light ones only sideways so their top/bottom bands stay put)."""
    ox = DRIFT + int(math.sin(t * 0.35 * speed) * DRIFT * 0.8)
    oy = DRIFT + (int(math.cos(t * 0.27 * speed) * DRIFT * 0.8) if vertical else 0)
    return big.crop((ox, oy, ox + w, oy + h))
