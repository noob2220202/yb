const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:7001";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

export interface MatchSelection {
  market: string;
  market_label: string;
  line: number | null;
  selection: string;
  selection_label: string;
  decimal_odds: number;
  bookmaker: string;
  fetched_at: string;
}

export interface Match {
  id: number;
  event: string;
  sport: string;
  league: string;
  commence_time: string;
  selections: MatchSelection[];
}

export interface HedgeBoxLeg {
  label: string;
  decimal_odds: number;
}

export interface HedgeBoxCalculateRequest {
  leg_a: HedgeBoxLeg;
  leg_b: HedgeBoxLeg;
  target_profit: number;
  stake_round_to?: number;
  excluded_odds?: number | null;
}

export interface HedgeBoxCalculateResult {
  leg_a_stake: number;
  leg_b_stake: number;
  total_stake: number;
  leg_a_payout: number;
  leg_b_payout: number;
  guaranteed_profit: number;
  profit_percent: number;
  implied_hit_rate_percent: number | null;
}

export interface HedgeBoxSendRequest extends HedgeBoxCalculateRequest {
  event: string;
  league: string;
  commence_time: string;
}

export interface HedgeBoxSendResult {
  box_id: number;
  sent: boolean;
  detail_url: string;
  message: string;
}

export interface HedgeBoxDetail {
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

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "x-api-key": API_KEY, "content-type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = `요청 실패 (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // 응답이 JSON이 아니면 기본 메시지 사용
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function fetchMatches(hoursAhead = 72, sport?: string): Promise<Match[]> {
  const params = new URLSearchParams({ hours_ahead: String(hoursAhead) });
  if (sport) params.set("sport", sport);
  return apiFetch<Match[]>(`/matches?${params.toString()}`);
}

export function calculateHedgeBox(req: HedgeBoxCalculateRequest): Promise<HedgeBoxCalculateResult> {
  return apiFetch<HedgeBoxCalculateResult>("/hedge-box/calculate", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function sendHedgeBox(req: HedgeBoxSendRequest): Promise<HedgeBoxSendResult> {
  return apiFetch<HedgeBoxSendResult>("/hedge-box/send", {
    method: "POST",
    body: JSON.stringify(req),
  });
}
