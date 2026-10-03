export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

export async function api<T>(path: string, sessionId: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Session-Id": sessionId, ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep the status text */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const post = <T,>(path: string, sessionId: string, body: unknown = {}) =>
  api<T>(path, sessionId, { method: "POST", body: JSON.stringify(body) });

export type LedgerBalances = {
  upay_capital: number;
  upay_pool: number;
  employee_wallets: number;
  employers: number;
  fees_income: number;
  loss_provision: number;
  external_spend: number;
};

export type SimState = {
  session_id: string;
  sim_date: string;
  start_date: string;
  work_month: string;
  day_of_month: number;
  next_payday: string | null;
  days_to_next_payday: number | null;
  open_employers: number;
  closed_employers: string[];
  kill_switch: boolean;
  pool_bdt: string;
  advances: {
    by_status: Record<string, { count: number; amount_bdt: number }>;
    outstanding_bdt: number;
    fees_income_bdt: number;
    loss_provision_bdt: number;
  };
  ledger: { reconciled: boolean; entries: number; total_paisa: number; balances_bdt: LedgerBalances };
};

export type SimEvent = {
  date: string;
  type: string;
  employer_id?: string;
  employee_id?: string;
  advance_id?: string;
  status?: string;
  step?: string;
  amount_bdt?: number;
};

export type ScenarioResult = {
  scenario: string;
  title: string;
  persona: { key: string; name: string; employee_id: string; employer_id: string };
  setup: string[];
  try: string[];
  expect: string[];
  state: SimState;
};
