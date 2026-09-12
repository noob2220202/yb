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
  app/scheduler.py     APScheduler polling loop — merges every configured
                       provider's quotes into one scan so cross-provider
                       arbitrage (e.g. Pinnacle vs. an Odds API book) is
                       actually detected, then notifies Telegram
  app/notifications/   Telegram bot notifier — 박스별로 정리된 확정픽을
                       대시보드를 안 열어도 받아볼 수 있는 서버 사이드 채널
frontend/            Next.js dashboard — 픽 박스(카드) 레이아웃, 벤토 요약 지표,
                     스테이크 계산기, 신규 확정픽 브라우저 알림
docker-compose.yml    postgres + backend + frontend (선택사항 — 로컬은 venv/npm으로 충분)
```

### 대시보드 디자인

핀테크/트레이딩 카테고리의 상위 모바일 UI에서 반복적으로 나타나는 패턴(벤토 그리드
요약 지표, 다크 모드 기본, 절제된 글래스모피즘, 8pt 간격 리듬, 숫자 강조 타이포)만
추출해 우리 톤(저자극·고신뢰의 다크 테마 + "확정 수익=민트" / "가치 엣지=앰버" 2트랙
컬러 코딩)으로 재조합했습니다. 특정 Dribbble 샷을 그대로 베끼지 않았습니다.

각 아비트리지 기회는 표가 아니라 **픽 박스**(카드)로 표시되어 이벤트·마켓·마진·다리별
배당을 한눈에 보여주고, 새로 감지된 픽은 잠깐 민트색 테두리로 하이라이트됩니다.
우측 상단 "🔔 알림 켜기"를 누르면 브라우저 권한을 요청하고, 이후 설정한 마진(%) 이상의
확정픽이 새로 뜰 때마다 OS 알림 + 소리(사인파 차임, 별도 음원 파일 불필요)로 알려줍니다.
임계값은 브라우저 `localStorage`에 저장됩니다.

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

### 상시 실행 (도커 없이, 계속 켜두기)

백엔드 프로세스가 살아있는 동안 `app/scheduler.py`의 APScheduler 잡이
`POLL_INTERVAL_SECONDS`(기본 60초)마다 자동으로 모든 프로바이더(Pinnacle 포함)를
폴링하고 스캔합니다 — 별도 크론 없이 그냥 프로세스를 계속 띄워두면 됩니다. 터미널을
닫아도 유지하려면:

```bash
cd backend
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 > backend.log 2>&1 &
```

```bash
cd frontend
nohup npm start > frontend.log 2>&1 &   # 먼저 npm run build 한 번 실행
```

(`tmux`/`screen` 세션 안에서 그냥 `uvicorn ...`, `npm run dev`를 포그라운드로 띄워두는
방법도 동일하게 잘 동작합니다. systemd 유닛으로 등록하면 재부팅 후에도 자동 재시작됩니다.)

### Docker Compose (선택사항)

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
  `build_providers()`. Every configured provider's quotes are merged into
  one scan per poll cycle (`app/scheduler.py:_collect_quotes`) — this is
  what lets the arbitrage engine compare a Pinnacle price against an Odds
  API price for the same match, not just prices within one provider.

## 텔레그램 알림 (브라우저를 안 열어도 받기)

대시보드의 브라우저 알림은 그 탭이 열려 있을 때만 동작합니다. 항상 켜져 있는
백엔드 프로세스 기준으로 알림을 받고 싶다면 `backend/.env`에 다음을 설정하세요:

```
TELEGRAM_BOT_TOKEN=<@BotFather 로 발급받은 토큰>
TELEGRAM_CHAT_ID=<봇과 대화한 채팅 ID>
TELEGRAM_MIN_MARGIN_PERCENT=1.0   # 이 마진(%) 이상만 알림
```

매 폴링 사이클마다 **새로 나타난** 확정픽만(이미 알림을 보낸, 여전히 살아있는
픽은 재알림하지 않음) 박스별로 정리해 하나의 메시지로 보냅니다:

```
🎯 확정 수익 픽 2건 발견

[1] Arsenal vs Chelsea
마켓: Moneyline 3Way
확정 마진: +2.44%
  ▸ home @ DemoBookA  2.10
  ▸ draw @ DemoBookB  3.60
  ▸ away @ DemoBookC  4.50

[2] ...
```

## Current scope / what's next

Shipped: core-market arbitrage (1X2, 2-way moneyline, totals, Asian
handicap, BTTS), push-aware math, stake calculator, exotic-market value
model (correct score, winning margin), API-key auth, live pick-box
dashboard, 신규 확정픽 브라우저 알림(+소리), 텔레그램 서버 사이드 알림,
크로스 프로바이더 아비트리지 병합.

Not built yet (natural next steps, intentionally out of scope for this
MVP): user signup/billing (Stripe), DB migrations (Alembic — currently
`create_all` on startup), more sports/markets, odds history charts,
per-user bookmaker account management for actually placing bets (this
scaffold only surfaces opportunities, it never places a bet for you).
