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

**MVP complete.** See [BUILD_PLAN.md](BUILD_PLAN.md) for milestone details and the
post-MVP roadmap (AI chat, ML pricing stages). The MVP targets **local, hourly-priced
moves**: form intake → instant rule-based quote (range) → accept link → automatic
booking → dashboard → actuals capture + CSV history import.

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

## What needs your accounts before going live

The code runs and is fully tested locally with faked providers. To deploy, you supply:
Supabase project (Postgres URL + JWT secret), an LLM key (chat milestone), a
geocoding/distance key, an email provider key, and Vercel + Railway accounts. See the
deployment checklist at the bottom of [BUILD_PLAN.md](BUILD_PLAN.md).
