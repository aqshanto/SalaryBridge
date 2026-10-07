"use client";

import { useEffect, useRef, useState } from "react";

import { useSim } from "@/components/SimProvider";
import { api, post, type Accepted, type Decision, type EmployeeSummary, type Explanation, type HistoryItem, type Persona, type EmployerOption, type StaffMember } from "@/lib/api";
import { longDate, money, shortDate } from "@/lib/i18n";

// A screen belongs to the simulation epoch it was opened in; any time move, scenario or reset returns to home.
type Stage = ({ kind: "home" } | { kind: "offer"; decision: Decision } | { kind: "done"; accepted: Accepted; before: number }) & { epoch: number };

/** Animates a number from `from` to `to` (used for the wallet balance). */
function useCountUp(from: number, to: number, ms = 900): number {
  const [value, setValue] = useState(from);
  useEffect(() => {
    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / ms);
      setValue(from + (to - from) * (1 - Math.pow(1 - p, 3)));
      if (p < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    // Browsers pause animation frames in hidden tabs; always land on the final value.
    const settle = setTimeout(() => setValue(to), ms + 100);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(settle);
    };
  }, [from, to, ms]);
  return value;
}

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-line bg-panel p-4 ${className}`}>{children}</div>;
}

function PrimaryButton({ children, onClick, disabled }: { children: React.ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} className="w-full rounded-lg bg-accent px-4 py-3 text-base font-semibold text-white transition hover:opacity-90 disabled:opacity-40">
      {children}
    </button>
  );
}

function Explained({ explanation, decisionId }: { explanation: Explanation; decisionId: string }) {
  const { t, lang, sessionId } = useSim();
  const [current, setCurrent] = useState(explanation);
  const [llmEnabled, setLlmEnabled] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api<{ llm_enabled: boolean }>("/config/public", sessionId)
      .then((c) => setLlmEnabled(c.llm_enabled))
      .catch(() => setLlmEnabled(false));
  }, [sessionId]);

  const reword = async () => {
    setLoading(true);
    try {
      setCurrent(await post<Explanation>("/advance/explain", sessionId, { decision_id: decisionId }));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <p className="leading-relaxed">{lang === "bn" ? current.bn : current.en}</p>
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span>{current.source === "llm" ? t.employee.aiLabel : t.employee.templateLabel}</span>
        {llmEnabled && current.source === "template" && (
          <button type="button" onClick={reword} disabled={loading} className="font-medium text-accent hover:underline disabled:opacity-50">
            {loading ? "…" : t.employee.aiWording}
          </button>
        )}
      </div>
    </div>
  );
}

function HistoryList({ items }: { items: HistoryItem[] }) {
  const { t, lang } = useSim();
  if (!items.length) return <p className="text-sm text-muted">{t.employee.noHistory}</p>;
  return (
    <ul className="divide-y divide-line">
      {items.map((h) => {
        const tone = h.status === "written_off" || h.status === "pending_write_off" ? "bg-bad-soft text-bad" : h.status === "recovered" ? "bg-good-soft text-good" : "bg-warn-soft text-warn";
        return (
          <li key={h.advance_id} className="flex items-center justify-between gap-3 py-2 text-sm">
            <div>
              <div className="font-medium tabular-nums">{money(h.amount_bdt, lang)}</div>
              <div className="text-xs text-muted">{shortDate(h.issue_date, lang)}{h.source === "history" ? "" : ` · ${t.live}`}</div>
            </div>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${tone}`}>
              {t.employee.status[h.status] ?? h.status}
              {h.status === "recovered" && h.on_time === false ? ` · ${t.employee.late}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function Done({ accepted, before, onBack }: { accepted: Accepted; before: number; onBack: () => void }) {
  const { t, lang } = useSim();
  const wallet = useCountUp(before, Number(accepted.wallet_balance_bdt));
  return (
    <Card className="text-center">
      <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-good-soft text-2xl text-good" aria-hidden>
        ✓
      </div>
      <h2 className="text-lg font-semibold">{t.employee.doneTitle}</h2>
      <div className="mt-3 text-xs text-muted">{t.employee.wallet}</div>
      <div className="text-3xl font-bold tabular-nums" data-testid="wallet">
        {money(wallet, lang)}
      </div>
      <p className="mt-3 text-sm text-muted">{t.employee.doneNote(money(accepted.total_due_bdt, lang), longDate(accepted.due_date, lang))}</p>
      <button type="button" onClick={onBack} className="mt-4 text-sm font-medium text-accent hover:underline">
        {t.employee.back}
      </button>
    </Card>
  );
}

export function EmployeeView() {
  const { t, lang, sessionId, version, epoch, demoEpoch, refresh } = useSim();
  const [personas, setPersonas] = useState<Persona[]>([]);
  // The chosen person belongs to the current Demo-mode epoch; Demo mode returns to Rahim.
  // Deep link from the ops "All requests" table: /employee?employee=<staff id>
  const [deepLink] = useState(() => (typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("employee")));
  const [choice, setChoice] = useState({ key: deepLink ? `emp:${deepLink}` : "rahim", demo: 0 });
  const selected = choice.demo === demoEpoch ? choice.key : "rahim";
  const [summary, setSummary] = useState<EmployeeSummary | null>(null);
  const [chosen, setChosen] = useState<number | null>(null);
  const [stored, setStage] = useState<Stage>({ kind: "home", epoch: 0 });
  const stage: Stage = stored.epoch === epoch ? stored : { kind: "home", epoch };
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const requestSeq = useRef(0);

  useEffect(() => {
    if (!sessionId) return;
    api<Persona[]>("/personas", sessionId).then(setPersonas).catch((e) => setError(String(e.message ?? e)));
  }, [sessionId]);

  const [employers, setEmployers] = useState<EmployerOption[]>([]);
  const [pickEmployer, setPickEmployer] = useState("");
  const [staff, setStaff] = useState<StaffMember[]>([]);
  const [other, setOther] = useState<Persona | null>(
    deepLink ? { key: `emp:${deepLink}`, name: deepLink, story: "", employee_id: deepLink, employer_id: "", industry: "", salary_bdt: 0, hire_date: "" } : null,
  );

  useEffect(() => {
    if (!sessionId) return;
    api<EmployerOption[]>("/employer", sessionId).then(setEmployers).catch(() => {});
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId || !pickEmployer) return;
    api<StaffMember[]>(`/employer/${pickEmployer}/staff`, sessionId).then(setStaff).catch(() => setStaff([]));
  }, [sessionId, pickEmployer]);

  const chooseOther = (id: string) => {
    if (!id) return;
    const member = staff.find((s) => s.employee_id === id);
    const employer = employers.find((e) => e.employer_id === pickEmployer);
    setOther({ key: `emp:${id}`, name: member?.name ?? id, story: "", employee_id: id, employer_id: pickEmployer, industry: employer?.industry ?? "", salary_bdt: member?.salary_bdt ?? 0, hire_date: member?.hire_date ?? "" });
    pick(`emp:${id}`);
  };

  const person = selected.startsWith("emp:")
    ? personas.find((p) => p.employee_id === selected.slice(4)) ?? (other && other.key === selected ? other : undefined)
    : personas.find((p) => p.key === selected);
  // Show a summary only for the selected person (the previous person's stays hidden while loading).
  const current = summary && person && summary.employee_id === person.employee_id ? summary : null;

  useEffect(() => {
    if (!sessionId || !person) return;
    const seq = ++requestSeq.current;
    api<EmployeeSummary>(`/employee/${person.employee_id}/summary`, sessionId)
      .then((s) => seq === requestSeq.current && setSummary(s))
      .catch((e) => seq === requestSeq.current && setError(String(e.message ?? e)));
  }, [sessionId, person, version]);

  const pick = (key: string) => {
    setChoice({ key, demo: demoEpoch });
    setChosen(null);
    setStage({ kind: "home", epoch });
    setError(null);
  };

  const max = current?.available_bdt ?? 0;
  const min = current?.min_advance_bdt ?? 500;
  const amount = Math.min(Math.max(chosen ?? max, min), Math.max(max, min));

  const request = async () => {
    if (!person) return;
    setWorking(true);
    setError(null);
    try {
      setStage({ kind: "offer", epoch, decision: await post<Decision>("/advance/offer", sessionId, { employee_id: person.employee_id, amount_bdt: amount }) });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorking(false);
    }
  };

  const accept = async (decision: Decision) => {
    setWorking(true);
    setError(null);
    const before = Number(current?.wallet_bdt ?? 0);
    try {
      const accepted = await post<Accepted>("/advance/accept", sessionId, { decision_id: decision.decision_id });
      setStage({ kind: "done", accepted, before, epoch });
      setChosen(null);
      await refresh(); // ledger panel + summary refetch
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="mx-auto max-w-md space-y-4">
      <div role="group" aria-label={t.employee.choose} className="flex gap-2">
        {personas.map((p) => (
          <button
            key={p.key}
            type="button"
            onClick={() => pick(p.key)}
            aria-pressed={p.key === selected}
            className={`flex-1 rounded-lg border px-3 py-2 text-left text-sm transition ${p.key === selected ? "border-accent bg-accent-soft" : "border-line bg-panel hover:border-accent"}`}
          >
            <div className="font-semibold">{p.name}</div>
            <div className="truncate text-xs text-muted">{p.industry}</div>
          </button>
        ))}
      </div>

      <details className="rounded-lg border border-line bg-panel px-3 py-2 text-sm" open={selected.startsWith("emp:")}>
        <summary className="cursor-pointer font-medium">{t.employee.otherTitle}</summary>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <label className="block">
            <span className="text-xs text-muted">{t.employee.employerPick}</span>
            <select value={pickEmployer} onChange={(e) => { setPickEmployer(e.target.value); setStaff([]); }} className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5">
              <option value="">{t.employee.pickPlaceholder}</option>
              {employers.map((e) => (
                <option key={e.employer_id} value={e.employer_id}>{e.employer_id} · {e.name}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-muted">{t.employee.staffPick}</span>
            <select value={selected.startsWith("emp:") ? selected.slice(4) : ""} onChange={(e) => chooseOther(e.target.value)} disabled={!staff.length} className="mt-1 w-full rounded-md border border-line bg-panel px-2 py-1.5 disabled:opacity-50">
              <option value="">{t.employee.pickPlaceholder}</option>
              {staff.map((s) => (
                <option key={s.employee_id} value={s.employee_id}>{s.name ? `${s.name} · ` : ""}{s.employee_id} · {money(s.salary_bdt, lang)}</option>
              ))}
            </select>
          </label>
        </div>
      </details>

      {error && <div className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad" role="alert">{error}</div>}

      {current && person && stage.kind === "home" && (
        <>
          <Card>
            <div className="flex items-baseline justify-between gap-2">
              <div className="font-semibold">{person.name}</div>
              <div className="text-xs text-muted">{current.employer_name}</div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <div>
                <div className="text-xs text-muted">{t.employee.earned}</div>
                <div className="text-lg font-semibold tabular-nums">{money(current.earned_to_date_bdt, lang)}</div>
                <div className="text-xs text-muted">{t.employee.daysOf(current.days_worked, current.days_in_month)}</div>
              </div>
              <div>
                <div className="text-xs text-muted">{max > 0 ? t.employee.available : t.employee.nothingNow}</div>
                <div className="text-2xl font-bold text-accent tabular-nums" data-testid="available">{money(max, lang)}</div>
                <div className="text-xs text-muted">{t.employee.salary}: {money(current.salary_bdt, lang)}</div>
              </div>
            </div>
          </Card>

          {!current.active ? (
            <Card><p className="text-sm">{t.employee.left}</p></Card>
          ) : max >= min ? (
            <Card className="space-y-4">
              <label htmlFor="amount" className="block text-sm font-medium">{t.employee.howMuch}</label>
              <div className="text-center text-3xl font-bold tabular-nums">{money(amount, lang)}</div>
              <input
                id="amount"
                type="range"
                min={min}
                max={max}
                step={100}
                value={amount}
                onChange={(e) => setChosen(Number(e.target.value))}
                className="w-full accent-[var(--accent)]"
                aria-valuetext={money(amount, lang)}
              />
              <div className="flex justify-between text-xs text-muted">
                <span>{money(min, lang)}</span>
                <span>{money(max, lang)}</span>
              </div>
              <p className="text-xs text-muted">
                {t.employee.fee} {money(current.fee_bdt, lang)} · {t.employee.deducted} {longDate(current.next_deduction_date, lang)}
              </p>
              <PrimaryButton onClick={request} disabled={working}>{t.employee.request(money(amount, lang))}</PrimaryButton>
            </Card>
          ) : (
            current.preview_message && (
              <Card>
                <h2 className="mb-1 font-semibold">{t.employee.declinedTitle}</h2>
                <p className="text-sm leading-relaxed">{lang === "bn" ? current.preview_message.bn : current.preview_message.en}</p>
              </Card>
            )
          )}
        </>
      )}

      {stage.kind === "offer" && (
        <Card className="space-y-4">
          {stage.decision.status === "offered" ? (
            <>
              <div className="text-xs font-semibold uppercase tracking-wide text-muted">{t.employee.yourOffer}</div>
              <div className="text-4xl font-bold tabular-nums">{money(stage.decision.approved_amount_bdt, lang)}</div>
              <dl className="grid grid-cols-2 gap-2 rounded-lg bg-surface p-3 text-sm">
                <dt className="text-muted">{t.employee.fee}</dt>
                <dd className="text-right font-medium tabular-nums">{money(stage.decision.fee_bdt, lang)}</dd>
                <dt className="text-muted">{t.employee.deducted}</dt>
                <dd className="text-right font-medium tabular-nums">{money(stage.decision.total_due_bdt, lang)}</dd>
                <dt className="text-muted">{t.employee.on}</dt>
                <dd className="text-right font-medium">{longDate(stage.decision.repayment_date, lang)}</dd>
              </dl>
            </>
          ) : (
            <h2 className="text-lg font-semibold">{stage.decision.status === "queued" ? t.employee.queuedTitle : t.employee.declinedTitle}</h2>
          )}
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{t.employee.why}</div>
            <Explained key={stage.decision.decision_id} explanation={stage.decision.explanation} decisionId={stage.decision.decision_id} />
          </div>
          {stage.decision.status === "offered" ? (
            <div className="space-y-2">
              <PrimaryButton onClick={() => accept(stage.decision)} disabled={working}>
                {t.employee.accept(money(stage.decision.approved_amount_bdt, lang))}
              </PrimaryButton>
              <button type="button" onClick={() => setStage({ kind: "home", epoch })} className="w-full py-2 text-sm text-muted hover:text-ink">
                {t.employee.cancel}
              </button>
            </div>
          ) : (
            <button type="button" onClick={() => setStage({ kind: "home", epoch })} className="w-full py-2 text-sm font-medium text-accent hover:underline">
              {t.employee.back}
            </button>
          )}
        </Card>
      )}

      {stage.kind === "done" && <Done accepted={stage.accepted} before={stage.before} onBack={() => setStage({ kind: "home", epoch })} />}

      {current && (
        <Card>
          <h2 className="mb-1 font-semibold">{t.employee.history}</h2>
          <HistoryList items={current.history} />
          <p className="mt-2 text-xs text-muted">{t.employee.sample}</p>
        </Card>
      )}
    </div>
  );
}
