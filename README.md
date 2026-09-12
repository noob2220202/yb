# YB — Cross-Bookmaker Arbitrage & Value-Edge Scanner

A SaaS scaffold that pulls odds from multiple bookmakers and finds:

1. **Guaranteed-profit arbitrage** across core markets (moneyline/1X2, totals,
   Asian handicap) — the same event & market priced differently enough
   across independent books that betting every outcome locks in a profit no
   matter the result.
2. **Exotic-market value edges** (correct score, winning margin) — a
   Poisson/Dixon-Coles scoreline model flags prices that diverge from its
   own probability estimate. **This is not guaranteed profit**, it's a
   ranked shortlist of positive-EV bets (see caveat below).

## Read this before you rely on it

- **Arbitrage needs ≥2 independent bookmakers.** A single price source
  (e.g. Pinnacle alone) can never be arbed against itself. The core
  arbitrage engine only fires when it has best-odds quotes for every
  outcome of a market from possibly-different books.
- **Correct score / winning margin are not risk-free.** Bookmakers rarely
  quote those identically enough for true arbitrage, so the exotic-market
  scanner instead compares its own scoreline model's probabilities against
  quoted prices. A flagged "edge" can still lose — treat it as a shortlist
  for a human to review, never as an auto-bet signal.
- **Scraping bookmaker websites directly is usually a ToS violation** and
  an ongoing anti-bot arms race. This project only talks to official,
  documented APIs (Pinnacle's own API, The Odds API). If you plug in more
  bookmakers later, prefer their official feeds/affiliate APIs over HTML
  scraping.
- **Odds and account terms are real money and real account risk.**
  Bookmakers can (and do) limit or close accounts that bet like an
  arbitrage bettor. This tool finds the numbers; using it is your call and
  your risk.

## Architecture

```
backend/            FastAPI service
  app/core/          Canonical enums + in-memory OddsQuote/Event schema
  app/providers/      Odds source adapters (pluggable)
    base.py           OddsProvider interface
    demo.py           Synthetic fixture data — works with zero API keys
    pinnacle.py       Pinnacle official API adapter (Basic Auth)
    oddsapi.py        The Odds API adapter (aggregates many bookmakers)
  app/engine/
    arbitrage.py       Core N-outcome arbitrage math + stake allocator
    scoreline_model.py Poisson/Dixon-Coles calibration + value-edge finder
    scanner.py         Orchestrates persistence + both scans per poll cycle
  app/db/             SQLAlchemy models (async, Postgres in prod / SQLite in tests)
  app/api/            REST routes, API-key auth
  app/scheduler.py     APScheduler polling loop
frontend/            Next.js dashboard (opportunities table + stake calculator,
                     value-edges table)
docker-compose.yml    postgres + backend + frontend for local/prod-like runs
```

### Why the math is split into two engines

`app/engine/arbitrage.py` only ever combines **the same market, the same
line, the same event** across bookmakers — that's the only case where
"guaranteed profit" is actually true. It also handles the push-risk
subtlety of whole-number totals/handicap lines correctly: a push refunds
every stake on that market, so the worst case is breakeven, never a loss
(see the module docstring for the derivation). Quarter lines (.25/.75) are
excluded outright rather than mispriced.

`app/engine/scoreline_model.py` is a different kind of tool: it estimates a
full scoreline probability grid from a match's own devigged 1X2 + Totals
prices (Dixon-Coles-adjusted independent Poisson, calibrated by
least-squares), then compares that model's view against quoted exotic
prices. This is a **value-betting** tool, not an arbitrage engine — keep
that distinction when presenting results to users.

## Running locally

### Backend

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # defaults to USE_DEMO_PROVIDER=true, no API keys needed
.venv/bin/uvicorn app.main:app --reload
```

Visit `http://localhost:8000/opportunities` with header `x-api-key:
dev-local-key` (or `/health`, no auth). With `USE_DEMO_PROVIDER=true` (the
default) it seeds a handful of synthetic events with a real, computed
arbitrage margin and a real value-edge on startup — no external accounts
required.

Run tests:

```bash
cd backend && .venv/bin/pytest
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Visit `http://localhost:3000`.

### Everything via Docker Compose

```bash
docker compose up --build
```

Backend on `:8000`, frontend on `:3000`, Postgres on `:5432`. Set
`PINNACLE_USERNAME`/`PINNACLE_PASSWORD`, `ODDS_API_KEY`, and
`USE_DEMO_PROVIDER=false` as environment variables (or a `.env` file
docker-compose reads) once you have real accounts.

## Connecting real odds

- **Pinnacle**: requires a Pinnacle account with API access approved and
  their terms accepted. See https://pinnacleapi.github.io/ for current
  endpoint docs — `app/providers/pinnacle.py` documents the response shape
  it expects and degrades gracefully (skips malformed records) if the
  schema drifts.
- **The Odds API** (https://the-odds-api.com): a paid aggregator that
  returns real prices from many independent bookmakers in one call — this
  is what makes genuine cross-bookmaker arbitrage possible. Set
  `ODDS_API_KEY` and `ODDS_API_SPORT_KEYS` (comma-separated, e.g.
  `soccer_epl,soccer_uefa_champs_league`).
- Add another bookmaker by implementing `OddsProvider` (see
  `app/providers/base.py`) and registering it in `app/scheduler.py`'s
  `build_providers()`.

## Current scope / what's next

Shipped: core-market arbitrage (1X2, 2-way moneyline, totals, Asian
handicap, BTTS), push-aware math, stake calculator, exotic-market value
model (correct score, winning margin), API-key auth, live dashboard.

Not built yet (natural next steps, intentionally out of scope for this
MVP): user signup/billing (Stripe), DB migrations (Alembic — currently
`create_all` on startup), alerting (push/email/webhook when a new
opportunity appears), more sports/markets, odds history charts, per-user
bookmaker account management for actually placing bets (this scaffold only
surfaces opportunities, it never places a bet for you).
