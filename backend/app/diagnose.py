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
    except Exception as exc:  # noqa: BLE001
        print("  valid Fernet key: NO ->", exc)
    for name in ("canva_client_id", "canva_client_secret", "anthropic_api_key"):
        v = getattr(settings, name)
        print(f"{name.upper()}: {'set (%d chars)' % len(v) if v else 'EMPTY'}")
    print("DESIGN_PROVIDER:", settings.design_provider)
    print("DATABASE_URL scheme:", settings.database_url.split(":", 1)[0])


if __name__ == "__main__":
    main()
