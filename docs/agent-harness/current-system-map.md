# HARN-001 — Current CUC agent/parsing system map

Snapshot basis: `alexsosn/cuc` branch `harn-001-system-inventory`, descended from the reviewed CUC line plus the fork-safety/HARN-000 documentation.

This document inventories the current agent-facing system before LangGraph/Langfuse integration. It deliberately separates linguistic/domain behavior from orchestration and repository side effects. The harness should wrap these capabilities, not rewrite the parser into LLM calls.

## 1. Architectural boundary

The current system is already layered, but the boundaries are imperfect:

```text
external / local evidence
  DULAT, UDB, modules DB, Notarius, TF releases, EUPT/legacy evidence
                         |
                         v
agent domain packages ----------------------+
  pipeline/                                 |
  morph_features/                           |
  linter/                                   |
  reviewed_evaluation/                      |
  reviewed_migration/                       |
  spacy_ugaritic/                           |
  text_fabric/                              |
                         |                   |
                         v                   |
agent/scripts/ CLI + operational adapters   |
                         |                   |
                         v                   |
filesystem/repository artifacts             |
  generated_sources -> auto_parsing         |
  reports              reviewed             |
                         |                   |
                         v                   |
.agents/skills = expert workflows that compose scripts, evidence, tests,
and human/editorial judgment
```

The future harness boundary should sit **above** the deterministic/domain packages and **below** the research/review orchestration. LangGraph should coordinate typed capabilities; it should not replace the parser steps, linter, scorer, alignment logic, or Text-Fabric conversion logic.

## 2. Existing deterministic/domain packages

| Surface | Current role | Side effects / constraints | Harness seam |
| --- | --- | --- | --- |
| `agent/pipeline/steps/**` | Ordered linguistic and structural refinements | `RefinementStep.refine_file()` rewrites a TSV in place; many individual `refine_row()` implementations are deterministic given their configured evidence | Prefer row/structured-data adapters; keep file writes outside graph reasoning nodes |
| `agent/pipeline/tablet_parsing.py` | End-to-end bootstrap/refine/ordered-step/report orchestration | Creates output directories; writes `auto_parsing`; invokes report generation; opens DULAT/UDB indexes | Treat as a coarse execution capability, not domain state itself |
| `agent/pipeline/instruction_refiner.py` | Conservative normalization | File mutation through refiner APIs | Adapter candidate |
| `agent/pipeline/dulat_*` | DULAT attestation, translation and provenance indexes | Reads SQLite/evidence; deterministic for fixed DB | Read-only capability / injectable evidence service |
| `agent/pipeline/*_context_step_factory.py` | Builds spaCy-backed context steps | Model/runtime-dependent but locally deterministic for fixed package/model/config | Factory remains domain layer |
| `agent/pipeline/config/**` | Lexical/rule allowlists, overrides and ordered configuration | Code/config changes alter generated corpus | Version/hash as run provenance |
| `agent/morph_features/**` | Decode/complete/render morphology feature bundles and paradigms | Primarily in-memory deterministic logic | Strong library seam; no framework coupling |
| `agent/linter/feature_validation.py` | Feature validation | Read/check | Deterministic evaluator capability |
| `agent/linter/morphology.py` | Morphological lint logic | Read/check | Deterministic evaluator capability |
| `agent/linter/lint.py` | Large linter CLI/runtime | Reads TSV + DULAT/UDB; emits diagnostics/exit status | Wrap result model rather than parsing prose where possible |
| `agent/reviewed_evaluation/{loader,models,scorer}.py` | Reviewed-vs-auto morphology evaluation | Read-only; exact-set/precision/recall/F1/Jaccard/coverage metrics | **Primary deterministic regression/eval seam** for HARN-003/Langfuse |
| `agent/reviewed_migration/migrator.py` | Align/migrate curated reviewed data across TF tokenization changes | Produces migrated reviewed representation; final publication is a curated write | Require preview + human/review gate before write |
| `agent/full_regeneration/{runner,reports}.py` | Regenerate parser output and materialize lint/scoring deltas | Broad generated-data/report writes | High-level mutation capability; never an LLM node internals |
| `agent/lint_reports/**` | Parse, compare, chart and materialize lint reports | Report writes; baseline provenance matters | Evaluator/report adapter |
| `agent/spacy_ugaritic/**` | Ugaritic spaCy document/context components | Runtime/model dependency | Keep below harness |
| `agent/text_fabric/**` | Export canonical TF tablet sources and editorial lookup support | Generated-source writes; reads TF releases | Explicit source-materialization capability |
| `agent/project_paths.py` | Resolves repo/agent roots, TF versions, local caches and generated/report paths | Environment-sensitive path selection (`CUC_*` vars); current code assumes agent package layout | Inject resolved paths/config into capabilities; do not let graph guess paths |
| `agent/dulat_patches.py` | Curated DULAT corrections/supplements | Changes evidence semantics | Version/hash as provenance |
| `agent/reviewed_normalization.py` | Reviewed-data normalization support | Curated-data semantics | Domain helper, not orchestration |

