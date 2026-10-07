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
