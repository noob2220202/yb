# YB — 헤지 박스 빌더

한 경기에서 서로 다른 두 결과에 나눠 베팅해, 둘 중 하나만 적중해도 목표
순이익(원)이 남도록 배팅금을 자동으로 역산해주는 관리자용 도구입니다.
계산 결과가 마음에 들면 버튼 한 번으로 텔레그램 채널에 이모지·버튼·상세
배팅 정보가 담긴 메시지로 내보낼 수 있습니다.

- **자동화되는 것**: 프로바이더(The Odds API / Pinnacle)에서 경기·배당을
  주기적으로 자동 수집해 "경기 목록"에 보여주는 것뿐입니다.
- **자동화되지 않는 것**: 어떤 경기의 어떤 두 결과를 묶을지는 항상
  관리자가 직접 고릅니다. 배당도 항상 관리자가 그대로 쓰거나 직접
  수정할 수 있습니다. 텔레그램 전송도 "내보내기" 버튼을 눌러야만
  나갑니다 — 자동으로 발사되는 픽은 없습니다.

## 핵심 개념: 확정 수익이 아닌 "헤지 박스"

이전 버전에 있던 크로스북 아비트리지(확정 수익) 스캐너는 제거했습니다.
이 도구는 그 대신 **관리자가 스스로 "이 경기는 사실상 이 두 결과 중
하나로 끝난다"고 판단한 두 선택지**(예: "무승부" + "홈팀 -0.5 핸디캡" —
둘을 합치면 원정팀이 이길 때만 진다)에 베팅금을 나눠, 어느 쪽이 맞아도
비슷한 순이익이 남게 만드는 방식입니다.

- **목표는 마진 %가 아니라 고정 순이익 금액(원)입니다.** "총 배팅금의
  10%"가 아니라 "얼마를 벌고 싶은지"를 입력하면, 그 금액이 나오도록
  두 다리의 배팅금을 자동으로 역산합니다 (`app/engine/hedgebox.py`).
- **확정 수익이 아닙니다.** 일부러 제외한 세 번째 결과가 실제로 나오면
  베팅금 전액을 잃습니다. 그 판단(제외한 결과가 충분히 희박한가)은
  전적으로 관리자의 몫이며, 이 도구는 계산만 해줄 뿐입니다.
- **선택적으로, 제외한 결과의 배당을 알고 있다면** 입력해서 세 배당을
  모두 디빅(devig)한 예상 적중률을 참고용으로 볼 수 있습니다 — 실제
  베팅 대상은 아니고 어디까지나 "이 조합이 정말 안전한가"를 가늠하는
  참고 숫자입니다.

## 사용 흐름

1. 홈 화면의 "경기 목록"에서 자동으로 받아온 경기·배당을 훑어봅니다.
2. 마음에 드는 두 선택지(꼭 같은 마켓일 필요 없음 — 승무패의 "무승부"와
   핸디캡의 "홈팀 -0.5"처럼 섞어도 됨)를 클릭하면 아래 "박스 빌더"의
   빈 다리에 자동으로 채워집니다. 배당은 언제든 직접 수정할 수 있고,
   자동으로 받아온 경기에 없는 조합이라면 박스 빌더에 전부 손으로
   입력해도 됩니다.
3. 목표 순이익(원)을 입력하고 "계산하기"를 누르면 각 다리에 걸어야 할
   배팅금과, 적중 시 순손익·마진(%)이 나옵니다.
4. 결과가 마음에 들면 "📤 텔레그램으로 내보내기"를 눌러 설정된 채널에
   박스를 전송합니다. 전송된 박스는 번호가 매겨져 저장되고, 메시지의
   "📋 박스 상세보기" 버튼을 누르면 채널 구성원 누구나(로그인 없이) 그
   박스의 상세 페이지를 볼 수 있습니다.

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
    hedgebox.py        2다리 헤지 박스 계산 (고정 순이익 → 배팅금 역산)
    scanner.py         오즈 수집(append-only snapshot) + 경기 목록 조회 쿼리
  app/db/             SQLAlchemy models (Event/Bookmaker/OddsSnapshot/SentHedgeBox)
  app/api/            REST routes, API-key auth
  app/scheduler.py     APScheduler polling loop — 모든 프로바이더를 주기적으로
                       수집해 OddsSnapshot으로 저장만 함 (자동 탐지/알림 없음)
  app/notifications/   텔레그램 전송 (박스 메시지 포맷 + 인라인 버튼)