### Important impurity inside the current boundary

`TabletParsingPipeline` imports `scripts.bootstrap_tablet_labeling` and `scripts.refine_results_mentions` as library modules. Therefore `agent/scripts/` is not purely a CLI layer today. Harness work must not assume "anything under scripts is shell-only". The immediate adapter strategy should reuse their callable functions as-is; extraction into cleaner library modules is optional refactoring after tests prove behavior.

Likewise, `RefinementStep` cleanly exposes `refine_row(row) -> row`, but its default `refine_file(path)` reads and overwrites the file. This is a useful natural split between domain transformation and filesystem side effect.

## 3. Agent skill inventory — 11/11 accounted for

`.agents/skills` currently contains 11 top-level skills:

| Skill | Role | Default effect class | Harness interpretation |
| --- | --- | --- | --- |
| `audit-onomastic-encoding` | Corpus-wide onomastic audit using DULAT/EUPT and bundled scripts | read/diagnose unless a separate fix is requested | expert research capability |
| `audit-split-token-migrations` | Validate split/join migration semantics | read/diagnose | evaluator/reviewer capability |
| `audit-ugaritic-analysis-reconstruction` | Check morphology reconstruction against surface/editorial semantics | read/diagnose | deterministic/knowledge-assisted evaluator |
| `migrate-reviewed-text-fabric` | Migrate hand-reviewed TSVs to a new TF tokenization | preview first; eventual curated `reviewed/**` write | high-risk curated mutation with explicit gate |
| `parse-ugaritic-feminine-endings` | Phenomenon-specific linguistic audit/repair workflow | research first; may lead to parser/reviewed changes | specialist research capability |
| `parse-ugaritic-gt-stems` | Gt-stem audit/repair workflow | research first; may lead to parser/reviewed changes | specialist research capability |
| `parse-ugaritic-n-stems` | N-stem audit/repair workflow | research first; may lead to parser/reviewed changes | specialist research capability |
| `regenerate-automatic-parsing` | Rebuild generated parser output and reports with staging/safeguards | broad generated-data mutation | explicit execution capability; never hand-edit output |
| `review-automatic-parsing` | Token-by-token scholarly adjudication into gold `reviewed/**` | curated human/editorial write | human/research workflow; not autonomous parser node |
| `review-linguistic-rule-change` | Turn expert feedback into measured rule/exception/test changes | research, then code/config/data mutation as justified | ideal research→plan→TDD workflow template |
| `triage-morphology-lint-regressions` | Stable baseline-vs-candidate ERROR comparison | read/diagnose by default | deterministic gate + investigation workflow |

### Skill structure already worth preserving

The skills are not merely prompts. Across the inventory they combine:

- `SKILL.md` workflow/decision procedure;
- `references/**` domain constraints/evidence interpretation;
- `scripts/**` executable audits/helpers;
- for several skills, `agents/openai.yaml` agent metadata.

HARN-008 should add only the machine-readable metadata genuinely needed for discovery/version/permissions. It should not copy the domain knowledge into LangGraph/LangChain-specific prompt classes.

## 4. `agent/scripts` inventory — 29/29 accounted for

Classification is by current operational responsibility. "Mutation" means the script can write an artifact; it does not imply that every invocation writes.

