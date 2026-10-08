"""AIOrchestrator: the single entry point for all LLM-powered operations."""
import json
import uuid
from datetime import date, timedelta
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.context import build_campaign_context
from app.ai.provider import LLMProvider, LLMRequest, LLMResult
from app.ai.schemas import CAPTION_LIMITS, AdaptOut, CampaignOut, CampaignRequest
from app.models import AIRun, Campaign, ContentItem, ContentVariant, Project

PROMPTS = Path(__file__).parent / "prompts"
ALLOWED_TEMPLATES = ("offer_square", "quote_square", "announcement_story")
MAX_ATTEMPTS = 2  # one repair retry


class AIGenerationError(RuntimeError):
    pass


def _load_prompt(name: str) -> tuple[str, str]:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8"), name


class AIOrchestrator:
    def __init__(self, session: Session, provider: LLMProvider):
        self.session = session
        self.provider = provider

    def generate_campaign(self, project_id: uuid.UUID, req: CampaignRequest) -> Campaign:
        project = self.session.get(Project, project_id)
        if project is None:
            raise LookupError("project not found")

        instructions, prompt_version = _load_prompt("campaign.v1")
        ctx = build_campaign_context(self.session, project, req.brief)
        forbidden = [r.text.lower() for r in project.rules if r.kind == "forbidden_word"]
        user_message = (
            f"{ctx.volatile}\n\n<request>\n"
            f"objective: {req.objective}\nduration_days: {req.duration_days}\n"
            f"post_count: {req.post_count}\nplatforms: {', '.join(req.platforms)}\n"
            f"tone: {req.tone or 'per brand'}\nbrief: {req.brief}\n"
            f"allowed_template_keys: {', '.join(ALLOWED_TEMPLATES)}\n</request>"
        )
        llm_req = LLMRequest(
            instructions=instructions,
            stable_context=ctx.stable,
            user_message=user_message,
            output_model=CampaignOut,
        )

        parsed, run = self._run(project_id, "generate_campaign", prompt_version, llm_req,
                                req.model_dump(mode="json"), lambda data: self._validate(data, req, forbidden))
        run_ok = run
        start = req.start_date or date.today()
        campaign = Campaign(
            project_id=project_id,
            name=parsed.name,
            objective=parsed.objective,
            brief=req.brief,
            strategy=parsed.strategy,
            duration_days=req.duration_days,
            start_date=start,
        )
        self.session.flush()
        campaign.ai_run_id = run_ok.id
        for item in parsed.content_items:
            campaign.items.append(
                ContentItem(
                    project_id=project_id,
                    type=item.type,
                    platform=item.platform,
                    day_offset=item.day_offset,
                    planned_date=start + timedelta(days=item.day_offset),
                    headline=item.headline,
                    caption=item.caption,
                    cta=item.cta,
                    design=item.design.model_dump(),
                )
            )
        self.session.add(campaign)
        self.session.commit()
        return campaign

    def _run(self, project_id: uuid.UUID, operation: str, prompt_version: str, llm_req: LLMRequest,
             request_dump: dict, validate):
        """Call the model with one repair retry, validate, and audit the run in `ai_runs`."""
        run = AIRun(
            project_id=project_id, operation=operation, prompt_version=prompt_version,
            model=getattr(self.provider, "model", "unknown"), status="error", attempts=1,
            input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, latency_ms=0,
            request=request_dump,
        )
        self.session.add(run)
        result: LLMResult | None = None
        parsed = None
        error: str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            run.attempts = attempt
            try:
                result = self.provider.generate_structured(llm_req)
                run.raw_output = result.data
                self._add_usage(run, result)
                parsed = validate(result.data)
                break
            except (ValidationError, ValueError) as exc:
                error = str(exc)
                if result is not None:  # ask the model to repair its own output
                    llm_req.history = [
                        {"role": "assistant", "content": json.dumps(result.data, ensure_ascii=False)},
                        {"role": "user", "content": f"The output was invalid: {error}\nFix it."},
                    ]
            except Exception as exc:  # provider/network failure
                error = str(exc)
                break
        if parsed is None:
            run.status = "invalid_output" if result is not None else "error"
            run.error = error
            self.session.commit()
            raise AIGenerationError(error or "generation failed")
        run.status = "ok"
        run.model = result.model if result else run.model
        self.session.flush()
        return parsed, run

    def adapt_content(self, item_id: uuid.UUID, platforms: list[str]) -> list[ContentVariant]:
        """Rewrite an item's caption for other platforms (same message, platform-appropriate form)."""
        item = self.session.get(ContentItem, item_id)
        if item is None:
            raise LookupError("content item not found")
        project = self.session.get(Project, item.project_id)
        existing = {v.platform for v in item.variants} | {item.platform}
        targets = [p for p in dict.fromkeys(platforms) if p not in existing]
        if not targets:
            return []
        instructions, prompt_version = _load_prompt("adapt.v1")
        ctx = build_campaign_context(self.session, project, item.caption)
        forbidden = [r.text.lower() for r in project.rules if r.kind == "forbidden_word"]
        limits = ", ".join(f"{p}: max {CAPTION_LIMITS[p]} chars" for p in targets)
        user_message = (
            f"{ctx.volatile}\n\n<source_post>\nplatform: {item.platform}\nheadline: {item.headline}\n"
            f"caption: {item.caption}\ncta: {item.cta}\n</source_post>\n\n"
            f"<request>\ntarget_platforms: {', '.join(targets)}\nlimits: {limits}\n</request>"
        )
        llm_req = LLMRequest(instructions=instructions, stable_context=ctx.stable, user_message=user_message,
                             output_model=AdaptOut)

        def validate(data: dict) -> AdaptOut:
            out = AdaptOut.model_validate(data)
            got = [v.platform for v in out.variants]
            if sorted(got) != sorted(targets):
                raise ValueError(f"expected exactly one caption per platform {targets}, got {got}")
            for v in out.variants:
                if not v.caption.strip():
                    raise ValueError(f"empty caption for {v.platform}")
                for word in forbidden:
                    if word in v.caption.lower():
                        raise ValueError(f"forbidden word used: {word}")
            return out

        parsed, run = self._run(item.project_id, "adapt_content", prompt_version, llm_req,
                                {"item_id": str(item_id), "platforms": targets}, validate)
        created = []
        for v in parsed.variants:
            variant = ContentVariant(content_item_id=item.id, platform=v.platform, caption=v.caption, ai_run_id=run.id)
            self.session.add(variant)
            created.append(variant)
        self.session.commit()
        return created

    @staticmethod
    def _add_usage(run: AIRun, r: LLMResult) -> None:
        run.input_tokens += r.input_tokens
        run.output_tokens += r.output_tokens
        run.cache_read_tokens += r.cache_read_tokens
        run.cache_write_tokens += r.cache_write_tokens
        run.latency_ms += r.latency_ms

    @staticmethod
    def _validate(data: dict, req: CampaignRequest, forbidden: list[str]) -> CampaignOut:
        out = CampaignOut.model_validate(data)
        if len(out.content_items) != req.post_count:
            raise ValueError(f"expected {req.post_count} items, got {len(out.content_items)}")
        for it in out.content_items:
            if it.platform not in req.platforms:
                raise ValueError(f"platform {it.platform} was not requested")
            if it.day_offset >= req.duration_days:
                raise ValueError(f"day_offset {it.day_offset} outside campaign duration")
            if it.design.template_key not in ALLOWED_TEMPLATES:
                raise ValueError(f"unknown template_key {it.design.template_key}")
            text = f"{it.headline} {it.caption} {it.cta}".lower()
            for word in forbidden:
                if word in text:
                    raise ValueError(f"forbidden word used: {word}")
        return out
