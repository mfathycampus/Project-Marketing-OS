"""Free-tier LLM providers (OpenAI-compatible) with a failover router.

Catalogue curated from https://github.com/mnfst/awesome-free-llm-apis. Free tiers, limits and model
names change often: override any default via env (`<NAME>_MODEL`, `<NAME>_BASE_URL`) and verify
against that list. Free-tier prompts may be logged or used for training by the provider, so never
route customer personal data through this path.
"""
import json
import time
from dataclasses import dataclass

import httpx

from app.ai.provider import LLMProvider, LLMRequest, LLMResult


@dataclass(frozen=True)
class FreeProviderSpec:
    name: str
    base_url: str
    default_model: str


CATALOG: dict[str, FreeProviderSpec] = {
    s.name: s
    for s in (
        FreeProviderSpec("groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
        FreeProviderSpec(
            "gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"
        ),
        FreeProviderSpec("cerebras", "https://api.cerebras.ai/v1", "llama-3.3-70b"),
        FreeProviderSpec("mistral", "https://api.mistral.ai/v1", "mistral-small-latest"),
        FreeProviderSpec(
            "openrouter", "https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"
        ),
        FreeProviderSpec("nvidia", "https://integrate.api.nvidia.com/v1", "meta/llama-3.3-70b-instruct"),
    )
}


class RetryableProviderError(RuntimeError):
    """Rate limit, outage or unusable output: the router should try the next provider."""


class OpenAICompatProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 60.0,
        max_tokens: int = 4096,
        client: httpx.Client | None = None,
    ):
        self.name, self.model, self.max_tokens = name, model, max_tokens
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client = client or httpx.Client(timeout=timeout_s)

    def generate_structured(self, req: LLMRequest) -> LLMResult:
        system = req.instructions + ("\n\n" + req.stable_context if req.stable_context else "")
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": req.user_message},
                *req.history,
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": req.tool_name,
                        "description": "Submit the final structured result.",
                        "parameters": req.output_model.model_json_schema(),
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": req.tool_name}},
        }
        started = time.monotonic()
        try:
            resp = self._client.post(self._url, json=body, headers=self._headers)
        except httpx.HTTPError as exc:
            raise RetryableProviderError(f"{self.name}: {exc}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RetryableProviderError(f"{self.name}: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            # auth / bad model / unsupported tools: this provider is unusable, try the next one
            raise RetryableProviderError(f"{self.name}: HTTP {resp.status_code} {resp.text[:200]}")
        try:
            payload = resp.json()
            msg = payload["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            raw = calls[0]["function"]["arguments"] if calls else msg.get("content") or ""
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise RetryableProviderError(f"{self.name}: unparseable response ({exc})") from exc
        usage = payload.get("usage") or {}
        return LLMResult(
            data=data,
            model=f"{self.name}:{self.model}",
            input_tokens=usage.get("prompt_tokens", 0) or 0,
            output_tokens=usage.get("completion_tokens", 0) or 0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


class RouterProvider:
    """Tries providers in order; a provider that fails is skipped for `cooldown_s` seconds."""

    def __init__(self, providers: list[LLMProvider], cooldown_s: float = 60.0, clock=time.monotonic):
        if not providers:
            raise ValueError("RouterProvider needs at least one provider")
        self.providers = providers
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._blocked_until: dict[int, float] = {}
        self.model = "router"

    def generate_structured(self, req: LLMRequest) -> LLMResult:
        now = self._clock()
        candidates = [p for p in self.providers if self._blocked_until.get(id(p), 0) <= now]
        # if everything is cooling down, still try them all rather than fail without a call
        errors: list[str] = []
        for p in candidates or self.providers:
            try:
                result = p.generate_structured(req)
            except RetryableProviderError as exc:
                self._blocked_until[id(p)] = self._clock() + self.cooldown_s
                errors.append(str(exc))
                continue
            self.model = result.model
            return result
        raise RuntimeError("all LLM providers failed: " + "; ".join(errors))


def build_free_providers(names: list[str], env: dict[str, str]) -> list[OpenAICompatProvider]:
    """Build providers for `names` that have an API key in `env` (`GROQ_API_KEY`, ...)."""
    out = []
    for name in names:
        spec = CATALOG.get(name.strip().lower())
        if spec is None:
            raise ValueError(f"unknown free provider '{name}'; known: {', '.join(CATALOG)}")
        key = env.get(f"{spec.name.upper()}_API_KEY", "").strip()
        if not key:
            continue  # not configured: silently skip so the chain still works
        out.append(
            OpenAICompatProvider(
                spec.name,
                env.get(f"{spec.name.upper()}_BASE_URL") or spec.base_url,
                key,
                env.get(f"{spec.name.upper()}_MODEL") or spec.default_model,
            )
        )
    return out
