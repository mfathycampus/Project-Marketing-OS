import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.state_machine import ContentStatus as S, transition
from app.designs.provider import DesignProvider, DesignRequest
from app.designs.registry import REGISTRY, DesignSpecError, build_values
from app.models import ContentItem, DesignAsset, DesignJob, Project
from app.storage.local import LocalStorage


class DesignService:
    def __init__(self, session: Session, provider: DesignProvider, storage: LocalStorage | None = None):
        self.session = session
        self.provider = provider
        self.storage = storage or LocalStorage()

    def generate(self, item_id: uuid.UUID) -> DesignAsset:
        item = self.session.get(ContentItem, item_id)
        if item is None:
            raise LookupError("content item not found")
        project = self.session.get(Project, item.project_id)
        brand = project.brand_profile

        template_key = item.design.get("template_key", "")
        values = build_values(template_key, item.headline, item.cta, item.design.get("fields", {}))
        req = DesignRequest(
            template_key=template_key,
            values=values,
            primary_color=brand.primary_color if brand else "#0F766E",
            secondary_color=brand.secondary_color if brand else "#F59E0B",
            brand_name=brand.name if brand else project.name,
        )
        digest = hashlib.sha256(
            json.dumps([self.provider.name, template_key, values, req.primary_color, req.secondary_color],
                       sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:24]
        req.idempotency_key = f"{item.id}:{digest}"

        existing = self.session.scalar(select(DesignJob).where(DesignJob.idempotency_key == req.idempotency_key))
        if existing and existing.status == "completed":
            return self.session.scalar(select(DesignAsset).where(DesignAsset.job_id == existing.id))

        job = existing or DesignJob(content_item_id=item.id, provider=self.provider.name,
                                    idempotency_key=req.idempotency_key, attempts=0)
        job.status, job.error = "processing", None
        job.attempts += 1
        item.status = transition(item.status, S.DESIGN_PENDING).value
        self.session.add(job)
        self.session.commit()

        try:
            result = self.provider.render(req)
            job.external_job_id = result.external_job_id
            key = f"designs/{item.project_id}/{item.id}/{digest}.png"
            self.storage.put(key, result.png)
            asset = DesignAsset(
                content_item_id=item.id, job_id=job.id, provider=self.provider.name,
                template_key=template_key, storage_key=key, width=result.width,
                height=result.height, external_url=result.external_url,
            )
            self.session.add(asset)
            job.status = "completed"
            item.status = transition(item.status, S.DESIGN_READY).value
            self.session.commit()
            return asset
        except Exception as exc:
            job.status, job.error = "failed", str(exc)[:1000]
            item.status = transition(item.status, S.AI_GENERATED).value
            self.session.commit()
            raise

    def latest_asset(self, item_id: uuid.UUID) -> DesignAsset | None:
        return self.session.scalar(
            select(DesignAsset).where(DesignAsset.content_item_id == item_id)
            .order_by(DesignAsset.created_at.desc())
        )


__all__ = ["DesignService", "DesignSpecError", "REGISTRY"]