| Script | Responsibility | Effect class / harness note |
| --- | --- | --- |
| `annotate_dulat_source_provenance.py` | Add/refresh DULAT provenance annotations | generated/output mutation; adapter only |
| `bootstrap_tablet_labeling.py` | First-pass DULAT candidate labeling | shared library **and** CLI/output writer; imported by pipeline |
| `build_1_3_from_tania.py` | Historical/legacy reviewed-data construction | one-off migration utility; do not make generic harness primitive |
| `build_dulat_attestation_index.py` | Build DULAT attestation index artifact | evidence/index materialization |
| `build_reviewed_from_auto.py` | Build/seed reviewed representation from automatic output | curated-data bootstrap mutation; requires explicit review semantics |
| `compare_eupt.py` | Compare CUC analyses with EUPT evidence | read-only research/evaluation |
| `compare_lint_errors.py` | Stable lint ERROR multiset comparison | deterministic gate; exit status is machine-friendly |
| `discover_formula_bigrams.py` | Discover recurrent formula bigrams | read-only corpus research |
| `discover_formula_trigrams.py` | Discover recurrent formula trigrams | read-only corpus research |
| `dulat_lookup.py` | Query DULAT evidence | read-only evidence tool |
| `emit_1_4_col8.py` | Historical KTU 1.4 column transformation | one-off data utility; not generic harness primitive |
| `export_text_fabric_tablet_sources.py` | Export canonical TF release to generated tablet TSV sources | generated-source materialization |
| `extract_notarius_evidence.py` | Extract/index Notarius evidence | external-evidence materialization |
| `fix_col4_1_5_1_6.py` | Historical targeted correction utility | one-off mutation; should not be exposed as autonomous generic fix |
| `fix_reconstruct_1_5_1_6.py` | Historical targeted reconstruction correction | one-off mutation |
| `generate_lint_reports.py` | Generate lint report artifacts | report mutation/evaluator wrapper |
| `install_git_hooks.sh` | Install tracked Git hooks | repository configuration side effect; human/setup action |
| `migrate_reviewed_tablet.py` | CLI wrapper for reviewed TF migration | preview/output mutation; final reviewed write must be gated |
| `notarius_refinement_pass.py` | Apply Notarius-informed refinement pass | evidence-driven mutation/research utility |
| `parse_lint_reports.py` | Parse committed lint reports for summaries/UI | read/report adapter |
| `reconcile_1_4.py` | Historical KTU 1.4 reconciliation | one-off data mutation |
| `refine_results_mentions.py` | Contextual DULAT/corpus candidate refinement | shared library **and** CLI; imported by pipeline; major adapter seam |
| `regenerate_tablets_and_reports.py` | Full regeneration + lint/scoring report wrapper | broad generated-data/report mutation |
| `run_tablet_parsing_pipeline.py` | Main tablet parsing CLI | broad generated-data mutation; coarse capability |
| `score_reviewed_morphology.py` | Reviewed-vs-auto agreement CLI | read-only deterministic evaluator; JSON output available |
| `seed_reviewed_column_range.py` | Seed a bounded reviewed column from auto output | curated-data worklist mutation; not equivalent to review |
| `spacy_l_context_spike.py` | Research spike for spaCy `l` context | experimental/read/research; not production harness primitive |
| `token_ref_index.py` | Build/query token/reference mapping support | index/read helper; provenance-sensitive |
| `x_broken_server_report.py` | Diagnose/report historical server/data breakage | diagnostic/report utility |

### Script grouping for the first harness

The first vertical slice should expose only a small allowlist:

**Read/evaluate:**
- reviewed evaluator (`reviewed_evaluation` directly or `score_reviewed_morphology.py --json`);
- lint regression comparator;
- selected DULAT/corpus lookup helpers.

**Execute deterministic parser:**
- `TabletParsingPipeline` through a controlled staging-directory adapter.

**Do not initially expose:**
- historical one-off `fix_*`, `build_1_3_*`, `emit_*`, `reconcile_*` scripts;
- direct `reviewed/**` writers;
- git-hook installer;
- unrestricted filesystem or GitHub mutation.

## 5. Data and artifact authority

