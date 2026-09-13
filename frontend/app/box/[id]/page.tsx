"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:7001";

interface HedgeBoxDetail {
  id: number;
  created_at: string;
  event: string;
  league: string;
  commence_time: string;
  leg_a_label: string;
  leg_a_odds: number;
  leg_a_stake: number;
  leg_b_label: string;
  leg_b_odds: number;
  leg_b_stake: number;
  total_stake: number;
  guaranteed_profit: number;
  profit_percent: number;
  implied_hit_rate_percent: number | null;
}

function formatKst(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Asia/Seoul",
  });
}

export default function BoxDetailPage() {
  const params = useParams<{ id: string }>();
  const [box, setBox] = useState<HedgeBoxDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // 공개 엔드포인트 — 관리자 키를 붙이지 않습니다 (텔레그램 채널의 누구나 볼 수 있어야 함).
    fetch(`${API_BASE_URL}/hedge-box/${params.id}`, { cache: "no-store" })
      .then(async (res) => {
        if (!res.ok) throw new Error(res.status === 404 ? "박스를 찾을 수 없습니다." : `요청 실패 (${res.status})`);
        return res.json();
      })
      .then(setBox)
      .catch((err) => setError((err as Error).message));
  }, [params.id]);

  if (error) {
    return (
      <main className="page">
        <p className="error">{error}</p>
      </main>
    );
  }

  if (!box) {
    return (
      <main className="page">
        <p className="hint">불러오는 중...</p>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="header">
        <h1>🐐 BOX #{box.id}</h1>
        <p className="hint small">
          {box.league && `${box.league} · `}
          {formatKst(box.commence_time)}
        </p>
      </header>

      <section className="card">
        <h2>{box.event}</h2>
        <div className="result-box positive">
          <div className="result-row">
            <span>① {box.leg_a_label} @ {box.leg_a_odds.toFixed(2)}</span>
            <strong>{box.leg_a_stake.toLocaleString()}원</strong>
          </div>
          <div className="result-row">
            <span>② {box.leg_b_label} @ {box.leg_b_odds.toFixed(2)}</span>
            <strong>{box.leg_b_stake.toLocaleString()}원</strong>
          </div>
          <div className="result-summary">
            <div>
              <span className="label">총 배팅</span>
              <span className="value">{box.total_stake.toLocaleString()}원</span>
            </div>
            <div>
              <span className="label">적중 시 순손익</span>
              <span className="value profit">
                +{box.guaranteed_profit.toLocaleString()}원 ({box.profit_percent.toFixed(1)}%)
              </span>
            </div>
            {box.implied_hit_rate_percent !== null && (
              <div>
                <span className="label">예상 적중률</span>
                <span className="value">{box.implied_hit_rate_percent.toFixed(1)}%</span>
              </div>
            )}
          </div>
          <p className="warning">
            ⚠️ 확정 수익이 아닙니다 — 두 다리 모두 빗나가면(제외된 결과가 실제로 나오면) 베팅금
            전액을 잃습니다. 배팅은 본인 판단과 책임 하에 진행하세요.
          </p>
        </div>
      </section>
    </main>
  );
}
