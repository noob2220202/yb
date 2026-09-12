const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
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

export interface ParlayLeg {
  event_label: string;
  market: string;
  line: number | null;
  selection: string;
  bookmaker: string;
  decimal_odds: number;
  fair_probability: number;
}

export interface ParlayValueFind {
  id: number;
  bookmaker: string;
  combined_odds: number;
  combined_fair_probability: number;
  edge_percent: number;
  legs: ParlayLeg[];
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

export function fetchParlayValue(): Promise<ParlayValueFind[]> {
  return apiFetch<ParlayValueFind[]>("/parlay-value?limit=100");
}

export function fetchStakePlan(opportunityId: number, totalStake: number): Promise<StakePlan> {
  return apiFetch<StakePlan>(`/opportunities/${opportunityId}/stake-plan?total_stake=${totalStake}`);
}
