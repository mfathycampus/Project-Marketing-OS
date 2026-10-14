import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.service import move
from app.content.state_machine import ContentStatus as S
from app.designs.provider import DesignProvider, DesignRequest
from app.designs.registry import REGISTRY, DesignSpecError, build_values
from app.models import ContentItem, DesignAsset, DesignJob, Project, VideoAsset
from app.storage.local import LocalStorage
from app.config import settings
from app.video import broll, voice
from app.video.renderer import PLATFORM_ASPECT, VideoRenderer, VideoRequest, scenes_for


class DesignService:
    def __init__(self, session: Session, provider: DesignProvider, storage: LocalStorage | None = None):
        self.session = session
        self.provider = provider
        self.storage = storage or LocalStorage()

    def _prepare(self, item_id: uuid.UUID):
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
        video = self._video_request(item, values, req) if item.media_type == "video" else None
        digest = hashlib.sha256(
            json.dumps([self.provider.name, template_key, values, req.primary_color, req.secondary_color,
                        video.__dict__ if video else None], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:24]
        req.idempotency_key = f"{item.id}:{digest}"
        return item, req, digest

    @staticmethod
    def _video_request(item: ContentItem, values: dict, req: DesignRequest) -> VideoRequest:
        scenes = scenes_for(values.get("headline", ""), values.get("description", ""), values.get("price", ""),
                            item.cta, item.design.get("video_scenes"))
        return VideoRequest(scenes=scenes, cta=item.cta, brand_name=req.brand_name, primary_color=req.primary_color,
                            secondary_color=req.secondary_color, aspect=PLATFORM_ASPECT.get(item.platform, "9:16"),
                            music_seed=str(item.id), voice=voice.current_voice(),
                            broll=[q for q in item.design.get("video_broll") or [] if q.strip()] if broll.enabled() else [])

    def enqueue(self, item_id: uuid.UUID) -> DesignJob:
        """Create (or reuse) a design job and mark the item DESIGN_PENDING. Rendering happens in run_job."""
        item, req, _ = self._prepare(item_id)
        job = self.session.scalar(select(DesignJob).where(DesignJob.idempotency_key == req.idempotency_key))
        if job and job.status in ("completed", "pending", "processing"):
            return job
        if job is None:
            job = DesignJob(content_item_id=item.id, provider=self.provider.name,
                            idempotency_key=req.idempotency_key, attempts=0)
        job.status, job.error = "pending", None
        if item.status != S.DESIGN_PENDING.value:
            move(self.session, item, S.DESIGN_PENDING, "design queued")
        self.session.add(job)
        self.session.commit()
        return job

    def run_job(self, job_id: uuid.UUID) -> DesignAsset | None:
        job = self.session.get(DesignJob, job_id)
        item, req, digest = self._prepare(job.content_item_id)
        job.status = "processing"  # idempotent: the worker may already have claimed it
        job.attempts += 1
        self.session.commit()
        try:
            result = self.provider.render(req)
            job.external_job_id = result.external_job_id
            key = f"designs/{item.project_id}/{item.id}/{digest}.png"
            self.storage.put(key, result.png)
            asset = DesignAsset(
                content_item_id=item.id, job_id=job.id, provider=self.provider.name,
                template_key=req.template_key, storage_key=key, width=result.width,
                height=result.height, external_url=result.external_url,
            )
            self.session.add(asset)
            if item.media_type == "video":
                self.session.flush()
                video = VideoRenderer().render(self._video_request(item, req.values, req))
                vkey = f"videos/{item.project_id}/{item.id}/{digest}.mp4"
                self.storage.put(vkey, video.mp4)
                self.session.add(VideoAsset(content_item_id=item.id, job_id=job.id, storage_key=vkey,
                                            width=video.width, height=video.height, duration_ms=video.duration_ms))
            job.status = "completed"
            move(self.session, item, S.DESIGN_READY, "design ready")
            self.session.commit()
            return asset
        except Exception as exc:
            job.status, job.error = "failed", str(exc)[:1000]
            if item.status == S.DESIGN_PENDING.value:
                move(self.session, item, S.AI_GENERATED, "design failed")
            self.session.commit()
            raise

    def generate(self, item_id: uuid.UUID) -> DesignAsset:
        """Synchronous convenience: enqueue then render now."""
        job = self.enqueue(item_id)
        if job.status == "completed":
            return self.session.scalar(select(DesignAsset).where(DesignAsset.job_id == job.id))
        return self.run_job(job.id)

    def latest_asset(self, item_id: uuid.UUID) -> DesignAsset | None:
        return self.session.scalar(
            select(DesignAsset).where(DesignAsset.content_item_id == item_id)
            .order_by(DesignAsset.created_at.desc())
        )


    def latest_video(self, item_id: uuid.UUID) -> VideoAsset | None:
        return self.session.scalar(
            select(VideoAsset).where(VideoAsset.content_item_id == item_id).order_by(VideoAsset.created_at.desc()))


__all__ = ["DesignService", "DesignSpecError", "REGISTRY"]
