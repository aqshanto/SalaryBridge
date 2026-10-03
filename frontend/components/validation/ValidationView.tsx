"use client";

import { useEffect, useState } from "react";

import { CalibrationChart, LossApprovalChart, SeriesLegend } from "@/components/validation/Charts";
import { useSim } from "@/components/SimProvider";
import { api, type ProfileResult, type ValidationData } from "@/lib/api";
import { longDate, money, pct, toBn } from "@/lib/i18n";

function Card({ title, children, note }: { title: string; children: React.ReactNode; note?: string }) {
  return (
    <section className="rounded-xl border border-line bg-panel p-4">
      <h2 className="font-semibold">{title}</h2>
      {note && <p className="mb-3 mt-1 text-sm text-muted">{note}</p>}
      {!note && <div className="mb-3" />}
      {children}
    </section>
  );
}

function Verdict({ pass, explained }: { pass: boolean; explained?: boolean }) {
  const { t } = useSim();
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${pass ? "bg-good-soft text-good" : "bg-bad-soft text-bad"}`}>
      {pass ? "✓" : "✗"} {pass ? t.validation.pass : t.validation.fail}
      {!pass && explained ? ` · ${t.validation.explained}` : ""}
    </span>
  );
}

type Econ = { fee: number; cap: number; rate: number; buffer: number; ops: number; stress: number };

function economics(base: ProfileResult["economics_base"], e: Econ, baseCap: number) {
  const m = base.world_months;
  const advances = base.advances_ml / m;
  const capScale = e.cap / baseCap;
  const funds = (base.funds_ml_bdt / m) * capScale;
  const loss = (base.loss_ml_bdt / m) * capScale * (1 + e.stress / 100);
  const revenue = advances * e.fee;
  const capital = funds * (base.avg_days_outstanding / 365) * (e.rate / 100);
  const idle = funds * (e.buffer / 100) * (30 / 365) * (e.rate / 100);
  const ops = advances * e.ops;
  const costs = loss + capital + idle + ops;
  return { advances, funds, loss, revenue, capital, idle, ops, net: revenue - costs, breakEven: advances ? costs / advances : 0 };
}

function Slider({ label, value, min, max, step, onChange, format }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; format: (v: number) => string }) {
  const { t } = useSim();
  return (
    <label className="block text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <span>{label} <span className="rounded bg-warn-soft px-1 text-[10px] font-semibold uppercase text-warn">{t.validation.assumption}</span></span>
        <span className="font-medium tabular-nums">{format(value)}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(ev) => onChange(Number(ev.target.value))} className="w-full accent-[var(--accent)]" />
    </label>
  );
}

function Economics({ data }: { data: ValidationData }) {
  const { t, lang } = useSim();
  const policy = data.policy;
  const baseCap = Number(policy.cap_pct_of_salary);
  const [profile, setProfile] = useState<"a" | "b">("a");
  const [e, setE] = useState<Econ>({
    fee: Number(policy.fee_flat_bdt),
    cap: baseCap,
    rate: Number(policy.capital_rate_annual_pct),
    buffer: Number(policy.capital_buffer_pct),
    ops: Number(policy.ops_cost_per_advance_bdt),
    stress: 0,
  });
  const base = (profile === "a" ? data.profile_a : data.profile_b).economics_base;
  const r = economics(base, e, baseCap);
  const num = (n: number) => (lang === "bn" ? toBn(n) : String(n));
  const set = (k: keyof Econ) => (v: number) => setE((old) => ({ ...old, [k]: v }));
  const row = (label: string, value: number, sign = "−") => (
    <div className="flex justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd className="tabular-nums">{sign} {money(value, lang)}</dd>
    </div>
  );

  return (
    <Card title={t.validation.econTitle} note={t.validation.econNote}>
      <div className="mb-3 flex gap-2" role="group">
        {(["a", "b"] as const).map((p) => (
          <button key={p} type="button" onClick={() => setProfile(p)} aria-pressed={profile === p}
            className={`rounded-md border px-3 py-1 text-sm ${profile === p ? "border-accent bg-accent-soft" : "border-line"}`}>
            {p === "a" ? t.validation.profileA : t.validation.profileB}
          </button>
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-3">
          <Slider label={t.validation.fee} value={e.fee} min={0} max={100} step={5} onChange={set("fee")} format={(v) => money(v, lang)} />
          <Slider label={t.validation.cap} value={e.cap} min={5} max={baseCap} step={5} onChange={set("cap")} format={(v) => `${num(v)}%`} />
          <Slider label={t.validation.capitalRate} value={e.rate} min={0} max={25} step={1} onChange={set("rate")} format={(v) => `${num(v)}%`} />
          <Slider label={t.validation.buffer} value={e.buffer} min={0} max={50} step={5} onChange={set("buffer")} format={(v) => `${num(v)}%`} />
          <Slider label={t.validation.opsCost} value={e.ops} min={0} max={30} step={1} onChange={set("ops")} format={(v) => money(v, lang)} />
          <Slider label={t.validation.stress} value={e.stress} min={0} max={200} step={10} onChange={set("stress")} format={(v) => `+${num(v)}%`} />
          <p className="text-xs text-muted">{t.validation.graceNote}</p>
        </div>
        <div>
          <dl className="space-y-1 text-sm">
            <div className="flex justify-between gap-3"><dt className="text-muted">{t.validation.advances} ({t.validation.measured})</dt><dd className="tabular-nums">{num(Math.round(r.advances))}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted">{t.validation.funds}</dt><dd className="tabular-nums">{money(r.funds, lang)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-muted">{t.validation.daysOut} ({t.validation.measured})</dt><dd className="tabular-nums">{num(base.avg_days_outstanding)}</dd></div>
            <div className="my-2 border-t border-line" />
            {row(t.validation.revenue, r.revenue, "+")}
            {row(t.validation.lossCost, r.loss)}
            {row(t.validation.capitalCost, r.capital)}
            {row(t.validation.idleCost, r.idle)}
            {row(t.validation.opsTotal, r.ops)}
            <div className="my-2 border-t border-line" />
            <div className="flex justify-between gap-3 text-base font-semibold">
              <dt>{t.validation.net}</dt>
              <dd className={`tabular-nums ${r.net >= 0 ? "text-good" : "text-bad"}`} data-testid="econ-net">{r.net >= 0 ? "+" : "−"} {money(Math.abs(r.net), lang)}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt>{t.validation.breakEven}</dt>
              <dd className="font-semibold tabular-nums" data-testid="econ-breakeven">{money(Math.ceil(r.breakEven), lang)}</dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-muted">{t.validation.formula}</p>
        </div>
      </div>
    </Card>
  );
}

export function ValidationView() {
  const { t, lang, sessionId } = useSim();
  const [data, setData] = useState<ValidationData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    api<ValidationData>("/validation", sessionId).then(setData).catch((e) => setError(String(e.message ?? e)));
  }, [sessionId]);

  if (error) return <div className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad" role="alert">{error}</div>;
  if (!data) return <p className="text-sm text-muted">…</p>;
  const a = data.profile_a;
  const b = data.profile_b;
  const num = (n: number) => (lang === "bn" ? toBn(n) : String(n));

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">{t.validation.title}</h1>
        <p className="mt-1 text-sm text-muted">{t.validation.intro(longDate(data.generated_on, lang))}</p>
      </div>

      <Card title={t.validation.targets}>
        <ul className="divide-y divide-line">
          {data.targets.map((target) => (
            <li key={target.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
              <div className="min-w-0">
                <span className="mr-2 font-mono text-xs text-muted">{target.id}</span>
                {target.target}
                <div className="text-xs text-muted">{target.value}</div>
              </div>
              <Verdict pass={target.pass} explained={target.explained} />
            </li>
          ))}
        </ul>
      </Card>

      <Card title={t.validation.lossVsApproval} note={t.validation.lossVsApprovalNote}>
        <SeriesLegend />
        <LossApprovalChart a={a} b={b} />
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-muted">
                <th className="py-1.5 font-medium" />
                <th className="py-1.5 text-right font-medium">A · {t.validation.approval}</th>
                <th className="py-1.5 text-right font-medium">A · {t.validation.lossRate}</th>
                <th className="py-1.5 text-right font-medium">B · {t.validation.approval}</th>
                <th className="py-1.5 text-right font-medium">B · {t.validation.lossRate}</th>
              </tr>
            </thead>
            <tbody>
              {(["no_product", "flat_cap", "ml_tier"] as const).map((k) => (
                <tr key={k} className="border-b border-line">
                  <td className="py-1.5">{k === "no_product" ? t.validation.noProduct : k === "flat_cap" ? t.validation.flatCap : t.validation.mlTier}</td>
                  <td className="py-1.5 text-right tabular-nums">{pct(a.policies[k].approval_rate ?? 0, lang, 1)}</td>
                  <td className="py-1.5 text-right tabular-nums">{a.policies[k].loss_rate === null ? "—" : pct(a.policies[k].loss_rate!, lang, 2)}</td>
                  <td className="py-1.5 text-right tabular-nums">{pct(b.policies[k].approval_rate ?? 0, lang, 1)}</td>
                  <td className="py-1.5 text-right tabular-nums">{b.policies[k].loss_rate === null ? "—" : pct(b.policies[k].loss_rate!, lang, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title={t.validation.equalTitle} note={t.validation.equalNote(pct(a.policies.equal_approval.decline_share, lang, 1))}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="py-1.5 font-medium">{t.validation.ranking}</th>
              <th className="py-1.5 text-right font-medium">A · {t.validation.lossRate}</th>
              <th className="py-1.5 text-right font-medium">B · {t.validation.lossRate}</th>
            </tr>
          </thead>
          <tbody>
            {Object.keys(t.validation.rankings).map((k) => {
              const va = a.policies.equal_approval[k];
              const vb = b.policies.equal_approval[k];
              const bestA = Math.min(...Object.keys(t.validation.rankings).map((x) => a.policies.equal_approval[x] ?? 1));
              const bestB = Math.min(...Object.keys(t.validation.rankings).map((x) => b.policies.equal_approval[x] ?? 1));
              return (
                <tr key={k} className="border-b border-line">
                  <td className="py-1.5">{t.validation.rankings[k]}</td>
                  <td className={`py-1.5 text-right tabular-nums ${va === bestA ? "font-semibold text-good" : ""}`}>{va === null ? "—" : pct(va, lang, 2)}</td>
                  <td className={`py-1.5 text-right tabular-nums ${vb === bestB ? "font-semibold text-good" : ""}`}>{vb === null ? "—" : pct(vb, lang, 2)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>

      <Card title={t.validation.calibrationTitle} note={t.validation.calibrationNote}>
        <SeriesLegend />
        <div className="grid gap-4 md:grid-cols-2">
          {Object.keys(a.calibration).map((k) => (
            <CalibrationChart key={k} title={t.validation.models[k] ?? k} a={a.calibration[k]} b={b.calibration[k]} />
          ))}
        </div>
      </Card>

      <Card title={t.validation.coverageTitle} note={t.validation.coverageNote}>
        <div className="grid gap-3 sm:grid-cols-2">
          {(["test_profile_a", "test_profile_b"] as const).map((k) => {
            const w = data.m4[k].world;
            const cov = w.model.coverage_p10_p90;
            const ok = cov >= 0.75 && cov <= 0.85;
            return (
              <div key={k} className="rounded-lg border border-line p-3">
                <div className="text-xs text-muted">{k.endsWith("a") ? t.validation.profileA : t.validation.profileB}</div>
                <div className="mt-1 flex items-center gap-2">
                  <span className="text-2xl font-bold tabular-nums">{pct(cov, lang)}</span>
                  <Verdict pass={ok} explained={!ok && Boolean(data.analyst_notes[k.endsWith("a") ? "T2-A" : "T2-B"])} />
                </div>
                <div className="text-xs text-muted">{t.validation.poolCovered}: {pct(w.pool_covers_actual_share, lang)}</div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card title={t.validation.fairnessTitle} note={t.validation.fairnessNote}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="py-1.5 font-medium">{t.validation.dimension}</th>
              <th className="py-1.5 text-right font-medium">A · {t.validation.gap}</th>
              <th className="py-1.5 text-right font-medium">B · {t.validation.gap}</th>
            </tr>
          </thead>
          <tbody>
            {Object.keys(a.fairness).map((dim) => (
              <tr key={dim} className="border-b border-line">
                <td className="py-1.5">{dim}</td>
                {[a, b].map((r, i) => {
                  const f = r.fairness[dim];
                  return (
                    <td key={i} className="py-1.5 text-right">
                      <span className="mr-2 tabular-nums">{f.ml_approval_gap_pp === null ? "—" : `${num(f.ml_approval_gap_pp)} pp`}</span>
                      <Verdict pass={f.within_threshold} explained={!f.within_threshold && Boolean(data.analyst_notes[`T3-${i === 0 ? "A" : "B"}-${dim}`])} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {(["profile_a", "profile_b"] as const).map((key) => {
          const bands = data[key].fairness.employer_size?.by_employer_risk_band;
          if (!bands) return null;
          const groups = Object.keys(Object.values(bands)[0] ?? {});
          return (
            <div key={key} className="mt-4 overflow-x-auto">
              <div className="mb-1 text-xs font-medium text-muted">
                {key === "profile_a" ? t.validation.profileA : t.validation.profileB} · {t.validation.bandTitle}
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-line text-left text-muted">
                    <th className="py-1 font-medium" />
                    {groups.map((g) => <th key={g} className="py-1 text-right font-medium">{g}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(bands).map(([band, cells]) => (
                    <tr key={band} className="border-b border-line">
                      <td className="py-1">{band}</td>
                      {groups.map((g) => (
                        <td key={g} className="py-1 text-right tabular-nums">
                          {cells[g]?.ml_approval_rate == null ? "—" : pct(cells[g].ml_approval_rate!, lang, 1)} <span className="text-muted">(n={num(cells[g]?.advances ?? 0)})</span>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })}
      </Card>

      {Object.keys(data.analyst_notes).length > 0 && (
        <Card title={t.validation.notesTitle}>
          <ul className="space-y-2 text-sm">
            {Object.entries(data.analyst_notes).map(([id, note]) => (
              <li key={id}><span className="font-mono text-xs font-semibold">{id}</span> — {note}</li>
            ))}
          </ul>
        </Card>
      )}

      <Economics data={data} />

      <Card title={t.validation.assumptionsTitle} note={t.validation.assumptionsNote}>
        <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          {Object.entries(data.policy).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3 border-b border-line py-1">
              <dt className="font-mono text-xs text-muted">{k}</dt>
              <dd className="tabular-nums">{String(v)}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </div>
  );
}
