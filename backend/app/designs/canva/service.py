"""Connection lifecycle: store encrypted tokens, refresh, capability check."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.designs.canva import oauth
from app.designs.canva.client import CanvaClient, CanvaError
from app.designs.canva.crypto import decrypt, encrypt
from app.designs.canva.provider import CanvaProvider
from app.models import CanvaConnection


def save_tokens(session: Session, tokens: dict, owner_id: str = "default") -> CanvaConnection:
    conn = session.scalar(select(CanvaConnection).where(CanvaConnection.owner_id == owner_id))
    if conn is None:
        conn = CanvaConnection(owner_id=owner_id, access_token_enc="", refresh_token_enc="", expires_at=utcnow())
        session.add(conn)
    conn.access_token_enc = encrypt(tokens["access_token"])
    conn.refresh_token_enc = encrypt(tokens["refresh_token"])
    conn.expires_at = utcnow() + timedelta(seconds=int(tokens.get("expires_in", 14400)) - 60)
    conn.scopes = (tokens.get("scope") or "").split()
    session.commit()
    return conn


def get_client(session: Session, owner_id: str = "default") -> CanvaClient:
    conn = session.scalar(select(CanvaConnection).where(CanvaConnection.owner_id == owner_id))
    if conn is None:
        raise CanvaError("Canva is not connected")
    if conn.expires_at <= utcnow():
        # TODO(sprint 3): lock the row (SELECT ... FOR UPDATE) so concurrent workers don't double-refresh
        conn = save_tokens(session, oauth.refresh(decrypt(conn.refresh_token_enc)), owner_id)
    return CanvaClient(decrypt(conn.access_token_enc))


def status(session: Session, owner_id: str = "default") -> dict:
    """What the UI shows: connected? autofill available?"""
    try:
        caps = get_client(session, owner_id).capabilities()
    except CanvaError as exc:
        return {"connected": False, "autofill": False, "detail": str(exc)}
    conn = session.scalar(select(CanvaConnection).where(CanvaConnection.owner_id == owner_id))
    conn.capabilities = caps
    session.commit()
    return {"connected": True, "autofill": "autofill" in caps, "capabilities": caps}


def canva_provider_for_owner(session: Session, owner_id: str = "default") -> CanvaProvider:
    return CanvaProvider(get_client(session, owner_id))
