"""Generate docs/demo_script.md by running the demo through the real API and recording what the screens will show.

Run (from backend/):  .venv/Scripts/python.exe -m scripts.demo_script
Every number in the script is observed from this run; re-run after any change to rules, models or data.
"""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, get_settings

OUT = BACKEND_DIR.parent / "docs" / "demo_script.md"
H = {"X-Session-Id": "demo-script-0001"}


def money(x: float) -> str:
    """Bangladeshi digit grouping, as the screens show it (en-IN): ৳22,07,073; negatives as −৳10,876."""
    n = round(abs(x))
    text = str(n)
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        text = ",".join([head, *groups, tail]) if head else ",".join([*groups, tail])
    return f"{'−' if x < 0 else ''}৳{text}"


def day(iso: str) -> str:
    from datetime import date

    d = date.fromisoformat(iso[:10])
    return f"{d.day} {d.strftime('%B')} {d.year}"


def pct(x: float, d: int = 0) -> str:
    return f"{x * 100:.{d}f}%"


def break_even(base: dict, policy: dict) -> tuple[float, float]:
    """Same formula as the validation page (plan.md §10) at the default assumptions."""
    m = base["world_months"]
    advances = base["advances_ml"] / m
    funds = base["funds_ml_bdt"] / m
    loss = base["loss_ml_bdt"] / m
    rate = policy["capital_rate_annual_pct"] / 100
    capital = funds * base["avg_days_outstanding"] / 365 * rate
    idle = funds * policy["capital_buffer_pct"] / 100 * 30 / 365 * rate
    ops = advances * policy["ops_cost_per_advance_bdt"]
    costs = loss + capital + idle + ops
    return advances * policy["fee_flat_bdt"] - costs, costs / advances


def run() -> dict:
    os.environ.setdefault("SIM_DIR", str(Path(tempfile.gettempdir()) / "salarybridge_demo_script"))
    get_settings.cache_clear()
    from app.main import app  # imported after SIM_DIR is set

    c = TestClient(app)
    obs: dict = {}
    c.post("/sim/reset", headers=H)
    obs["state0"] = c.get("/sim/state", headers=H).json()
    p = {x["key"]: x for x in c.get("/personas", headers=H).json()}
    obs["personas"] = p

    rahim, shapla = p["rahim"], p["shapla"]
    obs["rahim_summary"] = c.get(f"/employee/{rahim['employee_id']}/summary", headers=H).json()
    # The slider stops at the available amount, so the demo requests exactly that (what a click can do).
    obs["rahim_offer"] = c.post("/advance/offer", json={"employee_id": rahim["employee_id"], "amount_bdt": obs["rahim_summary"]["available_bdt"]}, headers=H).json()
    obs["rahim_accept"] = c.post("/advance/accept", json={"decision_id": obs["rahim_offer"]["decision_id"]}, headers=H).json()

    obs["shapla_summary"] = c.get(f"/employee/{shapla['employee_id']}/summary", headers=H).json()
    obs["shapla_offer"] = c.post("/advance/offer", json={"employee_id": shapla["employee_id"], "amount_bdt": obs["shapla_summary"]["available_bdt"]}, headers=H).json()
    obs["shapla_accept"] = c.post("/advance/accept", json={"decision_id": obs["shapla_offer"]["decision_id"]}, headers=H).json()  # so payday shows two

    obs["hr"] = c.get(f"/employer/{rahim['employer_id']}/dashboard", headers=H).json()
    obs["hr_confirm"] = c.post(f"/employer/{rahim['employer_id']}/deduction-notice/confirm", headers=H).json()
    obs["payday"] = c.post("/sim/jump-to-payday", json={"employer_id": rahim["employer_id"]}, headers=H).json()
    obs["rahim_after"] = c.get(f"/employee/{rahim['employee_id']}/summary", headers=H).json()

    s4 = c.post("/sim/scenario", json={"name": "s4"}, headers=H).json()
    row = lambda: next(e for e in c.get("/ops/employers", headers=H).json() if e["employer_id"] == s4["employer_id"])  # noqa: E731
    obs["s4"] = {"scenario": s4, "before": row()}
    c.post("/sim/jump-to-payday", json={"employer_id": s4["employer_id"]}, headers=H)
    obs["s4"]["after"] = row()

    s6 = c.post("/sim/scenario", json={"name": "s6"}, headers=H).json()
    obs["s6"] = {"scenario": s6, "kpis": c.get("/ops/summary", headers=H).json()}
    r6 = c.get(f"/employee/{rahim['employee_id']}/summary", headers=H).json()
    obs["s6"]["offer"] = c.post("/advance/offer", json={"employee_id": rahim["employee_id"], "amount_bdt": r6["available_bdt"]}, headers=H).json()
    obs["s6"]["approve"] = c.post(
        f"/ops/queue/{obs['s6']['offer']['decision_id']}/approve", json={"note": "Reliable employer; pool top-up requested"}, headers=H
    ).json()

    obs["validation"] = c.get("/validation").json()
    obs["policy"] = c.get("/config/public").json()["policy"]
    return obs


