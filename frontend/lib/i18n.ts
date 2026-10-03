export type Lang = "en" | "bn";

const en = {
  appName: "SalaryBridge",
  tagline: "Earned-wage advance prototype for upay · all data is synthetic",
  roles: { employee: "Employee", employer: "Employer HR", ops: "upay Ops", validation: "Validation" },
  sim: {
    today: "Simulated date",
    nextPayday: "Next payday",
    inDays: (n: number) => `in ${n} day${n === 1 ? "" : "s"}`,
    plus1: "+1 day",
    plus7: "+7 days",
    payday: "Jump to payday",
    reset: "Reset",
    scenarios: "Scenarios",
    killOn: "Kill switch ON: new advances paused",
    busy: "Working…",
  },
  scenarios: {
    s1: "Happy path",
    s2: "Employer pays late",
    s3: "Resigns before payday",
    s4: "Employer defaults",
    s5: "Chronic borrower",
    s6: "Eid surge",
    s7: "Kill switch",
  },
  guide: { setup: "Set up", try: "Try", expect: "Expect", close: "Close" },
  ledger: {
    title: "Money flow",
    reconciled: "Reconciled",
    notReconciled: "NOT reconciled",
    entries: (n: number) => `${n} ledger entries`,
    capital: "Capital set aside by upay",
    pool: "Advance pool",
    wallets: "In employee wallets",
    spent: "Spent by employees",
    employers: "Paid in by employers",
    fees: "Fees earned",
    losses: "Written off",
    events: "Latest events",
    noEvents: "Move time forward to see payroll and settlement events.",
  },
  apiDown: "Cannot reach the API",
  placeholder: "This view is built in a later step.",
};

const bn: typeof en = {
  appName: "স্যালারিব্রিজ",
  tagline: "upay-র জন্য অর্জিত-বেতন আগামের প্রোটোটাইপ · সব ডেটা কৃত্রিম",
  roles: { employee: "কর্মী", employer: "প্রতিষ্ঠানের HR", ops: "upay অপারেশন", validation: "যাচাই" },
  sim: {
    today: "সিমুলেশনের তারিখ",
    nextPayday: "পরের বেতনের দিন",
    inDays: (n: number) => `${toBn(n)} দিন পর`,
    plus1: "+১ দিন",
    plus7: "+৭ দিন",
    payday: "বেতনের দিনে যান",
    reset: "রিসেট",
    scenarios: "দৃশ্য",
    killOn: "কিল সুইচ চালু: নতুন আগাম বন্ধ",
    busy: "চলছে…",
  },
  scenarios: {
    s1: "স্বাভাবিক পথ",
    s2: "দেরিতে বেতন",
    s3: "বেতনের আগে চাকরি ছাড়া",
    s4: "প্রতিষ্ঠান বেতন দেয়নি",
    s5: "নিয়মিত ঋণগ্রহীতা",
    s6: "ঈদের চাপ",
    s7: "কিল সুইচ",
  },
  guide: { setup: "যা সাজানো হলো", try: "করে দেখুন", expect: "যা দেখবেন", close: "বন্ধ" },
  ledger: {
    title: "টাকার প্রবাহ",
    reconciled: "হিসাব মিলেছে",
    notReconciled: "হিসাব মেলেনি",
    entries: (n: number) => `${toBn(n)}টি লেজার এন্ট্রি`,
    capital: "upay-র রাখা মূলধন",
    pool: "আগামের পুল",
    wallets: "কর্মীদের ওয়ালেটে",
    spent: "কর্মীরা খরচ করেছেন",
    employers: "প্রতিষ্ঠান থেকে এসেছে",
    fees: "ফি আয়",
    losses: "লোকসান লেখা হয়েছে",
    events: "সাম্প্রতিক ঘটনা",
    noEvents: "বেতন ও সেটেলমেন্ট দেখতে সময় এগিয়ে নিন।",
  },
  apiDown: "API-তে পৌঁছানো যাচ্ছে না",
  placeholder: "এই অংশ পরের ধাপে তৈরি হবে।",
};

export const dict = { en, bn };
export type Dict = typeof en;

export function toBn(value: number | string): string {
  return String(value).replace(/\d/g, (d) => "০১২৩৪৫৬৭৮৯"[Number(d)]);
}

export function money(value: number, lang: Lang): string {
  const text = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(Math.round(value) + 0); // + 0 turns -0 into 0
  return lang === "bn" ? `৳${toBn(text)}` : `৳${text}`;
}

export function longDate(iso: string, lang: Lang): string {
  const d = new Date(`${iso}T00:00:00`);
  return new Intl.DateTimeFormat(lang === "bn" ? "bn-BD" : "en-GB", { day: "numeric", month: "long", year: "numeric" }).format(d);
}

export function shortDate(iso: string, lang: Lang): string {
  const d = new Date(`${iso}T00:00:00`);
  return new Intl.DateTimeFormat(lang === "bn" ? "bn-BD" : "en-GB", { day: "numeric", month: "short" }).format(d);
}
