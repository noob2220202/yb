"use client";

import { useEffect, useMemo, useState } from "react";
import {
  calculateHedgeBox,
  fetchMatches,
  sendHedgeBox,
  type HedgeBoxCalculateResult,
  type Match,
  type MatchSelection,
} from "@/lib/api";

function toDatetimeLocal(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatKst(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  });
}

interface BuilderLeg {
  label: string;
  odds: string;
}

function emptyLeg(): BuilderLeg {
  return { label: "", odds: "" };
}

export default function Page() {
  const [matches, setMatches] = useState<Match[]>([]);
  const [matchesError, setMatchesError] = useState<string | null>(null);
  const [matchesLoading, setMatchesLoading] = useState(true);

  const [event, setEvent] = useState("");
  const [league, setLeague] = useState("");
  const [commenceTime, setCommenceTime] = useState("");
  const [legA, setLegA] = useState<BuilderLeg>(emptyLeg());
  const [legB, setLegB] = useState<BuilderLeg>(emptyLeg());
  const [excludedOdds, setExcludedOdds] = useState("");
  const [targetProfit, setTargetProfit] = useState("10000");
  const [roundTo, setRoundTo] = useState("100");

  const [result, setResult] = useState<HedgeBoxCalculateResult | null>(null);
  const [calcError, setCalcError] = useState<string | null>(null);
  const [calculating, setCalculating] = useState(false);

  const [sendState, setSendState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const [sendDetailUrl, setSendDetailUrl] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  useEffect(() => {
    fetchMatches()
      .then(setMatches)
      .catch((err) => setMatchesError((err as Error).message))
      .finally(() => setMatchesLoading(false));
  }, []);

  function resetResult() {
    setResult(null);
    setCalcError(null);
    setSendState("idle");
    setSendDetailUrl(null);
    setSendError(null);
  }

  function pickSelection(match: Match, selection: MatchSelection) {
    resetResult();
    setEvent(match.event);
    setLeague(match.league);
    setCommenceTime(toDatetimeLocal(match.commence_time));

    const label = `${selection.market_label}${selection.line !== null ? " " + selection.line : ""} · ${selection.selection_label}`;
    const odds = String(selection.decimal_odds);

    if (!legA.label) {
      setLegA({ label, odds });
    } else if (!legB.label) {
      setLegB({ label, odds });
    } else {
      // 둘 다 채워져 있으면 두 번째 다리를 새 선택으로 교체
      setLegB({ label, odds });
    }
  }

  function clearLegs() {
    setLegA(emptyLeg());
    setLegB(emptyLeg());
    resetResult();
  }

  async function calculate() {
    resetResult();
    const oddsA = Number(legA.odds);
    const oddsB = Number(legB.odds);
    const profit = Number(targetProfit);
    const round = Number(roundTo) || 100;
    const excluded = excludedOdds.trim() ? Number(excludedOdds) : null;

    if (!legA.label.trim() || !legB.label.trim()) {
      setCalcError("두 다리 모두 이름을 입력하세요.");
      return;
    }
    if (!Number.isFinite(oddsA) || oddsA <= 1 || !Number.isFinite(oddsB) || oddsB <= 1) {
      setCalcError("배당은 1보다 큰 숫자여야 합니다.");
      return;
    }
    if (!Number.isFinite(profit) || profit <= 0) {
      setCalcError("목표 순이익은 0보다 커야 합니다.");
      return;
    }

    setCalculating(true);
    try {
      const res = await calculateHedgeBox({
        leg_a: { label: legA.label, decimal_odds: oddsA },
        leg_b: { label: legB.label, decimal_odds: oddsB },
        target_profit: profit,
        stake_round_to: round,
        excluded_odds: excluded,
      });
      setResult(res);
    } catch (err) {
      setCalcError((err as Error).message);
    } finally {
      setCalculating(false);
    }
  }

  async function sendToTelegram() {
    if (!result) return;
    setSendState("sending");
    setSendError(null);
    try {
      const res = await sendHedgeBox({
        event: event.trim() || "미입력 경기",
        league: league.trim(),
        commence_time: commenceTime ? new Date(commenceTime).toISOString() : new Date().toISOString(),
        leg_a: { label: legA.label, decimal_odds: Number(legA.odds) },
        leg_b: { label: legB.label, decimal_odds: Number(legB.odds) },
        target_profit: Number(targetProfit),
        stake_round_to: Number(roundTo) || 100,
        excluded_odds: excludedOdds.trim() ? Number(excludedOdds) : null,
      });
      setSendState(res.sent ? "sent" : "failed");
      setSendDetailUrl(res.detail_url);
      if (!res.sent) {
        setSendError("텔레그램 전송에 실패했습니다 (봇 토큰/채널 ID 설정을 확인하세요). 박스 자체는 저장되었습니다.");
      }
    } catch (err) {
      setSendState("failed");
      setSendError((err as Error).message);
    }
  }

  const groupedMatches = useMemo(() => {
    const byLeague = new Map<string, Match[]>();
    for (const m of matches) {
      const key = m.league || m.sport;
      if (!byLeague.has(key)) byLeague.set(key, []);
      byLeague.get(key)!.push(m);
    }
    return Array.from(byLeague.entries());
  }, [matches]);

  return (
    <main className="page">
      <header className="header">
        <h1>🐐 헤지 박스 빌더</h1>
        <p className="hint">
          한 경기에 배당 두 개를 골라 목표 순이익(원)을 채우면, 그 금액이 나오도록 배팅금을 자동
          계산합니다. + 값이 나오면 텔레그램 채널로 바로 내보낼 수 있습니다.
        </p>
      </header>

      <section className="card">
        <h2>경기 목록</h2>
        <p className="hint small">
          자동으로 받아온 배당입니다 — 참고용 시작값일 뿐, 실제로 걸 배당은 아래 박스 빌더에서
          직접 확인·수정하세요. 선택지를 클릭하면 박스 빌더의 빈 다리에 채워집니다.
        </p>
        {matchesLoading && <p className="hint">불러오는 중...</p>}
        {matchesError && <p className="error">{matchesError}</p>}
        {!matchesLoading && !matchesError && matches.length === 0 && (
          <p className="hint">지금은 자동으로 받아온 경기가 없어요 — 아래 박스 빌더에 직접 입력해도 됩니다.</p>
        )}
        <div className="match-list">
          {groupedMatches.map(([leagueName, leagueMatches]) => (
            <div key={leagueName} className="league-group">
              <h3>{leagueName}</h3>
              {leagueMatches.map((m) => (
                <MatchCard key={m.id} match={m} onPick={pickSelection} />
              ))}
            </div>
          ))}
        </div>
      </section>

      <section className="card builder">
        <h2>박스 빌더</h2>

        <div className="field-row">
          <label>
            경기
            <input value={event} onChange={(e) => setEvent(e.target.value)} placeholder="예: 크리스탈 팰리스 vs 입스위치" />
          </label>
          <label>
            리그
            <input value={league} onChange={(e) => setLeague(e.target.value)} placeholder="예: 프리미어리그" />
          </label>
        </div>
        <div className="field-row">
          <label>
            경기 시작 시각
            <input type="datetime-local" value={commenceTime} onChange={(e) => setCommenceTime(e.target.value)} />
          </label>
        </div>

        <div className="legs">
          <LegInput
            index={1}
            leg={legA}
            onChange={(leg) => {
              resetResult();
              setLegA(leg);
            }}
          />
          <LegInput
            index={2}
            leg={legB}
            onChange={(leg) => {
              resetResult();
              setLegB(leg);
            }}
          />
        </div>
        <button type="button" className="ghost-button" onClick={clearLegs}>
          두 다리 모두 비우기
        </button>

        <div className="field-row">
          <label>
            목표 순이익 (원)
            <input
              type="number"
              min={1}
              value={targetProfit}
              onChange={(e) => {
                resetResult();
                setTargetProfit(e.target.value);
              }}
            />
          </label>
          <label>
            배팅금 반올림 단위 (원)
            <input
              type="number"
              min={1}
              value={roundTo}
              onChange={(e) => {
                resetResult();
                setRoundTo(e.target.value);
              }}
            />
          </label>
        </div>
        <div className="field-row">
          <label>
            제외될 결과의 배당 (선택, 적중률 참고용)
            <input
              type="number"
              step={0.01}
              min={1.01}
              placeholder="예: 두 다리에 없는 나머지 결과의 배당"
              value={excludedOdds}
              onChange={(e) => {
                resetResult();
                setExcludedOdds(e.target.value);
              }}
            />
          </label>
        </div>

        <button type="button" className="primary-button" onClick={calculate} disabled={calculating}>
          {calculating ? "계산 중..." : "계산하기"}
        </button>
        {calcError && <p className="error">{calcError}</p>}

        {result && (
          <div className={`result-box ${result.guaranteed_profit > 0 ? "positive" : "negative"}`}>
            <div className="result-row">
              <span>① {legA.label} @ {Number(legA.odds).toFixed(2)}</span>
              <strong>{result.leg_a_stake.toLocaleString()}원</strong>
            </div>
            <div className="result-row">
              <span>② {legB.label} @ {Number(legB.odds).toFixed(2)}</span>
              <strong>{result.leg_b_stake.toLocaleString()}원</strong>
            </div>
            <div className="result-summary">
              <div>
                <span className="label">총 배팅</span>
                <span className="value">{result.total_stake.toLocaleString()}원</span>
              </div>
              <div>
                <span className="label">적중 시 순손익</span>
                <span className="value profit">
                  +{result.guaranteed_profit.toLocaleString()}원 ({result.profit_percent.toFixed(1)}%)
                </span>
              </div>
              {result.implied_hit_rate_percent !== null && (
                <div>
                  <span className="label">예상 적중률</span>
                  <span className="value">{result.implied_hit_rate_percent.toFixed(1)}%</span>
                </div>
              )}
            </div>
            <p className="warning">
              ⚠️ 확정 수익이 아닙니다 — 두 다리 모두 빗나가면(제외된 결과가 실제로 나오면) 베팅금
              전액을 잃습니다.
            </p>

            <button type="button" className="primary-button send-button" onClick={sendToTelegram} disabled={sendState === "sending"}>
              {sendState === "sending" ? "전송 중..." : "📤 텔레그램으로 내보내기"}
            </button>
            {sendState === "sent" && (
              <p className="success">
                전송 완료! <a href={sendDetailUrl ?? "#"} target="_blank" rel="noreferrer">박스 상세보기</a>
              </p>
            )}
            {sendState === "failed" && <p className="error">{sendError}</p>}
          </div>
        )}
      </section>
    </main>
  );
}

function MatchCard({ match, onPick }: { match: Match; onPick: (match: Match, selection: MatchSelection) => void }) {
  const groups = useMemo(() => {
    const byGroup = new Map<string, MatchSelection[]>();
    for (const s of match.selections) {
      const key = `${s.market}|${s.line ?? ""}`;
      if (!byGroup.has(key)) byGroup.set(key, []);
      byGroup.get(key)!.push(s);
    }
    return Array.from(byGroup.entries());
  }, [match]);

  return (
    <div className="match-card">
      <div className="match-card-head">
        <span className="match-time">{formatKst(match.commence_time)}</span>
        <span className="match-event">{match.event}</span>
      </div>
      <div className="market-groups">
        {groups.map(([key, selections]) => (
          <div key={key} className="market-group">
            <span className="market-group-label">
              {selections[0].market_label}
              {selections[0].line !== null ? ` ${selections[0].line}` : ""}
            </span>
            <div className="selection-chips">
              {selections.map((s) => (
                <button
                  key={`${s.market}|${s.line}|${s.selection}`}
                  type="button"
                  className="selection-chip"
                  onClick={() => onPick(match, s)}
                >
                  {s.selection_label} @{s.decimal_odds.toFixed(2)}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function LegInput({ index, leg, onChange }: { index: number; leg: BuilderLeg; onChange: (leg: BuilderLeg) => void }) {
  return (
    <div className="leg-input">
      <span className="leg-number">{index}</span>
      <input
        placeholder={`다리 ${index} 이름 (예: 무승부)`}
        value={leg.label}
        onChange={(e) => onChange({ ...leg, label: e.target.value })}
      />
      <input
        type="number"
        step={0.01}
        min={1.01}
        placeholder="배당"
        value={leg.odds}
        onChange={(e) => onChange({ ...leg, odds: e.target.value })}
      />
    </div>
  );
}
