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

## Adversarial review — 028a, 2026-09-21

Clean-context review of PR #73. No rubric violation on the production path;
findings fixed test-first in the same PR:

- **MEDIUM** — the read-only sqlite fallback could create a zero-byte file at an
  absent resource path. Connections are now `mode=ro` only and an absent file
  yields no evidence.
- **MEDIUM** — a located but unreadable resource (corrupt file, missing table or
  column, undecodable legacy text, malformed Burns row) aborted the run, and
  `StaticLocator` raised with the absolute path for a missing explicit path.
  The collector now degrades: one marker record `<source>:unreadable:<ErrorType>`
  per token, the failure in `EvidenceCollector.adapter_failures`, no exception
  text forwarded. `StaticLocator` and `build_evidence_policy` treat a missing
  path, a raising locator or a failing digest as absent.
- **MEDIUM (decision)** — corpus parallels served the reviewed rows of other
  tokens in the column under review, which are the evaluation gold on a
  benchmark rerun. Decision: the target column of the target tablet is excluded
  from the parallels index; other columns of the same tablet remain evidence.
  Excluding the whole tablet is the stricter alternative if a benchmark ever
  targets a whole tablet.
- **LOW** fixed — non-contiguous rows for one token now fail closed; priority
  ids outside the column are rejected by the loader; an editorial `#` line that
  is not a KTU marker keeps the line context instead of dropping tokens;
  `legacy-review` is absent when the tablet has no `reviewed/<tablet>.txt`;
  `from_json` raises `ValueError` on hostile payloads; skill scripts execute
  once per process; Burns rows with extra fields and `2-5` line ranges are read.
- **LOW** recorded, not fixed — of the 59 (tablet, column) pairs in
  `reviewed/` with an `auto_parsing/0.2.8` counterpart, 49 load and 10 refuse:
  7-column rows (trailing tab missing) in KTU 1.5 I, 1.5 II, 2.10, 2.11, and
  blank-surface tokens without a sign span in KTU 1.2 I, 1.2 III, 1.4 I,
  1.4 III, 1.4 VII, 2.24. The HARN-027 materializer refuses the same files; the
  linter accepts them. Normalising those rows is a reviewed-data task and a
  precondition for running those columns. Rows with a non-numeric id are
  skipped as every other reviewed-TSV consumer does.

### Fix round 2 — 2026-09-21

- **MEDIUM** — `_connect_ro` built the `file:` URI without percent-encoding, so a
  resource path containing `?`, `#` or `%` opened a truncated path read-write.
  Connections now use `Path.as_uri()`.
- **MEDIUM (design)** — degrading on every adapter exception could turn a
  broken adapter into a "completed" run with zero external evidence and an
  unchanged policy hash. Readiness is now decided once: `EvidenceCollector.build`
  locates and probes every enabled resource (open + schema query) before the
  policy is built, so an unreadable resource is absent in the policy and in its
  hash; the constructor refuses a policy that claims an unreadable resource.
  Per-token failures still degrade to one marker record but are counted per
  source, and three consecutive failures of one source abort the run as a defect.
  Located paths are carried from build to the collector (no re-locate).
- **LOW** — an external locator can no longer claim `locator_kind="repository"`;
  a failed Burns index build is cached; executing skill scripts no longer writes
  bytecode into the skill package (see HARN-030 for the provenance side).
- **Note** — `mode=ro` on a WAL-mode database still creates `-wal`/`-shm`
  sidecars in the resource directory; the real resources use
  `journal_mode=delete`. `immutable=1` was not adopted because the DULAT
  application may write to its cache while a run reads it.

### Delta re-review — 2026-09-21

APPROVE; both round-2 blockers re-probed closed (URI characters, symlinks, WAL;
broken adapter aborts after three tokens; unreadable resources absent in the
hash; build and constructor cannot disagree; real KTU 1.6 I / 1.14 I / 2.12 e2e
with zero failures and no paths). Remaining LOWs closed in the same PR:
intermittent failures abort once one source has failed on more than 25% of the
column (floor of two), and the constructor cross-checks every carried resource
path against the policy digest. Left as noted: the Burns index is built twice
per `build` (perf only) and WAL sidecars on a writable directory.

## Research — 028b, 2026-09-23

### Delta since 028a

028a is merged. HARN-029/030/031/032/033 subsequently added the Jev live
backend, provenance hardening, provider usage/I/O telemetry, and a lexical
stage. 028b must compose those APIs rather than create another provider
runtime or evidence path.

The ten malformed reviewed work units found during 028a are split to #80
(DATA-001). The production loader remains fail-closed; 028b will not pad,
repair, or infer missing scholarly values.

### Pre-completion materialization knot

The graph executes the three completion verifiers before `ColumnCompleted`, but
HARN-027's public `materialize_completed_column` deliberately refuses any state
without `completion`. Therefore `review-status-clean` and the lint-delta gate
cannot honestly inspect the candidate TSV unless the materializer exposes one
canonical pre-completion rendering boundary.

Decision: refactor HARN-027 internally so one pure renderer owns TSV replacement.
Expose `materialize_candidate_column(reviewed_text, state)` for a state that has
completed initial traversal, resolved all revisits, closed reconciliation, and
has structured reviewed rows for every snapshot token. It does *not* require
gate results or `ColumnCompletion`. `materialize_completed_column` remains the
persistence API and wraps the same renderer after its existing completion
checks. There must not be two rendering implementations.

