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
    except Exception as first:
        root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
        for pattern in ("chromium-*/chrome-linux*/chrome", "chromium/chrome-linux*/chrome", "chromium/chrome"):
            for exe in sorted(glob.glob(os.path.join(root, pattern)), reverse=True):
                return p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        raise RuntimeError(
            "متصفح Chromium غير مثبت لـ playwright. نفّذ: python -m playwright install chromium ثم أعد المحاولة "
            f"({str(first).splitlines()[0][:120]})") from first


class HtmlRenderer:
    name = "html"

    def render(self, req: DesignRequest) -> DesignResult:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "حزمة playwright غير مثبتة في بيئة Python التي تشغّل الخادم. نفّذ: "
                "python -m pip install playwright ثم python -m playwright install chromium، وأعد تشغيل الخادم") from exc

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
