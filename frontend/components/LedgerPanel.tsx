"use client";

import { useEffect, useRef, useState } from "react";

import { useSim } from "@/components/SimProvider";
import type { LedgerBalances, SimEvent } from "@/lib/api";
import { money, shortDate } from "@/lib/i18n";

type Row = { key: keyof LedgerBalances; label: string; value: number; tone?: "good" | "bad" };

function Amount({ value, lang }: { value: number; lang: "en" | "bn" }) {
  const [flash, setFlash] = useState(false);
  const previous = useRef(value);
  useEffect(() => {
    if (previous.current !== value) {
      setFlash(true);
      previous.current = value;
      const id = setTimeout(() => setFlash(false), 1200);
      return () => clearTimeout(id);
    }
  }, [value]);
  return <span className={`rounded px-1 tabular-nums ${flash ? "flash" : ""}`}>{money(value, lang)}</span>;
}

function describe(e: SimEvent, lang: "en" | "bn"): string {
  const amount = e.amount_bdt !== undefined ? ` ${money(e.amount_bdt, lang)}` : "";
  const who = e.employee_id ?? e.employer_id ?? "";
  const labels: Record<string, string> = {
    payroll_scheduled: lang === "bn" ? "বেতনের দিন" : "Payday",
    payroll_paid: lang === "bn" ? "বেতন দেওয়া হয়েছে" : "Wages paid",
    payroll_default: lang === "bn" ? "বেতন দেয়নি" : "Employer defaulted",
    deduction: lang === "bn" ? "কর্তন" : "Deduction",
    wallet_debit: lang === "bn" ? "ওয়ালেট থেকে কাটা" : "Wallet debit",
    carried_over: lang === "bn" ? "পরের মাসে স্থানান্তর" : "Carried over",
    pending_write_off: lang === "bn" ? "লোকসানের অপেক্ষায়" : "Awaiting write-off",
    missed_grace: lang === "bn" ? "গ্রেস পেরিয়েছে" : "Missed grace",
    write_off: lang === "bn" ? "লোকসান লেখা" : "Written off",
  };
  const status = e.type === "payroll_scheduled" && e.status && e.status !== "on_time" ? ` (${e.status})` : "";
  return `${labels[e.type] ?? e.type}${status} · ${who}${amount}`;
}

export function LedgerPanel() {
  const { t, lang, state, events } = useSim();
  const b = state?.ledger?.balances_bdt;
  if (!state || !b) return null; // e.g. a response from an older API version
  const rows: Row[] = [
    { key: "upay_capital", label: t.ledger.capital, value: -b.upay_capital },
    { key: "upay_pool", label: t.ledger.pool, value: b.upay_pool },
    { key: "employee_wallets", label: t.ledger.wallets, value: b.employee_wallets },
    { key: "external_spend", label: t.ledger.spent, value: b.external_spend },
    { key: "employers", label: t.ledger.employers, value: -b.employers },
    { key: "fees_income", label: t.ledger.fees, value: b.fees_income, tone: "good" },
    { key: "loss_provision", label: t.ledger.losses, value: b.loss_provision, tone: "bad" },
  ];
  // Payroll noise (every employer's payday) is summarised; advance-related events are listed.
  const shown = events.filter((e) => e.type !== "payroll_scheduled" && e.type !== "payroll_paid").slice(0, 8);

  return (
    <aside aria-label={t.ledger.title} className="space-y-4">
      <div className="rounded-lg border border-line bg-panel p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="font-semibold">{t.ledger.title}</h2>
          <span
            data-testid="reconciled"
            className={`rounded-full px-2 py-0.5 text-xs font-semibold ${state.ledger.reconciled ? "bg-good-soft text-good" : "bg-bad-soft text-bad"}`}
          >
            {state.ledger.reconciled ? `✓ ${t.ledger.reconciled}` : `✗ ${t.ledger.notReconciled}`}
          </span>
        </div>
        <div className="mb-3 flex items-center gap-1 text-xs text-muted" aria-hidden>
          <span className="rounded bg-accent-soft px-1.5 py-0.5">{t.ledger.pool}</span>→
          <span className="rounded bg-accent-soft px-1.5 py-0.5">{t.ledger.wallets}</span>→
          <span className="rounded bg-accent-soft px-1.5 py-0.5">{t.ledger.spent}</span>
        </div>
        <dl className="space-y-1.5 text-sm">
          {rows.map((r) => (
            <div key={r.key} className="flex items-baseline justify-between gap-3">
              <dt className="text-muted">{r.label}</dt>
              <dd className={`font-medium ${r.tone === "good" ? "text-good" : r.tone === "bad" && r.value > 0 ? "text-bad" : ""}`}>
                <Amount value={r.value} lang={lang} />
              </dd>
            </div>
          ))}
        </dl>
        <div className="mt-3 text-xs text-muted">{t.ledger.entries(state.ledger.entries)}</div>
      </div>
      <div className="rounded-lg border border-line bg-panel p-4">
        <h2 className="mb-2 font-semibold">{t.ledger.events}</h2>
        {shown.length === 0 ? (
          <p className="text-sm text-muted">{t.ledger.noEvents}</p>
        ) : (
          <ul className="space-y-1.5 text-sm">
            {shown.map((e, i) => (
              <li key={`${e.date}-${e.type}-${e.advance_id ?? e.employer_id}-${i}`} className="flex gap-2">
                <span className="shrink-0 text-xs text-muted">{shortDate(e.date, lang)}</span>
                <span>{describe(e, lang)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
