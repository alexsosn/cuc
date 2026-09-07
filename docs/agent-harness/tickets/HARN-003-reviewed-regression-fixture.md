# HARN-003 — Representative reviewed-morphology regression fixture

## Goal

Create a deliberately small, fast fixture from existing reviewed morphology that can be scored by the authoritative reviewed-morphology evaluator in every harness experiment/trace.

The fixture must preserve difficult behavior rather than cherry-pick only exact matches. It must cover an unambiguous token, genuine reviewed ambiguity, explicit `DULAT: NOT FOUND`, an ordered linguistic heuristic, and overgeneration while keeping original token IDs and enough source context to identify the relevant formula/line.

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
| `176084` | KTU 2.10:3 | overgeneration | Reviewed keeps imperative `!!rgm[`; auto retains four analyses (suffix conjugation, imperative, infinitive, noun). |
| `176085` | KTU 2.10:4 | `DULAT: NOT FOUND` | Auto is explicit unresolved `?` / `DULAT: NOT FOUND`; reviewed supplies the contextual `yšlm lk` analysis. |
| `176096` | KTU 2.10:9 | genuine ambiguity | Reviewed intentionally preserves two analyses (`in/~m~m` and `in m(nm`) from competing analyses. |
| `176102` | KTU 2.10:11 | independent lexical overgeneration | Reviewed keeps `yd(I)/`; auto offers `yd(I)/` and `yd(II)/`. |
| `159322` | KTU 1.6 I:12 | ordered formula context | First token of the active `aliyn bˤl` formula bigram. |
| `159323` | KTU 1.6 I:12 | ordered formula context | Second token of the active `aliyn bˤl` formula bigram, normalized to Baʿlu. |

The KTU 2.10 selections deliberately include mismatches and ambiguity; the fixture is not intended to be all-GREEN morphology data. Its purpose is a stable regression signal whose aggregate should change when candidate generation/disambiguation changes.

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

`manifest.json` records source paths, selected IDs, and coverage roles. It is metadata for tests/harness consumers, not scoring input.

## TDD plan

1. Add a fixture-contract test while the fixture directory does not yet exist.
2. RED must be an ordinary test failure, not collection/import failure.
3. The test will require the exact seven-ID selection and required coverage labels in the manifest.
4. The test will load the fixture through the existing `MorphologyTsvLoader` to verify ambiguity and overgeneration multiplicity are preserved.
5. The test will invoke the authoritative `scripts/score_reviewed_morphology.py` CLI with fixture reviewed/auto directories and `--json`; no scorer mock or duplicate metric code is allowed.
6. The aggregate JSON must contain exactly two file results / seven reviewed token IDs and remain small enough to attach to experiment/trace evidence.
7. Run the complete `agent/tests` suite on the fork-local PR.
8. Perform a logically independent adversarial review focused on representativeness, accidental source drift, context loss, ambiguity collapse, scorer bypass, absolute-path coupling, and edits to generated `auto_parsing/**` data.

## Acceptance interpretation

- **Checked into `agent/tests/fixtures/`:** all fixture data lives below the fixture tree above.
- **Representative selection rationale:** this document and the manifest state the source/role for each retained ID.
- **Existing scorer can run:** tests invoke the existing CLI end-to-end and parse its JSON output.
- **Small aggregate:** seven reviewed IDs across two tiny files is the target; tests enforce a conservative serialized JSON size ceiling so the payload remains suitable for every experiment/trace.

## Deferred

- changing the reviewed scorer or metric definitions;
- regenerating auto morphology;
- changing ordered heuristic behavior;
- turning the fixture into a broad benchmark dataset;
- Langfuse dataset registration/trace attachment, which consumes this fixture in later harness/runtime work.
