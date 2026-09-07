# HARN-003 — Representative reviewed-morphology regression fixture

## Goal

Create a deliberately small, fast fixture from existing reviewed morphology that can be scored by the authoritative reviewed-morphology evaluator in every harness experiment/trace.

The fixture must preserve difficult behavior rather than cherry-pick only exact matches. It must cover an unambiguous token, genuine reviewed ambiguity, explicit `DULAT: NOT FOUND`, an ordered linguistic heuristic, and overgeneration while keeping original token IDs and enough source context to identify the relevant formula/line.

HARN-003 is only a fast deterministic evaluator fixture. It is **not** the later whole-column agent benchmark: HARN-015/HARN-016 define held-out expert/model-comparison protocols, and the HARN-014 architecture keeps complete-column/every-token parsing semantics separate from this small scorer regression set.

## Existing evaluator boundary

`agent/scripts/score_reviewed_morphology.py` is the authoritative CLI. It resolves reviewed/auto files with `EvaluationTargetResolver`, loads TSVs with `MorphologyTsvLoader`, scores them with `MorphologyAgreementScorer`, and can emit aggregate JSON with `--json`.

HARN-003 therefore adds fixture data and tests only. It does not add a scorer, metric implementation, or wrapper that recomputes agreement.

## Source selection

Use two small source fragments:

1. `KTU 2.10` supplies the difficult evaluation cases in one compact letter.
2. `KTU 1.6 I:12` supplies a literal active ordered formula-context rule, `aliyn bˤl`, so the heuristic requirement is demonstrated by current pipeline code rather than inferred from a review comment.

Seven original token IDs are retained:

| ID | Source | Role | Selection rationale |
| --- | --- | --- | --- |
| `176080` | KTU 2.10:1 | unambiguous baseline | Reviewed and auto both have the single `tḥm/` analysis. |
| `176084` | KTU 2.10:3 | overgeneration | Reviewed keeps imperative `!!rgm[`; auto has four source rows collapsing to three distinct morphology options for the set-based scorer, none equal to the reviewed imperative encoding. |
| `176085` | KTU 2.10:4 | `DULAT: NOT FOUND` | Auto is explicit unresolved `?` / `DULAT: NOT FOUND`; reviewed supplies the contextual `yšlm lk` analysis. |
| `176096` | KTU 2.10:9 | genuine ambiguity | Reviewed intentionally preserves two analyses (`in/~m~m` and `in m(nm`) from competing analyses. |
| `176102` | KTU 2.10:11 | independent lexical overgeneration | Reviewed keeps `yd(I)/`; auto offers `yd(I)/` and `yd(II)/`. |
| `159322` | KTU 1.6 I:12 | ordered formula context | First token of the active `aliyn bˤl` formula bigram. |
| `159323` | KTU 1.6 I:12 | ordered formula context | Second token of the active `aliyn bˤl` formula bigram, normalized to Baʿlu. |

The KTU 2.10 selections deliberately include mismatches and ambiguity; the fixture is not intended to be all-GREEN morphology data. Its purpose is a stable regression signal whose aggregate should change when candidate generation/disambiguation or scorer semantics change.

## Ordered-heuristic evidence

The active tablet pipeline constructs `build_spacy_formula_context_steps()` near the start of its ordered refinement sequence. That factory returns `SpacyFormulaContextDisambiguator`. The component applies trigram rules before bigram rules, and `FORMULA_BIGRAM_RULES` contains the literal `aliyn` + `bˤl` rule with canonical targets.

The fixture therefore preserves both tokens and the `# KTU 1.6 I:12` structural row. A single isolated `bˤl` token would not be sufficient evidence for this requirement because the formula rule needs its neighboring surface.

## Fixture layout

```text
agent/tests/fixtures/harn_003_reviewed_morphology/
├── manifest.json
├── reviewed/
│   ├── KTU 1.6.tsv
│   └── KTU 2.10.tsv
└── auto/
    ├── KTU 1.6.tsv
    └── KTU 2.10.tsv
```

Each TSV is a minimal source-derived fragment. It retains the original schema for its source side, original IDs and analyses, and structural `# KTU ...` rows needed for context/reference resolution. It does not modify `reviewed/**` or `auto_parsing/**`.

`manifest.json` records source paths, the Git blob IDs of the exact source files from which the fragments were checked, selected IDs/coverage roles, and the expected aggregate summary from the authoritative scorer. It is metadata for tests/harness consumers, not scoring input.

## Stable scorer baseline

The checked-in fixture pins the complete `MetricSummary` schema and authoritative aggregate:

- compared IDs: `7`;
- reviewed options: `8`;
- automatic options: `11`;
- true-positive options: `4`;
- exact-set accuracy: `3/7`;
- micro precision: `4/11`;
- micro recall: `4/8`;
- micro F1: `8/19`;
- gold coverage: `4/7`;
- mean extra options: `1`;
- mean missing options: `4/7`;
- mean option-count error: `3/7`.

The JSON manifest stores the exact floating-point values emitted by the current authoritative implementation. This is intentionally a regression baseline: a legitimate scorer/fixture semantics change should require an explicit reviewed baseline update rather than silently passing.

## TDD history

1. Fixture-contract tests were committed before fixture data existed.
2. Initial RED was an ordinary test failure: four fixture-absence failures with the rest of the suite green.
3. Source-derived fixture fragments and manifest were added; the first implementation run exposed a test-side mismatch with the existing scorer JSON schema (`files`, not the invented `file_results`). The scorer was not modified.
4. Full suite then reached GREEN.
5. Independent adversarial review rejected that GREEN because the fixture did not yet pin the expected metric schema/aggregate required by #4.
6. A reviewer-driven test was committed first; it produced exactly one RED (`manifest.expected_summary` absent).
7. The manifest baseline was then added without changing scorer logic, restoring full GREEN.
8. Final review must re-check representativeness, source fidelity/provenance, ambiguity preservation, scorer bypass, payload size/determinism, generated-data safety, and the pinned metric contract on the exact final revision.

## Acceptance interpretation

- **Checked into `agent/tests/fixtures/`:** all fixture data lives below the fixture tree above.
- **Representative selection rationale:** this document and the manifest state the source/role for each retained ID.
- **Existing scorer can run:** tests invoke the existing CLI end-to-end and parse its JSON output.
- **Stable gate contract:** tests require the complete metric-field set and exact manifest `expected_summary`.
- **Small aggregate:** seven reviewed IDs across two tiny files is the target; tests enforce a conservative serialized JSON size ceiling so the payload remains suitable for every experiment/trace.
- **Source provenance:** manifest records exact source paths and Git blob IDs; source corpus/generated files are not modified by this ticket.

## Deferred

- changing the reviewed scorer or metric definitions;
- regenerating auto morphology;
- changing ordered heuristic behavior;
- turning the fixture into a broad benchmark dataset;
- whole-column model benchmarking and expert-feedback protocol (HARN-015/HARN-016);
- Langfuse dataset registration/trace attachment, which consumes this fixture in later harness/runtime work.
