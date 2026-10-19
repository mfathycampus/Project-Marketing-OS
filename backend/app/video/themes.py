"""Ready-made video looks. A theme changes the background, motion and colours; the content stays the same.

modern     brand gradient, two drifting lights, zoom-through cuts, text slides up        (default)
calm       softer gradient, one slow light, long cross-fades, text fades in gently
energetic  diagonal gradient, fast lights, white-flash punch cuts, text pops in, bigger type
corporate  clean light background with brand bands, no lights, sliding cuts, dark text
"""
from PIL import Image, ImageDraw

THEMES = {
    "modern": {"label": "عصري (الافتراضي)"},
    "calm": {"label": "هادئ"},
    "energetic": {"label": "حماسي"},
    "corporate": {"label": "رسمي (خلفية فاتحة)"},
}
WHITE, INK_DARK = (255, 255, 255), (22, 32, 42)


def _mix(a, b, k: float):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


def _gradient(w: int, h: int, start, end, diagonal: bool) -> Image.Image:
    mask = Image.new("L", (2, 2))
    mask.putdata([0, 128, 128, 255] if diagonal else [0, 0, 255, 255])
    mask = mask.resize((w, h), Image.BILINEAR)
    return Image.composite(Image.new("RGB", (w, h), end), Image.new("RGB", (w, h), start), mask)


def build(name: str, w: int, h: int, primary, secondary, dark) -> dict:
    """Everything the renderer needs for one look."""
    name = name if name in THEMES else "modern"
    t = {"name": name, "fg": WHITE, "emph": secondary, "pill": secondary, "pill_fg": INK_DARK, "shadow": True,
         "glows": 2, "glow_speed": 1.0, "transition": ("zoom", 0.3), "enter": (0.55, 70, False), "text_k": 1.0,
         "bar_track": (255, 255, 255, 60)}
    if name == "modern":
        t["bg"] = _gradient(w, h, primary, dark, diagonal=False)
    elif name == "calm":
        t.update(bg=_gradient(w, h, _mix(primary, WHITE, 0.12), _mix(dark, primary, 0.35), diagonal=False),
                 glows=1, glow_speed=0.5, transition=("fade", 0.6), enter=(0.9, 25, False), text_k=0.92)
    elif name == "energetic":
        t.update(bg=_gradient(w, h, _mix(primary, secondary, 0.25), _mix(dark, (0, 0, 0), 0.3), diagonal=True),
                 glows=2, glow_speed=2.2, transition=("punch", 0.2), enter=(0.35, 0, True), text_k=1.08)
    else:  # corporate
        bg = Image.new("RGB", (w, h), (247, 248, 250))
        d = ImageDraw.Draw(bg)
        band = max(10, h // 140)
        d.rectangle((0, 0, w, band), fill=primary)
        d.rectangle((0, h - band, w, h), fill=primary)
        d.ellipse((int(w * 0.55), int(-h * 0.12), int(w * 1.25), int(h * 0.28)), fill=_mix((247, 248, 250), primary, 0.07))
        d.ellipse((int(-w * 0.3), int(h * 0.72), int(w * 0.45), int(h * 1.15)), fill=_mix((247, 248, 250), secondary, 0.10))
        t.update(bg=bg, fg=INK_DARK, emph=primary, pill=primary, pill_fg=WHITE, shadow=False, glows=0,
                 transition=("slide", 0.35), enter=(0.4, 20, False), bar_track=(22, 32, 42, 40))
    return t


def on_footage(t: dict) -> dict:
    """Scenes with background footage are always dark-shaded, so they use light text whatever the theme."""
    if t["fg"] == WHITE:
        return t
    return {**t, "fg": WHITE, "shadow": True, "bar_track": (255, 255, 255, 60)}


# ---------- small vector icons (no font glyphs needed) ----------
def icon_check(size: int, ring, mark) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, size - 1, size - 1), fill=(*ring, 255))
    s = size
    d.line([(s * 0.27, s * 0.52), (s * 0.44, s * 0.68), (s * 0.74, s * 0.34)], fill=(*mark, 255), width=max(3, s // 9),
           joint="curve")
    return im


def icon_cross(size: int, ring, mark) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, size - 1, size - 1), fill=(*ring, 255))
    a, b, wdt = size * 0.32, size * 0.68, max(3, size // 9)
    d.line([(a, a), (b, b)], fill=(*mark, 255), width=wdt)
    d.line([(a, b), (b, a)], fill=(*mark, 255), width=wdt)
    return im


def cursor(size: int) -> Image.Image:
    """Mouse pointer (white with dark outline), tip at (0, 0)."""
    im = Image.new("RGBA", (size, int(size * 1.45)), (0, 0, 0, 0))
    s = size
    pts = [(2, 2), (2, s * 1.18), (s * 0.30, s * 0.92), (s * 0.52, s * 1.40), (s * 0.70, s * 1.32),
           (s * 0.48, s * 0.86), (s * 0.86, s * 0.86)]
    d = ImageDraw.Draw(im)
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(20, 20, 24, 255), width=max(2, s // 14))
    return im
