# MV Automation

Automates the sales & quoting workflow for moving companies: a customer submits a
moving request (form today, AI chat later), the platform extracts a structured
`MoveSpec`, a **deterministic** pricing engine estimates the cost, the customer gets an
instant quote, accepts it, and a booking is created automatically. Completed jobs feed
back so future estimates get more accurate.

> **Core principle:** LLMs are used only for conversation, structured extraction,
> missing-info detection, and summarization. **Pricing is never done by an LLM** — it
> comes from deterministic rules or ML models trained on historical jobs.

## Monorepo layout

```
.
├── backend/     FastAPI service — the single source of truth for business logic
├── frontend/    Next.js app — public funnel + company dashboard
├── ml/          Offline training pipelines (Stage 3+ pricing)  [added later]
└── docs/        Architecture & design notes
```

## Status

**Live.** 🚀

- **App:** https://mv-automation-dqus.vercel.app — try the demo funnel at
  [/acme-movers/quote](https://mv-automation-dqus.vercel.app/acme-movers/quote)
- **API:** https://mv-automation.onrender.com ([docs](https://mv-automation.onrender.com/docs))
- **Production stack:** Vercel (frontend) · Render (FastAPI backend) · Supabase
  (PostgreSQL + Auth, ES256 token verification via JWKS)

The MVP covers **local, hourly-priced moves**: form intake → instant rule-based quote
(range) → accept link → automatic booking → company dashboard → actuals capture + CSV
history import. See [BUILD_PLAN.md](BUILD_PLAN.md) for milestone history and the
roadmap (hardening, AI chat, ML pricing stages).

*The deployed demo currently uses a deterministic stand-in for driving distance and
logs emails instead of sending them. The real providers (Resend, Google Distance
Matrix) are implemented and tested — they activate via environment variables once API
keys are configured.*

## Quick start (full local demo)

Backend (terminal 1):

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# for dashboard login locally, set any SUPABASE_JWT_SECRET in .env, e.g.:
#   SUPABASE_JWT_SECRET=local-dev-secret-0123456789abcdef
alembic upgrade head              # defaults to a local SQLite file
python -m scripts.seed_dev        # demo companies: acme-movers, careful-movers
python -m scripts.mint_dev_token  # prints a dashboard login token
uvicorn app.main:app --reload     # API docs at http://localhost:8000/docs
```

Frontend (terminal 2):

```bash
cd frontend
npm install
cp .env.example .env.local        # defaults point at localhost:8000
npm run dev
```

Then:
- Customer funnel: http://localhost:3000/acme-movers/quote
- Dashboard: http://localhost:3000/dashboard (paste the minted dev token)

Tests (no external services or network needed — providers are faked):

```bash
cd backend && pytest        # 93 tests
cd frontend && npm test     # 13 tests
```

## Remaining external keys

Deployment (Supabase + Render + Vercel) and real authentication are done. Two API keys
switch the demo stand-ins to the real thing — env-var flips on Render, no code changes:

| Env vars (Render) | Turns on |
|---|---|
| `EMAIL_PROVIDER=resend` + `EMAIL_API_KEY` | Real quote/booking emails via Resend |
| `GEOCODING_PROVIDER=google` + `GEOCODING_API_KEY` | Real driving distance via Google Maps |

An Anthropic API key is only needed later, for the AI chat milestone.
