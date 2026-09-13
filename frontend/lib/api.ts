const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:7001";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY ?? "";

export interface Leg {
  selection: string;
  bookmaker: string;
  decimal_odds: number;
}

export interface Opportunity {
  id: number;
  event: string;
  sport: string;
  league: string;
  commence_time: string;
  market: string;
  line: number | null;
  total_implied_probability: number;
  margin_percent: number;
  push_possible: boolean;
  legs: Leg[];
  detected_at: string;
}

export interface StakeLeg extends Leg {
  stake: number;
  payout: number;
}

export interface StakePlan {
  total_stake: number;
  guaranteed_profit: number;
  profit_percent: number;
  push_possible: boolean;
  legs: StakeLeg[];
}

export interface ScanLegInput {
  market: string;
  line: number | null;
  selection: string;
  bookmaker: string;
  decimal_odds: number;
}

export interface ScanRequest {
  total_stake: number;
  legs: ScanLegInput[];
  min_hit_rate_percent?: number | null;
}

export interface ScanGroupResult {
  market: string;
  market_label: string;
  line: number | null;
  verified: boolean;
  is_arbitrage: boolean;
  total_implied_probability: number;
  margin_percent: number;
  push_possible: boolean;
  quarter_line: boolean;
  guaranteed_profit: number;
  profit_percent: number;
  legs: StakeLeg[];
  warning: string | null;
}

export interface HitRatePick {
  market: string;
  market_label: string;
  line: number | null;
  target_hit_rate_percent: number;
  achieved_hit_rate_percent: number;
  margin_percent: number;
  guaranteed_profit: number;
  profit_percent: number;
  excluded_selections: string[];
  legs: StakeLeg[];
}

export interface ScanResponse {
  groups: ScanGroupResult[];
  hit_rate_picks: HitRatePick[];
}

export interface SystemBetLegInput {
  label: string;
  bookmaker: string;
  decimal_odds: number;
  probability_percent?: number | null;
}

export interface SystemBetRequest {
  total_stake: number;
  min_hit_rate_percent: number;
  legs: SystemBetLegInput[];
}

export interface SystemBetBreakdownItem {
  combo_size: number;
  count: number;
}

export interface SystemBetResult {
  num_selections: number;
  min_hits: number;
  achieved_hit_rate_percent: number;
  num_bets: number;
  unit_stake: number;
  total_stake: number;
  expected_profit: number;
  expected_profit_percent: number;
  best_case_profit: number;
  best_case_profit_percent: number;
  breakdown: SystemBetBreakdownItem[];
  used_naive_probability: boolean;
  warning: string;
}

export interface ValueEdge {
  id: number;
  event: string;
  sport: string;
  market: string;
  line: number | null;
  selection: string;
  bookmaker: string;
  quoted_decimal_odds: number;
  model_probability: number;
  implied_probability: number;
  edge_percent: number;
  detected_at: string;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "x-api-key": API_KEY, ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function fetchOpportunities(): Promise<Opportunity[]> {
  return apiFetch<Opportunity[]>("/opportunities?limit=100");
}

export function fetchValueEdges(): Promise<ValueEdge[]> {
  return apiFetch<ValueEdge[]>("/value-edges?limit=100");
}

export function fetchStakePlan(opportunityId: number, totalStake: number): Promise<StakePlan> {
  return apiFetch<StakePlan>(`/opportunities/${opportunityId}/stake-plan?total_stake=${totalStake}`);
}

export async function scanManualOdds(req: ScanRequest): Promise<ScanResponse> {
  const res = await fetch(`${API_BASE_URL}/calculator/scan`, {
    method: "POST",
    headers: { "x-api-key": API_KEY, "content-type": "application/json" },
    cache: "no-store",
    body: JSON.stringify(req),
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
  return res.json() as Promise<ScanResponse>;
}

export async function calculateSystemBet(req: SystemBetRequest): Promise<SystemBetResult> {
  const res = await fetch(`${API_BASE_URL}/calculator/system-bet`, {
    method: "POST",
    headers: { "x-api-key": API_KEY, "content-type": "application/json" },
    cache: "no-store",
    body: JSON.stringify(req),
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
  return res.json() as Promise<SystemBetResult>;
}
