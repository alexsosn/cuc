# HARN-028 — Production column runner

Issue: alexsosn/cuc#72. Delivered as two fork-local PRs:

- **028a** (this document's first half): column loader, evidence policy, optional
  local evidence adapters.
- **028b**: completion gates against the real scripts, gold-less evaluation,
  write path, CLI, first dry run.

## Research — 2026-09-20

### What the graph needs and what exists

`compile_column_review_graph` takes six adapters. Outside `agent/tests/`, only
`adjudicate`/`reconcile` have a production implementation (`live_providers`).
`grep ColumnSnapshot(` outside tests: zero hits. The graph also requires at
least one `EvidenceRecord` per token visit (`_record_evidence`), so a run with
every external resource disabled still needs a repository-internal evidence
source. The current automatic parse is that source and is always enabled.

### Conventions fixed by HARN-027

`ColumnTask.tablet` is `"KTU 1.6"`, `ColumnTask.column` is `"I"`,
`ColumnToken.line_ref` is `"I:2"`; the materializer requires the source line
marker `# KTU 1.6 I:2` to equal `f"{task.tablet} {token.line_ref}"`. The loader
adopts these exactly. Columnless tablets (`# KTU 2.10 4`) are not addressable by
the materializer; the loader represents them with column `"-"` and line_ref
`"4"`, and 028b extends the materializer prefix for that case.

### Data shapes

- `auto_parsing/<tf>/KTU <n>.tsv`: 7 columns (`id, surface form, morphological
  parsing, DULAT, POS, gloss, comments`), `# KTU <n> <col>:<line>` markers,
  several rows per token for alternatives. KTU 1.6: 1028 tokens, 236 with more
  than one row.
- `reviewed/KTU <n>.tsv`: 8 columns (adds `sign span`); same markers; must be
  seeded for the target column by `seed_reviewed_column_range.py` before a run.
- KTU 1.6 column I token sets are identical in both files (299 tokens).

### Evidence resources (optional, local-only)

Located by `.agents/skills/review-automatic-parsing/scripts/sources.py`:
explicit path → `CUC_*` env var → sibling checkout. Never committed.

| source_id | resource | lookup |
|---|---|---|
| `auto-parsing` | repository | rows for the token in the pinned auto file |
| `dulat` | `dulat_search.sqlite` `dulat_reverse_refs(norm_ref, entry_id, payload)` | `norm_ref = "KTU 1.6 I:2"`; payload JSON has `label`, `sense_labels`, `reference_translations` |
| `eupt` | `modules_cache.sqlite` `module_records(module_id, ref_norm, content_text)` | `module_id in (EUPT_vocalisation, EUPT_translation, EUPT_commentary)`, `ref_norm = "KTU 1.6 I:2"` (columnless texts are normalised as column I) |
| `tropper` | `<ocr>/…ocr.index.sqlite` `ktu(tablet, column, line, pages, verified, …)` | pages that cite the line; pointer evidence only |
| `legacy-review` | `reviewed/KTU <n>.txt` (+ `reviewed/orig/`) | `legacy_align.load` aligned by `(column, line)` then surface |
| `corpus-parallels` | `reviewed/*.tsv` in the repository | other reviewed tokens with the same normalised surface |
| `burns-cultic-vocabulary` | `context_labeling/**/*.csv` | rows whose `ktu`/`references` cite the line |
| `published-translations` | modules cache, whole-tablet documents | **skipped this slice** (no line alignment; decided 2026-09-20) |

`sources.py` `locate()` raises `SystemExit` when an explicit/env path does not
exist; the adapter layer converts that into a recorded absence, never a crash.

### Ablations

An ablation arm is an `EvidencePolicy`: the ordered set of enabled source ids
plus, for each enabled resource that was actually found, a digest of the
resource file (so two runs on different DULAT snapshots are not comparable).
`BenchmarkCase.evidence_policy_sha256` and `ParsingWorkloadRef` already key
comparability on that hash. The resolved availability (enabled / found /
absent) is part of the skill context forwarded to the model so the model knows
which sources were unavailable rather than silently empty.

### Leakage

`EvidenceRecord.summary` carries resource text and lives in `ColumnRunState`.
That state is persisted only under ignored local directories; nothing in this
slice writes it anywhere else. The policy hash and availability record contain
source ids, digests and locator kinds (`explicit`/`env`/`sibling`/`absent`),
never paths or text.

## Plan — 028a

### `harness.evidence_policy`

- `EvidenceSourceId` constants and `ALL_SOURCE_IDS`.
- `ResourceAvailability(source_id, enabled, available, locator_kind,
  resource_sha256 | None)`.
- `EvidencePolicy(enabled_sources, availability)`: enabled sources must be
  known ids and contain `auto-parsing`; `sha256` over the canonical JSON of
  enabled ids and (source_id, resource_sha256) pairs; `to_dict`/`from_dict`.
- `build_evidence_policy(requested, locator)` resolves availability.

### `harness.column_loader`

- `AutomaticRow(morphological_parsing, dulat, pos, gloss, comments)`.
- `LoadedColumn(task, snapshot, automatic_rows: Mapping[token_id,
  tuple[AutomaticRow, ...]], reviewed_text, auto_sha256, reviewed_sha256)`.
- `load_column(repo_root, *, tablet, column, tf_version, repository_revision,
  task_id, evidence_priority_token_ids=())`:
  1. read the pinned auto file and the reviewed file; strict header checks;
  2. collect the target column's tokens in order from both files;
  3. require identical token id sequence and identical surfaces; require every
     reviewed row of the column to have 8 columns and a non-empty sign span
     block-consistent (the materializer will re-check);
  4. bind the task through `build_column_task_from_capability` so gates come
     from the manifest;
  5. `snapshot_id = sha256(tf_version, tablet, column, token ids)`,
     `source_ref = "auto_parsing/<tf>/KTU <n>.tsv#<col>"`,
     `source_provenance = json({auto_sha256, reviewed_sha256, tf_version})`.

### `harness.evidence_adapters`

- `TokenEvidenceContext(loaded_column, state, token, operation_id)`.
- `EvidenceAdapter` protocol: `source_id`, `resource_kind | None`,
  `collect(ctx, resource_path) -> tuple[EvidenceRecord, ...]`.
- One adapter per source in the table above (translations excluded).
- `SkillScriptLocator` wraps `sources.py`'s `locate(kind, required=False)` and
  maps `SystemExit` to `None`; a `StaticLocator(mapping)` exists for tests.
- `EvidenceCollector(policy, adapters, loaded_column, locator)` exposes
  `initialize_skill_context(state, operation_id)` and
  `collect_evidence(state, token, skill_context, operation_id)`; evidence ids
  are `f"{operation_id}:{source_id}:{n}"`; disabled or absent sources
  contribute nothing and are listed in the skill context as such.

### Manifest

`required_evidence` → `available_evidence` in the manifest schema and the
`review-automatic-parsing` manifest; `SKILL.md` evidence section states the
sources are optional. HARN-008 tests updated in the same commit.

## TDD gates — 028a (RED first)

1. loader: token order/surfaces equal across files; a token missing from either
   file, a surface mismatch, a missing column, a wrong TF version, a malformed
   reviewed row, a missing sign span all fail closed with the offending id;
2. loader: task binds the canonical capability and manifest gates; snapshot
   provenance carries both digests; real `KTU 1.6` column I loads 299 tokens;
3. policy: `auto-parsing` cannot be disabled; unknown ids rejected; two policies
   that differ only in one enabled source or one resource digest hash
   differently; JSON round trip;
4. availability: an enabled source whose resource is absent is recorded
   `available=False`, `locator_kind="absent"`, and the collector returns no
   record for it and does not raise;
5. each adapter against a fixture resource with the real schema returns the
   expected records, and returns nothing for a line with no rows;
6. collector: every token visit returns at least the `auto-parsing` record;
   evidence ids unique across two visits of the same token; skill context is
   JSON-serialisable and lists enabled/available/absent sources;
7. leakage: a sentinel string placed in a fixture resource appears only in
   `EvidenceRecord.summary`, never in the policy hash input, the availability
   record, or the skill context.

## Non-goals — 028a

Completion gates, evaluation, materialisation, writes, CLI, provider calls.

## Review rubric — 028a

Reject if the slice can: narrow token scope from a worklist; load a column
whose reviewed and automatic token sequences differ; produce equal policy
hashes for different enabled sets or resource versions; crash on an absent
resource; put a resource path or resource text anywhere except
`EvidenceRecord.summary`/`source_ref`; return zero evidence for a token.
