"use client";

import { useEffect, useRef, useState } from "react";

import { useSim } from "@/components/SimProvider";
import { api, download, post, type EmployerDashboard, type EmployerRow } from "@/lib/api";
import { longDate, money, shortDate, toBn } from "@/lib/i18n";

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-line bg-panel p-4 ${className}`}>{children}</section>;
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card>
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums">{value}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </Card>
  );
}

function Settings({ dash, onSaved }: { dash: EmployerDashboard; onSaved: () => void }) {
  const { t, lang, sessionId } = useSim();
  const [optedIn, setOptedIn] = useState(dash.settings.opted_in);
  const [cap, setCap] = useState(dash.settings.cap_pct);
  const [saved, setSaved] = useState(false);
  const num = (n: number) => (lang === "bn" ? toBn(n) : String(n));
  const caps = [5, 10, 15, 20, 25, 30].filter((c) => c <= dash.upay_cap_pct);

  const save = async () => {
    await api(`/employer/${dash.employer_id}/settings`, sessionId, { method: "PUT", body: JSON.stringify({ opted_in: optedIn, cap_pct: cap }) });
    setSaved(true);
    onSaved();
  };

  return (
    <Card className="space-y-4">
      <h2 className="font-semibold">{t.employer.settingsTitle}</h2>
      <label className="flex items-center justify-between gap-3 text-sm">
        <span>{t.employer.optedIn}</span>
        <input type="checkbox" checked={optedIn} onChange={(e) => { setOptedIn(e.target.checked); setSaved(false); }} className="h-5 w-5 accent-[var(--accent)]" />
      </label>
      <div className="text-sm">
        <label htmlFor="cap" className="block">{t.employer.cap}</label>
        <select
          id="cap"
          value={cap}
          onChange={(e) => { setCap(Number(e.target.value)); setSaved(false); }}
          className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5"
        >
          {caps.map((c) => (
            <option key={c} value={c}>{num(c)}%</option>
          ))}
        </select>
        <p className="mt-1 text-xs text-muted">{t.employer.capNote(`${num(dash.upay_cap_pct)}%`)}</p>
      </div>
      <div className="text-sm">
        <div>{t.employer.payrollDay}</div>
        <div className="text-muted">{t.employer.payrollDayValue(num(dash.payroll_day))}</div>
      </div>
      <div className="flex items-center gap-3">
        <button type="button" onClick={save} className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90">
          {t.employer.save}
        </button>
        {saved && <span className="text-sm text-good">✓ {t.employer.saved}</span>}
      </div>
    </Card>
  );
}

export function EmployerView() {
  const { t, lang, sessionId, version } = useSim();
  const [employers, setEmployers] = useState<EmployerRow[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [dash, setDash] = useState<EmployerDashboard | null>(null);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const num = (n: number) => (lang === "bn" ? toBn(n) : String(n));

  useEffect(() => {
    if (!sessionId) return;
    api<EmployerRow[]>("/employer", sessionId)
      .then((rows) => {
        setEmployers(rows);
        setSelected((cur) => cur || rows[0]?.employer_id || "");
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, [sessionId, version]);

  useEffect(() => {
    if (!sessionId || !selected) return;
    const mine = ++seq.current;
    api<EmployerDashboard>(`/employer/${selected}/dashboard`, sessionId)
      .then((d) => mine === seq.current && setDash(d))
      .catch((e) => mine === seq.current && setError(String(e.message ?? e)));
  }, [sessionId, selected, version, reload]);

  const current = dash && dash.employer_id === selected ? dash : null;
  const notice = current?.notice;

  const confirm = async () => {
    try {
      await post(`/employer/${selected}/deduction-notice/confirm`, sessionId);
      setReload((r) => r + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="employer" className="text-sm text-muted">{t.employer.signInAs}</label>
        <select
          id="employer"
          value={selected}
          onChange={(e) => { setSelected(e.target.value); setError(null); }}
          className="min-w-0 flex-1 rounded-md border border-line bg-panel px-2 py-1.5 text-sm sm:flex-none"
        >
          {employers.map((e) => (
            <option key={e.employer_id} value={e.employer_id}>
              {e.employer_id} · {e.name}{e.persona ? ` — ${t.employer.personaEmployer(e.persona)}` : ""}
            </option>
          ))}
        </select>
      </div>

      {error && <div className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad" role="alert">{error}</div>}

      {current && (
        <>
          {current.status === "closed" && <div className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">{t.employer.closed}</div>}

          <div className="grid gap-3 sm:grid-cols-3">
            <Kpi label={t.employer.people} value={num(current.employees_with_deductions)} />
            <Kpi label={t.employer.total} value={money(current.total_to_deduct_bdt, lang)} />
            <Kpi label={t.employer.payday} value={current.payday ? longDate(current.payday, lang) : "—"} sub={current.payday ? undefined : t.employer.noPayday} />
          </div>

          <Card>
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
              <h2 className="font-semibold">{t.employer.noticeTitle}</h2>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={!notice?.items.length}
                  onClick={() => download(`/employer/${selected}/deduction-notice.csv`, sessionId, `deduction-notice-${selected}-${notice?.payday ?? "none"}.csv`).catch((e) => setError(String(e)))}
                  className="rounded-md border border-line px-3 py-1.5 text-sm hover:bg-accent-soft disabled:opacity-40"
                >
                  {t.employer.download}
                </button>
                <button
                  type="button"
                  disabled={!notice?.items.length}
                  onClick={confirm}
                  className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-40"
                >
                  {t.employer.confirm}
                </button>
              </div>
            </div>
            <p className="mb-3 text-sm text-muted">{t.employer.noticeHint}</p>
            {notice && notice.items.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-xs text-muted">
                      <th className="py-2 pr-3 font-medium">{t.employer.colName}</th>
                      <th className="py-2 pr-3 font-medium">{t.employer.colTaken}</th>
                      <th className="py-2 pr-3 font-medium">{t.employer.colKind}</th>
                      <th className="py-2 text-right font-medium">{t.employer.colAmount}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {notice.items.map((i) => (
                      <tr key={i.advance_id} className="border-b border-line">
                        <td className="py-2 pr-3">
                          <div className="font-medium">{i.name}</div>
                          {i.name !== i.employee_id && <div className="text-xs text-muted">{i.employee_id}</div>}
                        </td>
                        <td className="py-2 pr-3">{shortDate(i.issue_date, lang)}</td>
                        <td className="py-2 pr-3">{i.kind === "carried_over" ? t.employer.kindCarried : t.employer.kindNew}</td>
                        <td className="py-2 text-right tabular-nums">{money(i.amount_due_bdt, lang)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td className="py-2 font-semibold" colSpan={3}>{t.employer.totalRow}</td>
                      <td className="py-2 text-right font-bold tabular-nums" data-testid="notice-total">{money(notice.total_bdt, lang)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            ) : (
              <p className="text-sm text-muted">{t.employer.empty}</p>
            )}
            {notice?.confirmation && notice.payday && (
              <p className="mt-3 rounded-md bg-good-soft px-3 py-2 text-sm text-good">
                ✓ {t.employer.confirmed(money(notice.confirmation.total_bdt, lang), longDate(notice.payday, lang), longDate(notice.confirmation.acked_on, lang))}
              </p>
            )}
            <p className="mt-3 text-xs text-muted">
              {t.employer.simulated} · {t.employer.settled(num(current.advances_settled_this_session), money(current.remitted_this_session_bdt, lang))}
            </p>
          </Card>

          <Settings key={current.employer_id} dash={current} onSaved={() => setReload((r) => r + 1)} />
          <p className="text-xs text-muted">🔒 {t.employer.privacy}</p>
        </>
      )}
    </div>
  );
}
