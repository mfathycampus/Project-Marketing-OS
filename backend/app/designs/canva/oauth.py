"""Canva OAuth 2.0 Authorization Code + PKCE.

NOTE: endpoints/params written from Canva Connect docs knowledge; canva.dev was unreachable
from the build sandbox, so verify against a live Canva developer app before relying on it.
"""
import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx

from app.config import settings

AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
SCOPES = [
    "design:content:read", "design:content:write", "design:meta:read",
    "brandtemplate:meta:read", "brandtemplate:content:read",
    "asset:read", "asset:write", "profile:read",
]


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(state: str, challenge: str) -> str:
    q = {
        "code_challenge_method": "s256", "response_type": "code",
        "client_id": settings.canva_client_id, "redirect_uri": settings.canva_redirect_uri,
        "scope": " ".join(SCOPES), "code_challenge": challenge, "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(q)}"


def _basic() -> str:
    raw = f"{settings.canva_client_id}:{settings.canva_client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def exchange_code(code: str, verifier: str, http: httpx.Client | None = None) -> dict:
    http = http or httpx.Client(timeout=30)
    r = http.post(f"{settings.canva_api_base}/oauth/token", headers={"Authorization": _basic()}, data={
        "grant_type": "authorization_code", "code": code,
        "code_verifier": verifier, "redirect_uri": settings.canva_redirect_uri,
    })
    r.raise_for_status()
    return r.json()


def refresh(refresh_token: str, http: httpx.Client | None = None) -> dict:
    http = http or httpx.Client(timeout=30)
    r = http.post(f"{settings.canva_api_base}/oauth/token", headers={"Authorization": _basic()}, data={
        "grant_type": "refresh_token", "refresh_token": refresh_token,
    })
    r.raise_for_status()
    return r.json()  # refresh tokens rotate: always persist the new one