def render(o: dict) -> str:
    r_sum, r_off, r_acc = o["rahim_summary"], o["rahim_offer"], o["rahim_accept"]
    s_off = o["shapla_offer"]
    hr, payday = o["hr"], o["payday"]["state"]
    s4b, s4a = o["s4"]["before"], o["s4"]["after"]
    s6 = o["s6"]
    v = o["validation"]
    passed = sum(t["pass"] for t in v["targets"])
    failed = [t["id"] for t in v["targets"] if not t["pass"]]
    a_pol, b_pol = v["profile_a"]["policies"], v["profile_b"]["policies"]
    net_a, be_a = break_even(v["profile_a"]["economics_base"], o["policy"])
    _, be_b = break_even(v["profile_b"]["economics_base"], o["policy"])
    rahim_says = r_off["explanation"]["en"]
    shapla_says = s_off["explanation"]["en"]

    return f"""# Demo Script (3 minutes)

Generated by `python -m scripts.demo_script`, which ran this exact flow through the API and recorded what each screen
shows. Do not edit numbers by hand; re-run the script after any change. Story and screens: `plot.md`.

**Before you start:** open the API `/health` 10 minutes early (free hosting sleeps). Press **Demo mode** (top bar):
it resets the simulation to {day(o['state0']['sim_date'])} and opens the employee screen with Rahim.

| Time | Screen | Click | What the audience sees |
|---|---|---|---|
| 0:00–0:20 | Employee | — | Hook: it is the 20th, salary comes on the 1st, Rahim needs money today. |
| 0:20–0:50 | Employee (Rahim) | Leave the slider at the maximum ({money(r_sum['available_bdt'])}) → **Request {money(r_sum['available_bdt'])}** → **Get {money(r_off['approved_amount_bdt'])} now** | Earned so far {money(r_sum['earned_to_date_bdt'])} ({r_sum['days_worked']} of {r_sum['days_in_month']} days); offer **{money(r_off['approved_amount_bdt'])}** (tier {r_off['tier']}), fee {money(r_off['fee_bdt'])}, **{money(r_off['total_due_bdt'])}** deducted on {day(r_off['repayment_date'])}; wallet becomes {money(float(r_acc['wallet_balance_bdt']))} |
| 0:50–1:10 | Employee (Shapla) | **Shapla** → **Request {money(o['shapla_summary']['available_bdt'])}** | Her limit is only **{money(o['shapla_summary']['available_bdt'])}** of a {money(s_off['hard_cap_bdt'])} salary cap (tier {s_off['tier']}); the offer says why: her employer has paid late recently |
| 1:10–1:30 | Employer HR | **Employer HR** → {hr['employer_id']} → **Confirm remittance** | One list, one total: **{money(hr['total_to_deduct_bdt'])}** on {day(hr['payday'])}; no risk scores anywhere |
| 1:30–1:50 | (any) | **Jump to payday** | Date {day(payday['sim_date'])}; employer remits once; outstanding {money(payday['advances']['outstanding_bdt'])}; fees earned {money(payday['advances']['fees_income_bdt'])}; ledger **✓ Reconciled** |
| 1:50–2:05 | upay Ops | **S4 · Employer defaults** → **Jump to payday** | {s4b['employer_id']} risk {pct(s4b['prob_late'])} → **{pct(s4a['prob_late'])} (known fact, by rule)**, status {s4a['status']}, exposure {money(s4a['exposure_bdt'])}; staff of that employer are paused |
| 2:05–2:20 | upay Ops | **S6 · Eid surge** → Employee: Rahim **Request** → Ops: note + **Approve** | Pool {money(s6['kpis']['pool_bdt'])} below need {money(s6['kpis']['required_pool_bdt'])}; Rahim's request goes to the queue ({s6['offer']['status']}); approved with a written note |
| 2:20–2:50 | Validation | **Validation** | {passed} of {len(v['targets'])} pre-declared targets pass; failures shown openly ({', '.join(failed)}). ML vs flat cap loss: A {pct(a_pol['ml_tier']['loss_rate'], 2)} vs {pct(a_pol['flat_cap']['loss_rate'], 2)}, B {pct(b_pol['ml_tier']['loss_rate'], 2)} vs {pct(b_pol['flat_cap']['loss_rate'], 2)}. Economics slider: at the {money(o['policy']['fee_flat_bdt'])} fee the month nets {money(net_a)} (A); **break-even fee {money(math.ceil(be_a))} (A) / {money(math.ceil(be_b))} (B)** (rounded up, as on the page) |
| 2:50–3:00 | — | — | Close: synthetic data, legal review and HR interviews before a pilot; next step is controlled validation on real, governed data. |

## Talking points per beat

- **Rahim (0:20):** "The limit is wages already earned, capped at {o['policy']['cap_pct_of_salary']:g}% of salary. The AI can only make
  the offer smaller or send it to a person; it can never say no on its own." His screen says: *{rahim_says}*
- **Shapla (0:50):** "She earns {o['personas']['shapla']['salary_bdt'] / o['personas']['rahim']['salary_bdt']:.1f}× Rahim's salary, yet her limit is a smaller share of it, and the screen says why." Her screen says: *{shapla_says}*
- **HR (1:10):** "One list and one transfer a month. HR never sees anyone's risk."
- **Payday (1:30):** "Every taka is in a double-entry ledger; the badge proves it still balances."
- **Default (1:50):** "When something is a known fact, we use a rule, not a model. Our model had never seen a default, so we
  don't let it guess."
- **Eid (2:05):** "The capital forecast tells upay how much to keep in the pool. When the pool is short, a person decides."
- **Validation (2:20):** "We wrote the targets before we had results. Two fail, and we show why."

## Bangla lines (optional)

- রহিম: "এ পর্যন্ত যা উপার্জন করেছেন, তার একটা অংশ এখনই। ফি আর কাটার তারিখ আগে থেকেই দেখানো হয়।"
- শাপলা: "একই অনুরোধে ছোট অফার, আর কারণটাও বলা থাকে।"
- HR: "মাসে একটা তালিকা, একবার টাকা পাঠানো। HR কারও ঝুঁকি দেখে না।"
- যাচাই: "লক্ষ্যগুলো ফল দেখার আগেই লেখা। যেগুলো ব্যর্থ, সেগুলোও খোলাখুলি দেখাই।"

## If something goes wrong

- **"Cannot reach the API":** the free server is waking up; wait a minute and press **Reset**.
- **Numbers differ from this script:** someone changed rules, models or data; re-run `python -m scripts.demo_script`.
- **A scenario looks wrong:** press its button again (each scenario starts from a clean session).
"""


def main() -> None:
    obs = run()
    OUT.write_text(render(obs), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
