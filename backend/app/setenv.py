"""Write keys into .env without hand-editing: python -m app.setenv LINKEDIN_CLIENT_ID LINKEDIN_CLIENT_SECRET

Values are typed/pasted at a prompt (secrets are hidden), stripped of spaces/quotes, and the line is
replaced in place (or appended). Every .env the app reads gets the same value so none overrides it.
"""
import getpass
import sys
from pathlib import Path

from app.config import _ROOT

OPTIONAL = {"TTS_VOICE"}
ENV_FILES = (_ROOT / ".env", _ROOT / "backend" / ".env")


def clean(value: str) -> str:
    return value.strip().strip("\"'").strip()


def set_key(path: Path, name: str, value: str) -> None:
    lines = path.read_bytes().decode("utf-8-sig", "replace").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == name and not line.lstrip().startswith("#"):
            if not done:
                out.append(f"{name}={value}")
                done = True
            continue  # drop duplicates
        out.append(line)
    if not done:
        out.append(f"{name}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def targets(name: str) -> list[Path]:
    """Root .env, plus backend/.env if it also defines the key (it is read last and would win)."""
    found = [p for p in ENV_FILES if p.exists() and any(
        l.split("=", 1)[0].strip() == name for l in p.read_bytes().decode("utf-8-sig", "replace").splitlines())]
    return found or [ENV_FILES[0]]


def main(names: list[str]) -> None:
    for name in names:
        ask = getpass.getpass if "SECRET" in name or "KEY" in name else input
        hint = " (Enter to skip)" if name in OPTIONAL else ""
        value = clean(ask(f"{name}{hint} (paste then Enter): "))
        if not value:
            print(f"  {name}: skipped (empty)")
            continue
        for path in targets(name):
            set_key(path, name, value)
            print(f"  {name}: saved to {path} ({len(value)} chars)")
    print("Done. Restart the server (run start.bat again) to use the new values.")


if __name__ == "__main__":
    main([a.upper() for a in sys.argv[1:]] or ["LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET"])
