"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Opportunity,
  StakePlan,
  ValueEdge,
  fetchOpportunities,
  fetchStakePlan,
  fetchValueEdges,
} from "@/lib/api";

const REFRESH_MS = 15000;
const THRESHOLD_KEY = "yb.notifyThreshold";
const NOTIFY_KEY = "yb.notifyEnabled";

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
    </main>
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
