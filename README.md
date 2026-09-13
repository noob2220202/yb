# YB — Cross-Bookmaker Arbitrage & Value-Edge Scanner

A SaaS scaffold that pulls odds from multiple bookmakers and finds:

1. **Guaranteed-profit arbitrage** across core markets (moneyline/1X2, totals,
   Asian handicap — including quarter lines like -0.25/-1.75, see "쿼터
   라인" below) — the same event & market priced differently enough
   across independent books that betting every outcome locks in a profit no
   matter the result.
2. **Value edges** — a Poisson/Dixon-Coles scoreline model flags prices
   that diverge from its own probability estimate, two ways: exotic
   markets (correct score, BTTS) priced by any book, and a
   single book's OWN secondary Totals/Asian-Handicap lines disagreeing
   with its own primary-line-calibrated model (works with just one
   provider — see "피나클만 쓰는 경우" below). **Neither is guaranteed
   profit**, it's a ranked shortlist of positive-EV bets (see caveat
   below).

Cross-match parlays (다폴더) are deliberately NOT built here — combining
independent matches into one bet slip can only ever compound each match's
own vig, never remove it; the only way a parlay combo becomes truly
guaranteed is if every leg already has its own single-match arbitrage, in
which case combining them adds no benefit and only fragments capital
across exponentially many slips. See "왜 다폴더가 없는지" below for the
full reasoning.

## Read this before you rely on it

- **Arbitrage needs ≥2 independent bookmakers.** A single price source
  (e.g. Pinnacle alone) can never be arbed against itself. The core
  arbitrage engine only fires when it has best-odds quotes for every
  outcome of a market from possibly-different books.
- **Correct score / BTTS are not risk-free.** Bookmakers rarely
  quote those identically enough for true arbitrage, so the exotic-market
  scanner instead compares its own scoreline model's probabilities against
  quoted prices. A flagged "edge" can still lose — treat it as a shortlist
  for a human to review, never as an auto-bet signal. (`winning_margin` is
  still defined in the type system and the scoreline model can price it,
  but no configured provider actually fetches that market — see
  "마켓 커버리지" below.)
