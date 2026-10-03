"use client";

import { useSim } from "@/components/SimProvider";

export function Placeholder({ role }: { role: "employee" | "employer" | "ops" | "validation" }) {
  const { t } = useSim();
  return (
    <section className="rounded-lg border border-dashed border-line bg-panel p-8">
      <h1 className="text-xl font-semibold">{t.roles[role]}</h1>
      <p className="mt-2 text-muted">{t.placeholder}</p>
    </section>
  );
}
