"use client";

import { useState } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useSim } from "@/components/SimProvider";
import type { CapitalForecast } from "@/lib/api";
import { money, toBn, type Lang } from "@/lib/i18n";

function monthLabel(month: string, lang: Lang): string {
  const d = new Date(`${month}-01T00:00:00`);
  return new Intl.DateTimeFormat(lang === "bn" ? "bn-BD" : "en-GB", { month: "short", year: "numeric" }).format(d);
}

function lakh(value: number, lang: Lang): string {
  const text = `${Math.round(value / 100_000)}`;
  return lang === "bn" ? `৳${toBn(text)} লাখ` : `৳${text}L`;
}

type Row = { label: string; band: [number, number]; p10: number; p50: number; p90: number; required: number; eid: boolean };

function ChartTooltip({ active, payload, lang }: { active?: boolean; payload?: { payload: Row }[]; lang: Lang }) {
  const { t } = useSim();
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div className="rounded-md border border-line bg-panel px-3 py-2 text-xs shadow-sm">
      <div className="mb-1 font-semibold">
        {r.label}
        {r.eid ? ` · ${t.ops.eid}` : ""}
      </div>
      <div className="grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5">
        <span className="text-muted">{t.ops.p50}</span>
        <span className="text-right tabular-nums">{money(r.p50, lang)}</span>
        <span className="text-muted">{t.ops.band}</span>
        <span className="text-right tabular-nums">{money(r.p10, lang)} – {money(r.p90, lang)}</span>
        <span className="text-muted">{t.ops.required}</span>
        <span className="text-right tabular-nums">{money(r.required, lang)}</span>
      </div>
    </div>
  );
}

export function CapitalChart({ forecast }: { forecast: CapitalForecast }) {
  const { t, lang } = useSim();
  const [table, setTable] = useState(false);
  const rows: Row[] = forecast.months.map((m) => ({
    label: monthLabel(m.month, lang),
    band: [m.p10_bdt, m.p90_bdt],
    p10: m.p10_bdt,
    p50: m.p50_bdt,
    p90: m.p90_bdt,
    required: m.required_pool_bdt,
    eid: m.is_eid,
  }));
  const top = Math.max(forecast.pool_bdt, ...rows.map((r) => r.required)) * 1.1;

  return (
    <div>
      <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Legend">
        <li className="flex items-center gap-1.5"><span className="h-3 w-4 rounded-sm bg-accent/25" />{t.ops.band}</li>
        <li className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-accent" />{t.ops.p50}</li>
        <li className="flex items-center gap-1.5"><span className="h-0 w-4 border-t-2 border-dashed border-muted" />{t.ops.required}</li>
        <li className="flex items-center gap-1.5"><span className="h-0.5 w-4 bg-ink" />{t.ops.poolNow}</li>
      </ul>
      {table ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="py-1.5 font-medium">{t.ops.month}</th>
              <th className="py-1.5 text-right font-medium">P10</th>
              <th className="py-1.5 text-right font-medium">P50</th>
              <th className="py-1.5 text-right font-medium">P90</th>
              <th className="py-1.5 text-right font-medium">{t.ops.required}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-b border-line">
                <td className="py-1.5">{r.label}{r.eid ? ` · ${t.ops.eid}` : ""}</td>
                <td className="py-1.5 text-right tabular-nums">{money(r.p10, lang)}</td>
                <td className="py-1.5 text-right tabular-nums">{money(r.p50, lang)}</td>
                <td className="py-1.5 text-right tabular-nums">{money(r.p90, lang)}</td>
                <td className="py-1.5 text-right tabular-nums">{money(r.required, lang)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="h-64 w-full" role="img" aria-label={t.ops.forecastTitle}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 16, right: 16, bottom: 4, left: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--line)" />
              <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: "var(--line)" }} tick={{ fill: "var(--muted)", fontSize: 12 }} />
              <YAxis
                domain={[0, top]}
                tickFormatter={(v: number) => lakh(v, lang)}
                tickLine={false}
                axisLine={false}
                width={64}
                tick={{ fill: "var(--muted)", fontSize: 12 }}
              />
              <Tooltip content={<ChartTooltip lang={lang} />} cursor={{ stroke: "var(--muted)", strokeDasharray: "3 3" }} />
              <Area dataKey="band" stroke="none" fill="var(--accent)" fillOpacity={0.22} isAnimationActive={false} activeDot={false} />
              <Line dataKey="p50" stroke="var(--accent)" strokeWidth={2} dot={{ r: 4, fill: "var(--accent)", stroke: "var(--panel)", strokeWidth: 2 }} isAnimationActive={false} />
              <Line dataKey="required" stroke="var(--muted)" strokeWidth={2} strokeDasharray="5 4" dot={{ r: 4, fill: "var(--muted)", stroke: "var(--panel)", strokeWidth: 2 }} isAnimationActive={false} />
              <ReferenceLine
                y={forecast.pool_bdt}
                stroke="var(--ink)"
                strokeWidth={2}
                label={{ value: `${t.ops.poolNow} ${lakh(forecast.pool_bdt, lang)}`, position: "insideTopRight", fill: "var(--ink)", fontSize: 12 }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted">
        <span>{t.ops.forecastNote} ({forecast.model_version})</span>
        <button type="button" onClick={() => setTable((v) => !v)} className="font-medium text-accent hover:underline">
          {table ? t.chartView : t.ops.tableView}
        </button>
      </div>
    </div>
  );
}
