"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  HitRatePick,
  Opportunity,
  ScanGroupResult,
  ScanLegInput,
  ScanResponse,
  StakePlan,
  ValueEdge,
  fetchOpportunities,
  fetchStakePlan,
  fetchValueEdges,
  scanManualOdds,
} from "@/lib/api";

const REFRESH_MS = 15000;
const THRESHOLD_KEY = "yb.notifyThreshold";
const NOTIFY_KEY = "yb.notifyEnabled";

interface ScanMarketOption {
  value: string;
  label: string;
  selections: string[] | null; // null = free-text selection (correct score, custom)
  needsLine: boolean;
}

const SCAN_MARKETS: ScanMarketOption[] = [
  { value: "moneyline_3way", label: "승무패 (승/무/패)", selections: ["home", "draw", "away"], needsLine: false },
  { value: "moneyline_2way", label: "승패 (무승부 없음)", selections: ["home", "away"], needsLine: false },
  { value: "european_handicap", label: "유럽식 핸디캡 (3-way)", selections: ["home", "draw", "away"], needsLine: true },
  { value: "asian_handicap", label: "아시안 핸디캡 (쿼터 라인 지원)", selections: ["home", "away"], needsLine: true },
  { value: "totals", label: "오버언더", selections: ["over", "under"], needsLine: true },
  { value: "btts", label: "양팀득점 (BTTS)", selections: ["yes", "no"], needsLine: false },
  { value: "correct_score", label: "정확한 스코어 (검증 안 됨)", selections: null, needsLine: false },
  { value: "custom", label: "기타/커스텀 (검증 안 됨)", selections: null, needsLine: false },
];

interface ScanRowState {
  id: number;
  market: string;
  customLabel: string;
  line: string;
  selection: string;
  bookmaker: string;
  odds: string;
  group: string;
}

let nextScanRowId = 0;
function makeScanRow(): ScanRowState {
  const def = SCAN_MARKETS[0];
  return {
    id: nextScanRowId++,
    market: def.value,
    customLabel: "",
    line: "",
    selection: def.selections ? def.selections[0] : "",
    bookmaker: "",
    odds: "",
    group: "",
  };
}

function marketLabel(market: string, line: number | null): string {
  const base = market
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
  return line === null || line === undefined ? base : `${base} (${line})`;
}

function playChime() {
  try {
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new AudioCtx();
    const notes = [880, 1320];
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.12);
      gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + i * 0.12 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.12 + 0.3);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + i * 0.12);
      osc.stop(ctx.currentTime + i * 0.12 + 0.32);
    });
  } catch {
    // 오디오를 재생할 수 없는 환경(자동재생 차단 등)은 조용히 무시
  }
}

