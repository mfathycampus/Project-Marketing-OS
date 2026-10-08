"""Meta (Facebook Pages + Instagram) via the Graph API.

Written for an app in *Development mode*: it works for Pages/accounts of people who hold a role in the app,
without App Review. Endpoints follow Meta's Graph API docs from memory (developers.facebook.com was not reachable
from the build sandbox), so verify against a real app: `GET /api/v1/meta/status`, then connect and publish one post.
"""
import base64
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.designs.canva.crypto import decrypt, derive_key, encrypt
from app.models import SocialConnection
from app.social.provider import PermanentPublishError, PublishPayload, PublishResult

SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts",
          "instagram_basic", "instagram_content_publish"]
STATE_TTL_S = 900


class MetaError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def _graph(path: str = "") -> str:
    return f"https://graph.facebook.com/{settings.meta_graph_version}{path}"


# ---------- signed OAuth state (survives restarts, no server memory) ----------
def _sig(data: str) -> str:
    key = derive_key(settings.encryption_key)
    return hmac.new(key, data.encode(), hashlib.sha256).hexdigest()[:32]


def make_state(project_id: uuid.UUID) -> str:
    body = f"{project_id}.{int(time.time())}"
    return base64.urlsafe_b64encode(f"{body}.{_sig(body)}".encode()).decode()


def read_state(state: str) -> uuid.UUID:
    try:
        raw = base64.urlsafe_b64decode(state.encode()).decode()
        body, sig = raw.rsplit(".", 1)
        pid, ts = body.split(".")
        ok = hmac.compare_digest(sig, _sig(body)) and time.time() - int(ts) <= STATE_TTL_S
        if ok:
            return uuid.UUID(pid)
    except Exception:  # noqa: BLE001
        pass
    raise MetaError("invalid or expired login state, start the connection again")


def login_url(project_id: uuid.UUID) -> str:
    q = {"client_id": settings.meta_app_id, "redirect_uri": settings.meta_redirect_uri,
         "state": make_state(project_id), "response_type": "code"}
    if settings.meta_config_id:
        q["config_id"] = settings.meta_config_id
    else:
        q["scope"] = ",".join(x.strip() for x in settings.meta_scopes.split(",") if x.strip()) or ",".join(SCOPES)
    return f"https://www.facebook.com/{settings.meta_graph_version}/dialog/oauth?{urlencode(q)}"


