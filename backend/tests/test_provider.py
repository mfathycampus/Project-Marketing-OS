import json
from types import SimpleNamespace as NS

import pytest

from app.ai.provider import AnthropicProvider, LLMRequest
from app.ai.schemas import CampaignOut


def fake_response(text, stop="end_turn", thinking_first=True):
    content = ([NS(type="thinking", thinking="")] if thinking_first else []) + [NS(type="text", text=text)]
    return NS(content=content, stop_reason=stop,
              usage=NS(input_tokens=10, output_tokens=20, cache_read_input_tokens=5, cache_creation_input_tokens=0))


def provider_with(resp):
    prov = AnthropicProvider(api_key="x", model="claude-sonnet-5-5")
    calls = []

    def create(**kw):
        calls.append(kw)
        return resp

    prov._client = NS(messages=NS(create=create))
    return prov, calls


def req():
    return LLMRequest(instructions="i", stable_context="ctx", user_message="u", output_model=CampaignOut)


def test_uses_structured_outputs_not_forced_tool_choice():
    prov, calls = provider_with(fake_response(json.dumps({"ok": 1})))
    out = prov.generate_structured(req())
    kw = calls[0]
    assert "tool_choice" not in kw and "tools" not in kw
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in kw  # thinking left at the model default
    assert kw["system"][1]["cache_control"] == {"type": "ephemeral"}
    assert out.data == {"ok": 1} and out.cache_read_tokens == 5  # skipped the thinking block


def test_schema_is_closed_and_has_no_open_dicts():
    import anthropic

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(anthropic.transform_schema(CampaignOut))


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_early_stop_raises_so_orchestrator_can_retry(stop):
    prov, _ = provider_with(fake_response("{", stop=stop))
    with pytest.raises(ValueError):
        prov.generate_structured(req())
