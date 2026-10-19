import json

import httpx
import pytest
from pydantic import BaseModel

from app.ai.free_providers import (
    OpenAICompatProvider,
    RetryableProviderError,
    RouterProvider,
    build_free_providers,
)
from app.ai.provider import FakeProvider, LLMRequest, LLMResult


class Out(BaseModel):
    title: str


def _req():
    return LLMRequest(instructions="i", stable_context="c", user_message="u", output_model=Out)


def _provider(handler, name="groq"):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(name, "https://x.test/v1", "k", "m", client=client)


def _ok(args):
    return httpx.Response(200, json={
        "choices": [{"message": {"tool_calls": [{"function": {"arguments": json.dumps(args)}}]}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7},
    })


def test_tool_call_parsed_and_request_shape():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _ok({"title": "hi"})

    res = _provider(handler).generate_structured(_req())
    assert res.data == {"title": "hi"} and res.model == "groq:m"
    assert (res.input_tokens, res.output_tokens) == (5, 7)
    assert seen["url"] == "https://x.test/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert seen["body"]["tool_choice"]["function"]["name"] == "submit_result"


def test_json_content_fallback():
    def handler(_):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"title": "x"}'}}]})

    assert _provider(handler).generate_structured(_req()).data == {"title": "x"}


@pytest.mark.parametrize("status", [401, 429, 503])
def test_http_errors_are_retryable(status):
    with pytest.raises(RetryableProviderError):
        _provider(lambda _: httpx.Response(status, text="no")).generate_structured(_req())


def test_garbage_is_retryable():
    with pytest.raises(RetryableProviderError):
        _provider(lambda _: httpx.Response(200, json={"choices": []})).generate_structured(_req())


def test_router_fails_over_and_cools_down():
    now = [0.0]
    calls = {"bad": 0}

    def bad_handler(_):
        calls["bad"] += 1
        return httpx.Response(429)

    bad = _provider(bad_handler, "groq")
    good = FakeProvider([{"title": "a"}, {"title": "b"}, {"title": "c"}])
    router = RouterProvider([bad, good], cooldown_s=60, clock=lambda: now[0])
    assert router.generate_structured(_req()).data == {"title": "a"}
    assert router.generate_structured(_req()).data == {"title": "b"}
    assert calls["bad"] == 1  # skipped while cooling down
    now[0] = 61
    router.generate_structured(_req())
    assert calls["bad"] == 2  # retried after cooldown


def test_router_all_fail():
    bad = _provider(lambda _: httpx.Response(500))
    with pytest.raises(RuntimeError, match="all LLM providers failed"):
        RouterProvider([bad]).generate_structured(_req())


def test_build_skips_unconfigured_and_rejects_unknown():
    ps = build_free_providers(["groq", "gemini"], {"GROQ_API_KEY": "k", "GROQ_MODEL": "custom"})
    assert [p.name for p in ps] == ["groq"] and ps[0].model == "custom"
    with pytest.raises(ValueError):
        build_free_providers(["nope"], {})
