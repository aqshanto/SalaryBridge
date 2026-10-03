"use client";

import { useEffect, useRef, useState } from "react";

import { CapitalChart } from "@/components/ops/CapitalChart";
import { useSim } from "@/components/SimProvider";
import { api, post, type CapitalForecast, type EmployerRisk, type Monitor, type OpsKpis, type QueueItem } from "@/lib/api";
import { money, pct, reasonLabel, toBn } from "@/lib/i18n";

type OpsData = { kpis: OpsKpis; forecast: CapitalForecast; queue: QueueItem[]; employers: EmployerRisk[]; monitor: Monitor };

function Card({ title, children, className = "" }: { title?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-line bg-panel p-4 ${className}`}>
      {title && <h2 className="mb-3 font-semibold">{title}</h2>}
      {children}
    </section>
  );
}

function Kpi({ label, value, sub, alert }: { label: string; value: string; sub?: string; alert?: boolean }) {
  return (
    <div className={`rounded-xl border p-4 ${alert ? "border-warn bg-warn-soft" : "border-line bg-panel"}`}>
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

function QueueCard({ item, onDone }: { item: QueueItem; onDone: () => void }) {
  const { t, lang, sessionId } = useSim();
  const [note, setNote] = useState("");
  const [amount, setAmount] = useState(item.proposed_bdt);
  const [error, setError] = useState<string | null>(null);
  const act = async (action: "approve" | "reject") => {
    try {
      await post(`/ops/queue/${item.decision_id}/${action}`, sessionId, action === "approve" ? { note, amount_bdt: amount } : { note });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const risk = (label: string, value?: number) =>
    value === undefined ? null : (
      <div className="flex justify-between gap-2">
        <span className="text-muted">{label}</span>
        <span className="tabular-nums">{pct(value, lang)}</span>
      </div>
    );
  const review = item.reasons.filter((r) => r.direction === "review" || r.source === "m4" || r.code.startsWith("TIER_"));

  return (
    <li className="rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="font-medium">
          {item.employee_id} <span className="text-xs text-muted">· {item.employer_id} · {item.decision_id}</span>
        </div>
        {item.tier && <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs font-semibold text-accent">{t.tier} {item.tier}</span>}
      </div>
      {item.expired && <div className="mt-1 text-xs text-warn">{t.ops.expired}</div>}
      <div className="mt-2 grid gap-3 text-sm sm:grid-cols-2">
        <div className="space-y-0.5">
          <div className="flex justify-between gap-2"><span className="text-muted">{t.ops.requested}</span><span className="tabular-nums">{money(item.requested_bdt, lang)}</span></div>
          <div className="flex justify-between gap-2"><span className="text-muted">{t.ops.proposed}</span><span className="tabular-nums">{money(item.proposed_bdt, lang)}</span></div>
          <div className="flex justify-between gap-2"><span className="text-muted">{t.ops.cap}</span><span className="tabular-nums">{money(item.hard_cap_bdt, lang)}</span></div>
        </div>
        <div className="space-y-0.5">
          {risk(t.ops.riskFail, item.risk.prob_fail)}
          {risk(t.ops.riskLeave, item.risk.prob_leave)}
          {risk(t.ops.riskEmployer, item.risk.employer_prob_late)}
        </div>
      </div>
      {review.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-muted">
          {review.map((r) => (
            <li key={r.code}>• <span className="font-medium text-ink">{r.code}</span>{r.detail ? `: ${r.detail}` : ""}</li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 text-xs">
          <span className="text-muted">{t.ops.note}</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={t.ops.notePlaceholder} className="mt-0.5 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-sm" />
        </label>
        <label className="w-28 text-xs">
          <span className="text-muted">{t.ops.amount}</span>
          <input type="number" min={500} max={item.hard_cap_bdt} step={100} value={amount} onChange={(e) => setAmount(Number(e.target.value))} className="mt-0.5 w-full rounded-md border border-line bg-panel px-2 py-1.5 text-sm tabular-nums" />
        </label>
        <button type="button" onClick={() => act("approve")} disabled={item.expired} className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-40">{t.ops.approve}</button>
        <button type="button" onClick={() => act("reject")} className="rounded-md border border-line px-3 py-1.5 text-sm hover:bg-bad-soft">{t.ops.reject}</button>
      </div>
      {error && <div className="mt-2 text-xs text-bad" role="alert">{error}</div>}
    </li>
  );
}

function RiskBar({ value }: { value: number }) {
  return (
    <div className="h-2 w-24 overflow-hidden rounded-full bg-line" aria-hidden>
      <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function OpsView() {
  const { t, lang, sessionId, version, refresh, state } = useSim();
  const [data, setData] = useState<OpsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [allEmployers, setAllEmployers] = useState(false);
  const [reload, setReload] = useState(0);
  const seq = useRef(0);
  const num = (n: number) => (lang === "bn" ? toBn(n) : String(n));

  useEffect(() => {
    if (!sessionId) return;
    const mine = ++seq.current;
    Promise.all([
      api<OpsKpis>("/ops/summary", sessionId),
      api<CapitalForecast>("/ops/capital-forecast", sessionId),
      api<QueueItem[]>("/ops/queue", sessionId),
      api<EmployerRisk[]>("/ops/employers", sessionId),
      api<Monitor>("/ops/borrowing-monitor", sessionId),
    ])
      .then(([kpis, forecast, queue, employers, monitor]) => mine === seq.current && setData({ kpis, forecast, queue, employers, monitor }))
      .catch((e) => mine === seq.current && setError(String(e.message ?? e)));
  }, [sessionId, version, reload]);

  const afterAction = async () => {
    await refresh(); // ledger + version
    setReload((r) => r + 1);
  };

  const toggleKill = async () => {
    await post("/ops/kill-switch", sessionId, { on: !state?.kill_switch });
    await afterAction();
  };

  if (error) return <div className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad" role="alert">{error}</div>;
  if (!data) return <p className="text-sm text-muted" role="status">{t.loading}</p>;
  const { kpis, forecast, queue, employers, monitor } = data;
  const shownEmployers = allEmployers ? employers : employers.slice(0, 8);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label={t.ops.outstanding} value={money(kpis.outstanding_bdt, lang)} />
        <Kpi label={t.ops.pool} value={money(kpis.pool_bdt, lang)} sub={t.ops.poolSub(money(kpis.required_pool_bdt, lang))} alert={kpis.pool_below_required} />
        <Kpi
          label={t.ops.lossRate}
          value={kpis.loss_rate === null ? t.ops.none : pct(kpis.loss_rate, lang, 1)}
          sub={t.ops.lossSub(money(kpis.loss_provision_bdt, lang), money(kpis.disbursed_bdt, lang))}
        />
        <Kpi label={t.ops.approvalRate} value={kpis.approval_rate === null ? t.ops.none : pct(kpis.approval_rate, lang)} sub={t.ops.approvalSub(num(kpis.decisions))} />
      </div>
      {kpis.pool_below_required && <div className="rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">⚠ {t.ops.poolShort}</div>}

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">{t.ops.killTitle}</h2>
            <p className={`text-sm ${kpis.kill_switch ? "text-bad" : "text-muted"}`}>{kpis.kill_switch ? t.ops.killOn : t.ops.killOff}</p>
          </div>
          <button
            type="button"
            onClick={toggleKill}
            className={`rounded-md px-3 py-1.5 text-sm font-semibold ${kpis.kill_switch ? "border border-line hover:bg-good-soft" : "bg-bad text-white hover:opacity-90"}`}
          >
            {kpis.kill_switch ? t.ops.killTurnOff : t.ops.killTurnOn}
          </button>
        </div>
      </Card>

      <Card title={t.ops.forecastTitle}>
        <CapitalChart forecast={forecast} />
      </Card>

      <Card title={`${t.ops.queueTitle} (${num(queue.length)})`}>
        {queue.length === 0 ? (
          <p className="text-sm text-muted">{t.ops.queueEmpty}</p>
        ) : (
          <ul className="space-y-3">
            {queue.map((q) => (
              <QueueCard key={q.decision_id} item={q} onDone={afterAction} />
            ))}
          </ul>
        )}
      </Card>

      <Card title={t.ops.employersTitle}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="py-2 pr-3 font-medium">{t.ops.colEmployer}</th>
                <th className="py-2 pr-3 font-medium">{t.ops.colRisk}</th>
                <th className="py-2 pr-3 font-medium">{t.ops.colTrend}</th>
                <th className="py-2 pr-3 font-medium">{t.ops.colWhy}</th>
                <th className="py-2 text-right font-medium">{t.ops.colExposure}</th>
              </tr>
            </thead>
            <tbody>
              {shownEmployers.map((e) => (
                <tr key={e.employer_id} className="border-b border-line align-top">
                  <td className="py-2 pr-3">
                    <div className="font-medium">{e.employer_id}</div>
                    <div className="text-xs text-muted">{e.name}{e.status === "closed" ? ` · ${t.ops.closed}` : ""}</div>
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex items-center gap-2">
                      <RiskBar value={e.prob_late} />
                      <span className="tabular-nums">{pct(e.prob_late, lang)}</span>
                    </div>
                    {e.risk_source === "rule" && <div className="text-xs text-muted">{t.ops.byRule}</div>}
                  </td>
                  <td className={`py-2 pr-3 tabular-nums ${e.trend > 0.02 ? "text-bad" : e.trend < -0.02 ? "text-good" : "text-muted"}`}>
                    {e.trend > 0.02 ? "▲" : e.trend < -0.02 ? "▼" : "•"} {pct(Math.abs(e.trend), lang)}
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex flex-wrap gap-1">
                      {e.reasons.map((r) => (
                        <span key={r.code} className={`rounded-full px-2 py-0.5 text-xs ${r.direction === "raises_risk" ? "bg-warn-soft text-warn" : "bg-good-soft text-good"}`}>
                          {r.direction === "raises_risk" ? "↑" : "↓"} {reasonLabel(r.code, lang)}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="py-2 text-right tabular-nums">{e.exposure_bdt ? money(e.exposure_bdt, lang) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <button type="button" onClick={() => setAllEmployers((v) => !v)} className="mt-2 text-sm font-medium text-accent hover:underline">
          {allEmployers ? t.ops.showLess : `${t.ops.showAll} (${num(employers.length)})`}
        </button>
      </Card>

      <Card title={t.ops.monitorTitle}>
        <p className="mb-3 text-sm text-muted">{t.ops.monitorNote(`${num(monitor.threshold_top_pct)}%`)}</p>
        <div className="mb-3 grid grid-cols-3 gap-3 text-center">
          <div><div className="text-xl font-bold tabular-nums">{num(monitor.screened)}</div><div className="text-xs text-muted">{t.ops.screened}</div></div>
          <div><div className="text-xl font-bold tabular-nums">{num(monitor.unusual)}</div><div className="text-xs text-muted">{t.ops.unusual}</div></div>
          <div><div className="text-xl font-bold tabular-nums">{num(monitor.chronic)}</div><div className="text-xs text-muted">{t.ops.chronic}</div></div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="py-2 pr-3 font-medium">{t.ops.colStaff}</th>
                <th className="py-2 pr-3 font-medium">{t.ops.colScore}</th>
                <th className="py-2 pr-3 font-medium" />
                <th className="py-2 pr-3 text-right font-medium">{t.ops.colReq}</th>
                <th className="py-2 pr-3 text-right font-medium">{t.ops.colStreak}</th>
                <th className="py-2 text-right font-medium">{t.ops.colFull}</th>
              </tr>
            </thead>
            <tbody>
              {monitor.top.map((m) => (
                <tr key={m.employee_id} className="border-b border-line">
                  <td className="py-1.5 pr-3">
                    <div className="font-medium">{m.employee_id}</div>
                    <div className="text-xs text-muted">{m.employer_id}</div>
                  </td>
                  <td className="py-1.5 pr-3 tabular-nums">{lang === "bn" ? toBn(m.anomaly_score.toFixed(2)) : m.anomaly_score.toFixed(2)}</td>
                  <td className="py-1.5 pr-3">
                    <div className="flex flex-wrap gap-1">
                      {m.unusual && <span className="rounded-full bg-warn-soft px-2 py-0.5 text-xs text-warn">{t.ops.unusual}</span>}
                      {m.chronic && <span className="rounded-full bg-accent-soft px-2 py-0.5 text-xs text-accent">{t.ops.chronic}</span>}
                    </div>
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{num(m.requests_6m)}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{num(m.streak_months)}</td>
                  <td className="py-1.5 text-right tabular-nums">{pct(m.full_cap_share, lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
