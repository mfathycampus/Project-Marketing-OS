"""Pure-Python renderer (Pillow). Needs no browser, so it always works where the backend runs.

Mirrors the three HTML templates closely enough for publishing. Arabic is shaped with
libraqm when Pillow has it, otherwise with arabic-reshaper + python-bidi.
"""
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, features

from app.designs.provider import DesignRequest, DesignResult
from app.designs.registry import REGISTRY

FONTS_DIR = Path(__file__).parent / "fonts"  # drop e.g. Tajawal-Regular.ttf / Tajawal-Bold.ttf here
DARK = "#0b1f1d"
INK = "#1a1a1a"

_WIN = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
_REGULAR = [
    _WIN / "segoeui.ttf", _WIN / "tahoma.ttf", _WIN / "arial.ttf",
    Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
]
_BOLD = [
    _WIN / "segoeuib.ttf", _WIN / "tahomabd.ttf", _WIN / "arialbd.ttf",
    Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]


def _font_path(bold: bool) -> str | None:
    custom = os.environ.get("DESIGN_FONT_BOLD" if bold else "DESIGN_FONT")
    if custom and Path(custom).is_file():
        return custom
    if FONTS_DIR.is_dir():
        files = sorted(p for p in FONTS_DIR.iterdir() if p.suffix.lower() in (".ttf", ".otf"))
        picked = [p for p in files if ("bold" in p.name.lower()) == bold] or files
        if picked:
            return str(picked[0])
    for p in _BOLD if bold else _REGULAR:
        if p.is_file():
            return str(p)
    return None


HAS_RAQM = features.check("raqm")

_cache: dict[tuple[bool, int, bool], ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool):
    key = (bold, size, HAS_RAQM)
    if key not in _cache:
        path = _font_path(bold)
        # Without raqm the text is pre-shaped by arabic-reshaper/bidi, so the font must not reorder it again.
        engine = ImageFont.Layout.RAQM if HAS_RAQM else ImageFont.Layout.BASIC
        _cache[key] = ImageFont.truetype(path, size, layout_engine=engine) if path else ImageFont.load_default(size)
    return _cache[key]


def _shape(text: str, rtl: bool) -> tuple[str, dict]:
    """Return text ready to draw + extra draw kwargs."""
    if HAS_RAQM:
        return text, ({"direction": "rtl", "language": "ar"} if rtl else {})
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:
        return text, {}
    return get_display(arabic_reshaper.reshape(text), base_dir="R" if rtl else "L"), {}


def _rgb(color: str, fallback: str) -> tuple[int, int, int]:
    try:
        return Image.new("RGB", (1, 1), color).getpixel((0, 0))
    except (ValueError, TypeError):
        return Image.new("RGB", (1, 1), fallback).getpixel((0, 0))


@dataclass
class Block:
    text: str
    size: int
    bold: bool = False
    color: tuple = (255, 255, 255)
    alpha: float = 1.0
    line_height: float = 1.3
    pill: tuple | None = None  # background colour -> drawn as a rounded label
    pad: tuple[int, int] = (0, 0)  # (vertical, horizontal) padding inside the pill
    radius: int = 999
    align: str = "start"  # start | center
    margin_top: int = 0


