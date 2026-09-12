#!/usr/bin/env python3
"""Standalone diagnostic: check what The Odds API actually returns for a
soccer league BEFORE paying for anything beyond the free Starter plan.

Why this exists: The Odds API's own docs say "spreads and totals markets
are mainly available for US sports and bookmakers at this time," and give
no soccer example beyond h2h. Whether that caveat still meaningfully
applies to soccer, and what btts/correct_score outcome names actually
look like, can only be settled by a real response — not by reading docs.
The free "Starter" plan (500 credits/month, no payment) is enough to run
this. Only spend money on a paid plan after this script shows the
markets/coverage you actually need.

Usage:
    ODDS_API_KEY=xxxx python scripts/verify_odds_api.py
    python scripts/verify_odds_api.py --key xxxx --sport soccer_epl

Uses only the standard library + httpx (already a project dependency) —
no app imports, so it works even before the rest of the app is set up.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

BASE_URL = "https://api.the-odds-api.com"


def fetch(client: httpx.Client, sport_key: str, api_key: str, markets: str) -> tuple[int, object, dict[str, str]]:
    resp = client.get(
        f"{BASE_URL}/v4/sports/{sport_key}/odds",
        params={"apiKey": api_key, "regions": "us,uk,eu", "markets": markets, "oddsFormat": "decimal"},
    )
    try:
        payload = resp.json()
    except ValueError:
        payload = resp.text
    return resp.status_code, payload, dict(resp.headers)


def summarize(label: str, status: int, payload: object, headers: dict[str, str]) -> None:
    print(f"\n=== {label} ===")
    print(f"HTTP {status}")
    remaining = headers.get("x-requests-remaining")
    used = headers.get("x-requests-used")
    if remaining is not None:
        print(f"credits remaining: {remaining} (used so far this key: {used})")

    if status != 200:
        print("응답 (에러로 추정):")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
        return

    if not isinstance(payload, list):
        print("예상치 못한 응답 형태 (list가 아님):", type(payload))
        return

    print(f"이벤트 수: {len(payload)}")
    if not payload:
        print(">>> 빈 리스트 — 이 마켓/스포츠 조합에 응답이 아예 없습니다.")
        return

    market_books: dict[str, set[str]] = {}
    sample_correct_score_names: list[str] = []
    sample_spread_points: set[float] = set()
    sample_total_points: set[float] = set()

    for event in payload[:5]:
        for bm in event.get("bookmakers", []):
            for market in bm.get("markets", []):
                key = market.get("key", "?")
                market_books.setdefault(key, set()).add(bm.get("title", bm.get("key", "?")))
                if key == "correct_score":
                    for o in market.get("outcomes", [])[:6]:
                        sample_correct_score_names.append(str(o.get("name")))
                if key == "spreads":
                    for o in market.get("outcomes", []):
                        if o.get("point") is not None:
                            sample_spread_points.add(o["point"])
                if key == "totals":
                    for o in market.get("outcomes", []):
                        if o.get("point") is not None:
                            sample_total_points.add(o["point"])

    print("\n실제로 응답에 나타난 마켓별 북메이커:")
    if not market_books:
        print("  (마켓이 하나도 없음 — 이 조합은 이 플랜/스포츠에서 지원 안 되는 것으로 보입니다)")
    for key, books in sorted(market_books.items()):
        print(f"  {key}: {sorted(books)}")

    if sample_spread_points:
        print(f"\nspreads 라인 예시: {sorted(sample_spread_points)}")
        quarter = [p for p in sample_spread_points if abs(p * 4 % 1) < 1e-6 and abs(p * 2 % 1) > 1e-6]
        print(f"  쿼터 라인(.25/.75) 포함 여부: {'예 -> ' + str(sorted(quarter)) if quarter else '아니오 (정수/반정수만)'}")
    if sample_total_points:
        print(f"totals 라인 예시: {sorted(sample_total_points)}")

    if sample_correct_score_names:
        print(f"\ncorrect_score 실제 outcome 이름 예시: {sample_correct_score_names}")
        print("  -> app/providers/oddsapi.py의 정규식 파서가 이 포맷에서 정수 2개를")
        print("     제대로 뽑아내는지 직접 확인하세요 (예: '2:1', '1-0' 등은 OK,")
        print("     다른 포맷이면 파서 조정 필요).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default=os.environ.get("ODDS_API_KEY", ""))
    parser.add_argument("--sport", default=os.environ.get("ODDS_API_SPORT_KEYS", "soccer_epl").split(",")[0])
    args = parser.parse_args()

    if not args.key:
        print("ODDS_API_KEY가 없습니다. --key로 넘기거나 환경변수로 설정하세요.", file=sys.stderr)
        print("무료 Starter 플랜(월 500 크레딧, 결제 불필요)으로 https://the-odds-api.com 에서 발급 가능합니다.", file=sys.stderr)
        return 1

    print(f"스포츠: {args.sport}  (이 스크립트는 크레딧을 소비합니다 — 요청 1건당 markets 세트별로 1크레딧 내외)")

    with httpx.Client(timeout=15.0) as client:
        status, payload, headers = fetch(client, args.sport, args.key, "h2h,spreads,totals")
        summarize("코어 마켓 (h2h,spreads,totals)", status, payload, headers)

        status2, payload2, headers2 = fetch(client, args.sport, args.key, "btts,correct_score")
        summarize("추가 마켓 (btts,correct_score)", status2, payload2, headers2)

    print(
        "\n결론 가이드: 위에서 spreads/totals에 북메이커가 실제로 찍혀 있으면 "
        "이 프로젝트의 아비트리지 핵심 기능이 이 스포츠에 대해 동작합니다. "
        "비어 있으면 유료 플랜을 사도 똑같이 빌 가능성이 높으니, 다른 sport_key나 "
        "region 조합을 먼저 이 스크립트로 시험해보세요."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
