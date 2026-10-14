"""Image preparation for platforms with shape rules."""
import io

from PIL import Image, ImageFilter, ImageEnhance

IG_MIN, IG_MAX = 0.8, 1.91  # Instagram feed: 4:5 .. 1.91:1


def instagram_jpeg(image: bytes) -> bytes:
    """JPEG that Instagram accepts as a feed post. Out-of-range shapes (e.g. a 9:16 story design) are fitted
    whole onto a 4:5 (or 1.91:1) canvas over a blurred, darkened copy of themselves — nothing is cropped."""
    im = Image.open(io.BytesIO(image)).convert("RGB")
    w, h = im.size
    ratio = w / h
    if ratio < IG_MIN:
        cw, ch = 1080, 1350
    elif ratio > IG_MAX:
        cw, ch = 1080, round(1080 / IG_MAX)
    else:
        cw = ch = 0
    if cw:
        cover = max(cw / w, ch / h)
        bg = im.resize((round(w * cover), round(h * cover)), Image.LANCZOS)
        left, top = (bg.width - cw) // 2, (bg.height - ch) // 2
        bg = bg.crop((left, top, left + cw, top + ch)).filter(ImageFilter.GaussianBlur(40))
        bg = ImageEnhance.Brightness(bg).enhance(0.6)
        fit = min(cw / w, ch / h)
        fg = im.resize((round(w * fit), round(h * fit)), Image.LANCZOS)
        bg.paste(fg, ((cw - fg.width) // 2, (ch - fg.height) // 2))
        im = bg
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()