export default function Page() {
  const [opportunities, setOpportunities] = useState<Opportunity[] | null>(null);
  const [valuePicks, setValuePicks] = useState<ValueEdge[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newIds, setNewIds] = useState<Set<number>>(new Set());
  const [notifyEnabled, setNotifyEnabled] = useState(false);
  const [threshold, setThreshold] = useState(1.0);
  const seenIds = useRef<Set<number> | null>(null);

  useEffect(() => {
    try {
      const savedThreshold = localStorage.getItem(THRESHOLD_KEY);
      if (savedThreshold) setThreshold(Number(savedThreshold));
      setNotifyEnabled(localStorage.getItem(NOTIFY_KEY) === "1" && Notification?.permission === "granted");
    } catch {
      // 프라이빗 브라우징 등에서 localStorage 접근 불가 시 기본값 유지
    }
  }, []);

  const notifyNewPicks = useCallback(
    (fresh: Opportunity[]) => {
      if (!notifyEnabled) return;
      const qualifying = fresh.filter((o) => o.margin_percent >= threshold);
      if (qualifying.length === 0) return;
      try {
        const top = qualifying[0];
        new Notification("확정 수익 픽 발견", {
          body: `${top.event} · ${marketLabel(top.market, top.line)} · +${top.margin_percent.toFixed(2)}%${
            qualifying.length > 1 ? ` 외 ${qualifying.length - 1}건` : ""
          }`,
          tag: "yb-arbitrage",
        });
      } catch {
        // 알림 생성 실패는 무시 (권한 회수 등)
      }
    },
    [notifyEnabled, threshold]
  );

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [opps, edges] = await Promise.all([fetchOpportunities(), fetchValueEdges()]);
        if (cancelled) return;

        if (seenIds.current !== null) {
          const fresh = opps.filter((o) => !seenIds.current!.has(o.id));
          if (fresh.length > 0) {
            setNewIds(new Set(fresh.map((o) => o.id)));
            notifyNewPicks(fresh);
            if (fresh.some((o) => o.margin_percent >= threshold)) playChime();
            setTimeout(() => setNewIds(new Set()), 4000);
          }
        }
        seenIds.current = new Set(opps.map((o) => o.id));

        const sorted = [...edges].sort((a, b) => b.edge_percent - a.edge_percent);

        setOpportunities(opps);
        setValuePicks(sorted);
        setError(null);
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      }
    }

    load();
    const interval = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [notifyNewPicks, threshold]);

  async function enableNotifications() {
    try {
      const permission = await Notification.requestPermission();
      const enabled = permission === "granted";
      setNotifyEnabled(enabled);
      localStorage.setItem(NOTIFY_KEY, enabled ? "1" : "0");
    } catch {
      setNotifyEnabled(false);
    }
  }

  function updateThreshold(value: number) {
    setThreshold(value);
    try {
      localStorage.setItem(THRESHOLD_KEY, String(value));
    } catch {
      // 저장 실패는 무시, 세션 동안만 적용
    }
  }

  const avgMargin =
    opportunities && opportunities.length > 0
      ? opportunities.reduce((sum, o) => sum + o.margin_percent, 0) / opportunities.length
      : 0;

  return (
    <main>
      <header className="app-header">
        <div className="brand">
          <div className="brand-mark">YB</div>
          <div>
            <h1>확정픽 스캐너</h1>
            <p className="subtitle">여러 북메이커 배당을 실시간 대조해 무위험 구간을 찾습니다</p>
          </div>
        </div>
        <span className="live-pill">
          <span className="live-dot" />
          실시간 감지 중
        </span>
      </header>

      {error && <div className="disclaimer">API에 연결할 수 없습니다: {error}</div>}

      <div className="bento">
        <div className="bento-tile accent">
          <span className="label">확정 수익 픽</span>
          <span className="value">{opportunities?.length ?? "–"}</span>
        </div>
        <div className="bento-tile">
          <span className="label">평균 마진</span>
          <span className="value">{opportunities?.length ? `${avgMargin.toFixed(1)}%` : "–"}</span>
        </div>
        <div className="bento-tile">
          <span className="label">가치 픽</span>
          <span className="value">{valuePicks?.length ?? "–"}</span>
        </div>
      </div>

      <div className="notify-bar">
        <span>
          {notifyEnabled ? (
            <>
              <strong>알림 켜짐</strong> · 마진 {threshold}% 이상일 때 알려드려요
            </>
          ) : (
            "새 확정픽이 뜨면 즉시 알림을 받아보세요"
          )}
        </span>
        <div className="notify-actions">
          <input
            type="number"
            step={0.1}
            min={0}
            value={threshold}
            onChange={(e) => updateThreshold(Number(e.target.value))}
            aria-label="알림 최소 마진 %"
          />
          {!notifyEnabled && (
            <button className="btn" onClick={enableNotifications}>
              🔔 알림 켜기
            </button>
          )}
        </div>
      </div>

      <section>
        <div className="section-head">
          <h2>확정 수익 픽</h2>
          <span className="count">{opportunities?.length ?? 0}건</span>
        </div>
        <p className="hint">
          동일 이벤트·동일 마켓에서 북메이커별 최고 배당만 모은 조합입니다. 모든 다리에 베팅하면
          결과와 무관하게 표시된 마진만큼 확정 수익 (또는 &quot;푸시 가능&quot; 표기 시 최악의
          경우 원금 보전)이 발생합니다.
        </p>
        {opportunities === null ? (
          <div className="empty">불러오는 중…</div>
        ) : opportunities.length === 0 ? (
          <div className="empty">지금은 감지된 확정픽이 없어요. 잠시 후 다시 확인해 주세요.</div>
        ) : (
          <div className="pick-grid">
            {opportunities.map((opp) => (
              <PickBox key={opp.id} opportunity={opp} isNew={newIds.has(opp.id)} />
            ))}
          </div>
        )}
      </section>

      <section>
        <div className="section-head">
          <h2>가치 픽 (단폴더)</h2>
          <span className="count">{valuePicks?.length ?? 0}건</span>
        </div>
        <p className="hint">
          정확한 스코어·승리마진 등 노출가 높은 마켓과, 같은 북메이커의 다른 라인끼리 가격이
          어긋나는 경우를 엣지% 순으로 보여줍니다. <strong>확정 수익이 아닙니다</strong> —
          참고용 순위표로만 활용하세요.
        </p>
        {valuePicks === null ? (
          <div className="empty">불러오는 중…</div>
        ) : valuePicks.length === 0 ? (
          <div className="empty">기준치 이상의 가치 픽이 없어요.</div>
        ) : (
          <div className="pick-grid">
            {valuePicks.map((edge) => (
              <ValueEdgeBox key={edge.id} edge={edge} />
            ))}
          </div>
        )}
      </section>

      <ManualCalculator />
    </main>
  );
}

