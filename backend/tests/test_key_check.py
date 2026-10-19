import httpx

from app.ai import key_check
from app.ai.free_providers import CATALOG


def _client(status_by_key):
    def handler(request):
        key = request.headers["authorization"].removeprefix("Bearer ")
        return httpx.Response(status_by_key.get(key, 401))
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_check_key_statuses():
    c = _client({"good": 200, "slow": 429, "bad": 401, "boom": 500})
    spec = CATALOG["groq"]
    assert key_check.check_key(spec, "good", client=c)[0] is True
    assert key_check.check_key(spec, "slow", client=c)[0] is True
    assert key_check.check_key(spec, "bad", client=c) == (False, "key rejected")
    assert key_check.check_key(spec, "boom", client=c) == (False, "HTTP 500")


def test_set_env_var_keeps_other_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\nGROQ_API_KEY=old\nB=2\n")
    key_check.set_env_var(p, "GROQ_API_KEY", "new")
    key_check.set_env_var(p, "GEMINI_API_KEY", "g")
    assert p.read_text() == "A=1\nGROQ_API_KEY=new\nB=2\nGEMINI_API_KEY=g\n"
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_setup_saves_only_valid_keys(tmp_path, monkeypatch, capsys):
    for spec in CATALOG.values():
        monkeypatch.delenv(f"{spec.name.upper()}_API_KEY", raising=False)
    answers = iter(["good", "bad"] + [""] * 10)  # groq valid, gemini invalid, rest skipped
    env = tmp_path / ".env"
    res = key_check.run(True, env, _client({"good": 200}), ask=lambda _: next(answers))
    assert res["groq"].startswith("ok") and res["gemini"].startswith("not saved")
    assert "GROQ_API_KEY=good" in env.read_text() and "GEMINI" not in env.read_text()
    assert "good" not in capsys.readouterr().out  # keys are never printed
