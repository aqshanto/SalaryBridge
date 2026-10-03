# Responsible AI and Security Controls

Every control below names the code that implements it and the test that guards it.
`tests/test_responsible_ai.py::test_doc_references_existing_tests` fails if any test named here stops existing.

Run everything: `.venv/Scripts/python.exe -m pytest` (from `backend/`). Quick loop: `-m "not slow and not integration"`.

## 1. Data and privacy

| Control | Where | Guarded by |
|---|---|---|
| Synthetic data only; no real names, phone numbers or IDs | `data/generator.py` (fictional employer names, generated IDs) | Design rule; personas are named only in the demo layer (`app/sim/personas.py`) |
| Gender, region and age band are used only for fairness audits, never as model inputs | `FORBIDDEN_FEATURES` in `app/ml/m1_employer.py` … `m5_abuse.py` | `tests/test_responsible_ai.py::test_saved_models_never_use_audit_or_hidden_attributes`, `tests/test_responsible_ai.py::test_saved_booster_features_match_the_declared_lists` |
| Hidden simulation labels (employer reliability type, borrower behaviour) never reach a model | same | same tests |
| The decision log stores inputs and model versions but no audit attributes | `app/services/decision.py` (`_log`) | `tests/test_decision.py::test_every_decision_is_logged_with_inputs_and_versions` |
| Employer HR never sees risk scores, tiers or reasons | `app/routers/employer.py` | `tests/test_employer.py::test_hr_responses_contain_no_risk_information` |
| No secrets in public endpoints; tests never call the real LLM API | `app/routers/config.py`, `tests/conftest.py` | `tests/test_config.py::test_public_config_has_no_secrets` |

## 2. Models are point-in-time (no leakage)

| Control | Guarded by |
|---|---|
| M1 uses only payroll facts known on the decision date | `tests/test_m1_employer.py::test_features_ignore_everything_after_the_decision_date` |
| M2/M3 use only earlier advances whose outcome was already known | `tests/test_m2_repayment.py::test_features_ignore_later_advances_and_unknown_outcomes` |
| M4 uses only months before the forecast origin | `tests/test_m4_capital.py::test_features_use_only_months_before_the_origin` |
| M5 ignores requests on or after the decision date | `tests/test_m5_abuse.py::test_features_ignore_requests_on_or_after_the_decision_date` |

## 3. Who decides

| Control | Where | Guarded by |
|---|---|---|
| Only fixed rules can decline; a model can only shrink the limit or send the case to a person | `app/services/decision.py` | `tests/test_decision.py::test_only_rules_can_decline`, `tests/test_responsible_ai.py::test_extreme_model_risk_queues_for_a_person_and_never_declines` |
| Tier D, large amounts, unusual patterns and pool shortfalls go to a person | `app/rules/tiers.py`, `app/rules/abuse.py`, `app/services/decision.py` | `tests/test_rules.py::test_large_amount_needs_human_review`, `tests/test_scenarios.py::test_s6_eid_surge_raises_the_band_and_queues_requests` |
| Every human approval or rejection needs a written note, and is logged | `app/services/advances.py` | `tests/test_advances.py::test_queue_decisions_need_a_note`, `tests/test_advances.py::test_ops_approval_pays_out_and_leaves_the_queue` |
| A person can never approve above the rule-based hard cap | same | `tests/test_advances.py::test_ops_can_approve_a_smaller_amount_but_not_above_the_cap` |
| Kill switch pauses all new money; existing advances still settle | `app/sim/session.py`, `app/services/advances.py` | `tests/test_advances.py::test_kill_switch_blocks_new_money_but_keeps_existing_advances` |
| Known facts beat predictions: a defaulted employer is shown at 100% by rule, not by M1 | `app/services/employers.py` | `tests/test_scenarios.py::test_s4_employer_default_raises_risk_pauses_staff_and_shows_exposure` |

## 4. Fair to the borrower

