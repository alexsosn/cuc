# HARN-027 — Structured curated-row decisions and completed-column draft materializer

## Research

The primary deliverable in `docs/agent-harness/architecture.md` is morphologically parsed CUC, but the completed HARN-004 runtime currently ends at `TokenDecision` state plus HARN-015 evaluation. Its decision contract carries only morphology strings, evidence IDs and a summary; there is no lossless path from a completed graph state to the 8-column curated `reviewed/**` representation.

The real reviewed layout is:

```text
id | surface form | sign span | morphological parsing | DULAT | POS | gloss | comments
```

This is a semantic gap, not only a formatting gap. KTU 1.5 token `158592` contains distinct alternatives that can share a morphology string while differing in root/DULAT/POS/gloss. Therefore the evaluator-facing set of morphology strings cannot be used as the complete curated-row identity.

The review skill also requires each edit to be re-derived from the lexeme and explicitly warns against patching one analysis field while inheriting the remainder. A production bridge must carry the complete proposed curated row.

`seed_reviewed_column_range.py` already owns the pre-review step that inserts missing reviewed rows and immutable TF sign spans. HARN-027 deliberately reuses that boundary: the draft materializer accepts an already-seeded reviewed TSV and preserves token ID, surface and sign span from that file. It does not create sign spans, seed a column, write files, or touch `auto_parsing/**`.

## Contract decision

Introduce a framework-neutral structured row value with:

- `morphological_parsing`
- `dulat`
- `pos`
- `gloss`
- `comments`

Extend `TokenDecision` with optional `reviewed_rows` while preserving existing morphology-only construction and serialized payloads. When `reviewed_rows` is present, its morphology projection must equal `analyses` after first-occurrence deduplication. This preserves HARN-015/HARN-016 morphology-set semantics while allowing multiple lexical alternatives with the same morphology.

Production materialization is stricter than the state contract: every **latest** token decision must carry structured rows.

## Materializer design

Add a pure module `agent/harness/reviewed_materialization.py` with a function shaped like:

```text
materialize_completed_column(reviewed_tsv_text, completed_state) -> str
```

It must:

1. validate `ColumnRunState` completion rather than infer completion from the presence of decisions;
2. require structured rows on every latest decision for the snapshot token set;
3. parse the current 8-column reviewed TSV strictly;
4. identify every source row for each snapshot token and require one contiguous alternative block per token;
5. require source token order to match snapshot order and source surface values to equal the snapshot surfaces;
6. preserve source token ID, surface and sign span from the first row in each block and require those immutable fields to be consistent across pre-existing alternatives;
7. replace the complete block at its first source occurrence with all latest structured alternatives, preserving all non-target source lines exactly;
8. reject structured comments containing the workflow-only seed marker rather than silently publishing it;
9. return deterministic text and perform no filesystem/network/GitHub I/O.

The materializer will not attempt to prove scholarly correctness of DULAT/POS/gloss. Those are adapter/evidence responsibilities; HARN-027 only guarantees that the structured decision is represented faithfully and cannot be mixed with stale source fields.

## TDD gates

### RED 1 — state contract

Tests first for:
- structured row round-trip;
- existing morphology-only decision backward compatibility;
- duplicate morphology across distinct structured rows;
- morphology projection mismatch rejection;
- strict field types and duplicate identical row rejection.

### RED 2 — pure materializer

Tests first for:
- completed 3-token replacement preserving unrelated lines byte-for-byte;
- multiple old alternatives replaced by latest structured alternatives;
- duplicate-morphology lexical alternatives survive;
- source/snapshot surface mismatch and missing/noncontiguous target blocks rejected;
- malformed target 8-column rows rejected;
- incomplete/open/unresolved/no-completion state rejected;
- missing structured output rejected;
- seed marker cannot be emitted;
- repeated calls deterministic and module has no write API.

## Non-goals

- live evidence adapters;
- model/provider invocation;
- filesystem write/commit;
- column seeding or sign-span generation;
- changing morphology scoring semantics.

Those are follow-up production-runner work after HARN-027 establishes a lossless output contract.

## Review rubric

Reject the PR if it can:
- lose two lexical readings merely because they share morphology;
- materialize a partial/failed graph run;
- inherit DULAT/POS/gloss/comment from stale source rows instead of the latest decision;
- alter rows outside the snapshot;
- synthesize or accept mismatched immutable token identity/surface/sign-span fields;
- leak `SEEDED from auto-parse` into curated output;
- mutate `reviewed/**`, `auto_parsing/**`, GitHub, or the network from the pure materializer;
- break existing morphology-only benchmark/evaluation decisions or their JSON round trips.

## Adversarial review — 2026-09-20

Clean-context review of the final diff against issue #70. No rubric blocker
reproduced; two MEDIUM findings were fixed test-first in the same PR, and the
remaining LOW findings are recorded here as explicit scope limits.

### Fixed

- **MEDIUM** — the seed marker was rejected only in `comments`; it could be
  emitted through `dulat`, `pos`, `gloss` or `morphological_parsing`. The
  materializer now checks every structured field.
- **MEDIUM** — `_tsv_field` rejected only `\t`, `\n`, `\r`, but the materializer
  and every reviewed-TSV consumer in `agent/scripts/` split on
  `str.splitlines()`, which also honours U+2028/U+2029, NEL, VT, FF and
  FS/GS/RS. A structured field containing one of those rendered a file that
  re-parsed with a phantom row. The guard now rejects any value for which
  `value.splitlines() != [value]`.
- **LOW** — `TokenDecision.from_dict` raised `TypeError` instead of the
  contract's `ValueError` for `"reviewed_rows": None`.

### Escalated, not fixed here

- **LOW** — `reviewed/KTU 1.5.tsv`, `KTU 2.10.tsv` and `KTU 2.11.tsv` contain
  hand-curated rows with other than 8 columns; the strict parser therefore
  refuses the whole file. Fail-closed is the intended behaviour; the data
  repair belongs to a reviewed-data ticket, not the materializer.
- **LOW** — columnless tablets (`# KTU 2.10 1`, no roman column) are not
  addressable because the line-marker prefix is `"{tablet} {column}:"`. The
  production runner (HARN-028) must define the columnless work unit.
- **LOW** — mixed line endings inside one target block are normalised to the
  first source row's ending; lines outside the block are byte-preserved.