# ---------- Graph client ----------
class MetaClient:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=60)

    def _call(self, method: str, url: str, **kw) -> dict:
        r = self.http.request(method, url, **kw)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            raise MetaError(err.get("message") or f"HTTP {r.status_code}", err.get("code"))
        return data

    def exchange_code(self, code: str) -> str:
        d = self._call("GET", _graph("/oauth/access_token"), params={
            "client_id": settings.meta_app_id, "client_secret": settings.meta_app_secret,
            "redirect_uri": settings.meta_redirect_uri, "code": code})
        d = self._call("GET", _graph("/oauth/access_token"), params={  # short-lived -> long-lived user token
            "grant_type": "fb_exchange_token", "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret, "fb_exchange_token": d["access_token"]})
        return d["access_token"]

    def pages(self, user_token: str) -> list[dict]:
        """Pages the user manages; page tokens obtained from a long-lived user token do not expire."""
        d = self._call("GET", _graph("/me/accounts"), params={
            "fields": "id,name,access_token,instagram_business_account{id,username}", "access_token": user_token})
        return d.get("data", [])

    def whoami(self, user_token: str) -> dict:
        """Account name + granted permissions: explains an empty page list. Never returns tokens."""
        out = {"name": "", "granted": [], "declined": []}
        try:
            out["name"] = self._call("GET", _graph("/me"), params={"fields": "name", "access_token": user_token}).get("name", "")
            perms = self._call("GET", _graph("/me/permissions"), params={"access_token": user_token}).get("data", [])
            out["granted"] = [x["permission"] for x in perms if x.get("status") == "granted"]
            out["declined"] = [x["permission"] for x in perms if x.get("status") != "granted"]
        except MetaError:
            pass
        return out

    def post_photo(self, page_id: str, token: str, image: bytes, caption: str) -> dict:
        return self._call("POST", _graph(f"/{page_id}/photos"), data={"caption": caption, "access_token": token},
                          files={"source": ("post.png", image, "image/png")})

    def post_text(self, page_id: str, token: str, message: str) -> dict:
        return self._call("POST", _graph(f"/{page_id}/feed"), data={"message": message, "access_token": token})

    def ig_create(self, ig_id: str, token: str, image_url: str, caption: str) -> str:
        return self._call("POST", _graph(f"/{ig_id}/media"),
                          data={"image_url": image_url, "caption": caption, "access_token": token})["id"]

    def ig_status(self, container_id: str, token: str) -> str:
        return self._call("GET", _graph(f"/{container_id}"),
                          params={"fields": "status_code", "access_token": token}).get("status_code", "")

    def ig_publish(self, ig_id: str, token: str, container_id: str) -> str:
        return self._call("POST", _graph(f"/{ig_id}/media_publish"),
                          data={"creation_id": container_id, "access_token": token})["id"]

    def ig_permalink(self, media_id: str, token: str) -> str | None:
        try:
            return self._call("GET", _graph(f"/{media_id}"),
                              params={"fields": "permalink", "access_token": token}).get("permalink")
        except MetaError:
            return None


# ---------- provider ----------
AUTH_CODES = {190, 102, 10, 200}  # expired/invalid token or missing permission


class MetaProvider:
    def __init__(self, conn: SocialConnection, session: Session, client: MetaClient | None = None,
                 ig_wait_s: float = 30.0):
        self.conn, self.session, self.client, self.ig_wait_s = conn, session, client or MetaClient(), ig_wait_s
        self.name = conn.platform

    def publish(self, payload: PublishPayload) -> PublishResult:
        token = decrypt(self.conn.token_enc)
        try:
            if self.conn.platform == "facebook":
                return self._facebook(token, payload)
            return self._instagram(token, payload)
        except MetaError as exc:
            if exc.code in AUTH_CODES:
                self.conn.status = "expired"
                raise PermanentPublishError(f"انتهت صلاحية ربط {self.conn.platform}، أعد الربط ({exc})") from exc
            raise

    def _facebook(self, token: str, p: PublishPayload) -> PublishResult:
        res = (self.client.post_photo(self.conn.account_id, token, p.image, p.caption) if p.image
               else self.client.post_text(self.conn.account_id, token, p.caption))
        post_id = res.get("post_id") or res.get("id")
        return PublishResult("published", post_id, f"https://www.facebook.com/{post_id}" if post_id else None)

    def _instagram(self, token: str, p: PublishPayload) -> PublishResult:
        if not p.image or p.item_id is None:
            raise PermanentPublishError("إنستغرام يحتاج صورة؛ ولّد التصميم أولًا")
        if not settings.public_base_url:
            raise PermanentPublishError("PUBLIC_BASE_URL غير مضبوط: إنستغرام يجلب الصورة من رابط عام")
        url = f"{settings.public_base_url.rstrip('/')}/api/v1/content-items/{p.item_id}/design/image?format=jpeg"
        container = self.client.ig_create(self.conn.account_id, token, url, p.caption)
        deadline = time.monotonic() + self.ig_wait_s
        while True:
            status = self.client.ig_status(container, token)
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                raise PermanentPublishError(f"إنستغرام رفض الصورة ({status}). تأكد أن الرابط عام وأن الأبعاد مقبولة")
            if time.monotonic() > deadline:
                raise MetaError("instagram media processing timed out")
            time.sleep(1.5)
        media_id = self.client.ig_publish(self.conn.account_id, token, container)
        return PublishResult("published", media_id, self.client.ig_permalink(media_id, token))


def provider_for(session: Session, project_id: uuid.UUID, platform: str) -> MetaProvider | None:
    conn = session.scalar(select(SocialConnection).where(
        SocialConnection.project_id == project_id, SocialConnection.platform == platform,
        SocialConnection.status == "active"))
    return MetaProvider(conn, session) if conn else None


# ---------- connect flow helpers ----------
def pending_payload(pages: list[dict]) -> str:
    """Encrypt page tokens for temporary storage until the user chooses a page."""
    return encrypt(json.dumps(pages, ensure_ascii=False))


def save_selection(session: Session, project_id: uuid.UUID, page: dict) -> list[SocialConnection]:
    """Create/replace the project's Facebook connection (and Instagram if the page has one linked)."""
    out = []
    targets = [("facebook", page["id"], page.get("name", ""), page["access_token"])]
    ig = page.get("instagram_business_account")
    if ig:
        targets.append(("instagram", ig["id"], ig.get("username", ""), page["access_token"]))  # IG uses the page token
    for platform, account_id, name, token in targets:
        conn = session.scalar(select(SocialConnection).where(
            SocialConnection.project_id == project_id, SocialConnection.platform == platform))
        if conn is None:
            conn = SocialConnection(project_id=project_id, platform=platform, account_id=account_id,
                                    account_name=name, token_enc=encrypt(token))
            session.add(conn)
        else:
            conn.account_id, conn.account_name, conn.token_enc, conn.status = account_id, name, encrypt(token), "active"
        out.append(conn)
    return out
