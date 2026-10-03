"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useSim } from "@/components/SimProvider";

const ROLES = ["employee", "employer", "ops", "validation"] as const;

export function Header() {
  const { t, lang, setLang } = useSim();
  const pathname = usePathname();

  return (
    <header className="border-b border-line bg-panel">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
        <div className="min-w-0">
          <div className="text-lg font-semibold tracking-tight">{t.appName}</div>
          <div className="text-xs text-muted">{t.tagline}</div>
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
                  active ? "bg-accent text-white" : "text-muted hover:bg-accent-soft hover:text-ink"
                }`}
              >
                {t.roles[role]}
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex overflow-hidden rounded-md border border-line text-sm" role="group" aria-label="Language">
          {(["en", "bn"] as const).map((code) => (
            <button
              key={code}
              type="button"
              onClick={() => setLang(code)}
              aria-pressed={lang === code}
              className={`px-3 py-1 ${lang === code ? "bg-ink text-panel" : "text-muted hover:text-ink"}`}
            >
              {code === "en" ? "EN" : "বাংলা"}
            </button>
          ))}
        </div>
      </div>
    </header>
  );
}
