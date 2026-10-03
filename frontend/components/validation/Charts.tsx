"use client";

import {
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useSim } from "@/components/SimProvider";
import type { BinaryMetrics, ProfileResult } from "@/lib/api";
import { pct } from "@/lib/i18n";

const AXIS = { tickLine: false, tick: { fill: "var(--muted)", fontSize: 12 } } as const;

export function SeriesLegend() {
  const { t } = useSim();
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted" aria-label="Legend">
      <li className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-series-a" />{t.validation.profileA}</li>
      <li className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rotate-45 bg-series-b" />{t.validation.profileB}</li>
    </ul>
  );
}

type Point = { x: number; y: number; label: string };

function points(r: ProfileResult, labels: { flat: string; ml: string }): Point[] {
  const p = r.policies;
  return [
    { x: p.flat_cap.approval_rate ?? 0, y: p.flat_cap.loss_rate ?? 0, label: labels.flat },
    { x: p.ml_tier.approval_rate ?? 0, y: p.ml_tier.loss_rate ?? 0, label: labels.ml },
  ];
}

function PointTooltip({ active, payload }: { active?: boolean; payload?: { payload: Point & { series: string } }[] }) {
  const { t, lang } = useSim();
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border border-line bg-panel px-3 py-2 text-xs shadow-sm">
      <div className="font-semibold">{p.series} · {p.label}</div>
      <div>{t.validation.approval}: {pct(p.x, lang, 1)}</div>
      <div>{t.validation.lossRate}: {pct(p.y, lang, 2)}</div>
    </div>
  );
}

export function LossApprovalChart({ a, b }: { a: ProfileResult; b: ProfileResult }) {
  const { t, lang } = useSim();
  const labels = { flat: t.validation.flatCap, ml: t.validation.mlTier };
  const pa = points(a, labels).map((p) => ({ ...p, series: "A" }));
  const pb = points(b, labels).map((p) => ({ ...p, series: "B" }));
  const maxY = Math.max(...[...pa, ...pb].map((p) => p.y)) * 1.25;
  return (
    <div className="h-64 w-full" role="img" aria-label={t.validation.lossVsApproval}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 16, right: 24, bottom: 20, left: 8 }}>
          <CartesianGrid stroke="var(--line)" />
          <XAxis type="number" dataKey="x" domain={[0.75, 1.02]} tickFormatter={(v: number) => pct(v, lang)} axisLine={{ stroke: "var(--line)" }} {...AXIS}
            label={{ value: t.validation.approval, position: "insideBottom", offset: -12, fill: "var(--muted)", fontSize: 12 }} />
          <YAxis type="number" dataKey="y" domain={[0, maxY]} tickFormatter={(v: number) => pct(v, lang, 1)} axisLine={false} width={56} {...AXIS} />
          <Tooltip content={<PointTooltip />} cursor={{ strokeDasharray: "3 3", stroke: "var(--muted)" }} />
          <Scatter data={pa} fill="var(--series-a)" shape="circle" isAnimationActive={false}>
            <LabelList dataKey="label" position="top" fill="var(--ink)" fontSize={11} />
          </Scatter>
          <Scatter data={pb} fill="var(--series-b)" shape="diamond" isAnimationActive={false}>
            <LabelList dataKey="label" position="bottom" fill="var(--ink)" fontSize={11} />
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

export function CalibrationChart({ title, a, b }: { title: string; a: BinaryMetrics; b: BinaryMetrics }) {
  const { t, lang } = useSim();
  const rows = (m: BinaryMetrics) => m.calibration.map((bin) => ({ x: bin.mean_predicted, y: bin.observed_rate }));
  const top = Math.max(...[...rows(a), ...rows(b)].flatMap((r) => [r.x, r.y])) * 1.1;
  return (
    <div>
      <div className="mb-1 text-sm font-medium">{title}</div>
      <div className="h-52 w-full" role="img" aria-label={title}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart margin={{ top: 8, right: 16, bottom: 20, left: 0 }}>
            <CartesianGrid stroke="var(--line)" />
            <XAxis type="number" dataKey="x" domain={[0, top]} tickFormatter={(v: number) => pct(v, lang)} axisLine={{ stroke: "var(--line)" }} {...AXIS}
              label={{ value: t.validation.predicted, position: "insideBottom", offset: -12, fill: "var(--muted)", fontSize: 12 }} />
            <YAxis type="number" dataKey="y" domain={[0, top]} tickFormatter={(v: number) => pct(v, lang)} axisLine={false} width={48} {...AXIS} />
            <Tooltip formatter={(v) => pct(Number(v), lang, 1)} labelFormatter={(v) => `${t.validation.predicted} ${pct(Number(v), lang, 1)}`} />
            <ReferenceLine segment={[{ x: 0, y: 0 }, { x: top, y: top }]} stroke="var(--muted)" strokeDasharray="4 4" ifOverflow="extendDomain" />
            <Line data={rows(a)} dataKey="y" name="A" stroke="var(--series-a)" strokeWidth={2} dot={{ r: 4, fill: "var(--series-a)", stroke: "var(--panel)", strokeWidth: 2 }} isAnimationActive={false} />
            <Line data={rows(b)} dataKey="y" name="B" stroke="var(--series-b)" strokeWidth={2} dot={{ r: 4, fill: "var(--series-b)", stroke: "var(--panel)", strokeWidth: 2 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
