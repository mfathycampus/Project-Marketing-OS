"""Thin Canva Connect REST client (unverified against live API, see oauth.py note)."""
import time

import httpx

from app.config import settings


class CanvaError(RuntimeError):
    pass


class CanvaClient:
    def __init__(self, access_token: str, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30)
        self.base = settings.canva_api_base
        self.headers = {"Authorization": f"Bearer {access_token}"}

    def _req(self, method: str, path: str, **kw) -> dict:
        r = self.http.request(method, f"{self.base}{path}", headers=self.headers, **kw)
        if r.status_code == 403:
            raise CanvaError(f"forbidden (missing scope or capability): {r.text[:200]}")
        if r.status_code >= 400:
            raise CanvaError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def capabilities(self) -> list[str]:
        return self._req("GET", "/users/me/capabilities").get("capabilities", [])

    def list_brand_templates(self) -> list[dict]:
        return self._req("GET", "/brand-templates", params={"dataset": "non_empty"}).get("items", [])

    def brand_template_dataset(self, brand_template_id: str) -> dict:
        return self._req("GET", f"/brand-templates/{brand_template_id}/dataset").get("dataset", {})

    def create_autofill(self, brand_template_id: str, data: dict, title: str = "") -> str:
        body = {"brand_template_id": brand_template_id, "data": data}
        if title:
            body["title"] = title[:255]
        return self._req("POST", "/autofills", json=body)["job"]["id"]

    def get_autofill(self, job_id: str) -> dict:
        return self._req("GET", f"/autofills/{job_id}")["job"]

    def create_export(self, design_id: str) -> str:
        body = {"design_id": design_id, "format": {"type": "png"}}
        return self._req("POST", "/exports", json=body)["job"]["id"]

    def get_export(self, export_id: str) -> dict:
        return self._req("GET", f"/exports/{export_id}")["job"]

    def poll(self, fn, job_id: str, timeout_s: int | None = None, interval_s: float = 1.5) -> dict:
        deadline = time.monotonic() + (timeout_s or settings.canva_poll_timeout_s)
        while True:
            job = fn(job_id)
            if job.get("status") == "success":
                return job
            if job.get("status") == "failed":
                raise CanvaError(f"job {job_id} failed: {job.get('error')}")
            if time.monotonic() > deadline:
                raise CanvaError(f"job {job_id} timed out")
            time.sleep(interval_s)