Columnless work units use marker prefix `f"{tablet} "` instead of
`f"{tablet} {column}:"`; the source marker must still match each token's exact
`line_ref`.

### Completion-verifier composition

Create `harness.completion_verifiers` with a host object bound to the
`LoadedColumn` and an ignored local run directory.

- `report-token-count`: latest decisions must cover exactly every snapshot token;
  evidence records exact expected/observed counts.
- `review-status-clean`: render the candidate draft, evaluate the same seeded /
  undocumented-`?` semantics as `review_status.py`, and require the *target work
  unit* to be clean. The script's scanning logic is refactored into a reusable
  text/path helper so CLI and harness share one implementation.
- `lint-error-delta-no-regression`: write baseline and candidate only under the
  ignored local run directory, run the real `agent.linter.lint.lint_file`
  structural/no-DB path on each, and require candidate error count <= baseline
  error count. No external DULAT/UDB resource is required for this gate.
  Provenance records the linter module plus the candidate/baseline SHA-256s,
  never candidate text.

The verifier returns only `CompletionGateResult`; candidate resource text or
reviewed TSV content never enters committed reports.

### Gold-less evaluation

Keep `ParsingEvaluationRecord.target` as one stable class for compatibility.
Extend `EvaluationTarget` with a `kind` (`gold` / `no-gold`) and make
`reviewed_ref` / `reviewed_provenance` optional only for `no-gold`. Existing
payloads default to `gold`.

A no-gold target still names the behaviour evaluator and feedback protocol but
contains no reviewed-data reference. Comparability includes `target.kind`, so
gold-backed and no-gold records are never comparable even if every run identity
field matches. No-gold evaluation contains behaviour + efficiency only; it must
not run the reviewed morphology scorer.

### Output/write boundary

Dry-run output is an ignored local artifact only. Real fork output is a single
HARN-023 `UPDATE_CONTENTS` request for `reviewed/<tablet>.tsv`; the request
payload carries the materialized full file and exact expected source digest/branch
metadata needed by the trusted adapter. The runner itself has no GitHub client,
raw endpoint, or second repository mutation path. `auto_parsing/**` is never a
write target.

The public write helper therefore returns/executes a declared
`GitHubEffectRequest` through an injected `GitHubEffectGateway` + journal. It
does not implement GitHub HTTP.

### CLI boundary

`python -m harness.run_column` is composition only: parse args, load the column,
build evidence policy/collector, select the existing provider binding, invoke
the existing graph, persist state/artifacts under an ignored local run
directory, evaluate, and either emit a dry-run draft or pass the fork-local
write request to a trusted host.

Paid/live execution still requires the existing
`ProviderExecutionPolicy.allow_paid_live_execution`; the CLI must not weaken it.
A test-double execution path is first-class for end-to-end tests.

## Plan — 028b

### Gate B1 — candidate renderer + completion verifiers

RED first:
1. candidate materialization works before `ColumnCompleted` but only after full
   traversal + closed reconciliation + structured decisions;
2. completed and candidate renderers are byte-identical for the same decisions;
3. columnless markers render correctly and a mismatched line marker fails closed;
4. `review-status-clean` fails on a seeded row and an undocumented unresolved
   row, passes on a clean target work unit, and ignores dirt in another column;
5. lint delta fails on a synthetic +1 structural/error issue and passes when
   candidate errors are equal/fewer than baseline;
6. token-count gate fails on missing/latest decision coverage;
7. verifier evidence/provenance contains hashes/counts/command identifiers but
   no candidate TSV text.

Implement only after an exact RED run.

### Gate B2 — explicit no-gold evaluation

RED first:
1. legacy/gold `EvaluationTarget` JSON remains compatible;
2. no-gold target round-trips with no reviewed reference;
3. invalid gold/no-gold field combinations fail closed;
4. otherwise-identical gold/no-gold records compare non-comparable on
   `target.kind`;
5. no-gold evaluation factory emits behaviour/efficiency only and never invokes
   the reviewed morphology scorer.

Implement only after B1 GREEN and exact B2 RED.

### Gate B3 — controlled output + CLI composition

RED first:
1. dry run writes only inside the configured ignored run directory and leaves
   `reviewed/**` / `auto_parsing/**` unchanged;
2. real output creates exactly one declared HARN-023 `UPDATE_CONTENTS` request
   for the target reviewed file; no raw GitHub adapter is reachable from model
   ports;
3. stale reviewed source digest / wrong path / wrong repository / wrong branch
   fails closed before provider mutation;
4. live provider execution without `allow_paid_live_execution` is refused;
5. a test-double provider completes the KTU 1.6 fixture end to end through
   loader → evidence → graph → gates → evaluation → dry-run draft;
6. terminal run metadata contains ids/hashes/metrics only and no resource or
   prompt text.

Implement only after B2 GREEN and exact B3 RED.

## Review rubric — 028b

Reject the PR if any path can:
- materialize a partial/skipped column;
- pass completion gates against text other than the exact candidate that will
  be persisted;
- leak reviewed gold into a no-gold model/evaluation path;
- write `reviewed/**` except through the one HARN-023 `UPDATE_CONTENTS` request,
  or write `auto_parsing/**` at all;
- perform a paid call without the existing explicit execution flag;
- silently repair the malformed reviewed data tracked in #80;
- emit resource/prompt/candidate text into committed metrics or run summaries.

Every review blocker receives a failing regression before its fix.