class _Canvas:
    def __init__(self, img: Image.Image, rtl: bool, left: int, right: int):
        self.img, self.rtl, self.left, self.right = img, rtl, left, right
        self.draw = ImageDraw.Draw(img, "RGBA")

    def _width(self, text: str, font) -> float:
        shaped, kw = _shape(text, self.rtl)
        return self.draw.textlength(shaped, font=font, **kw)

    def lines(self, b: Block, scale: float) -> tuple[list[str], object]:
        font = _font(max(12, int(b.size * scale)), b.bold)
        limit = self.right - self.left - 2 * b.pad[1]
        out: list[str] = []
        for para in b.text.split("\n"):
            cur = ""
            for word in para.split():
                trial = f"{cur} {word}".strip()
                if cur and self._width(trial, font) > limit:
                    out.append(cur)
                    cur = word
                else:
                    cur = trial
            if cur:
                out.append(cur)
        return out, font

    def height(self, b: Block, scale: float) -> int:
        if not b.text.strip():
            return 0
        lines, font = self.lines(b, scale)
        return int(len(lines) * font.size * b.line_height + 2 * b.pad[0] * scale) + int(b.margin_top * scale)

    def paint(self, b: Block, top: int, scale: float) -> None:
        if not b.text.strip():
            return
        lines, font = self.lines(b, scale)
        top += int(b.margin_top * scale)
        lh = font.size * b.line_height
        pv, ph = int(b.pad[0] * scale), int(b.pad[1] * scale)
        widths = [self._width(l, font) for l in lines]
        box_w = int(max(widths)) + 2 * ph
        if b.align == "center":
            x0 = (self.left + self.right - box_w) // 2
        elif self.rtl:
            x0 = self.right - box_w
        else:
            x0 = self.left
        if b.pill:
            box_h = int(len(lines) * lh) + 2 * pv
            r = min(int(b.radius * scale), box_h // 2)
            self.draw.rounded_rectangle((x0, top, x0 + box_w, top + box_h), radius=r, fill=b.pill)
        fill = (*b.color, int(255 * b.alpha))
        for i, (line, w) in enumerate(zip(lines, widths)):
            shaped, kw = _shape(line, self.rtl)
            inner_w = box_w - 2 * ph
            if b.align == "center":
                x = x0 + ph + (inner_w - w) / 2
            elif self.rtl:
                x = x0 + ph + inner_w - w
            else:
                x = x0 + ph
            y = top + pv + i * lh + lh / 2
            self.draw.text((x, y), shaped, font=font, fill=fill, anchor="lm", **kw)


def _gradient(w: int, h: int, start, end, diagonal: bool) -> Image.Image:
    mask = Image.new("L", (2, 2))
    mask.putdata([0, 128, 128, 255] if diagonal else [0, 0, 255, 255])
    mask = mask.resize((w, h), Image.BILINEAR)
    return Image.composite(Image.new("RGB", (w, h), end), Image.new("RGB", (w, h), start), mask)


def _space_between(c: _Canvas, groups: list[list[Block]], top: int, bottom: int) -> None:
    """CSS flex column + justify-content:space-between; shrinks text until everything fits."""
    scale = 1.0
    while True:
        heights = [sum(c.height(b, scale) for b in g) for g in groups]
        if sum(heights) <= bottom - top or scale < 0.4:
            break
        scale *= 0.92
    gap = max(0, (bottom - top - sum(heights)) / max(1, len(groups) - 1))
    y = float(top)
    for g, h in zip(groups, heights):
        yy = y
        for b in g:
            c.paint(b, int(yy), scale)
            yy += c.height(b, scale)
        y += h + gap


class PillowRenderer:
    name = "pillow"

    def render(self, req: DesignRequest) -> DesignResult:
        spec = REGISTRY[req.template_key]
        w, h = spec.width, spec.height
        v = {k: (req.values.get(k) or "").strip() for k in ("headline", "price", "description", "cta")}
        primary, secondary = _rgb(req.primary_color, "#0F766E"), _rgb(req.secondary_color, "#F59E0B")
        white, ink, dark = (255, 255, 255), _rgb(INK, INK), _rgb(DARK, DARK)
        brand = req.brand_name or ""

        if req.template_key == "quote_square":
            img = Image.new("RGB", (w, h), secondary)
            c = _Canvas(img, req.rtl, 100, w - 100)
            blocks = [
                Block(v["headline"], 120, True, ink, line_height=1.3),
                Block(v["description"], 48, False, ink, line_height=1.5, margin_top=50),
                Block(v["cta"], 46, True, primary, margin_top=50),
            ]
            scale = 1.0
            while sum(c.height(b, scale) for b in blocks) > h - 320 and scale > 0.4:
                scale *= 0.92
            y = (h - sum(c.height(b, scale) for b in blocks)) / 2
            for b in blocks:
                c.paint(b, int(y), scale)
                y += c.height(b, scale)
            c.paint(Block(brand, 36, False, ink, alpha=0.7), h - 60 - int(36 * 1.3), 1.0)
        elif req.template_key == "announcement_story":
            img = _gradient(w, h, primary, dark, diagonal=False)
            c = _Canvas(img, req.rtl, 90, w - 90)
            _space_between(c, [
                [Block(brand, 48, alpha=0.85)],
                [Block(v["headline"], 140, True, line_height=1.25),
                 Block(v["description"], 54, alpha=0.9, line_height=1.5, margin_top=50)],
                [Block(v["cta"], 64, True, ink, line_height=1.2, pill=secondary, pad=(30, 90), align="center")],
            ], 140, h - 140)
        else:  # offer_square
            img = _gradient(w, h, primary, dark, diagonal=True)
            c = _Canvas(img, req.rtl, 90, w - 90)
            groups = [
                [Block(brand, 40, alpha=0.85)],
                [Block(v["headline"], 110, True, line_height=1.25),
                 Block(v["description"], 44, alpha=0.9, line_height=1.5, margin_top=24)],
            ]
            if v["price"]:
                groups.append([Block(v["price"], 90, True, ink, line_height=1.2, pill=secondary, pad=(10, 50), radius=24)])
            groups.append([Block(v["cta"], 52, True, primary, line_height=1.2, pill=white, pad=(22, 64))])
            _space_between(c, groups, 90, h - 90)

        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return DesignResult(png=buf.getvalue(), width=w, height=h)


def diagnostics() -> dict:
    return {
        "python": sys.executable,
        "raqm": HAS_RAQM,
        "font_regular": _font_path(False),
        "font_bold": _font_path(True),
    }
