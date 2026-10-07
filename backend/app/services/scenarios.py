"""One-click demo scenarios S1–S7 from plot.md §4.

Each scenario starts from a clean session (unless reset=False), sets up what the story needs, and
returns who to use, what was set up and what the audience should see. Nothing here changes how the
system decides; it only arranges the world (resignations, forced payroll outcomes, Eid flags, pool size).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta

from app.llm.explain import _fmt_date, _fmt_num
from app.services import advances
from app.services.capital import forecast
from app.services.decision import decide
from app.sim.personas import personas_for
from app.sim.session import SimSession

ALIASES = {
    "s1": "s1_happy_path",
    "s2": "s2_risky_employer",
    "s3": "s3_resigns_before_payday",
    "s4": "s4_employer_defaults",
    "s5": "s5_chronic_borrower",
    "s6": "s6_eid_surge",
    "s7": "s7_kill_switch",
}


class ScenarioError(ValueError):
    pass


NAMES_BN = {"Rahim": "রহিম", "Shapla": "শাপলা", "Karim": "করিম"}


def _n(x: int) -> str:
    return _fmt_num(int(x), "bn")


def _d(iso: str) -> str:
    return _fmt_date(str(iso)[:10], "bn")


def _bn(name: str) -> str:
    return NAMES_BN.get(name, name)


def _month_bn(month: str) -> str:
    return _d(f"{month}-01").split(" ", 1)[1]  # drop the day: "অক্টোবর ২০২৬"


def _personas(session: SimSession) -> dict:
    start = session.world.first_live_month.replace(day=session.settings.sim_start_day)
    return {p.key: asdict(p) for p in personas_for(session.settings.database_url, start)}


def _take(session: SimSession, employee_id: str, amount: int) -> dict:
    d = decide(session, employee_id, amount, session.settings.policy)
    if d.status != "offered":
        raise ScenarioError(f"Setup needed an automatic offer for {employee_id} but got '{d.status}'")
    accepted = advances.accept(session, d.decision_id)
    if accepted.get("status") == "awaiting_employer":  # the scenario's setup includes HR's confirmation
        accepted = advances.employer_decide(session, d.employer_id, d.decision_id, True)
    return accepted


def _work_month(session: SimSession) -> str:
    return session.sim_date.isoformat()[:7]


def s1_happy_path(session: SimSession, p: dict) -> dict:
    return {
        "title": "Happy path",
        "persona": p["rahim"],
        "setup": [],
        "try": [f"Ask for 5,000 BDT as {p['rahim']['name']}", "Accept the offer", "Jump to payday"],
        "expect": ["Offer at the 20% limit with reasons", "On payday the employer remits once and the advance is recovered", "Ledger reconciles"],
        "bn": {
            "title": "স্বাভাবিক পথ",
            "setup": [],
            "try": [f"{_bn(p['rahim']['name'])} হিসেবে {_n(5000)} টাকা চান", "অফার গ্রহণ করুন", "বেতনের দিনে যান"],
            "expect": ["কারণসহ ২০% সীমায় অফার", "বেতনের দিনে প্রতিষ্ঠান একবারে টাকা পাঠায়, আগাম ফেরত আসে", "লেজারের হিসাব মেলে"],
        },
    }


def s2_risky_employer(session: SimSession, p: dict) -> dict:
    shapla = p["shapla"]
    session.force_payroll(shapla["employer_id"], _work_month(session), "late", 3, 1.0)
    return {
        "title": "Risky employer",
        "persona": shapla,
        "setup": [f"{shapla['employer_id']} will pay this month's wages 3 days late (within the {session.settings.policy.grace_days}-day grace)"],
        "try": [f"Ask for 10,000 BDT as {shapla['name']}", "Accept the smaller offer", "Advance 20 days"],
        "expect": ["A smaller limit than the hard cap, with employer-lateness reasons", "Recovered late but within grace"],
        "bn": {
            "title": "দেরিতে বেতন দেওয়া প্রতিষ্ঠান",
            "setup": [f"{shapla['employer_id']} এ মাসের বেতন {_n(3)} দিন দেরিতে দেবে (গ্রেস {_n(session.settings.policy.grace_days)} দিনের মধ্যে)"],
            "try": [f"{_bn(shapla['name'])} হিসেবে {_n(10000)} টাকা চান", "ছোট অফারটি গ্রহণ করুন", f"{_n(20)} দিন এগিয়ে যান"],
            "expect": ["সর্বোচ্চ সীমার চেয়ে কম অফার, কারণ হিসেবে প্রতিষ্ঠানের দেরি", "দেরিতে, তবে গ্রেসের মধ্যে ফেরত"],
        },
    }


def s3_resigns_before_payday(session: SimSession, p: dict) -> dict:
    rahim = p["rahim"]
    adv = _take(session, rahim["employee_id"], 5_000)
    end = session.sim_date + timedelta(days=3)
    session.resign(rahim["employee_id"], end, "voluntary")
    return {
        "title": "Resigns before payday",
        "persona": rahim,
        "setup": [f"{rahim['name']} took {adv['amount_bdt']} BDT ({adv['advance_id']})", f"{rahim['name']} leaves the job on {end.isoformat()}, before payday {adv['due_date']}"],
        "try": ["Jump to payday", f"Advance {session.settings.policy.writeoff_after_days + 5} days"],
        "expect": ["Waterfall: employer final settlement, then wallet, then write-off", "Loss counted in loss_provision if nothing could be collected"],
        "advance_id": adv["advance_id"],
        "bn": {
            "title": "বেতনের আগে চাকরি ছাড়া",
            "setup": [
                f"{_bn(rahim['name'])} {_n(adv['amount_bdt'])} টাকা নিয়েছেন ({adv['advance_id']})",
                f"{_bn(rahim['name'])} {_d(end.isoformat())} তারিখে চাকরি ছাড়েন, বেতনের দিন {_d(adv['due_date'])}-এর আগে",
            ],
            "try": ["বেতনের দিনে যান", f"{_n(session.settings.policy.writeoff_after_days + 5)} দিন এগিয়ে যান"],
            "expect": ["ধাপে ধাপে আদায়: প্রতিষ্ঠানের চূড়ান্ত পাওনা, তারপর ওয়ালেট, তারপর লোকসান লেখা", "কিছু আদায় না হলে লোকসান হিসাবে লেখা হয়"],
        },
    }


def s4_employer_defaults(session: SimSession, p: dict) -> dict:
    shapla = p["shapla"]
    adv = _take(session, shapla["employee_id"], 3_000)
    session.force_payroll(shapla["employer_id"], _work_month(session), "default", 0, 0.0)
    return {
        "title": "Employer defaults",
        "persona": shapla,
        "employer_id": shapla["employer_id"],
        "setup": [f"{shapla['name']} took {adv['amount_bdt']} BDT ({adv['advance_id']})", f"{shapla['employer_id']} will not pay this month's wages"],
        "try": [f"Jump to payday for {shapla['employer_id']}", "Open the ops employer table", "Ask for an advance as any colleague"],
        "expect": ["Employer payroll risk rises and the employer shows as closed", "New advances for its staff are paused (EMPLOYER_CLOSED)", "Exposure for that employer is shown until it is written off"],
        "advance_id": adv["advance_id"],
        "bn": {
            "title": "প্রতিষ্ঠান বেতন দেয়নি",
            "setup": [f"{_bn(shapla['name'])} {_n(adv['amount_bdt'])} টাকা নিয়েছেন ({adv['advance_id']})", f"{shapla['employer_id']} এ মাসের বেতন দেবে না"],
            "try": [f"{shapla['employer_id']}-এর বেতনের দিনে যান", "অপারেশনের প্রতিষ্ঠান-টেবিল খুলুন", "যেকোনো সহকর্মী হিসেবে আগাম চান"],
            "expect": ["প্রতিষ্ঠানের ঝুঁকি বাড়ে এবং বন্ধ দেখায়", "এর কর্মীদের নতুন আগাম বন্ধ (EMPLOYER_CLOSED)", "লোকসান লেখা পর্যন্ত বকেয়া দেখা যায়"],
        },
    }


def s5_chronic_borrower(session: SimSession, p: dict) -> dict:
    return {
        "title": "Chronic borrower",
        "persona": p["karim"],
        "setup": [],
        "try": [f"Ask for 3,000 BDT as {p['karim']['name']}"],
        "expect": ["A one-month pause (cooling-off) with a supportive message, not a hard 'no'", "Chronic borrowing flagged"],
        "bn": {
            "title": "নিয়মিত ঋণগ্রহীতা",
            "setup": [],
            "try": [f"{_bn(p['karim']['name'])} হিসেবে {_n(3000)} টাকা চান"],
            "expect": ["সরাসরি 'না' নয়, সহায়ক বার্তাসহ এক মাসের বিরতি", "নিয়মিত ঋণ নেওয়া চিহ্নিত হয়"],
        },
    }


def s6_eid_surge(session: SimSession, p: dict) -> dict:
    normal = forecast(session)
    month = _work_month(session)
    session.set_pool(normal["current_required_pool_bdt"], "Pool sized for a normal month")
    next_month = next((m["month"] for m in normal["months"] if m["month"] > month), month)
    session.add_eid_months([month, next_month])
    surge = forecast(session)
    return {
        "title": "Eid surge",
        "persona": p["rahim"],
        "setup": [
            f"Pool set to a normal month's need: {normal['current_required_pool_bdt']:,} BDT",
            f"{month} and {next_month} marked as Eid months",
        ],
        "try": ["Open the ops capital forecast", f"Ask for an advance as {p['rahim']['name']}"],
        "expect": ["The forecast band rises", "Pool is now below the P90 + buffer need", "Requests go to the human queue (POOL_BELOW_FORECAST)"],
        "required_before_bdt": normal["current_required_pool_bdt"],
        "required_after_bdt": surge["current_required_pool_bdt"],
        "pool_bdt": surge["pool_bdt"],
        "bn": {
            "title": "ঈদের চাপ",
            "setup": [
                f"পুল স্বাভাবিক মাসের প্রয়োজনে রাখা হলো: {_n(normal['current_required_pool_bdt'])} টাকা",
                f"{_month_bn(month)} ও {_month_bn(next_month)} ঈদের মাস হিসেবে চিহ্নিত",
            ],
            "try": ["অপারেশনের মূলধন-পূর্বাভাস খুলুন", f"{_bn(p['rahim']['name'])} হিসেবে আগাম চান"],
            "expect": ["পূর্বাভাসের ব্যান্ড উপরে ওঠে", "পুল এখন P90 + বাফারের প্রয়োজনের চেয়ে কম", "অনুরোধ মানুষের তালিকায় যায় (POOL_BELOW_FORECAST)"],
        },
    }


def s7_kill_switch(session: SimSession, p: dict) -> dict:
    rahim = p["rahim"]
    adv = _take(session, rahim["employee_id"], 2_000)
    session.set_kill_switch(True)
    return {
        "title": "Kill switch",
        "persona": p["shapla"],
        "setup": [f"{rahim['name']} already has an advance ({adv['advance_id']})", "Kill switch turned on"],
        "try": [f"Ask for an advance as {p['shapla']['name']}", "Jump to payday"],
        "expect": ["New requests are blocked with a clear message", f"{rahim['name']}'s existing advance still settles on payday"],
        "advance_id": adv["advance_id"],
        "bn": {
            "title": "কিল সুইচ",
            "setup": [f"{_bn(rahim['name'])}-এর একটি চলমান আগাম আছে ({adv['advance_id']})", "কিল সুইচ চালু করা হয়েছে"],
            "try": [f"{_bn(p['shapla']['name'])} হিসেবে আগাম চান", "বেতনের দিনে যান"],
            "expect": ["নতুন অনুরোধ স্পষ্ট বার্তাসহ বন্ধ থাকে", f"{_bn(rahim['name'])}-এর চলমান আগাম বেতনের দিনে ঠিকই আদায় হয়"],
        },
    }


SCENARIOS = {f.__name__: f for f in (s1_happy_path, s2_risky_employer, s3_resigns_before_payday, s4_employer_defaults, s5_chronic_borrower, s6_eid_surge, s7_kill_switch)}


def run(session: SimSession, name: str, reset: bool = True) -> dict:
    key = ALIASES.get(name.lower(), name.lower())
    if key not in SCENARIOS:
        raise ScenarioError(f"Unknown scenario '{name}'. Choose one of: {', '.join(SCENARIOS)}")
    if reset:
        session = session.reset()
    out = SCENARIOS[key](session, _personas(session))
    return {"scenario": key, **out, "state": session.state()}
