"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, post, type ScenarioResult, type SimEvent, type SimState } from "@/lib/api";
import { dict, type Dict, type Lang } from "@/lib/i18n";
import { setItem, useStoredValue } from "@/lib/storage";

type SimContextValue = {
  sessionId: string;
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: Dict;
  state: SimState | null;
  events: SimEvent[];
  version: number; // bumps after every simulation change so views can refetch
  epoch: number; // bumps when time moves, a scenario runs or the session resets (not on a plain refresh)
  demoEpoch: number; // bumps only on Demo mode: views return to their demo defaults (Rahim)
  demoMode: () => Promise<void>;
  busy: boolean;
  error: string | null;
  guide: ScenarioResult | null;
  closeGuide: () => void;
  advance: (days: number) => Promise<void>;
  jumpToPayday: () => Promise<void>;
  reset: () => Promise<void>;
  runScenario: (name: string) => Promise<void>;
  refresh: () => Promise<void>;
};

const SimContext = createContext<SimContextValue | null>(null);

function newSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function SimProvider({ children }: { children: React.ReactNode }) {
  const sessionId = useStoredValue("sb.session", "", newSessionId);
  const storedLang = useStoredValue("sb.lang", "en");
  const lang: Lang = storedLang === "bn" ? "bn" : "en";
  const [state, setState] = useState<SimState | null>(null);
  const [events, setEvents] = useState<SimEvent[]>([]);
  const [version, setVersion] = useState(0);
  const [epoch, setEpoch] = useState(0);
  const [demoEpoch, setDemoEpoch] = useState(0);
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [guide, setGuide] = useState<ScenarioResult | null>(null);

  const setLang = useCallback((next: Lang) => setItem("sb.lang", next), []);

  useEffect(() => {
    document.documentElement.lang = lang; // screen readers switch voice with the toggle
  }, [lang]);

  const apply = useCallback((next: SimState, newEvents: SimEvent[] = []) => {
    setState(next);
    if (newEvents.length) setEvents((old) => [...newEvents.slice(-30).reverse(), ...old].slice(0, 30));
    setVersion((v) => v + 1);
    setError(null);
  }, []);

  const guarded = useCallback(
    async (fn: () => Promise<void>) => {
      setBusy(true);
      try {
        await fn();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const refresh = useCallback(
    () => guarded(async () => apply(await api<SimState>("/sim/state", sessionId))),
    [guarded, apply, sessionId],
  );

  // First load: state is set in the fetch callback, never synchronously in the effect.
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    api<SimState>("/sim/state", sessionId)
      .then((next) => !cancelled && apply(next))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [sessionId, apply]);

  const advance = useCallback(
    (days: number) =>
      guarded(async () => {
        const body = await post<{ state: SimState; events: SimEvent[] }>("/sim/advance-time", sessionId, { days });
        setEpoch((e) => e + 1);
        apply(body.state, body.events);
      }),
    [guarded, apply, sessionId],
  );

  const jumpToPayday = useCallback(
    () =>
      guarded(async () => {
        const body = await post<{ state: SimState; events: SimEvent[] }>("/sim/jump-to-payday", sessionId, {});
        setEpoch((e) => e + 1);
        apply(body.state, body.events);
      }),
    [guarded, apply, sessionId],
  );

  const reset = useCallback(
    () =>
      guarded(async () => {
        setEvents([]);
        setGuide(null);
        setEpoch((e) => e + 1);
        apply(await post<SimState>("/sim/reset", sessionId));
      }),
    [guarded, apply, sessionId],
  );

  const runScenario = useCallback(
    (name: string) =>
      guarded(async () => {
        const result = await post<ScenarioResult>("/sim/scenario", sessionId, { name });
        setEvents([]);
        setGuide(result);
        setEpoch((e) => e + 1);
        apply(result.state);
      }),
    [guarded, apply, sessionId],
  );

  const demoMode = useCallback(async () => {
    await reset();
    setDemoEpoch((d) => d + 1);
    router.push("/employee");
  }, [reset, router]);

  const value = useMemo<SimContextValue>(
    () => ({
      sessionId,
      lang,
      setLang,
      t: dict[lang],
      state,
      events,
      version,
      epoch,
      demoEpoch,
      demoMode,
      busy,
      error,
      guide,
      closeGuide: () => setGuide(null),
      advance,
      jumpToPayday,
      reset,
      runScenario,
      refresh,
    }),
    [sessionId, lang, setLang, state, events, version, epoch, demoEpoch, demoMode, busy, error, guide, advance, jumpToPayday, reset, runScenario, refresh],
  );

  return <SimContext.Provider value={value}>{children}</SimContext.Provider>;
}

export function useSim(): SimContextValue {
  const ctx = useContext(SimContext);
  if (!ctx) throw new Error("useSim must be used inside <SimProvider>");
  return ctx;
}
