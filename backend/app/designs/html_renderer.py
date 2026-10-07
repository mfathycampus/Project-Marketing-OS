"""HTML/CSS -> PNG renderer (Chromium via Playwright). No external service needed."""
import glob
import os
from html import escape
from pathlib import Path
from string import Template

from app.designs.provider import DesignRequest, DesignResult
from app.designs.registry import REGISTRY

TEMPLATES = Path(__file__).parent / "templates"


def render_html(req: DesignRequest) -> str:
    spec = REGISTRY[req.template_key]
    raw = (TEMPLATES / f"{req.template_key}.html").read_text(encoding="utf-8")
    ctx = {f.name.upper(): escape(req.values.get(f.name, "")) for f in spec.fields}
    ctx.update(
        WIDTH=spec.width, HEIGHT=spec.height, PRIMARY=escape(req.primary_color),
        SECONDARY=escape(req.secondary_color), BRAND=escape(req.brand_name),
        DIR="rtl" if req.rtl else "ltr",
    )
    return Template(raw).safe_substitute(ctx)


def _launch(p):
    """Launch Chromium; fall back to any installed build when Playwright's pinned one is missing."""
    try:
        return p.chromium.launch()
    except Exception:
        root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
        for pattern in ("chromium-*/chrome-linux*/chrome", "chromium/chrome-linux*/chrome", "chromium/chrome"):
            for exe in sorted(glob.glob(os.path.join(root, pattern)), reverse=True):
                return p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        raise


class HtmlRenderer:
    name = "html"

    def render(self, req: DesignRequest) -> DesignResult:
        from playwright.sync_api import sync_playwright

        spec = REGISTRY[req.template_key]
        html = render_html(req)
        with sync_playwright() as p:
            browser = _launch(p)
            try:
                page = browser.new_page(viewport={"width": spec.width, "height": spec.height})
                page.set_content(html, wait_until="load")
                png = page.screenshot(type="png")
            finally:
                browser.close()
        return DesignResult(png=png, width=spec.width, height=spec.height)