| Control | Where | Guarded by |
|---|---|---|
| Advance + fee never exceed wages already earned, so a payday deduction never goes negative | `app/rules/eligibility.py` | `tests/test_rules.py::test_amount_plus_fee_never_exceeds_earned_wages_or_salary_cap` |
| The fee, the total deducted and the date are shown before accepting | `app/llm/explain.py`, employee view | `tests/test_explain.py::test_template_without_key_states_amount_fee_and_date_in_both_languages` |
| Debt-trap guard: advances in 3 months in a row pause new advances for a month, with a supportive message | `app/rules/eligibility.py` | `tests/test_rules.py::test_chronic_borrower_gets_cooling_off`, `tests/test_scenarios.py::test_s5_chronic_borrower_gets_a_supportive_pause` |
| Employee text never calls the person risky, never shows sensitive reasons, and never contradicts the outcome | `app/llm/explain.py` | `tests/test_explain.py::test_employee_text_hides_sensitive_and_contradictory_reasons`, `tests/test_explain.py::test_risk_points_only_when_the_tier_was_lowered` |
| Every decision carries machine-readable reasons | `app/rules/eligibility.py`, `app/ml/*` | `tests/test_rules.py::test_every_trace_item_is_machine_readable`, `tests/test_m2_repayment.py::test_inference_returns_tier_and_reasons` |
| Fairness audit with a pre-declared threshold; failures are reported, not hidden | `scripts/validate.py` | `tests/test_validation.py::test_every_plan_target_is_checked`, `tests/test_validation.py::test_failed_targets_are_shown_as_failed_and_explanations_are_marked` |

## 5. The LLM

| Control | Where | Guarded by |
|---|---|---|
| The LLM only rewords an allow-listed fact sheet; it never decides | `app/llm/explain.py` (`facts_for`) | `tests/test_explain.py::test_good_llm_reply_is_used_and_request_is_safe` |
| Prompt injection: text in decision fields never reaches the LLM or the output | same | `tests/test_explain.py::test_injected_text_in_decision_fields_never_reaches_the_llm_or_the_output` |
| Output check: any changed number, missing amount, banned word, refusal, error or bad JSON falls back to the template | same (`check`) | `tests/test_explain.py::test_llm_that_changes_a_number_falls_back_to_the_template`, `tests/test_explain.py::test_every_llm_failure_falls_back_to_the_template` |
| Works with no API key (deterministic templates) | same | `tests/test_explain.py::test_template_without_key_states_amount_fee_and_date_in_both_languages` |

## 6. Money and system integrity

| Control | Where | Guarded by |
|---|---|---|
| Double-entry ledger in integer paisa; every entry balances; tampering is detected | `app/ledger/` | `tests/test_ledger.py::test_reconcile_after_1000_random_entries`, `tests/test_ledger.py::test_reconcile_detects_tampering`, `tests/test_ledger.py::test_no_float_money_in_ledger_code` |
| Simulation sessions are isolated; session IDs cannot escape the sessions folder | `app/sim/session.py` | `tests/test_sim.py::test_sessions_are_isolated`, `tests/test_sim.py::test_missing_or_bad_session_id_is_rejected` |
| CORS allow-list from configuration | `app/main.py`, `CORS_ORIGINS` | `tests/test_responsible_ai.py::test_cors_allows_only_configured_origins` |
| Request validation; odd identifiers return 4xx, never a server error | Pydantic schemas, routers | `tests/test_responsible_ai.py::test_offer_rejects_bad_input`, `tests/test_responsible_ai.py::test_odd_identifiers_are_404_not_500` |
| Reported numbers come from code; analyst notes add no unprinted numbers | `scripts/validate.py` | `tests/test_validation.py::test_report_is_generated_and_matches_the_json`, `tests/test_validation.py::test_analyst_notes_contain_no_numbers_except_printed_labels` |

## 7. Known limitations (not solved in this prototype)

- **No authentication or authorisation.** Anyone who can reach the API can act as any role; the roles are separate screens, not permissions. Real use needs sign-in, role-based access, and audit of who did what.
- **Synthetic data only.** The models and results show the method works on its own documented assumptions (`docs/assumptions.md`), not on real upay data.
- **Two validation targets fail** (see `docs/validation_report.md`): the capital forecast interval is too wide on Profile A, and there is an approval gap by employer size on Profile B that is not fully explained (`employer_headcount` is a model feature).
- **M5 is weaker than a simple rule** at finding chronic borrowers; it is used only to send cases to a person.
- **The real LLM path has not been run against the live API** in this project (no key); only the template and a fake client are tested. Any failure falls back to the template.
- **Regulatory status of earned-wage advances in Bangladesh is unknown**; a legal review is needed before any pilot.
- Simulation state lives in temporary SQLite files that are deleted after the session TTL; there is no long-term retention policy because there is no real data.