frontend/            Next.js — 경기 목록 + 박스 빌더 (관리자용),
                     /box/[id] 박스 상세(공개), /guide 헤지 배팅 설명(공개)
docker-compose.yml    postgres + backend + frontend (선택사항 — 로컬은 venv/npm으로 충분)
```

### API

| Method | Path | 인증 | 설명 |
|---|---|---|---|
| GET | `/health` | 없음 | 헬스체크 |
| GET | `/matches` | API 키 | 자동 수집된 예정 경기 + 최신 배당 목록 |
| POST | `/hedge-box/calculate` | API 키 | 두 배당 + 목표 순이익 → 배팅금/순손익 계산 (미리보기, 저장 안 함) |
| POST | `/hedge-box/send` | API 키 | 서버에서 다시 계산 → 저장 → 텔레그램 전송 |
| GET | `/hedge-box/{id}` | **없음 (공개)** | 전송된 박스 상세 — 텔레그램 "박스 상세보기" 버튼이 여는 페이지 |

`/hedge-box/{id}`가 인증 없이 공개인 이유는 텔레그램 채널 구성원이 봇
API 키 없이도 버튼을 눌러 볼 수 있어야 하기 때문입니다.

## 헤지 박스 계산 방법

두 배당을 `odds_a`, `odds_b`, 목표 순이익을 `T`라 하면:

1. 두 다리의 페이아웃이 같아지도록(`stake_a*odds_a == stake_b*odds_b ==
   payout`) 배팅금을 나눕니다.
2. 이때 총 배팅금은 `payout * (1/odds_a + 1/odds_b)`이고, 순이익은
   `payout * (1 - p)`입니다 (`p = 1/odds_a + 1/odds_b`).
3. `T = payout * (1-p)`를 `payout`에 대해 풀면 `payout = T / (1-p)` —
   이 값이 존재하려면(0보다 크려면) `p < 1`이어야 합니다. 즉 **고른 두
   배당의 역수 합이 이미 1 미만**이어야 하는데, 이는 정상적으로
   마진(오버라운드)이 걸린 시장에서 "결과 하나를 일부러 빼고 나머지
   둘만 산다"는 조작을 숫자로 표현한 것과 정확히 같습니다.
4. `p >= 1`인 조합(예: 두 배당 모두 너무 낮음)은 어떤 배팅금으로도
   목표 순이익을 만들 수 없으므로 계산기가 바로 거부합니다.

배팅금은 보기 좋은 숫자가 되도록 `stake_round_to`(기본 100원) 단위로
반올림하며, 그 결과 실제 순이익은 목표치와 약간(반올림 오차만큼) 다를
수 있습니다 — 결과 화면은 항상 반올림 이후의 실제 값을 보여줍니다.

## 실데이터 연결 — 권장 경로: The Odds API

피나클 자체 API는 2025년 7월 23일부로 신규 신청이 막혀 있어서(아래 참고),
**지금 가장 빠르고 확실하게 실배당을 받는 방법은 The Odds API**입니다.
피나클을 포함해 여러 북메이커 배당을 정식 라이선스로 재판매하는 서비스라
스크래핑도 아니고 ToS 위반도 아니며, 지역 차단 문제도 없습니다.

**⚠️ 유료 플랜 결제 전에 반드시 무료로 먼저 검증하세요.**

1. https://the-odds-api.com 에서 무료 Starter 키 발급 (월 500 크레딧,
   신용카드 불필요)
2. `backend/.env`에 `ODDS_API_KEY=<키>` 설정
3. `cd backend && python scripts/verify_odds_api.py` 실행 — 축구
   handicap/totals에 실제로 북메이커가 찍히는지, 쿼터 라인이 오는지
   원본 JSON 기준으로 직접 확인합니다.
4. 결과가 만족스러우면 그때 유료 플랜으로 업그레이드
   (https://theoddsapi.com/pricing).

피나클 직접 연동(선택, `PINNACLE_USERNAME`/`PINNACLE_PASSWORD`)은
2025-07-23부로 신규 승인이 막혀 있어 `api@pinnacle.com`에 별도 요청이
필요합니다 — 자세한 내용은 `backend/.env.example` 주석 참고. 이 프로젝트의
Pinnacle 어댑터는 공식 OpenAPI 스펙 기준으로 작성했지만 실응답으로
검증된 적은 없습니다.

경기 목록은 두 프로바이더 중 설정된 것을 전부 병합해서 보여줍니다 —
없으면 그냥 빈 목록이 뜰 뿐, 가짜 데이터를 채워 넣지 않습니다.

## 텔레그램 전송 설정

1. 텔레그램에서 `@BotFather`에게 `/newbot`으로 봇 생성 → 토큰 발급
2. 만든 봇을 대상 채널에 **관리자**로 추가
3. 봇과 아무 메시지나 한 번 주고받은 뒤,
   `https://api.telegram.org/bot<토큰>/getUpdates`로 chat id 확인
