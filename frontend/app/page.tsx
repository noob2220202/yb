"use client";

import { Fragment, useEffect, useState } from "react";
import {
  Opportunity,
  StakePlan,
  ValueEdge,
  fetchOpportunities,
  fetchStakePlan,
  fetchValueEdges,
} from "@/lib/api";

const REFRESH_MS = 15000;

function marketLabel(market: string, line: number | null): string {
  const base = market
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
  return line === null || line === undefined ? base : `${base} (${line})`;
}

export default function Page() {
  const [opportunities, setOpportunities] = useState<Opportunity[] | null>(null);
  const [valueEdges, setValueEdges] = useState<ValueEdge[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [opps, edges] = await Promise.all([fetchOpportunities(), fetchValueEdges()]);
        if (!cancelled) {
          setOpportunities(opps);
          setValueEdges(edges);
          setError(null);
        }
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
  }, []);

  return (
    <main>
      <h1>Arbitrage Scanner</h1>
      <p className="subtitle">Live cross-bookmaker arbitrage and scoreline value edges</p>

      {error && <div className="disclaimer">Could not reach the API: {error}</div>}

      <section>
        <h2>Guaranteed-profit opportunities</h2>
        <p className="hint">
          Every row here comes from best odds across independent bookmakers for the exact same
          market — betting all legs guarantees the shown profit (or breakeven, on a market flagged
          &quot;push possible&quot;).
        </p>
        <div className="card">
          {opportunities === null ? (
            <div className="empty">Loading…</div>
          ) : opportunities.length === 0 ? (
            <div className="empty">No arbitrage detected right now — check back shortly.</div>
          ) : (
            <OpportunityTable opportunities={opportunities} />
          )}
        </div>
      </section>

      <section>
        <h2>Exotic-market value edges</h2>
        <p className="hint">
          Correct score / winning margin prices that diverge from the scoreline model&apos;s own
          probabilities. <strong>Not guaranteed profit</strong> — a ranked shortlist, not a sure
          thing.
        </p>
        <div className="card">
          {valueEdges === null ? (
            <div className="empty">Loading…</div>
          ) : valueEdges.length === 0 ? (
            <div className="empty">No value edges above threshold right now.</div>
          ) : (
            <ValueEdgeTable edges={valueEdges} />
          )}
        </div>
      </section>
    </main>
  );
}

function OpportunityTable({ opportunities }: { opportunities: Opportunity[] }) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  return (
    <table>
      <thead>
        <tr>
          <th>Event</th>
          <th>Sport</th>
          <th>Market</th>
          <th>Margin</th>
          <th>Detected</th>
        </tr>
      </thead>
      <tbody>
        {opportunities.map((opp) => (
          <Fragment key={opp.id}>
            <tr
              className="clickable"
              onClick={() => setExpandedId(expandedId === opp.id ? null : opp.id)}
            >
              <td>{opp.event}</td>
              <td>{opp.sport}</td>
              <td>{marketLabel(opp.market, opp.line)}</td>
              <td>
                <span className="badge margin">+{opp.margin_percent.toFixed(2)}%</span>
                {opp.push_possible && <span className="badge push">push possible</span>}
              </td>
              <td>{new Date(opp.detected_at).toLocaleTimeString()}</td>
            </tr>
            {expandedId === opp.id && (
              <tr className="legs-row">
                <td colSpan={5}>
                  <StakeCalculator opportunity={opp} />
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

function StakeCalculator({ opportunity }: { opportunity: Opportunity }) {
  const [totalStake, setTotalStake] = useState(1000);
  const [plan, setPlan] = useState<StakePlan | null>(null);
  const [loading, setLoading] = useState(false);

  async function calculate() {
    setLoading(true);
    try {
      const result = await fetchStakePlan(opportunity.id, totalStake);
      setPlan(result);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    calculate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opportunity.id]);

  return (
    <div>
      <div className="stake-form">
        <label htmlFor={`stake-${opportunity.id}`}>Total stake</label>
        <input
          id={`stake-${opportunity.id}`}
          type="number"
          min={1}
          value={totalStake}
          onChange={(e) => setTotalStake(Number(e.target.value))}
        />
        <button onClick={calculate} disabled={loading}>
          {loading ? "Calculating…" : "Recalculate"}
        </button>
      </div>

      {plan && (
        <>
          <div className="profit-line">
            Guaranteed profit: <strong>{plan.guaranteed_profit.toFixed(2)}</strong> (
            {plan.profit_percent.toFixed(2)}% of stake)
            {plan.push_possible && " — or breakeven if the market pushes"}
          </div>
          <table>
            <thead>
              <tr>
                <th>Selection</th>
                <th>Bookmaker</th>
                <th>Odds</th>
                <th>Stake</th>
                <th>Payout</th>
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

function ValueEdgeTable({ edges }: { edges: ValueEdge[] }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Event</th>
          <th>Market</th>
          <th>Selection</th>
          <th>Bookmaker</th>
          <th>Odds</th>
          <th>Edge</th>
        </tr>
      </thead>
      <tbody>
        {edges.map((edge) => (
          <tr key={edge.id}>
            <td>{edge.event}</td>
            <td>{marketLabel(edge.market, null)}</td>
            <td>{edge.selection}</td>
            <td>{edge.bookmaker}</td>
            <td>{edge.quoted_decimal_odds.toFixed(2)}</td>
            <td>
              <span className="badge edge">+{edge.edge_percent.toFixed(1)}%</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
