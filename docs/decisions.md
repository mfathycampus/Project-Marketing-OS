# Verification results & decisions (Sprint 0)

## 1. Canva Autofill availability — NOT confirmed
- Canva docs: Autofill / Brand Templates require acting on behalf of a user in a **Canva Enterprise** org.
- The connected Canva account has a brand kit but **0 brand templates with autofill data fields**, so
  Autofill access could not be demonstrated.
- Decision: design generation sits behind a `DesignProvider` interface. First provider = HTML/SVG renderer
  (no external dependency). Canva Autofill becomes a provider once Enterprise access is confirmed
  via `GET /v1/users/me/capabilities`.

## 2–4. Product questions (assumed defaults, change if wrong)
- Users: single owner / agency first (`owner_id` on projects, no organizations table yet).
- First social target: Meta (Instagram + Facebook). Start Meta app review early.
- Language: Arabic-first (`brand_profiles.language`, `dialect`), RTL UI.

## 5. Build scope
Sprint 1 delivered: projects, Brand Brain, Context Engine, AIOrchestrator.generate_campaign
(forced tool-use structured output, one repair retry, platform limits, forbidden-word enforcement,
`ai_runs` audit, prompt caching on the stable brand context), content state machine, Alembic migration.

Content status ends at APPROVED; scheduled/published/failed belong to per-platform publications (Sprint 4).

## Sprint 2 (designs)
- `DesignProvider` interface; `HtmlRenderer` (Chromium/Playwright, RTL Arabic verified visually) is the default.
- `DesignService` records `design_jobs` (idempotency key = item + provider + values + brand colors), stores PNG,
  and moves the content item AI_GENERATED → DESIGN_PENDING → DESIGN_READY (back to AI_GENERATED on failure).
- Canva adapter (PKCE OAuth, encrypted tokens, capability check, brand-template dataset, autofill job, export)
  is implemented against **mocked** HTTP only. canva.dev was unreachable from the build sandbox, so endpoint paths
  and payloads are from memory and MUST be verified against a live Canva app (`/canva/status`) before use.
- Known gap: Canva refresh uses no row lock yet; polling is synchronous inside the request (worker in Sprint 3).

## Sprint 3 (workflow + UI)
- Canva Autofill verified available (`/canva/status` -> autofill true) but publishing a brand template is blocked
  by the school team's permissions, and AI design generation is disabled by its admin. HTML renderer stays default.
- Approval workflow: submit / approve / reject / approve-all, every transition validated and logged in
  `content_status_history`. Approved/archived content is locked from edits.
- Calendar: `campaigns.start_date` + `content_items.planned_date`; `/projects/{id}/calendar?start&end`.
- Design worker: DB-backed queue (`design_jobs` is source of truth), in-process thread, atomic claim,
  crash recovery (processing -> pending on start). Move to Celery/Redis only when running multiple processes.
- UI: no-build static app (Preact + htm vendored, RTL Arabic) served by FastAPI at `/ui/`.

## Sprint 4 (scheduling + publishing)
- `publications` table owns post-approval state per platform (scheduled -> publishing -> awaiting_manual|published|failed|cancelled).
  Content status stays APPROVED, as designed.
- `SocialProvider` interface; shipped providers: `manual` (default: at the due time the post is queued for hand
  posting with copy-text/download-image) and `dryrun`. Real adapters register in `app/social/provider.py`.
- Scheduler thread: atomic claim of due rows, retry with backoff (5min x attempt, max 3), crash recovery.
  Times are stored in UTC; defaults computed from the project's timezone (Asia/Riyadh) at 19:00.
- Why no Meta adapter yet: Instagram/Facebook publishing needs an approved Meta app (Business Verification, review
  of `instagram_content_publish` / `pages_manage_posts`) and **publicly reachable image URLs**, which means object
  storage (S3-compatible) instead of local disk. Both are prerequisites, not code.

## Meta provider (Development mode)
- Own-account publishing without App Review: OAuth login -> long-lived user token -> page token (non-expiring) stored
  encrypted in `social_connections`; Facebook posts upload the image bytes, Instagram posts use a public JPEG URL
  (`PUBLIC_BASE_URL` + `/design/image?format=jpeg`).
- A connected account for (project, platform) wins over the default provider, so unconnected platforms stay manual.
- Auth failures (code 190 etc.) mark the connection `expired` and fail the publication permanently (no pointless retries).
- UNVERIFIED against live Meta: the sandbox could not reach developers.facebook.com. Tested with mocked HTTP only.
  Graph version is configurable (`META_GRAPH_VERSION`, default v23.0).