- **Scraping bookmaker websites directly is usually a ToS violation** and
  an ongoing anti-bot arms race. This project only talks to official,
  documented APIs (Pinnacle's own API, The Odds API). If you plug in more
  bookmakers later, prefer their official feeds/affiliate APIs over HTML
  scraping.
- **Odds and account terms are real money and real account risk.**
  Bookmakers can (and do) limit or close accounts that bet like an
  arbitrage bettor. This tool finds the numbers; using it is your call and
  your risk.
- **No dummy data, anywhere.** With no provider configured, every
  endpoint correctly returns empty — the app never fabricates a pick to
  show something. The only way anything appears is a real
  `ODDS_API_KEY` and/or approved `PINNACLE_USERNAME`/`PINNACLE_PASSWORD`.

## 실데이터 연결 — 권장 경로: The Odds API

피나클 자체 API는 2025년 7월 23일부로 신규 신청이 막혀 있어서(아래 참고),
**지금 가장 빠르고 확실하게 실배당을 받는 방법은 The Odds API**입니다.
피나클을 포함해 여러 북메이커 배당을 정식 라이선스로 재판매하는 서비스라
스크래핑도 아니고 ToS 위반도 아니며, 지역 차단 문제도 없습니다.

**⚠️ 유료 플랜 결제 전에 반드시 무료로 먼저 검증하세요.** The Odds API 공식
문서에는 "spreads/totals 마켓은 현재 주로 미국 스포츠·북메이커용"이라는
문구가 있고, 문서에 실린 축구 예시는 h2h(승무패)뿐이라 — 이 프로젝트의
핵심인 축구 핸디캡/오버언더 커버리지가 문서만으로는 확정되지 않습니다.
다행히 **무료 Starter 플랜(월 500 크레딧, 신용카드 불필요)**이 있어서
한 푼도 안 쓰고 실제 응답을 확인할 수 있습니다:

1. https://the-odds-api.com 에서 무료 키 발급
2. `backend/.env`에 `ODDS_API_KEY=<키>` 설정
3. `cd backend && python scripts/verify_odds_api.py` 실행 — 축구
   handicap/totals에 실제로 북메이커가 찍히는지, 쿼터 라인이 오는지,
   `correct_score`의 선택지 이름 포맷이 파서 가정과 맞는지 원본 JSON
   기준으로 직접 보여줍니다.
4. 결과가 원하는 만큼 나온다면 그때 유료 플랜(20K $30/월, 100K $59/월,
   5M $119/월 등 — 가격은 바뀔 수 있으니 사이트에서 재확인)으로 업그레이드.

이 하나의 구독으로:

- 피나클의 실제 가격을 "Pinnacle"이라는 북메이커 이름으로 그대로 받고
- 동시에 다른 북메이커들 가격도 받아서 → **진짜 크로스북 아비트리지 계산이
  바로 가능**해집니다 (피나클 하나만으로는 절대 안 됨 — 아래 참고).

설정: `backend/.env`에 `ODDS_API_KEY`만 넣으면 됩니다. 이미 만들어둔
`OddsApiProvider`가 응답에 있는 북메이커를 이름 그대로 저장하는 범용
구조라서 코드 수정 없이 바로 동작합니다. https://theoddsapi.com/pricing

## 피나클만 쓰는 경우 (Odds API 없이 피나클 직접 연동만)

베팅을 피나클에서만 할 계획이라면 **"확정 수익 픽"(아비트리지) 섹션은 구조적으로
계속 비어 있는 게 정상입니다** — 아비트리지는 정의상 최소 2개의 독립적인 배당이
있어야 하고, 한 북메이커 자체 마켓 안에서는 마진(오버라운드) 때문에 항상 배당
역수 합이 1보다 큽니다. 피나클은 특히 마진이 업계 최저 수준이고 마켓 간 가격도
일관되게 매기기로 유명해서, 피나클 하나만으로 무위험 아비트리지가 나오는 건
현실적으로 기대하면 안 됩니다.

대신 이 설정에서 실제로 동작하는 건 **"가치 베팅 엣지"** 섹션입니다 —
`app/engine/scoreline_model.py`의 `find_cross_line_edges`가 피나클이 한 경기에
거는 여러 라인(토탈 1.5/2.5/3.5, 핸디캡 -1.5/-0.5/+0.5 등)을 서로 비교해서,
"이 경기의 주 라인으로 캘리브레이션한 모델이 보는 확률과, 피나클 자신이 다른
라인에 매긴 가격이 어긋나는" 경우를 찾습니다. 북메이커가 하나뿐이면 비교 대상도
자동으로 전부 그 북메이커 자신의 가격이 되므로, 별도 설정 없이 바로 "피나클
내부 가격 정합성 검사"로 동작합니다. 다만 **확정 수익이 아니고, 피나클이
정확히 이런 불일치를 최소화하도록 운영되는 북메이커라 발견 빈도는 낮을 것으로
예상됩니다** — 그게 정상입니다.

## 왜 다폴더가 없는지

이 프로젝트는 의도적으로 **다폴더(파레이)를 전혀 다루지 않습니다** — 크로스매치
파레이든 세임게임 파레이든. 이유는 단순한 스코프 문제가 아니라 수학적입니다:

- 다폴더의 배당은 각 다리 배당의 곱이고, 각 북메이커의 마진(vig)도 다리 수만큼
  **곱으로 누적**됩니다. 즉 다폴더로 묶는다고 마진이 사라지지 않고, 오히려
  압축(compound)됩니다.
- 이미 각 경기가 개별적으로 아비트리지가 성립한다면, 그 경기들을 다폴더로
  묶어봤자 수학적으로 이득이 전혀 없습니다 (같은 총수익을 훨씬 더 복잡하고
  자본이 여러 슬립에 쪼개지는 방식으로 얻을 뿐).
- 반대로 각 경기가 개별적으로 아비트리지가 없다면(즉 정상적인 마진이 걸린
  시장이라면), 그 경기를 다폴더에 포함시키는 순간 전체 조합의 기대값은
  반드시 더 나빠집니다.
- "모든 결과를 커버하는 다폴더 조합"으로 승률 자체를 100%로 만드는 것도
  가능은 하지만, 그러면 배당 합이 마진 때문에 항상 1 미만이 되어 **승률
  100%·손실 100% 보장**이라는 자기모순적인 결과만 나옵니다.

정리하면: 다폴더+단폴더, 다폴더+다폴더 조합으로 단폴더 아비트리지보다 나은
승률/수익을 만들 수 있는 방법은 존재하지 않습니다. 그래서 이 프로젝트는
`app/engine/arbitrage.py`의 단폴더(단일 경기, 두 개 이상의 북메이커) 아비트리지
하나에만 집중합니다 — 이게 유일하게 진짜 "확정 수익"이 성립하는 경우입니다.

## 쿼터 라인 (아시안 핸디캡/토탈스 .25 · .75)

일반 정수/반정수 라인(0, ±0.5, ±1.5 ...)과 달리, 쿼터 라인(-0.25, -0.75, +1.75
등)은 스테이크가 인접한 두 반정수 라인에 절반씩 나뉘어 걸리는 것과 동일하게
정산됩니다. 그 결과 가능한 결과가 (완승 / 반반(마진) / 완패) 세 가지이고, 그
"마진" 버킷은 정수 라인의 전액 푸시(환불)와 달리 **절반은 환불, 절반은 실제로
이기거나 진다**는 점이 다릅니다 (예: -0.25에서 무승부면 절반 손실; -0.75에서
1점차 승리면 절반 승리 — 실제 북메이커의 쿼터 라인 정산 규칙 그대로).

`app/engine/arbitrage.py`가 이 세 버킷의 최악의 경우 수익을 정확히 계산해서
(단순 `sum(1/odds) < 1` 공식이 아니라, 두 다리의 스테이크 배분을 직접 최적화)
쿼터 라인도 이제 확정 수익 스캐너에 포함됩니다 — 예전에는 아예 제외되던
라인들이라, 실제 북메이커들이 흔히 쓰는 쿼터 라인만큼 발견 가능한 기회가
늘어납니다. (이 3버킷 모델은 정수 개의 시뮬레이션으로 교차 검증했습니다.)

단, 같은 계산을 단순화한 이항(win/push/lose) 모델을 쓰는
`scoreline_model.find_cross_line_edges`(가치 베팅 엣지 쪽)는 쿼터 라인을
정확히 모델링하지 못하므로 그쪽 스캐너에서는 쿼터 라인을 제외합니다 — 잘못된
근사치보다는 아예 안 보여주는 쪽을 택했습니다.

## 마켓 커버리지 (축구)

| 마켓 | 확정 수익 엔진 | 가치 베팅 엔진 | 실데이터 수집 |
|---|---|---|---|
| 승무패 | ✅ | ✅ | ✅ Pinnacle, Odds API — 확실 |
| 핸디캡(정수·반정수)/오버언더 | ✅ | ✅ | ⚠️ Pinnacle, Odds API — **축구 커버리지 문서상 불확실, 아래 참고** |
| 아시안 핸디캡/토탈스 쿼터 라인(.25/.75) | ✅ | 제외(위 참고) | ⚠️ 위와 동일 + 쿼터 라인 자체 제공 여부 미확인 |
| 양팀득점(BTTS) | ✅ | — | ✅ Odds API (`btts` 마켓, 키는 확인됨) |
| 정확한 스코어 | — | ✅ | ✅ Odds API (`correct_score` 마켓, 아래 참고) |
| 몇점차승리(winning margin) | — | ✅ | ❌ 알려진 제공자 없음 |

**⚠️ 축구 핸디캡/오버언더 커버리지가 왜 "불확실"인가**: The Odds API 공식
문서가 "spreads/totals 마켓은 현재 주로 미국 스포츠·북메이커용"이라고
명시하고 있고, 문서의 축구 예시도 h2h뿐이라 — 유료로 전환하기 전에
`scripts/verify_odds_api.py`로 실제 응답을 반드시 확인하세요 (위 "실데이터
연결" 참고). Pinnacle 쪽은 스펙상 spreads/totals를 지원하지만 계정 승인이
막혀 있어 이 프로젝트에서 실응답 검증 자체가 안 된 상태입니다.

`OddsApiProvider`가 `btts`/`correct_score`를 요청·파싱하도록 추가했습니다
(The Odds API 공식 마켓 키로 확인됨). `correct_score`는 응답의 정확한
`outcomes[].name` 포맷을 실제 유료 응답으로 검증하지 못해 — 정규식으로 두 개의
정수를 뽑아 "H-A"로 매핑하는 최선 추정(best-effort) 파서입니다; 실 계정으로
한 번 검증 후 필요하면 조정하세요. `winning_margin`은 Pinnacle/Odds API 어디에도
표준 마켓 키가 없어 실데이터 연결을 붙이지 않았습니다 (타입 시스템·스코어라인
모델은 계속 지원하지만, 항상 빈 상태일 것입니다).

Pinnacle의 correct score/BTTS/margin은 `/v1/odds/special` 이라는 별도
엔드포인트로만 노출되는데, 이 엔드포인트는 자유 형식 `category`/`name`
문자열로 마켓을 표현해서 (예: `"Will the 4th quarter be odd or even?"`)
실제 응답 샘플 없이는 안전하게 파싱할 방법을 검증할 수 없습니다. 게다가
Pinnacle 신규 API 승인 자체가 2025-07-23부로 막혀 있어 이 세션에서는 특수
마켓 연동을 시도하지 않았습니다 — 잘못 파싱해서 스코어를 틀리게 표시하는
것보다는 아예 안 붙이는 쪽이 안전합니다.

## Architecture

```
backend/            FastAPI service
  app/core/          Canonical enums + in-memory OddsQuote/Event schema
  app/providers/      Odds source adapters (pluggable) — no dummy data,
                      each returns nothing until its credentials are set
    base.py           OddsProvider interface
    pinnacle.py       Pinnacle official API adapter (Basic Auth)
    oddsapi.py        The Odds API adapter (aggregates many bookmakers)
  app/engine/
    arbitrage.py       Core N-outcome arbitrage math + stake allocator
                       (incl. quarter-line .25/.75 settlement — see below)
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
"가치 픽" 섹션은 단폴더 가치 엣지(정확한 스코어·BTTS, 같은 북메이커 크로스라인
불일치)를 엣지% 순으로 보여줍니다.
우측 상단 "🔔 알림 켜기"를 누르면 브라우저 권한을 요청하고, 이후 설정한 마진(%) 이상의
확정픽이 새로 뜰 때마다 OS 알림 + 소리(사인파 차임, 별도 음원 파일 불필요)로 알려줍니다.
임계값은 브라우저 `localStorage`에 저장됩니다.

맨 아래 **"수동 계산기"** 섹션은 스캐너/DB와 완전히 별개로 동작합니다 — 마켓 구분 없이
한 경기에 대해 찾은 배당을 전부 한 표에 쏟아부으면 됩니다 (승무패, 유럽식 핸디캡
(3-way), 아시안 핸디캡(쿼터 라인 포함), 오버언더, 양팀득점, 정확한 스코어, 기타
커스텀까지 행을 자유롭게 추가). 백엔드의 `POST /calculator/scan`이 입력을
(마켓, 라인)별로 묶어서 **그중 실제로 100% 마진이 나는 조합만 골라** 보여줍니다:

- **엔진 검증(`verified: true`)**: 승무패·유럽식 핸디캡·아시안 핸디캡(쿼터 라인
  포함)·오버언더·BTTS처럼 이 프로젝트가 완전한 결과 분할(clean partition)임을
  아는 마켓에서, 필요한 선택지가 정확히 다 채워졌을 때만. 실제 감지된
  확정픽과 똑같은 `app/engine/arbitrage.py` 엔진으로 계산되므로 `is_arbitrage`가
  참이면 진짜 수학적 보장입니다.
- **검증 안 됨(`verified: false`)**: 정확한 스코어나 자유 입력 커스텀 마켓, 혹은
  선택지가 빠지거나 중복된 경우. 배당 합산(1/배당의 합)은 똑같이 계산해서
  보여주지만, **입력한 선택지가 실제로 일어날 수 있는 모든 경우의 수를 빠짐없이
  덮었을 때만** 진짜 확정 수익입니다 (정확한 스코어처럼 경우의 수가 사실상
  무한한 마켓은 "기타 전체" 캐치올 없이는 절대 확정 수익이 될 수 없음) — 이걸
  매 결과마다 경고 문구로 명시합니다.

확정 수익이 아닌 조합도 숨기지 않고 그대로 보여줍니다 (마진이 몇 %인지, 왜 검증이
안 되는지까지) — 조용히 걸러버리는 것보다 투명하게 보여주는 쪽을 택했습니다. 한
번에 한 경기 분량만 입력해야 합니다 (여러 경기를 섞으면 같은 마켓끼리 잘못 묶임).

**서로 다른 마켓을 일부러 묶기(`group`)**: 기본은 (마켓, 라인)이 같아야만 한
그룹으로 묶이지만, 각 행에 같은 "그룹" 이름을 적으면 승무패 행과 오버언더 행처럼
서로 다른 마켓이라도 강제로 한 그룹으로 묶어서 배당 합을 계산해줍니다. 다만 이렇게
묶은 그룹은 **절대 `verified: true`가 되지 않습니다** — 승패와 오버언더처럼 서로
다른 마켓의 결과는 대부분 통계적으로 독립이 아니라서(예: 이기는 팀과 총 득점은
서로 영향을 줌), 각 마켓의 배당을 그대로 묶어 계산한 숫자는 진짜 확정 수익이
아니기 때문입니다. 북메이커가 실제로 파는 결합 마켓(예: "홈팀 승리 & 오버 2.5")의
가격 자체를 하나의 배당으로 넣었을 때만 이 숫자가 의미를 가지며, 결과 카드에 이
사실이 경고 문구로 항상 표시됩니다.

**목표 적중률 기반 부분 커버 픽 (`min_hit_rate_percent`)**: "확정 수익 찾기" 버튼
옆에 목표 적중률(%)을 입력하면, 완전한 결과 분할이 검증되는 마켓(승무패·유럽식
핸디캡·아시안 핸디캡·오버언더·BTTS)마다 **일부러 선택지 일부를 빼고** 남은
선택지들의 공정 확률(배당을 devig한 값) 합이 목표 적중률 이상이 되는 조합 중
마진이 가장 큰 것을 별도로 찾아 "적중률 목표 픽" 섹션에 보여줍니다. 예를 들어
승무패 3개 중 확률이 가장 낮은 무승부를 빼고 승/패만 헤지하면, 목표 적중률만
넘긴다면 마진이 3-way 전체 커버보다 항상 같거나 높아집니다 (덜 커버할수록
분모(배당 역수 합)가 작아지므로). **이건 확정 수익이 아닙니다** — 제외한
선택지가 실제로 나오면 그 조합에 건 돈을 전부 잃습니다. 그래서 기존 확정 수익
카드와 완전히 다른 섹션·다른 배지로 분리해서 보여주고, 제외된 선택지도 항상
같이 표시합니다. 정확한 스코어처럼 전체 경우의 수를 알 수 없는(검증 안 되는)
마켓에는 이 계산 자체를 절대 적용하지 않습니다 — "나머지 확률"을 신뢰할 근거가
없기 때문입니다.

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
least-squares), then compares that model's view against quoted prices —
either an exotic market (`find_value_edges`) or the same match's other
Totals/Asian-Handicap lines (`find_cross_line_edges`, the one that keeps
working with a single provider). This is a **value-betting** tool, not an
arbitrage engine — keep that distinction when presenting results to users.

## Running locally

### Backend

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in ODDS_API_KEY and/or PINNACLE_USERNAME/PASSWORD
.venv/bin/uvicorn app.main:app --reload --port 7001
```

Visit `http://localhost:7001/opportunities` with header `x-api-key:
dev-local-key` (or `/health`, no auth). **With no provider configured yet,
this correctly returns `[]`** — that's not broken, that's the app refusing
to show fabricated picks. Fill in `ODDS_API_KEY` (recommended, see above)
and/or approved Pinnacle credentials to see real data.

Run tests (these use hand-computed fixture data under `tests/fixtures.py`,
never the real app — see its docstring):

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

Visit `http://localhost:7000`.

### 상시 실행 (도커 없이, 계속 켜두기)

백엔드 프로세스가 살아있는 동안 `app/scheduler.py`의 APScheduler 잡이
`POLL_INTERVAL_SECONDS`(기본 60초)마다 자동으로 모든 프로바이더(Pinnacle 포함)를
폴링하고 스캔합니다 — 별도 크론 없이 그냥 프로세스를 계속 띄워두면 됩니다. 터미널을
닫아도 유지하려면:

```bash
cd backend
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 7001 > backend.log 2>&1 &
```

```bash
cd frontend
nohup npm start > frontend.log 2>&1 &   # 먼저 npm run build 한 번 실행
```

(`tmux`/`screen` 세션 안에서 그냥 `uvicorn ...`, `npm run dev`를 포그라운드로 띄워두는
방법도 동일하게 잘 동작합니다. systemd 유닛으로 등록하면 재부팅 후에도 자동 재시작됩니다.)

### 상시 실행 (PM2, 도커·가상환경 없이 한 번에)

가상환경 없이 시스템 Python에 그대로 설치하고, 백엔드·프론트엔드를 PM2 하나로
같이 띄우고 싶다면:

```bash
# 1) 백엔드 의존성 (venv 없이, 시스템/사용자 site-packages에 설치)
cd backend
pip3 install --user -r requirements.txt
# Debian/Ubuntu류에서 "externally-managed-environment" 에러가 나면:
#   pip3 install --user --break-system-packages -r requirements.txt
cp .env.example .env   # DATABASE_URL을 sqlite+aiosqlite:///./yb.db 로 바꾸기

# 2) 프론트엔드 빌드 (PM2는 `next start`만 실행하므로 build가 미리 되어 있어야 함)
cd ../frontend
npm install
cp .env.local.example .env.local
npm run build

# 3) PM2 설치 (전역, 한 번만)
npm install -g pm2

# 4) 저장소 루트에서 백엔드+프론트엔드 한 번에 기동
cd ..
pm2 start ecosystem.config.js
```

저장소 루트의 `ecosystem.config.js`가 `yb-backend`(`python3 -m uvicorn ...`,
`backend/.env`를 그대로 읽음, 7001번 포트)와 `yb-frontend`(`npm start`, 7000번 포트) 두
프로세스를 정의합니다. 자주 쓰는 명령:

```bash
pm2 status              # 두 프로세스 상태 확인
pm2 logs                # 로그 실시간으로 보기 (Ctrl+C로 종료해도 프로세스는 안 죽음)
pm2 logs yb-backend      # 백엔드 로그만
pm2 restart ecosystem.config.js   # .env 등 설정 바꾼 뒤 재시작
pm2 stop ecosystem.config.js      # 둘 다 정지
pm2 save && pm2 startup           # 서버 재부팅 후에도 자동으로 다시 뜨게 등록
```

`backend/.env`를 고친 뒤에는 (예: `ODDS_API_KEY` 나중에 채워 넣을 때) 반드시
`pm2 restart yb-backend`로 재시작해야 반영됩니다 — pydantic-settings가 프로세스
시작 시점에 `.env`를 한 번만 읽기 때문입니다.

### Docker Compose (선택사항)

```bash
docker compose up --build
```

Backend on `:7001`, frontend on `:7000`, Postgres on `:5432`. Set
`ODDS_API_KEY` and/or `PINNACLE_USERNAME`/`PINNACLE_PASSWORD` as
environment variables (or a `.env` file docker-compose reads) once you
have real accounts — with neither set, it comes up fine and shows an
empty dashboard.

## Connecting real odds

- **Pinnacle**: **public API access has been closed since July 23rd,
  2025** — an existing account/password is not enough anymore; new access
  requires emailing api@pinnacle.com. This adapter is built against the
  official OpenAPI schema at
  https://github.com/pinnacleapi/pinnacleapi-documentation (specifically
  `openapi-specification/linesapi-oas.yaml`), not guessed — but it has
  still never been exercised against a real, authenticated response
  (Pinnacle also blocks requests from a long list of jurisdictions with
  HTTP 451, which is as far as this project got). Two things worth
  knowing about how it actually works:
  - **Two calls, joined by event id.** `GET /v2/odds` returns only
    numeric event ids and the actual prices — no team names or start
    times at all. Those come from a separate `GET /v1/fixtures` call
    (note: its top-level key is the *singular* `league`, while odds uses
    *plural* `leagues` — an easy mismatch to introduce by hand). The
    adapter fetches both per sport and joins them; an odds event with no
    matching fixture is dropped rather than guessed at.
  - **Fair-use rate limits are enforced in code, not just documented.**
    Pinnacle limits `/odds` (and `/fixtures`) to 1 request per 2 minutes
    per sportId, and `/sports` to once per hour. `PinnacleProvider`
    tracks this itself and reuses the last successful result when called
    again too soon — so a short `POLL_INTERVAL_SECONDS` can never get an
    account throttled or suspended regardless of how this project is
    configured.
  - The parser is still defensive at every nesting level (payload,
    league, event, period, individual row) — a malformed or
    differently-shaped item is skipped rather than raising. Both this and
    the fixtures/odds join are covered by tests
    (`tests/test_providers.py`, including `httpx.MockTransport`
    integration tests of the whole fetch flow), so a schema mismatch
    degrades to "fewer quotes" instead of crashing the poll — but "covered
    by tests against the documented schema" is not the same claim as
    "confirmed against live data."
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
TELEGRAM_MIN_MARGIN_PERCENT=1.0        # 확정 수익 픽: 이 마진(%) 이상만 알림
TELEGRAM_MIN_EDGE_PERCENT=3.0          # 단폴더 가치 엣지: 이 모델 엣지(%) 이상만 알림
```

매 폴링 사이클마다 **새로 나타난** 것만(이미 알림을 보낸, 여전히 살아있는 픽/엣지는
재알림하지 않음) 박스별로 정리해 채널 두 개로 나눠 보냅니다:

```
🎯 확정 수익 픽 2건 발견

[1] Arsenal vs Chelsea
마켓: Moneyline 3Way
확정 마진: +2.44%
  ▸ home @ Pinnacle  2.10
  ▸ draw @ BookB  3.60
  ▸ away @ BookC  4.50

[2] ...
```

```
🔎 가치 베팅 엣지 1건 발견 (확정 수익 아님)

[1] Man City vs Newcastle
마켓: Totals (라인 3.5) · 선택: over
Pinnacle @ 4.72  (모델 엣지 +35.0%)
```

피나클만 설정한 경우 첫 번째 채널은 거의 항상 비어 있고(정상입니다 — "피나클만 쓰는
경우" 참고), 두 번째 채널이 실질적으로 계속 오는 알림이 됩니다.

## Current scope / what's next

Shipped: core-market arbitrage (1X2, 2-way moneyline, European 3-way
handicap, totals, Asian handicap incl. quarter lines, BTTS), push-aware
math, stake calculator (both auto, from scanner-detected opportunities,
and manual — throw in odds across as many markets as you want for one
match via `POST /calculator/scan` / the "수동 계산기" dashboard section,
and it picks out which combination(s) actually clear 100% margin),
value-edge model (exotic markets + same-book
cross-line consistency — works with just Pinnacle), API-key auth, live
pick-box dashboard,
신규 확정픽 브라우저 알림(+소리), 확정픽/가치엣지 각각 별도 채널의
텔레그램 서버 사이드 알림, 크로스 프로바이더 아비트리지 병합,
dummy-data-free (설정 안 하면 빈 화면). 다폴더(파레이)는 의도적으로 없음 — 위
"왜 다폴더가 없는지" 참고.

Still blocking real use (see "실데이터 연결" above), in priority order:
1. `ODDS_API_KEY` 구독 (Business 플랜, $99/월) — 이게 있어야 실제로 뭔가 뜸.
2. (선택) 피나클 API 신규 신청 승인 (`api@pinnacle.com`) — 승인돼도 이
   프로젝트의 파서는 공식 스펙 기준으로 짰을 뿐 실응답으로 검증된 적은 없어서,
   승인 후 한 번 실제 응답과 대조해봐야 함.
3. 어딘가에 상시 실행 (본인 PC 또는 VPS) — 이 세션/컨테이너는 껐다 켜면
   사라짐, "상시 실행" 항목 참고.

Not built yet (natural next steps, intentionally out of scope for this
MVP): user signup/billing (Stripe), DB migrations (Alembic — currently
`create_all` on startup), more sports/markets, odds history charts,
per-user bookmaker account management for actually placing bets (this
scaffold only surfaces opportunities, it never places a bet for you).
Parlays (same-game or cross-match) are not a "not built yet" — see "왜
다폴더가 없는지" above for why that's a deliberate, permanent choice
rather than a missing feature.