| Path/surface | Authority | Mutation policy |
| --- | --- | --- |
| `tf/<version>/**` | canonical Text-Fabric release inputs | source data; parser harness reads/materializes from it |
| `agent/generated_sources/cuc_tablets_tsv/<version>/**` | generated raw TSV projection of TF | regenerate; never curate manually |
| `auto_parsing/<version>/**` | generated automatic parsing | **never hand-edit**; generator/config/code + test + regeneration only |
| `reviewed/**` | curated scholarly gold/editorial data | direct edits are allowed only as explicit review/migration work; never overwritten by regeneration |
| `agent/reports/**` / configured report directory | generated lint/evaluation artifacts | regenerate from authoritative input; provenance matters |
| `agent/data_sources/**` | tracked supporting evidence/artifacts | source-specific provenance required |
| `agent/local_sources/**` | local caches (DULAT/UDB/modules/Notarius) | environment input; do not commit secrets/proprietary/unintended caches |
| `lexicon_and_grammar/**` | linguistic resources/conventions | domain evidence/config |
| `morphemes_files/**` | legacy/historical annotation/evidence | comparison/migration input, not current generated authority |

`ProjectPaths` also permits `CUC_DULAT_DB`, `CUC_UDB_DB`, `CUC_MODULES_DB`, `CUC_NOTARIUS_*`, `CUC_SOURCE_DIR`, `CUC_OUTPUT_DIR`, and `CUC_REPORTS_DIR`. A harness run therefore needs an explicit resolved configuration snapshot; merely recording the command is insufficient provenance.

## 6. Current orchestration and side-effect map

### Tablet parse/regeneration

`run_tablet_parsing_pipeline.py` resolves paths, optionally refreshes generated TF sources, constructs `PipelineConfig`, and invokes `TabletParsingPipeline.run()`.

`TabletParsingPipeline.run()` then:

1. discovers/partitions target files;
2. creates the output directory;
3. bootstraps DULAT analyses into output TSVs;
4. refines candidates using DULAT/UDB/context evidence;
5. runs instruction normalization;
6. executes the ordered `RefinementStep` chain in-place;
7. enforces per-step change-ratio safeguards;
8. regenerates lint reports.

This means one apparent "parser call" crosses several side-effect boundaries. The harness should model the operation as an explicit staged execution with input/output artifact IDs rather than pretending it is a pure graph node.

### Full regeneration

`FullRegenerationRunner` composes TF materialization, `TabletParsingPipeline`, lint generation, reviewed scoring, and delta-report writes. It is already an orchestration layer. LangGraph should invoke it only when the task explicitly calls for a broad regeneration; it should not reimplement this sequence node-by-node without a demonstrated need.

### Reviewed data

`review-automatic-parsing` explicitly defines reviewed data as gold created by token-by-token scholarly review; seeding is only a worklist. `migrate-reviewed-text-fabric` likewise requires a preview outside `reviewed/**`, alignment review, lint/tests, then an intentional final copy. These policies are stronger than generic filesystem permissions and must survive as capability-specific gates.

### Git/repository

- `.githooks/pre-commit` activates only for staged `auto_parsing/**/*.tsv`; it lints staged vs `HEAD` and blocks only newly introduced ERROR occurrences.
- The hook depends on `agent/.venv/bin/python`, local DULAT cache, git index/HEAD, and a worktree-aware lookup.
- `.github/workflows/python-app.yml` is currently a legacy root CI path. HARN-000 proved that root `pytest` is not a valid harness test gate on the current branch line and that workflow Python/runtime/test-root assumptions drift from `agent/pyproject.toml`.
- `.github/AGENT_SAFETY.md` plus root `AGENTS.md`/`CLAUDE.md` define fork isolation; `DT-UCPH/cuc` writes require explicit human authorization.

## 7. Testing and evaluation topology

There are two distinct test surfaces:

1. root `tests/` — small original CUC/Text-Fabric tests (`test_general.py`, `hometest_cuc_texts.py`, helpers);
2. `agent/tests/` — the large morphology/parser/linter/regeneration/review suite configured by `agent/pyproject.toml` with `testpaths = ["tests"]` and Python `>=3.13`.

Many `agent/tests` import `pipeline`, `scripts`, `linter`, `morph_features`, etc. as top-level modules. They therefore assume the `agent/` package root is on `sys.path` / is the working directory. HARN-000's bare root `pytest` under Python 3.10 produced 105 collection errors before the probe, demonstrating that root CI cannot currently be treated as the authoritative agent test invocation.

HARN-011 must establish the persistent CI contract instead of hiding this with a global `pytest.ini`.

### Existing machine-friendly gates to reuse

