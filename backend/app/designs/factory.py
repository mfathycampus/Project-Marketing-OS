import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.designs.html_renderer import HtmlRenderer, RendererUnavailable
from app.designs.pillow_renderer import PillowRenderer
from app.designs.provider import DesignProvider, DesignRequest, DesignResult

log = logging.getLogger(__name__)


class AutoRenderer:
    """Chromium (HTML templates) when it works, Pillow otherwise — so design generation never hard-fails
    because a browser or a Python package is missing on the machine."""

    name = "auto"
    chromium_ok: bool | None = None  # None = not tried yet; False = skip Chromium for this process
    last_error: str = ""

    def render(self, req: DesignRequest) -> DesignResult:
        cls = type(self)
        if cls.chromium_ok is not False:
            try:
                result = HtmlRenderer().render(req)
                cls.chromium_ok = True
                return result
            except RendererUnavailable as exc:
                cls.chromium_ok, cls.last_error = False, str(exc)
                log.warning("Chromium renderer unavailable, using Pillow from now on: %s", exc)
            except Exception as exc:  # a one-off browser failure: still deliver this design
                cls.last_error = str(exc)
                log.warning("Chromium render failed, falling back to Pillow: %s", exc)
        return PillowRenderer().render(req)


def get_design_provider(session: Session | None = None) -> DesignProvider:
    if settings.design_provider == "canva":
        from app.designs.canva.service import canva_provider_for_owner

        return canva_provider_for_owner(session)
    if settings.design_provider == "pillow":
        return PillowRenderer()
    return AutoRenderer()


def health() -> dict:
    """What the design engine will actually use on this machine (shown in the UI)."""
    from app.designs import pillow_renderer

    try:
        import playwright  # noqa: F401
        playwright_installed = True
    except ImportError:
        playwright_installed = False
    info = pillow_renderer.diagnostics()
    font_ok = bool(info["font_regular"] and info["font_bold"])
    try:
        import arabic_reshaper  # noqa: F401
        from bidi.algorithm import get_display  # noqa: F401
        shaping_ok = True
    except ImportError:
        shaping_ok = info["raqm"]
    return {
        "provider": settings.design_provider,
        "python": info["python"],
        "playwright_installed": playwright_installed,
        "chromium_ok": AutoRenderer.chromium_ok,
        "chromium_error": AutoRenderer.last_error,
        "pillow_ready": font_ok and shaping_ok,
        "arabic_shaping": "raqm" if info["raqm"] else ("arabic-reshaper" if shaping_ok else "missing"),
        "font_regular": info["font_regular"],
        "font_bold": info["font_bold"],
        "ffmpeg": _ffmpeg(),
        "voice": settings.tts_voice if _voice_on() else None,
        "footage": bool(settings.pexels_api_key),
    }


def _voice_on() -> bool:
    from app.video import voice

    return voice.enabled()


def _ffmpeg() -> str | None:
    from app.video.renderer import VideoError, ffmpeg_exe

    try:
        return ffmpeg_exe()
    except VideoError:
        return None
