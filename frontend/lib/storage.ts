"use client";

import { useSyncExternalStore } from "react";

/** A tiny localStorage-backed store. Every access is guarded: storage can be blocked or missing. */
const listeners = new Set<() => void>();

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

const memory = new Map<string, string>(); // fallback when storage is blocked

export function getItem(key: string): string | null {
  return read(key) ?? memory.get(key) ?? null;
}

export function setItem(key: string, value: string) {
  memory.set(key, value);
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* blocked: the in-memory copy keeps this page working */
  }
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  const onStorage = () => fn();
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("storage", onStorage);
  };
}

/** Value from storage on the client, `serverValue` during server rendering and hydration. */
export function useStoredValue(key: string, serverValue: string, init?: () => string): string {
  return useSyncExternalStore(
    subscribe,
    () => {
      let value = getItem(key);
      if (value === null && init) {
        value = init();
        memory.set(key, value);
        try {
          window.localStorage.setItem(key, value);
        } catch {
          /* blocked */
        }
      }
      return value ?? serverValue;
    },
    () => serverValue,
  );
}
