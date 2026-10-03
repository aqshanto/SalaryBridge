"use client";

import { useEffect, useState } from "react";

type Health = { status: string; version: string; seed: number };

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<Health>;
      })
      .then(setHealth)
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-4 p-6">
      <h1 className="text-2xl font-semibold">SalaryBridge</h1>
      <p className="text-sm opacity-70">Earned-wage advance sandbox for upay (synthetic data)</p>
      <div className="rounded-lg border px-4 py-3 text-sm">
        {health && (
          <span>
            API: {health.status} · v{health.version} · seed {health.seed}
          </span>
        )}
        {error && <span className="text-red-600">API unreachable: {error}</span>}
        {!health && !error && <span>Checking API…</span>}
      </div>
    </main>
  );
}
