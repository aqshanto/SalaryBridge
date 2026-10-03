"use client";

import { useSim } from "@/components/SimProvider";
import { longDate, toBn } from "@/lib/i18n";

const SCENARIOS = ["s1", "s2", "s3", "s4", "s5", "s6", "s7"] as const;

function Button({ onClick, disabled, children, tone = "default" }: { onClick: () => void; disabled?: boolean; children: React.ReactNode; tone?: "default" | "primary" | "quiet" }) {
  const styles = {
    default: "border border-line bg-panel hover:bg-accent-soft",
    primary: "bg-accent text-white hover:opacity-90",
    quiet: "text-muted hover:text-ink hover:bg-accent-soft",
  }[tone];
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={`rounded-md px-3 py-1.5 text-sm font-medium transition disabled:opacity-50 ${styles}`}>
      {children}
    </button>
  );
}

export function SimulatorBar() {
  const { t, lang, state, busy, error, advance, jumpToPayday, reset, runScenario, guide, closeGuide } = useSim();
  const num = (n: number) => (lang === "bn" ? toBn(n) : String(n));

  return (
    <section aria-label="Simulator" className="border-b border-line bg-panel/70">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-2.5">
        <div className="mr-2">
          <div className="text-xs text-muted">{t.sim.today}</div>
          <div className="font-semibold" data-testid="sim-date">
            {state ? longDate(state.sim_date, lang) : "…"}
          </div>
        </div>
        {state?.next_payday && (
          <div className="mr-2 text-sm">
            <span className="text-muted">{t.sim.nextPayday}: </span>
            {longDate(state.next_payday, lang)} <span className="text-muted">({t.sim.inDays(state.days_to_next_payday ?? 0)})</span>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => advance(1)} disabled={busy || !state}>{t.sim.plus1}</Button>
          <Button onClick={() => advance(7)} disabled={busy || !state}>{t.sim.plus7}</Button>
          <Button onClick={jumpToPayday} disabled={busy || !state} tone="primary">{t.sim.payday}</Button>
          <Button onClick={reset} disabled={busy} tone="quiet">{t.sim.reset}</Button>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 lg:ml-auto">
          <span className="text-xs text-muted">{t.sim.scenarios}:</span>
          {SCENARIOS.map((s, i) => (
            <button
              key={s}
              type="button"
              title={t.scenarios[s]}
              onClick={() => runScenario(s)}
              disabled={busy}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition disabled:opacity-50 ${
                guide?.scenario.startsWith(s) ? "border-accent bg-accent-soft text-accent" : "border-line hover:border-accent"
              }`}
            >
              S{num(i + 1)} · {t.scenarios[s]}
            </button>
          ))}
        </div>
        {busy && <span className="text-xs text-muted" role="status">{t.sim.busy}</span>}
      </div>
      {state?.kill_switch && (
        <div className="bg-bad-soft px-4 py-1.5 text-center text-sm font-medium text-bad" role="alert">{t.sim.killOn}</div>
      )}
      {error && (
        <div className="bg-bad-soft px-4 py-1.5 text-center text-sm text-bad" role="alert">
          {t.apiDown}: {error}
        </div>
      )}
      {guide && (
        <div className="mx-auto max-w-7xl px-4 pb-3">
          <div className="rounded-lg border border-accent/40 bg-accent-soft p-3 text-sm">
            <div className="mb-2 flex items-start justify-between gap-3">
              <div className="font-semibold">
                {guide.scenario.slice(0, 2).toUpperCase()} · {guide.title}{" "}
                <span className="font-normal text-muted">— {guide.persona.name}</span>
              </div>
              <button type="button" onClick={closeGuide} className="text-xs text-muted hover:text-ink">{t.guide.close}</button>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {(["setup", "try", "expect"] as const).map((k) => (
                <div key={k}>
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted">{t.guide[k]}</div>
                  <ul className="mt-1 list-disc space-y-0.5 pl-4">
                    {(guide[k].length ? guide[k] : ["—"]).map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