function ManualCalculator() {
  const [rows, setRows] = useState<ScanRowState[]>(() => [makeScanRow(), makeScanRow()]);
  const [totalStake, setTotalStake] = useState(100000);
  const [minHitRate, setMinHitRate] = useState("");
  const [results, setResults] = useState<ScanResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function updateRow(id: number, patch: Partial<ScanRowState>) {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  function setRowMarket(id: number, marketValue: string) {
    const def = SCAN_MARKETS.find((m) => m.value === marketValue) ?? SCAN_MARKETS[0];
    updateRow(id, { market: marketValue, selection: def.selections ? def.selections[0] : "" });
  }

  function addRow() {
    setRows((prev) => [...prev, makeScanRow()]);
  }

  function removeRow(id: number) {
    setRows((prev) => (prev.length > 1 ? prev.filter((r) => r.id !== id) : prev));
  }

  async function scan() {
    setErrorMsg(null);
    setResults(null);

    const legs: ScanLegInput[] = [];
    for (const row of rows) {
      const def = SCAN_MARKETS.find((m) => m.value === row.market) ?? SCAN_MARKETS[0];
      if (row.odds.trim() === "") continue; // 빈 행은 조용히 건너뜀

      const odds = Number(row.odds);
      if (!Number.isFinite(odds) || odds <= 1) {
        setErrorMsg("배당은 1보다 큰 숫자여야 합니다.");
        return;
      }

      const selection = row.selection.trim();
      if (!selection) {
        setErrorMsg("선택지를 입력하지 않은 항목이 있어요.");
        return;
      }

      let line: number | null = null;
      if (def.needsLine) {
        const lineValue = Number(row.line);
        if (row.line.trim() === "" || !Number.isFinite(lineValue)) {
          setErrorMsg(`"${def.label}" 항목에는 라인(예: 2.5, -0.25) 입력이 필요합니다.`);
          return;
        }
        line = lineValue;
      }

      const marketKey = row.market === "custom" ? row.customLabel.trim() || "커스텀" : row.market;
      const leg: ScanLegInput = { market: marketKey, line, selection, bookmaker: row.bookmaker.trim(), decimal_odds: odds };
      if (row.group.trim()) leg.group = row.group.trim();
      legs.push(leg);
    }

    if (legs.length < 2) {
      setErrorMsg("최소 2개 이상 배당을 입력하세요.");
      return;
    }
    if (!Number.isFinite(totalStake) || totalStake <= 0) {
      setErrorMsg("총 베팅 금액은 0보다 커야 합니다.");
      return;
    }

    let minHitRatePercent: number | null = null;
    if (minHitRate.trim() !== "") {
      const parsed = Number(minHitRate);
      if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) {
        setErrorMsg("목표 적중률은 0~100 사이 숫자여야 합니다.");
        return;
      }
      minHitRatePercent = parsed;
    }

    setLoading(true);
    try {
      const res = await scanManualOdds({
        total_stake: totalStake,
        legs,
        ...(minHitRatePercent !== null ? { min_hit_rate_percent: minHitRatePercent } : {}),
      });
      setResults(res);
    } catch (err) {
      setErrorMsg((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section>
      <div className="section-head">
        <h2>수동 계산기</h2>
      </div>
      <p className="hint">
        마켓 상관없이 찾은 배당을 전부 입력하세요 — 승무패, 유럽식/아시안 핸디캡(쿼터 라인
        포함), 오버언더, 양팀득점, 정확한 스코어, 기타 커스텀까지 한 번에 넣으면 그중 확정
        수익(100% 마진)이 나는 조합을 찾아 보여드립니다. 같은 마켓·라인끼리는 자동으로
        묶이고, <strong>서로 다른 마켓을 일부러 같이 묶어보고 싶으면</strong> "그룹"란에
        같은 이름을 적어주세요 (단, 서로 다른 마켓은 대부분 결과가 통계적으로 독립이 아니라
        묶은 결과가 검증되지는 않아요 — 결과 카드에 이유가 표시됩니다).{" "}
        <strong>한 경기 분량만</strong> 넣어주세요 — 여러 경기를 섞으면 그룹이 잘못 묶여요.
      </p>

      <div className="pick-box calc-box">
        <div className="scan-rows">
          {rows.map((row) => {
            const def = SCAN_MARKETS.find((m) => m.value === row.market) ?? SCAN_MARKETS[0];
            return (
              <div className="scan-row" key={row.id}>
                <select value={row.market} onChange={(e) => setRowMarket(row.id, e.target.value)}>
                  {SCAN_MARKETS.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>

                <input
                  type="text"
                  placeholder="그룹 (선택, 같은 이름끼리 묶임)"
                  value={row.group}
                  onChange={(e) => updateRow(row.id, { group: e.target.value })}
                />

                {row.market === "custom" && (
                  <input
                    type="text"
                    placeholder="마켓 이름 (예: 코너킥 오버 9.5)"
                    value={row.customLabel}
                    onChange={(e) => updateRow(row.id, { customLabel: e.target.value })}
                  />
                )}

                {def.needsLine && (
                  <input
                    type="number"
                    step={0.25}
                    placeholder="라인"
                    value={row.line}
                    onChange={(e) => updateRow(row.id, { line: e.target.value })}
                  />
                )}

                {def.selections ? (
                  <select value={row.selection} onChange={(e) => updateRow(row.id, { selection: e.target.value })}>
                    {def.selections.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type="text"
                    placeholder="선택지 (예: 2-1)"
                    value={row.selection}
                    onChange={(e) => updateRow(row.id, { selection: e.target.value })}
                  />
                )}

                <input
                  type="text"
                  placeholder="북메이커"
                  value={row.bookmaker}
                  onChange={(e) => updateRow(row.id, { bookmaker: e.target.value })}
                />
                <input
                  type="number"
                  step={0.01}
                  min={1.01}
                  placeholder="배당"
                  value={row.odds}
                  onChange={(e) => updateRow(row.id, { odds: e.target.value })}
                />
                <button
                  type="button"
                  className="scan-row-remove"
                  onClick={() => removeRow(row.id)}
                  disabled={rows.length <= 1}
                  aria-label="항목 삭제"
                >
                  ✕
                </button>
              </div>
            );
          })}
        </div>

        <div className="scan-actions">
          <button className="btn secondary" onClick={addRow} type="button">
            + 항목 추가
          </button>
          <label className="calc-field">
            <span>총 베팅 금액</span>
            <input
              type="number"
              min={1}
              value={totalStake}
              onChange={(e) => setTotalStake(Number(e.target.value))}
            />
          </label>
          <label className="calc-field">
            <span>목표 적중률 % (선택)</span>
            <input
              type="number"
              min={0}
              max={100}
              step={1}
              placeholder="예: 70"
              value={minHitRate}
              onChange={(e) => setMinHitRate(e.target.value)}
            />
          </label>
          <button className="btn" onClick={scan} disabled={loading}>
            {loading ? "계산 중…" : "확정 수익 찾기"}
          </button>
        </div>

        {errorMsg && <div className="calc-error">{errorMsg}</div>}
      </div>

      {results !== null && (
        <div className="scan-results">
          {results.groups.length === 0 ? (
            <div className="empty">계산할 그룹이 없어요 — 같은 마켓·라인에 최소 2개 이상 입력하세요.</div>
          ) : (
            <div className="pick-grid">
              {results.groups.map((group, i) => (
                <ScanResultBox key={`${group.market}-${group.line}-${i}`} group={group} />
              ))}
            </div>
          )}

          {results.hit_rate_picks.length > 0 && (
            <div className="hit-rate-section">
              <div className="section-head">
                <h2>적중률 목표 픽 (부분 커버 — 확정 수익 아님)</h2>
              </div>
              <p className="hint">
                아래는 일부러 선택지 일부를 빼고, 남은 선택지들의 공정 확률 합이 목표
                적중률 이상일 때 마진이 가장 큰 조합입니다. <strong>제외된 선택지 결과가
                나오면 이 조합 전체를 잃습니다</strong> — 위 확정 수익 카드와는 완전히
                다른 성격이니 헷갈리지 마세요.
              </p>
              <div className="pick-grid">
                {results.hit_rate_picks.map((pick, i) => (
                  <HitRatePickBox key={`${pick.market}-${pick.line}-${i}`} pick={pick} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function ScanResultBox({ group }: { group: ScanGroupResult }) {
  const toneClass = group.is_arbitrage ? "profit-tone" : "loss-tone";
  return (
    <div className={`pick-box ${toneClass}`}>
      <div className="pick-top">
        <div>
          <div className="pick-event">{group.market_label}</div>
          <div className="pick-meta">
            {group.quarter_line && (
              <>
                <span>쿼터 라인</span>
                <span>·</span>
              </>
            )}
            <span>선택지 {group.legs.length}개</span>
          </div>
        </div>
        <div className="pick-margin">
          <div className="num">
            {group.margin_percent >= 0 ? "+" : ""}
            {group.margin_percent.toFixed(2)}%
          </div>
          <div className="cap">{group.is_arbitrage ? "확정 마진" : "마진 없음"}</div>
        </div>
      </div>

      <div className="legs-strip">
        {group.legs.map((leg, i) => (
          <span className="leg-chip" key={`${leg.selection}-${leg.bookmaker}-${i}`}>
            <span className="sel">{leg.selection}</span>
            <span className="book">{leg.bookmaker}</span>
            <span className="odds">{leg.decimal_odds.toFixed(2)}</span>
          </span>
        ))}
      </div>

      {group.warning && <p className="hint scan-warning">{group.warning}</p>}

      {group.is_arbitrage && (
        <table className="stake-table">
          <thead>
            <tr>
              <th>선택</th>
              <th>북메이커</th>
              <th>배당</th>
              <th>베팅액</th>
              <th>환급액</th>
            </tr>
          </thead>
          <tbody>
            {group.legs.map((leg, i) => (
              <tr key={`${leg.selection}-${leg.bookmaker}-row-${i}`}>
                <td>{leg.selection}</td>
                <td>{leg.bookmaker}</td>
                <td>{leg.decimal_odds.toFixed(2)}</td>
                <td>{leg.stake.toFixed(0)}</td>
                <td>{leg.payout.toFixed(0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="pick-actions">
        {group.push_possible && <span className="badge push">푸시 가능</span>}
        <span className={`badge ${group.verified ? "verified" : "not-guaranteed"}`}>
          {group.verified ? "엔진 검증" : "검증 안 됨"}
        </span>
        {group.is_arbitrage && (
          <span className="scan-profit">
            확정 수익 <strong>{group.guaranteed_profit.toFixed(0)}</strong>
          </span>
        )}
      </div>
    </div>
  );
}

function HitRatePickBox({ pick }: { pick: HitRatePick }) {
  return (
    <div className="pick-box value-tone">
      <div className="pick-top">
        <div>
          <div className="pick-event">{pick.market_label}</div>
          <div className="pick-meta">
            <span>적중률 {pick.achieved_hit_rate_percent.toFixed(1)}% (목표 {pick.target_hit_rate_percent.toFixed(0)}%)</span>
          </div>
        </div>
        <div className="pick-margin">
          <div className="num small">
            {pick.margin_percent >= 0 ? "+" : ""}
            {pick.margin_percent.toFixed(2)}%
          </div>
          <div className="cap">부분 커버 마진</div>
        </div>
      </div>

      <div className="legs-strip">
        {pick.legs.map((leg, i) => (
          <span className="leg-chip" key={`${leg.selection}-${leg.bookmaker}-${i}`}>
            <span className="sel">{leg.selection}</span>
            <span className="book">{leg.bookmaker}</span>
            <span className="odds">{leg.decimal_odds.toFixed(2)}</span>
          </span>
        ))}
      </div>

      <p className="hint scan-warning">
        제외: <strong>{pick.excluded_selections.join(", ")}</strong> — 이 결과가 나오면 아래
        베팅액 전부를 잃습니다.
      </p>

      <table className="stake-table">
        <thead>
          <tr>
            <th>선택</th>
            <th>북메이커</th>
            <th>배당</th>
            <th>베팅액</th>
            <th>환급액</th>
          </tr>
        </thead>
        <tbody>
          {pick.legs.map((leg, i) => (
            <tr key={`${leg.selection}-${leg.bookmaker}-row-${i}`}>
              <td>{leg.selection}</td>
              <td>{leg.bookmaker}</td>
              <td>{leg.decimal_odds.toFixed(2)}</td>
              <td>{leg.stake.toFixed(0)}</td>
              <td>{leg.payout.toFixed(0)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="pick-actions">
        <span className="badge not-guaranteed">확정 아님 · 부분 커버</span>
        <span className="scan-profit">
          적중 시 수익 <strong>{pick.guaranteed_profit.toFixed(0)}</strong>
        </span>
      </div>
    </div>
  );
}

function PickBox({ opportunity, isNew }: { opportunity: Opportunity; isNew: boolean }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={`pick-box profit-tone${isNew ? " is-new" : ""}`}>
      <div className="pick-top">
        <div>
          <div className="pick-event">{opportunity.event}</div>
          <div className="pick-meta">
            <span>{opportunity.league || opportunity.sport}</span>
            <span>·</span>
            <span>{marketLabel(opportunity.market, opportunity.line)}</span>
          </div>
        </div>
        <div className="pick-margin">
          <div className="num">+{opportunity.margin_percent.toFixed(2)}%</div>
          <div className="cap">확정 마진</div>
        </div>
      </div>

      <div className="legs-strip">
        {opportunity.legs.map((leg) => (
          <span className="leg-chip" key={`${leg.bookmaker}-${leg.selection}`}>
            <span className="sel">{leg.selection}</span>
            <span className="book">{leg.bookmaker}</span>
            <span className="odds">{leg.decimal_odds.toFixed(2)}</span>
          </span>
        ))}
      </div>

      <div className="pick-actions">
        {opportunity.push_possible && <span className="badge push">푸시 가능</span>}
        <button className="btn secondary" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "계산기 닫기" : "베팅 금액 계산"}
        </button>
        <time>{new Date(opportunity.detected_at).toLocaleTimeString("ko-KR")}</time>
      </div>

      {expanded && (
        <div className="stake-panel">
          <StakeCalculator opportunity={opportunity} />
        </div>
      )}
    </div>
  );
}

function StakeCalculator({ opportunity }: { opportunity: Opportunity }) {
  const [totalStake, setTotalStake] = useState(1000);
  const [plan, setPlan] = useState<StakePlan | null>(null);
  const [loading, setLoading] = useState(false);

  const calculate = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchStakePlan(opportunity.id, totalStake);
      setPlan(result);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opportunity.id]);

  useEffect(() => {
    calculate();
  }, [calculate]);

  return (
    <div>
      <div className="stake-form">
        <label htmlFor={`stake-${opportunity.id}`}>총 베팅 금액</label>
        <input
          id={`stake-${opportunity.id}`}
          type="number"
          min={1}
          value={totalStake}
          onChange={(e) => setTotalStake(Number(e.target.value))}
        />
        <button className="btn secondary" onClick={calculate} disabled={loading}>
          {loading ? "계산 중…" : "다시 계산"}
        </button>
      </div>

      {plan && (
        <>
          <div className="profit-line">
            확정 수익: <strong>{plan.guaranteed_profit.toFixed(2)}</strong> (
            {plan.profit_percent.toFixed(2)}%)
            {plan.push_possible && " · 푸시 시 원금 보전"}
          </div>
          <table className="stake-table">
            <thead>
              <tr>
                <th>선택</th>
                <th>북메이커</th>
                <th>배당</th>
                <th>베팅액</th>
                <th>환급액</th>
              </tr>
            </thead>
            <tbody>
              {plan.legs.map((leg) => (
                <tr key={`${leg.bookmaker}-${leg.selection}`}>
                  <td>{leg.selection}</td>
                  <td>{leg.bookmaker}</td>
                  <td>{leg.decimal_odds.toFixed(2)}</td>
                  <td>{leg.stake.toFixed(2)}</td>
                  <td>{leg.payout.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function ValueEdgeBox({ edge }: { edge: ValueEdge }) {
  return (
    <div className="pick-box value-tone">
      <div className="pick-top">
        <div>
          <div className="pick-event">{edge.event}</div>
          <div className="pick-meta">
            <span>{marketLabel(edge.market, edge.line)}</span>
            <span>·</span>
            <span>{edge.selection}</span>
          </div>
        </div>
        <div className="pick-margin">
          <div className="num small">+{edge.edge_percent.toFixed(1)}%</div>
          <div className="cap">모델 엣지</div>
        </div>
      </div>
      <div className="legs-strip">
        <span className="leg-chip">
          <span className="book">{edge.bookmaker}</span>
          <span className="odds">{edge.quoted_decimal_odds.toFixed(2)}</span>
        </span>
      </div>
      <div className="pick-actions">
        <span className="badge not-guaranteed">확정 아님</span>
        <time>{new Date(edge.detected_at).toLocaleTimeString("ko-KR")}</time>
      </div>
    </div>
  );
}
