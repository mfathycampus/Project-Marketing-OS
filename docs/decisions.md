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
