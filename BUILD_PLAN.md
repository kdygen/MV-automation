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

### Post-MVP

| 8 | Deploy + real authentication (Supabase Postgres, Render, Vercel, ES256/JWKS auth) | ✅ done (2026-09) |
| 8b | Real providers — Resend email + Google Maps distance (code ✅ tested; activate via env keys) | ⬜ keys pending |
| 8c | Hardening — rate limiting, Sentry, backups check | ⬜ next |
| 9 | AI chat assistant (extraction schema, missing-info loop) | ⬜ |
| 10 | Pricing Stage 2 — similar-job retrieval, shadow comparison | ⬜ |
| 11 | Pricing Stage 3 — XGBoost hours model, model registry, shadow mode | ⬜ |
| 12 | Pricing Stage 4 — scheduled retraining, drift monitoring | ⬜ |

**Deployed (2026-09):** app at https://mv-automation-dqus.vercel.app · API at
https://mv-automation.onrender.com · Supabase PostgreSQL + Auth (ES256 via JWKS).
Full loop verified live: submit → instant quote → accept → booking → dashboard login.

Each ML stage ships in **shadow mode** first (computed alongside the active engine,
deltas logged) and is promoted per-company only when it beats the incumbent on actuals.

## Deployment checklist

- [x] Supabase project created → `DATABASE_URL` (session pooler), `SUPABASE_URL`,
      `SUPABASE_ANON_KEY`
- [x] `alembic upgrade head` run against the Supabase Postgres (revisions 0001–0005)
- [x] Backend deployed to Render (Docker, `$PORT` binding, production DB guard)
- [x] Frontend deployed to Vercel; `NEXT_PUBLIC_API_URL` points at the backend
- [x] CORS locked to the Vercel origin
- [x] Real Supabase Auth — ES256 tokens verified via the project JWKS; first owner
      provisioned with `scripts/provision_staff.py`
- [ ] Geocoding/distance key → `GEOCODING_API_KEY`, `GEOCODING_PROVIDER=google`
- [ ] Email key → `EMAIL_API_KEY`, `EMAIL_PROVIDER=resend` (+ verify sending domain)
- [x] Rate limiting on public endpoints (per-IP sliding window; 429 on abuse)
- [x] Supabase RLS enabled deny-all on all business tables (defense-in-depth; verified
      live — app unaffected, anon-key access blocked)
- [ ] Sentry error monitoring — code ready, activates when `SENTRY_DSN` is set
- [ ] LLM key (AI sales assistant) → `OPENAI_API_KEY`