- per-step `rows_changed / rows_processed` safeguard in `TabletParsingPipeline`;
- lint ERROR baseline comparator (`compare_lint_errors.py` / `lint_reports.regression`);
- reviewed morphology scorer with JSON output and structured models;
- numerous focused parser/linter/migration unit tests;
- dry-run modes for parser/regeneration/migration/seeding workflows.

These are much stronger harness gates than asking an LLM whether output "looks correct".

## 8. Candidate harness contracts/adapters

The following seams can be defined framework-neutrally in HARN-002:

### `EvidenceQuery`
Read-only calls over DULAT/UDB/corpus/TF/evidence sources. Returns structured evidence plus source/config provenance.

### `ParserExecution`
Inputs: source artifact/version, target names, parser revision/config, DULAT/UDB revisions, staging output path, change-ratio policy.

Outputs: structured summary + immutable references/hashes to staged artifacts. Publication into `auto_parsing/**` is a separate side effect.

### `DeterministicEvaluator`
Examples: lint regression and reviewed morphology scoring. Returns structured metrics/findings and an execution classification; never lets model text set pass/fail.

### `SkillCapability`
A versioned reference to a current `.agents/skills/<name>` package, its allowed tools/evidence, expected artifacts, and permission class. Domain instructions remain in the skill package.

### `CuratedDataChange`
Represents a proposed reviewed-data edit/migration separately from generated output. Must carry evidence/provenance and require the task-specific review gate.

### `RepositoryChange`
Fork-local branch/file/PR operation with explicit destination and operation ID. Upstream writes are a separate human-authorized capability and cannot be reached through a generic adapter.

## 9. Boundaries the harness should not cross in its first implementation

1. Do not rewrite deterministic parser steps as LLM prompts.
2. Do not give an agent unrestricted access to historical `fix_*` scripts merely because they exist.
3. Do not make `reviewed/**` publication an automatic consequence of a parser/evaluator result.
4. Do not treat a skill as only a prompt; preserve references/scripts/metadata.
5. Do not make LangGraph/LangChain/Langfuse types part of morphology, evaluation, migration, or path models.
6. Do not use report text as the source of truth when structured scorer/comparator results exist.
7. Do not let a graph guess local database/source paths; resolve and record them explicitly.
8. Do not equate workflow failure with failing tests; HARN-000 demonstrated `blocked-execution` as a distinct state.
9. Do not publish in-place output from a failed/interrupted regeneration; the regeneration skill already requires clean staging.

## 10. Findings that should feed later tickets

### HARN-002
Use framework-neutral contracts around artifacts, execution classifications, resolved evidence configuration, evaluator results, and side-effect operation IDs.

### HARN-003
Call `reviewed_evaluation` directly where practical; `score_reviewed_morphology.py --json` is already a stable CLI façade. Do not duplicate its metric implementation.

### HARN-004
The minimal LangGraph slice can be mostly stubs around real deterministic evaluator/parser adapters. The domain parser must remain unaware of LangGraph.

### HARN-008
Skill packaging already has a useful human/executable structure. Add minimal manifest/version/permission metadata rather than redesigning skills.

### HARN-009
Capability permissions need more granularity than "filesystem write": generated-output publication, curated reviewed-data edits, local cache materialization, fork GitHub writes, and upstream GitHub writes have different policies.

### HARN-011
Repair the test execution contract using the real `agent/` test root/runtime/import assumptions. Do not solve CI by globally narrowing pytest discovery.

## 11. HARN-001 validation gate

This research ticket is documentation-only, so its test gate is an explicit inventory/consistency check rather than executable product code.

- [x] Root repository surfaces enumerated from the branch tree.
- [x] All 11 `.agents/skills` top-level packages accounted for by name and effect class.
- [x] All 29 `agent/scripts` files accounted for by name and operational role.
- [x] Major reusable packages under `agent/` accounted for.
- [x] Generated vs curated artifact authority recorded.
- [x] Git hook and GitHub execution side effects recorded.
- [x] Test-root/runtime/import assumptions recorded, including HARN-000 failure evidence.
- [x] Candidate adapter seams identified without adding LangGraph/LangChain/Langfuse dependencies.
- [x] No parser, data, workflow, dependency, or upstream repository state changed by this ticket.

Independent review must attack this checklist rather than trusting it: compare the document against the actual directory trees, look for missing mutation paths, and challenge any item classified as deterministic/read-only when it can write or depend on mutable external state.
