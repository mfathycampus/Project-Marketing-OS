"""Verify free-LLM API keys and store the valid ones in .env.

    python -m app.ai.key_check            # check keys already in .env / environment
    python -m app.ai.key_check --setup    # prompt for missing keys (hidden input), verify, save

Keys are created by you on each provider's site (we never automate signups). Valid keys are written
to the repo-root .env only, which is gitignored; keys are never printed.
"""
import argparse
import getpass
import os
import stat
from pathlib import Path

import httpx
from dotenv import dotenv_values

from app.ai.free_providers import CATALOG, FreeProviderSpec

ENV_PATH = Path(__file__).resolve().parents[3] / ".env"


def check_key(spec: FreeProviderSpec, key: str, base_url: str | None = None,
              client: httpx.Client | None = None) -> tuple[bool, str]:
    """Cheap, quota-free check: list models with the key."""
    url = (base_url or spec.base_url).rstrip("/") + "/models"
    own = client is None
    client = client or httpx.Client(timeout=15)
    try:
        resp = client.get(url, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        return False, f"network error ({type(exc).__name__})"
    finally:
        if own:
            client.close()
    if resp.status_code == 200:
        return True, "ok"
    if resp.status_code in (401, 403):
        return False, "key rejected"
    if resp.status_code == 429:
        return True, "ok (rate limited right now, key looks valid)"
    return False, f"HTTP {resp.status_code}"


def set_env_var(path: Path, name: str, value: str) -> None:
    """Set NAME=value in a dotenv file, keeping other lines; file is made owner-only."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    new = f"{name}={value}"
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == name:
            lines[i] = new
            break
    else:
        lines.append(new)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def run(setup: bool, env_path: Path = ENV_PATH, client: httpx.Client | None = None,
        ask=getpass.getpass) -> dict[str, str]:
    env = {k: v for k, v in dotenv_values(env_path).items() if v} if env_path.exists() else {}
    env.update(os.environ)
    results: dict[str, str] = {}
    for spec in CATALOG.values():
        var = f"{spec.name.upper()}_API_KEY"
        key = env.get(var, "").strip()
        if not key and setup:
            print(f"\n{spec.name}: create a key at {spec.key_url}")
            key = ask(f"  paste {var} (Enter to skip): ").strip()
            if key:
                ok, msg = check_key(spec, key, env.get(f"{spec.name.upper()}_BASE_URL"), client)
                if ok:
                    set_env_var(env_path, var, key)
                    msg += f" -> saved to {env_path.name}"
                results[spec.name] = msg if ok else f"not saved: {msg}"
                print(f"  {spec.name}: {results[spec.name]}")
                continue
        if not key:
            results[spec.name] = "no key"
        else:
            ok, msg = check_key(spec, key, env.get(f"{spec.name.upper()}_BASE_URL"), client)
            results[spec.name] = msg if ok else f"INVALID: {msg}"
        print(f"{spec.name:<11} {results[spec.name]}")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--setup", action="store_true", help="prompt for missing keys and save valid ones")
    run(ap.parse_args().setup)
