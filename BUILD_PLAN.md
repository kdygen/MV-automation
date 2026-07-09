# MV Automation — Build Plan

This is the manifest the autonomous build follows. Each milestone is self-contained,
ships with tests, and is committed separately. Tests passing is the gate to advance.

## Principles

- **Pricing is deterministic.** LLMs never price. The pricing engine is a strategy
  interface (`PricingEngine.estimate(MoveSpec, PricingConfig) -> Estimate`); Stages 1–4
  are implementations behind it. Callers never change as the engine evolves.
- **FastAPI is the only data-access layer** for business data. The frontend calls REST;
  it never queries Supabase tables directly. Supabase RLS stays on as defense-in-depth.
- **Multi-tenant.** Every business row carries `company_id`; the service layer scopes
  every query; tenancy has dedicated tests.
- **Alembic owns the schema.** No click-ops in the Supabase UI.
- **External services behind interfaces with in-memory fakes** (geocoding, email, LLM)
  so tests and local dev need no network or keys.

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Foundations — skeleton, DB layer, companies/users, JWT, Alembic, CI, tests | ✅ done |
| 2 | Intake domain — leads + moving_requests, geocoding provider, public intake | ✅ done |
| 3 | Pricing engine Stage 1 — interface, rules, config versioning, golden tests | ✅ done |
| 4 | Quote lifecycle + bookings — token links, accept/expiry, review mode, email | ✅ done |
| 5 | Public funnel frontend — multi-step form, confirm step, quote + accept | ✅ done |
| 6 | Company dashboard — Supabase Auth, leads/quotes/bookings, pricing settings | ✅ done |
| 7 | Jobs + data foundation — actuals capture, CSV import, accuracy tracking | ✅ done |

**MVP complete (2026-07-09).** 93 backend tests + 13 frontend tests; full loop verified
end-to-end against the running stack: submit → instant quote → accept → booking →
actuals → accuracy scorecard.

### Post-MVP (roadmap, not in this build)

| 8 | Deploy + hardening (Vercel/Railway, rate limiting, Sentry, backups) |
| 9 | AI chat assistant (extraction schema, missing-info loop) |
| 10 | Pricing Stage 2 — similar-job retrieval, shadow comparison |
| 11 | Pricing Stage 3 — XGBoost hours model, model registry, shadow mode |
| 12 | Pricing Stage 4 — scheduled retraining, drift monitoring |

Each ML stage ships in **shadow mode** first (computed alongside the active engine,
deltas logged) and is promoted per-company only when it beats the incumbent on actuals.

## Deployment checklist (fill in when going live)

- [ ] Supabase project created → `DATABASE_URL`, `SUPABASE_JWT_SECRET`, `SUPABASE_URL`,
      `SUPABASE_ANON_KEY`
- [ ] `alembic upgrade head` run against the Supabase Postgres
- [ ] Geocoding/distance provider key → `GEOCODING_API_KEY`, set `GEOCODING_PROVIDER`
- [ ] Email provider key → `EMAIL_API_KEY`, set `EMAIL_PROVIDER`
- [ ] LLM key (chat milestone) → `ANTHROPIC_API_KEY`
- [ ] Backend deployed to Railway/Render (Docker)
- [ ] Frontend deployed to Vercel; `NEXT_PUBLIC_API_URL` points at the backend
- [ ] Supabase RLS policies applied (deny-all by default)
