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
  employee: {
    choose: "Demo person",
    salary: "Monthly salary",
    earned: "Earned so far this month",
    daysOf: (d: number, n: number) => `${d} of ${n} days`,
    available: "You can get up to",
    nothingNow: "No advance available right now",
    howMuch: "How much do you need?",
    request: (amount: string) => `Request ${amount}`,
    yourOffer: "Your offer",
    fee: "Fee",
    deducted: "Deducted from salary",
    on: "on",
    why: "Why this amount",
    accept: (amount: string) => `Get ${amount} now`,
    cancel: "Not now",
    aiWording: "Explain in simpler words (AI)",
    aiLabel: "Worded by Claude from the same facts",
    templateLabel: "Standard explanation",
    queuedTitle: "Request received",
    declinedTitle: "Not available right now",
    back: "Back",
    doneTitle: "Money sent to your wallet",
    wallet: "upay wallet",
    doneNote: (total: string, date: string) => `${total} will be deducted from your salary on ${date}. No other charges.`,
    history: "Your advances",
    noHistory: "No advances yet.",
    status: { open: "Due on payday", carried_over: "Carried to next payday", pending_write_off: "Unpaid", recovered: "Repaid", written_off: "Written off" } as Record<string, string>,
    late: "after grace",
    left: "No longer on payroll",
    sample: "History shows recent synthetic months.",
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
  employee: {
    choose: "ডেমো চরিত্র",
    salary: "মাসিক বেতন",
    earned: "এ মাসে এ পর্যন্ত উপার্জিত",
    daysOf: (d: number, n: number) => `${toBn(n)} দিনের মধ্যে ${toBn(d)} দিন`,
    available: "আপনি পেতে পারেন সর্বোচ্চ",
    nothingNow: "এই মুহূর্তে আগাম নেওয়া যাচ্ছে না",
    howMuch: "কত টাকা দরকার?",
    request: (amount: string) => `${amount} চাই`,
    yourOffer: "আপনার অফার",
    fee: "ফি",
    deducted: "বেতন থেকে কাটা হবে",
    on: "তারিখ",
    why: "কেন এই অঙ্ক",
    accept: (amount: string) => `এখনই ${amount} নিন`,
    cancel: "এখন না",
    aiWording: "আরও সহজ ভাষায় বলুন (AI)",
    aiLabel: "একই তথ্য থেকে Claude লিখেছে",
    templateLabel: "নির্ধারিত ব্যাখ্যা",
    queuedTitle: "অনুরোধ পাওয়া গেছে",
    declinedTitle: "এই মুহূর্তে পাওয়া যাচ্ছে না",
    back: "ফিরে যান",
    doneTitle: "টাকা আপনার ওয়ালেটে পাঠানো হয়েছে",
    wallet: "upay ওয়ালেট",
    doneNote: (total: string, date: string) => `${date} তারিখে বেতন থেকে ${total} কাটা হবে। অন্য কোনো খরচ নেই।`,
    history: "আপনার আগাম",
    noHistory: "এখনো কোনো আগাম নেই।",
    status: { open: "বেতনের দিনে কাটা হবে", carried_over: "পরের বেতনে কাটা হবে", pending_write_off: "অপরিশোধিত", recovered: "পরিশোধিত", written_off: "লোকসান লেখা" } as Record<string, string>,
    late: "গ্রেসের পরে",
    left: "আর বেতন তালিকায় নেই",
    sample: "ইতিহাসে সাম্প্রতিক কৃত্রিম মাসগুলো দেখানো হয়েছে।",
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
