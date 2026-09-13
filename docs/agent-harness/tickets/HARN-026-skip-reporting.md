# HARN-026 — Surface skipped-test reasons in authoritative CI

## Research

Issue #68 is the research source for this ticket. The current authoritative workflow runs the full agent suite as:

```text
.venv/bin/python -m pytest -q
```

and therefore reports the persistent skip count without the skip reason. Pytest's short test summary reporting can expose skipped tests with `-rs`; this changes reporting only, not test selection or skip semantics.

The current `langfuse-sdk-smoke` job is intentionally a separate focused test selection and must not inherit the new reporting option. HARN-025 also established that the full job is the sole shared uv cache writer while smoke restores but does not save; HARN-026 must not disturb that contract.

## Plan

1. Keep the existing RED contract test `agent/tests/test_ci_skip_reporting_contract.py` as the TDD gate.
2. Prove the branch fails only because the full-suite command lacks `-rs`.
3. Change only the full-suite pytest command to `.venv/bin/python -m pytest -q -rs`.
4. Require exact-head full CI to preserve normal pass/skip/subtest semantics and expose a human-readable `SKIPPED ... reason` line.
5. Classify the persistent skip from the final CI log; do not change the skip condition in this ticket.
6. Run a fresh logically independent adversarial review focused on test-selection drift, smoke-path drift, hidden skip-condition changes, and unrelated workflow/cache changes.

## Acceptance

- authoritative full-suite logs show the reason for every skipped test;
- no test is forced, unskipped, newly selected, or deselected by the workflow change;
- the smoke pytest command remains unchanged;
- no skip marker/condition is edited;
- HARN-025 cache ownership remains unchanged;
- no writes or notifications to `DT-UCPH/cuc`.
