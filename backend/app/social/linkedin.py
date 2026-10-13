"""LinkedIn company-page publishing (Posts API, `/rest/posts`).

UNVERIFIED against live LinkedIn: written from the LinkedIn Marketing/Community Management API docs from memory
(linkedin.com docs were not reachable from the build sandbox) and tested with mocked HTTP only.
Posting as an organization needs the **Community Management API** product (scope `w_organization_social`), which
LinkedIn approves per app. Without it the authorize/post calls fail with a clear permission error.
"""
import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.designs.canva.crypto import decrypt, derive_key, encrypt
from app.models import SocialConnection
from app.social.provider import PermanentPublishError, PublishPayload, PublishResult

AUTHORIZE_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
API = "https://api.linkedin.com/rest"
STATE_TTL_S = 900
# characters with special meaning in LinkedIn's "little text format" commentary
_RESERVED = "\\|{}@[]()<>#*_~"


class LinkedInError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def escape_commentary(text: str) -> str:
    return "".join("\\" + ch if ch in _RESERVED else ch for ch in text)


# ---------- signed state ----------
def _sig(data: str) -> str:
    return hmac.new(derive_key(settings.encryption_key), data.encode(), hashlib.sha256).hexdigest()[:32]


def make_state(project_id: uuid.UUID) -> str:
    body = f"li.{project_id}.{int(time.time())}"
    return base64.urlsafe_b64encode(f"{body}.{_sig(body)}".encode()).decode()


def read_state(state: str) -> uuid.UUID:
    try:
        body, sig = base64.urlsafe_b64decode(state.encode()).decode().rsplit(".", 1)
        tag, pid, ts = body.split(".")
        if tag == "li" and hmac.compare_digest(sig, _sig(body)) and time.time() - int(ts) <= STATE_TTL_S:
            return uuid.UUID(pid)
    except Exception:  # noqa: BLE001
        pass
    raise LinkedInError("invalid or expired login state, start the connection again")


def login_url(project_id: uuid.UUID) -> str:
    q = {"response_type": "code", "client_id": settings.linkedin_client_id, "redirect_uri": settings.linkedin_redirect_uri,
         "state": make_state(project_id), "scope": settings.linkedin_scopes}
    return f"{AUTHORIZE_URL}?{urlencode(q)}"


# ---------- client ----------
class LinkedInClient:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=60)

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "LinkedIn-Version": settings.linkedin_api_version,
                "X-Restli-Protocol-Version": "2.0.0"}

    def _check(self, r: httpx.Response) -> None:
        if r.status_code >= 400:
            try:
                msg = r.json().get("message") or r.text
            except ValueError:
                msg = r.text
            raise LinkedInError(f"{msg}"[:400], r.status_code)

    def exchange_code(self, code: str) -> dict:
        r = self.http.post(TOKEN_URL, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": settings.linkedin_redirect_uri,
            "client_id": settings.linkedin_client_id, "client_secret": settings.linkedin_client_secret})
        self._check(r)
        return r.json()

    def admin_organizations(self, token: str) -> list[dict]:
        """Company pages where the user is an administrator: [{id, name}]."""
        r = self.http.get(f"{API}/organizationAcls", headers=self._headers(token),
                          params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"})
        self._check(r)
        out = []
        for el in r.json().get("elements", []):
            urn = el.get("organization", "")
            org_id = urn.rsplit(":", 1)[-1]
            name = ""
            try:
                o = self.http.get(f"{API}/organizations/{org_id}", headers=self._headers(token))
                if o.status_code < 400:
                    name = o.json().get("localizedName", "")
            except httpx.HTTPError:
                pass
            out.append({"id": org_id, "name": name or f"organization {org_id}"})
        return out

    def upload_image(self, token: str, owner_urn: str, image: bytes) -> str:
        r = self.http.post(f"{API}/images", headers=self._headers(token), params={"action": "initializeUpload"},
                           json={"initializeUploadRequest": {"owner": owner_urn}})
        self._check(r)
        value = r.json()["value"]
        up = self.http.put(value["uploadUrl"], content=image, headers={"Authorization": f"Bearer {token}"})
        self._check(up)
        return value["image"]

    def create_post(self, token: str, owner_urn: str, text: str, image_urn: str | None) -> str:
        body = {
            "author": owner_urn, "commentary": escape_commentary(text), "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
        }
        if image_urn:
            body["content"] = {"media": {"id": image_urn}}
        r = self.http.post(f"{API}/posts", headers=self._headers(token), json=body)
        self._check(r)
        return r.headers.get("x-restli-id", "")


# ---------- provider ----------
class LinkedInProvider:
    name = "linkedin"

    def __init__(self, conn: SocialConnection, session: Session, client: LinkedInClient | None = None):
        self.conn, self.session, self.client = conn, session, client or LinkedInClient()

    def publish(self, payload: PublishPayload) -> PublishResult:
        if self.conn.expires_at is not None:
            exp = self.conn.expires_at if self.conn.expires_at.tzinfo else self.conn.expires_at.replace(tzinfo=timezone.utc)
            if exp <= datetime.now(timezone.utc):
                return self._expired("انتهت صلاحية ربط لينكدإن (60 يومًا)")
        token = decrypt(self.conn.token_enc)
        owner = f"urn:li:organization:{self.conn.account_id}"
        try:
            image_urn = self.client.upload_image(token, owner, payload.image) if payload.image else None
            post_urn = self.client.create_post(token, owner, payload.caption, image_urn)
        except LinkedInError as exc:
            if exc.status in (401, 403):
                return self._expired(f"({exc.status}) {exc}")
            raise
        url = f"https://www.linkedin.com/feed/update/{post_urn}" if post_urn else None
        return PublishResult("published", post_urn or None, url)

    def _expired(self, reason: str):
        self.conn.status = "expired"
        self.conn.last_error = reason[:500]
        raise PermanentPublishError(f"يحتاج ربط لينكدإن إلى إعادة: {reason}")


def provider_for(session: Session, project_id: uuid.UUID, platform: str) -> LinkedInProvider | None:
    conn = session.scalar(select(SocialConnection).where(
        SocialConnection.project_id == project_id, SocialConnection.platform == "linkedin",
        SocialConnection.status == "active"))
    return LinkedInProvider(conn, session) if conn else None


def pending_payload(token: str, expires_in: int, orgs: list[dict]) -> str:
    return encrypt(json.dumps({"token": token, "expires_in": expires_in, "orgs": orgs}, ensure_ascii=False))


def save_selection(session: Session, project_id: uuid.UUID, token: str, expires_in: int, org: dict) -> SocialConnection:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in or 5184000)
    conn = session.scalar(select(SocialConnection).where(
        SocialConnection.project_id == project_id, SocialConnection.platform == "linkedin"))
    if conn is None:
        conn = SocialConnection(project_id=project_id, platform="linkedin", account_id=org["id"], account_name=org["name"],
                                token_enc=encrypt(token), expires_at=expires_at)
        session.add(conn)
    else:
        conn.account_id, conn.account_name, conn.token_enc = org["id"], org["name"], encrypt(token)
        conn.status, conn.last_error, conn.expires_at = "active", None, expires_at
    return conn
