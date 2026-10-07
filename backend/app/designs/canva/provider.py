import httpx

from app.config import settings
from app.designs.canva.client import CanvaClient, CanvaError
from app.designs.provider import DesignRequest, DesignResult
from app.designs.registry import REGISTRY


class CanvaProvider:
    name = "canva"

    def __init__(self, client: CanvaClient, template_map: dict | None = None, interval_s: float = 1.5):
        self.client = client
        self.template_map = template_map if template_map is not None else settings.canva_template_map
        self.interval_s = interval_s

    def render(self, req: DesignRequest) -> DesignResult:
        mapping = self.template_map.get(req.template_key)
        if not mapping:
            raise CanvaError(f"no Canva template mapped for {req.template_key}")
        if "autofill" not in self.client.capabilities():
            raise CanvaError("Canva Autofill is not available for this account (needs Canva Enterprise)")

        dataset = self.client.brand_template_dataset(mapping["brand_template_id"])
        data: dict = {}
        for internal, canva_field in mapping["fields"].items():
            if canva_field not in dataset:
                raise CanvaError(f"template has no data field {canva_field}")
            if internal in req.values and dataset[canva_field].get("type") == "text":
                data[canva_field] = {"type": "text", "text": req.values[internal]}

        job_id = self.client.create_autofill(mapping["brand_template_id"], data, req.values.get("headline", ""))
        job = self.client.poll(self.client.get_autofill, job_id, interval_s=self.interval_s)
        design = job["result"]["design"]

        export_id = self.client.create_export(design["id"])
        export = self.client.poll(self.client.get_export, export_id, interval_s=self.interval_s)
        png = httpx.get(export["urls"][0], timeout=60).content  # export URLs are temporary: copy now
        spec = REGISTRY[req.template_key]
        return DesignResult(
            png=png, width=spec.width, height=spec.height, external_job_id=job_id,
            external_url=design.get("url") or design.get("urls", {}).get("edit_url"),
        )
