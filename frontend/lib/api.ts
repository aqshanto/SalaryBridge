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

export type Persona = { key: string; name: string; story: string; employee_id: string; employer_id: string; industry: string; salary_bdt: number; hire_date: string };

export type Reason = { code: string; source: string; direction: string; detail: string };

export type Explanation = { en: string; bn: string; source: "template" | "llm"; fallback_reason?: string | null; points?: string[] };

export type Decision = {
  decision_id: string;
  status: "offered" | "queued" | "declined";
  employee_id: string;
  requested_bdt: number;
  hard_cap_bdt: number;
  max_amount_bdt: number;
  approved_amount_bdt: number;
  fee_bdt: number;
  total_due_bdt: number;
  repayment_date: string;
  tier: string | null;
  reasons: Reason[];
  explanation: Explanation;
};

export type Accepted = { advance_id: string; amount_bdt: number; fee_bdt: number; total_due_bdt: number; due_date: string; wallet_balance_bdt: string };

export type HistoryItem = {
  advance_id: string;
  issue_date: string;
  amount_bdt: number;
  fee_bdt: number;
  due_date: string;
  status: string;
  settled_via: string[];
  on_time: boolean | null;
  source: "live" | "history";
};

export type EmployeeSummary = {
  employee_id: string;
  employer_name: string;
  salary_bdt: number;
  salary_to_upay: boolean;
  days_worked: number;
  days_in_month: number;
  earned_to_date_bdt: number;
  available_bdt: number;
  hard_cap_bdt: number;
  min_advance_bdt: number;
  fee_bdt: number;
  next_deduction_date: string;
  preview_status: "offered" | "queued" | "declined";
  preview_message: { en: string; bn: string } | null;
  wallet_bdt: string;
  active: boolean;
  history: HistoryItem[];
};

export type EmployerRow = { employer_id: string; name: string; industry: string; headcount: number; payroll_day: number; status: string; persona: string | null };

export type NoticeItem = { advance_id: string; employee_id: string; name: string; kind: "new" | "carried_over"; issue_date: string; amount_due_bdt: number };

export type Notice = {
  employer_id: string;
  generated_on: string;
  payday: string | null;
  items: NoticeItem[];
  employees: number;
  total_bdt: number;
  confirmation: { total_bdt: number; acked_on: string } | null;
};

export type EmployerDashboard = {
  employer_id: string;
  name: string;
  headcount: number;
  payroll_day: number;
  status: "open" | "closed";
  settings: { opted_in: boolean; cap_pct: number };
  upay_cap_pct: number;
  payday: string | null;
  employees_with_deductions: number;
  total_to_deduct_bdt: number;
  advances_settled_this_session: number;
  remitted_this_session_bdt: number;
  notice: Notice;
};

/** Download a file from an endpoint that needs the session header (a plain link cannot send it). */
export async function download(path: string, sessionId: string, filename: string) {
  const res = await fetch(`${API_BASE}${path}`, { headers: { "X-Session-Id": sessionId } });
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export type OpsKpis = {
  outstanding_bdt: number;
  pool_bdt: number;
  required_pool_bdt: number;
  pool_below_required: boolean;
  disbursed_bdt: number;
  fees_income_bdt: number;
  loss_provision_bdt: number;
  loss_rate: number | null;
  decisions: number;
  approval_rate: number | null;
  queue_open: number;
  kill_switch: boolean;
};

export type ForecastMonth = { month: string; horizon: number; p10_bdt: number; p50_bdt: number; p90_bdt: number; required_pool_bdt: number; is_eid: boolean };

export type CapitalForecast = {
  origin_month: string;
  model_version: string;
  buffer_pct: number;
  months: ForecastMonth[];
  current_month: string;
  current_required_pool_bdt: number;
  pool_bdt: number;
  pool_below_required: boolean;
};

export type QueueItem = {
  decision_id: string;
  sim_date: string;
  employee_id: string;
  employer_id: string;
  requested_bdt: number;
  proposed_bdt: number;
  hard_cap_bdt: number;
  tier: string | null;
  risk: { employer_prob_late?: number; prob_fail?: number; prob_leave?: number; anomaly_score?: number; unusual_pattern?: boolean };
  reasons: Reason[];
  expired: boolean;
};

export type EmployerRisk = {
  employer_id: string;
  name: string;
  status: "open" | "closed";
  prob_late: number;
  prob_late_before: number;
  trend: number;
  risk_source: "m1" | "rule";
  reasons: { code: string; direction: string }[];
  exposure_bdt: number;
};

export type Monitor = {
  screened: number;
  unusual: number;
  chronic: number;
  threshold_top_pct: number;
  top: { employee_id: string; employer_id: string; anomaly_score: number; unusual: boolean; chronic: boolean; requests_6m: number; streak_months: number; full_cap_share: number }[];
};

export type Bin = { mean_predicted: number; observed_rate: number; n: number };
export type BinaryMetrics = { n: number; positive_rate: number; pr_auc: number | null; brier: number; calibration: Bin[] };
export type PolicyResult = { approval_rate: number | null; funds_deployed_bdt: number; loss_bdt: number; loss_rate: number | null };
export type FairnessDim = { ml_approval_gap_pp: number | null; rule_approval_gap_pp: number | null; threshold_pp: number; within_threshold: boolean; by_employer_risk_band?: Record<string, Record<string, { ml_approval_rate: number | null; advances: number }>> };
export type ProfileResult = {
  worlds: string[];
  policies: {
    n_advances: number;
    no_product: PolicyResult;
    flat_cap: PolicyResult;
    ml_tier: PolicyResult & { queued_for_human: number; tier_counts: Record<string, number> };
    equal_approval: Record<string, number | null> & { decline_share: number };
  };
  economics_base: {
    world_months: number;
    advances_ml: number;
    funds_ml_bdt: number;
    loss_ml_bdt: number;
    avg_days_outstanding: number;
    mean_monthly_funds_ml_bdt: number;
  };
  fairness: Record<string, FairnessDim>;
  calibration: Record<string, BinaryMetrics>;
};
export type M4Split = { world: { model: { coverage_p10_p90: number }; pool_covers_actual_share: number } };
export type ValidationData = {
  generated_on: string;
  targets: { id: string; target: string; value: string; pass: boolean; explained?: boolean }[];
  profile_a: ProfileResult;
  profile_b: ProfileResult;
  m4: { test_profile_a: M4Split; test_profile_b: M4Split };
  analyst_notes: Record<string, string>;
  policy: Record<string, number | boolean>;
};
