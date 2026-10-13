"""LLM provider boundary. Only this module (and its implementations) touch the SDK."""
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
        system = [{"type": "text", "text": req.instructions}]
        if req.stable_context:
            system.append(
                {"type": "text", "text": req.stable_context, "cache_control": {"type": "ephemeral"}}
            )
        tool = {
            "name": req.tool_name,
            "description": "Submit the final structured result.",
            "input_schema": req.output_model.model_json_schema(),
        }
        messages = [{"role": "user", "content": req.user_message}, *req.history]
        started = time.monotonic()
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=settings.ai_max_output_tokens,
            system=system,
            messages=messages,
            tools=[tool],
            tool_choice={"type": "tool", "name": req.tool_name},
        )
        block = next((b for b in resp.content if b.type == "tool_use"), None)
        if block is None:
            raise ValueError("model returned no tool_use block")
        u = resp.usage
        return LLMResult(
            data=dict(block.input),
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


def _free_chain() -> list[LLMProvider]:
    import os

    from dotenv import dotenv_values

    from app.ai.free_providers import build_free_providers

    env: dict[str, str] = {}
    for f in settings.model_config.get("env_file") or ():  # same files Settings reads
        env.update({k: v for k, v in dotenv_values(f, encoding="utf-8-sig").items() if v})
    env.update(os.environ)  # real environment wins over .env
    names = [n for n in settings.free_llm_providers.split(",") if n.strip()]
    return build_free_providers(names, env)


def get_provider() -> LLMProvider:
    if settings.ai_provider == "fake":
        return FakeProvider()
    if settings.ai_provider in ("free", "hybrid"):
        from app.ai.free_providers import RouterProvider

        chain = _free_chain()
        if settings.ai_provider == "hybrid":
            chain.append(AnthropicProvider())
        if not chain:
            raise RuntimeError("AI_PROVIDER=%s but no free provider API key is set" % settings.ai_provider)
        return RouterProvider(chain, cooldown_s=settings.free_llm_cooldown_s)
    return AnthropicProvider()
