"""Safe config check: python -m app.diagnose  (never prints secret values)."""
import os

from app.config import _ROOT, settings


def main() -> None:
    for path in (_ROOT / ".env", _ROOT / "backend" / ".env"):
        print(f"{path}: {'FOUND' if path.exists() else 'missing'}")
    print("OS env ENCRYPTION_KEY set:", "ENCRYPTION_KEY" in os.environ)
    key = settings.encryption_key
    print("ENCRYPTION_KEY length:", len(key), "(expected 44)")
    print("  ends with '=':", key.endswith("="))
    print("  has space/quote/#:", any(c in key for c in " \"'#"))
    try:
        from cryptography.fernet import Fernet

        Fernet(key.encode())
        print("  valid Fernet key: YES")
    except Exception:  # noqa: BLE001
        print("  valid Fernet key: NO (app will derive a key from it; still usable if >= 32 chars)")
    for path in (_ROOT / ".env", _ROOT / "backend" / ".env"):
        if not path.exists():
            continue
        raw = path.read_bytes()
        has_bom = raw[:3] == b"\xef\xbb\xbf"
        is_utf16 = raw[:2] in (b"\xff\xfe", b"\xfe\xff")
        print(f"{path.name} bytes: BOM={has_bom} utf16={is_utf16}")
        for line in raw.decode("utf-8-sig", "replace").splitlines():
            if line.startswith("ENCRYPTION_KEY"):
                val = line.split("=", 1)[1] if "=" in line else ""
                print(f"  ENCRYPTION_KEY line: value length {len(val)}, ends with '=': {val.endswith('=')},"
                      f" non-ascii chars: {sum(ord(c) > 127 for c in val)}")
    for name in ("canva_client_id", "canva_client_secret", "anthropic_api_key"):
        v = getattr(settings, name)
        print(f"{name.upper()}: {'set (%d chars)' % len(v) if v else 'EMPTY'}")
    for name in ("meta_app_id", "meta_app_secret"):
        v = getattr(settings, name)
        print(f"{name.upper()}: {'set (%d chars)' % len(v) if v else 'EMPTY'}")
    print("META_REDIRECT_URI:", settings.meta_redirect_uri)
    print("PUBLIC_BASE_URL:", settings.public_base_url or "(not set)")
    print("DESIGN_PROVIDER:", settings.design_provider)
    # Which variable NAMES each .env actually contains (values never printed): catches typos/extra spaces.
    for path in (_ROOT / ".env", _ROOT / "backend" / ".env"):
        if path.exists():
            names = []
            for line in path.read_bytes().decode("utf-8-sig", "replace").splitlines():
                if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    flag = " <- SPACE BEFORE '='" if k != k.rstrip() else ""
                    names.append(f"{k.strip()}{'(empty)' if not v.strip() else ''}{flag}")
            print(f"{path} defines: {', '.join(names)}")
    print("DATABASE_URL scheme:", settings.database_url.split(":", 1)[0])


if __name__ == "__main__":
    main()