4. `backend/.env`에 설정:

```
TELEGRAM_BOT_TOKEN=<토큰>
TELEGRAM_CHAT_ID=<채팅/채널 ID>
PUBLIC_FRONTEND_URL=https://your-real-domain-or-ip:7000
```

`PUBLIC_FRONTEND_URL`은 전송된 박스의 "📋 박스 상세보기" /
"📖 헤지 배팅이란?" 텔레그램 버튼이 가리키는 주소입니다 — 로컬
`localhost`로 두면 채널 구성원이 못 여니, 실제 서버 주소로 바꿔야
합니다. 설정하지 않으면 전송 자체는 그냥 조용히 스킵됩니다(에러 없음) —
박스 자체는 항상 저장되고 관리자 화면에는 계속 결과가 보입니다.

전송되는 메시지 예시:

```
🐐 BOX #12
프리미어리그 · 09/12(금) 23:00 KST
⚽ 크리스탈 팰리스 vs 입스위치

① 무승부 @ 3.75 → 12,800원
② 크리스탈 팰리스 -0.5 @ 1.91 → 25,100원

💰 총 배팅 37,900원
✅ 적중 시 순손익 +10,016원 (26.4%)
📊 예상 적중률 약 92.0% (제외된 결과 배당 기준 참고용)

⚠️ 확정 수익이 아닙니다 — 두 다리 모두 빗나가면(제외된 결과가 실제로
나오면) 베팅금 전액을 잃습니다.

[📋 박스 상세보기]  [📖 헤지 배팅이란?]
```

## Getting started

### Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # ODDS_API_KEY, TELEGRAM_* 등 채워 넣기
uvicorn app.main:app --reload --port 7001
```

`curl http://localhost:7001/matches -H "x-api-key: dev-local-key"` (또는
`/health`, 인증 불필요). **프로바이더를 아직 설정하지 않았다면
정상적으로 `[]`가 돌아옵니다** — 고장이 아니라 가짜 데이터를 보여주지
않는다는 뜻입니다.

테스트 실행 (`tests/fixtures.py`의 손으로 계산한 고정 데이터만 사용,
실제 프로바이더 필요 없음):

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

`http://localhost:7000` 접속.

### 상시 실행 (PM2, 도커·가상환경 없이 한 번에)

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

`backend/.env`를 고친 뒤에는 반드시 `pm2 restart yb-backend`로
재시작해야 반영됩니다. `frontend/.env.local`을 고친 뒤에는
`NEXT_PUBLIC_*` 값이 빌드 시점에 굳어지므로 `npm run build`를 다시
실행한 뒤 `pm2 restart yb-frontend`해야 합니다.

### Docker Compose (선택사항)

```bash
docker compose up --build
```

Backend on `:7001`, frontend on `:7000`, Postgres on `:5432`.

## 알려진 제약사항

- **관리자 API 키가 프론트엔드 빌드에 그대로 노출됩니다.** `x-api-key`는
  `NEXT_PUBLIC_API_KEY`로 브라우저 JS 번들에 박히므로, 이 사이트 자체를
  누구나 열 수 있게 인터넷에 공개하면 그 키를 브라우저 개발자 도구로
  빼내 관리자 엔드포인트(박스 전송 등)를 호출할 수 있습니다. 지금은
  "관리자 혼자 쓰는 도구"라는 전제로 만들어졌으니, 다른 사람도 접근할
  수 있는 곳에 배포한다면 이 홈 화면 자체를 별도 인증(사내망, VPN,
  reverse proxy 인증 등) 뒤에 두는 걸 권장합니다. `/box/[id]`와
  `/guide`는 원래부터 공개 페이지라 이 문제와 무관합니다.
- 라이브 스코어/진행 상황 표시는 없습니다 — 오즈 프로바이더가 주는
  경기 시작 시각까지만 보여줍니다.
- DB 마이그레이션 도구(Alembic) 없이 시작 시 `create_all`만 실행합니다
  — 스키마를 바꾸면 기존 SQLite/Postgres 파일을 지우거나 직접
  마이그레이션해야 합니다.
