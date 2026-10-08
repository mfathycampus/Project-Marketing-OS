"""LLM provider boundary. Only this module (and its implementations) touch the SDK."""
import json
import time
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from app.config import settings


@dataclass
class LLMResult:
    data: dict
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0


@dataclass
class LLMRequest:
    instructions: str
    stable_context: str  # cached across calls
    user_message: str
    output_model: type[BaseModel]
    tool_name: str = "submit_result"
    history: list[dict] = field(default_factory=list)  # extra turns used for repair


class LLMProvider(Protocol):
    def generate_structured(self, req: LLMRequest) -> LLMResult: ...


class AnthropicProvider:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key or None)
        self.model = model or settings.anthropic_model

    def generate_structured(self, req: LLMRequest) -> LLMResult:
        import anthropic

        system = [{"type": "text", "text": req.instructions}]
        if req.stable_context:
            system.append(
                {"type": "text", "text": req.stable_context, "cache_control": {"type": "ephemeral"}}
            )
        # Structured outputs: the response is constrained to this schema. Forced tool_choice is
        # rejected by Claude 5.x models, and output_config.format is the supported replacement.
        schema = anthropic.transform_schema(req.output_model)
        messages = [{"role": "user", "content": req.user_message}, *req.history]
        started = time.monotonic()
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=settings.ai_max_output_tokens,
            system=system,
            messages=messages,
            output_config={"effort": settings.ai_effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if resp.stop_reason in ("max_tokens", "refusal"):
            raise ValueError(f"model stopped early: {resp.stop_reason}")
        text = next((b.text for b in resp.content if b.type == "text"), None)  # thinking blocks may come first
        if text is None:
            raise ValueError("model returned no text block")
        u = resp.usage
        return LLMResult(
            data=json.loads(text),
            model=self.model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


class FakeProvider:
    """Deterministic provider for tests and offline development."""

    model = "fake"

    def __init__(self, responses: list[dict] | None = None):
        self.responses = list(responses or [])
        self.calls: list[LLMRequest] = []

    def generate_structured(self, req: LLMRequest) -> LLMResult:
        self.calls.append(req)
        data = self.responses.pop(0) if self.responses else {}
        return LLMResult(data=data, model=self.model)


def get_provider() -> LLMProvider:
    if settings.ai_provider == "fake":
        return FakeProvider()
    return AnthropicProvider()
