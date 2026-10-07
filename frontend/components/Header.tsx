"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useSim } from "@/components/SimProvider";

const ROLES = ["employee", "employer", "ops", "validation"] as const;

export function Header() {
  const { t, lang, setLang } = useSim();
  const pathname = usePathname();

  return (
    <header className="border-b-4 border-brand-yellow bg-brand-navy text-white">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <span aria-hidden className="flex h-9 w-9 shrink-0 items-end justify-center gap-1 rounded-lg bg-white pb-2">
            <span className="h-3 w-3 rounded-full bg-brand-yellow" />
            <span className="h-3 w-3 rounded-full bg-brand-blue" />
          </span>
          <div className="min-w-0">
            <div className="text-lg font-semibold tracking-tight">{t.appName} <span className="text-sm font-medium text-brand-yellow">· upay</span></div>
            <div className="text-xs text-white/70">{t.tagline}</div>
          </div>
        </div>
        <nav aria-label="Role" className="order-3 grid w-full grid-cols-2 gap-1 sm:order-none sm:flex sm:w-auto">
          {ROLES.map((role) => {
            const active = pathname.startsWith(`/${role}`);
            return (
              <Link
                key={role}
                href={`/${role}`}
                aria-current={active ? "page" : undefined}
                className={`whitespace-nowrap rounded-md px-3 py-1.5 text-center text-sm font-medium transition-colors ${
                  active ? "bg-brand-yellow text-brand-navy" : "text-white/75 hover:bg-white/10 hover:text-white"
                }`}
              >
                {t.roles[role]}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex overflow-hidden rounded-md border border-white/30 text-sm" role="group" aria-label="Language">
          {(["en", "bn"] as const).map((code) => (
            <button
              key={code}
              type="button"
              onClick={() => setLang(code)}
              aria-pressed={lang === code}
              className={`px-3 py-1 ${lang === code ? "bg-brand-yellow font-semibold text-brand-navy" : "text-white/75 hover:text-white"}`}
            >
              {code === "en" ? "EN" : "বাংলা"}
            </button>
          ))}
        </div>
      </div>
    </header>
  );
}
