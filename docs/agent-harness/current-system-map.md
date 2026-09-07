# HARN-001 — Current CUC agent/parsing system map

Snapshot basis: `alexsosn/cuc` branch `harn-001-system-inventory`, descended from the reviewed CUC line plus the fork-safety/HARN-000 documentation.

This document inventories the current agent-facing system before LangGraph/Langfuse integration. It deliberately separates linguistic/domain behavior from orchestration and repository side effects. The harness should wrap these capabilities, not rewrite the parser into LLM calls.

> **HARN-014 architecture correction:** the existing skills are the source of truth for the agentic parsing runtime. In particular, `review-automatic-parsing` defines one complete column as the work/context unit and requires every token to be reviewed in order. The research-plan-TDD-review lifecycle is a separate GitHub-issue-driven development controller, not the parsing loop.

## 1. Architectural boundary

The current system is already layered, but the boundaries are imperfect:

```text
external / local evidence
  DULAT, UDB, modules DB, Notarius, TF releases, EUPT/legacy evidence
                         |
                         v
agent knowledge / conventions -------------+
  prompts/                                  |
  tracked data_sources/*.tsv                |
                         |                   |
                         v                   |
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
.agents/skills + one Claude-only skill = expert workflows that compose
scripts, evidence, tests, conventions, and human/editorial judgment
```

The future architecture has **two orchestration layers above the deterministic/domain packages**:

1. an agentic parsing harness that formalizes the existing skill-defined complete-column/every-token workflow;
2. a separate development controller that runs research -> plan -> TDD -> implementation -> tests/evals -> independent review for GitHub issues.

LangGraph may coordinate typed capabilities in either layer, but it must not replace parser steps, linter, scorer, alignment logic, Text-Fabric conversion logic, skills, or the repository's authoritative tagging conventions.

## 2. Existing deterministic/domain and knowledge packages

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
| `agent/prompts/**` | Authoritative morphology/tagging procedure and notation guidance | Knowledge changes alter human/agent adjudication semantics even when parser code is unchanged | Treat as versioned knowledge input; include hashes/revision in review traces |
| `agent/data_sources/*.tsv` | Tracked DULAT entry patches, generic parsing overrides, onomastic gloss overrides | Small tracked evidence/config tables can change parser/reviewer behavior | Explicit config/evidence dependency, hashed with the run |

### Important impurity inside the current boundary

`TabletParsingPipeline` imports `scripts.bootstrap_tablet_labeling` and `scripts.refine_results_mentions` as library modules. Therefore `agent/scripts/` is not purely a CLI layer today. Harness work must not assume "anything under scripts is shell-only". The immediate adapter strategy should reuse their callable functions as-is; extraction into cleaner library modules is optional refactoring after tests prove behavior.

Likewise, `RefinementStep` cleanly exposes `refine_row(row) -> row`, but its default `refine_file(path)` reads and overwrites the file. This is a useful natural split between domain transformation and filesystem side effect.

## 3. Agent skill inventory — 12 effective skills accounted for

`.agents/skills` contains 11 canonical top-level skill packages:

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
| `review-automatic-parsing` | Token-by-token scholarly adjudication into gold `reviewed/**`; complete column is the work/context unit and every token is reviewed in order | curated scholarly write | **primary agentic parsing workflow contract**; HARN-008/HARN-018 must formalize it without changing its semantics |
| `review-linguistic-rule-change` | Turn expert feedback into measured rule/exception/test changes | research, then code/config/data mutation as justified | template/source for systematic finding -> development issue/change semantics |
| `triage-morphology-lint-regressions` | Stable baseline-vs-candidate ERROR comparison | read/diagnose by default | deterministic gate + investigation workflow |

`.claude/skills` is mostly a compatibility surface: 11 entries are symlinks to the canonical `.agents/skills` packages. It also contains one **real, non-symlink, Claude-only package**, `parse-ugaritic-passive-participle`, with its own `SKILL.md`, `agents/`, `references/`, and `scripts/audit_passive_participle.py`. Harness discovery must therefore either canonicalize aliases and then add this package explicitly, or migrate it into the canonical skill tree before assuming `.agents/skills` is exhaustive.

### Skill structure already worth preserving

The skills are not merely prompts. Across the inventory they combine:

- `SKILL.md` workflow/decision procedure;
- `references/**` domain constraints/evidence interpretation;
- `scripts/**` executable audits/helpers;
- for several skills, `agents/openai.yaml` agent metadata.

In addition, multiple skills explicitly defer to `agent/prompts/**` for authoritative notation/procedure. In particular, `review-automatic-parsing` treats `Morphological_Labeling_Agent_Guide.md`, `Morphological_Labeling_Quick_Checklist.md`, and `Tagging conventions.md` as controlling knowledge. A future skill manifest therefore needs dependency references/hashes, not just a prompt body.

HARN-008 should extract the machine-readable metadata and state semantics genuinely needed for discovery/version/permissions/completion. It must not copy the domain knowledge into LangGraph/LangChain-specific prompt classes or reinterpret the complete-column/every-token workflow.

## 4. Executable/helper surface

### 4.1 `agent/scripts` inventory — 29/29 accounted for

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

### 4.2 Bundled skill executables/helpers — 15 accounted for

These are agent-facing executable surfaces even though they are not under `agent/scripts`:

| Skill package | Executable/helper | Effect class / harness note |
| --- | --- | --- |
| `audit-onomastic-encoding` | `scripts/audit_onomastic.py` | read/diagnose corpus audit |
| `audit-onomastic-encoding` | `scripts/eupt_align.py` | read/compare EUPT evidence |
| `audit-onomastic-encoding` | `scripts/lint_diff.sh` | lint baseline comparison; environment-sensitive |
| `audit-split-token-migrations` | `scripts/audit_split_token_pairs.py` | read/diagnose migration invariant audit |
| `audit-ugaritic-analysis-reconstruction` | `scripts/check_reconstruction.py` | deterministic reconstruction checker |
| `parse-ugaritic-feminine-endings` | `scripts/audit_feminine_endings.py` | linguistic audit |
| `parse-ugaritic-gt-stems` | `scripts/audit_gt_stems.py` | linguistic audit |
| `parse-ugaritic-n-stems` | `scripts/audit_n_stems.py` | linguistic audit |
| `review-automatic-parsing` | `scripts/audit_marker_layers.py` | marker-layer audit |
| `review-automatic-parsing` | `scripts/legacy_align.py` | legacy/current evidence alignment |
| `review-automatic-parsing` | `scripts/review_status.py` | reviewed work-status inspection |
| `review-automatic-parsing` | `scripts/sources.py` | shared evidence helper |
| `review-automatic-parsing` | `scripts/sources_lookup.py` | multi-source evidence lookup |
| `review-automatic-parsing` | `scripts/tropper_index.py` | Tropper evidence index/build/lookup; may materialize an index |
| Claude-only `parse-ugaritic-passive-participle` | `scripts/audit_passive_participle.py` | linguistic audit |

The presence of bundled executables means capability discovery cannot be based only on `agent/scripts`. Some are read-only audits; others can materialize indexes or depend on external/local evidence. Each must carry its own effect/provenance metadata before autonomous exposure.

### Script grouping for the first harness

The first vertical slice should expose only a small allowlist **sufficient to execute the real `review-automatic-parsing` column workflow**, not a synthetic dev-loop workflow.

**Read/evaluate:**
- reviewed evaluator (`reviewed_evaluation` directly or `score_reviewed_morphology.py --json`);
- lint regression comparator;
- selected DULAT/corpus lookup helpers;
- `review-automatic-parsing` worklist/evidence/status helpers;
- selected specialist audit scripts whose effects have been classified.

**Execute deterministic parser/regeneration when required:**
- `TabletParsingPipeline` through a controlled staging-directory adapter.

**Do not initially expose:**
- historical one-off `fix_*`, `build_1_3_*`, `emit_*`, `reconcile_*` scripts;
- unrestricted direct `reviewed/**` writers outside the specific skill workflow;
- git-hook installer;
- unrestricted filesystem or GitHub mutation;
- unclassified skill-bundled executables merely because a skill references them.

## 5. Data, knowledge, and artifact authority

| Path/surface | Authority | Mutation policy |
| --- | --- | --- |
| `tf/<version>/**` | canonical Text-Fabric release inputs | source data; parser harness reads/materializes from it |
| `agent/generated_sources/cuc_tablets_tsv/<version>/**` | generated raw TSV projection of TF | regenerate; never curate manually |
| `auto_parsing/<version>/**` | generated automatic parsing | **never hand-edit**; generator/config/code + test + regeneration only |
| `reviewed/**` | curated scholarly gold/editorial data | direct edits are allowed only as explicit review/migration work; never overwritten by regeneration |
| `agent/reports/**` / configured report directory | generated lint/evaluation artifacts | regenerate from authoritative input; provenance matters |
| `agent/prompts/**` | authoritative morphology/tagging knowledge for agent-assisted review | version and trace; edits change adjudication semantics |
| `agent/data_sources/{dulat_entry_patches,generic_parsing_overrides,onomastic_gloss_overrides}.tsv` | tracked supporting evidence/config | source-specific provenance required; changes can alter generated output |
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

### Reviewed data and agentic column review

`review-automatic-parsing` explicitly defines reviewed data as gold created by systematic token-by-token scholarly review. Its runtime semantics are stronger than generic filesystem permissions:

- a **column**, not a tablet, is one work/context unit;
- every token is reviewed in order;
- worklists/evidence passes identify where to look hardest but never reduce scope;
- the whole column is connected context;
- alternative sourced readings are preserved;
- completion is checked explicitly by status/lint/reconstruction rules;
- recurring/generalizable problems escalate to specialist audits or parser/linter fixes instead of being improvised locally.

Seeding is only a worklist. `migrate-reviewed-text-fabric` likewise requires a preview outside `reviewed/**`, alignment review, lint/tests, then an intentional final copy. These policies must survive as capability-specific gates.

### Knowledge dependencies

`review-automatic-parsing` explicitly delegates notation/procedure to the prompt/convention files, and phenomenon skills add their own references. Therefore a parsing trace is incomplete if it records only the model and skill name. At minimum it should record the skill package revision plus the authoritative knowledge/reference file hashes used in that run.

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

HARN-011 established the persistent fork-local agent CI contract; HARN-012/HARN-013 retain remaining policy/dependency hardening.

### Existing machine-friendly gates to reuse

- per-step `rows_changed / rows_processed` safeguard in `TabletParsingPipeline`;
- lint ERROR baseline comparator (`compare_lint_errors.py` / `lint_reports.regression`);
- reviewed morphology scorer with JSON output and structured models;
- numerous focused parser/linter/migration unit tests;
- dry-run modes for parser/regeneration/migration/seeding workflows.

These are much stronger harness gates than asking an LLM whether output "looks correct".

Parsing evaluation additionally needs explicit **complete-column coverage**, expert feedback, ambiguity/consistency measures, and model/skill/tool provenance; those belong to HARN-015/HARN-016 rather than a second morphology scorer.

## 8. Candidate harness contracts/adapters

The development-controller contracts defined by HARN-002 remain framework-neutral. Parsing runtime state is a separate HARN-018 concern.

### `EvidenceQuery`
Read-only calls over DULAT/UDB/corpus/TF/evidence sources. Returns structured evidence plus source/config provenance.

### `ParserExecution`
Inputs: source artifact/version, target names, parser revision/config, DULAT/UDB revisions, staging output path, change-ratio policy.

Outputs: structured summary + immutable references/hashes to staged artifacts. Publication into `auto_parsing/**` is a separate side effect.

### `DeterministicEvaluator`
Examples: lint regression and reviewed morphology scoring. Returns structured metrics/findings and an execution classification; never lets model text set pass/fail.

### `KnowledgeBundle`
Versioned references/hashes for `agent/prompts/**`, skill-local `references/**`, and tracked evidence/config tables used by a parsing/review run. It is input provenance, not free-form model memory.

### `SkillCapability`
A versioned reference to a canonical `.agents/skills/<name>` package or explicitly registered non-canonical skill, its work/context unit, ordered stages, allowed tools/evidence, bundled executables, expected artifacts, completion rules, aliases, prompt/reference dependencies, and permission class. Domain instructions remain in the skill package.

### `ColumnRunState` family
HARN-018 should derive framework-neutral `ColumnTask`, `ColumnSnapshot`, `TokenCursor`, `TokenDecision`, `EvidenceRecord`, `ColumnCompletion`, and reconciliation contracts from `review-automatic-parsing`. Completion must be impossible with skipped tokens.

### `CuratedDataChange`
Represents a proposed reviewed-data edit/migration separately from generated output. Must carry evidence/provenance and require the task-specific review/completion semantics.

### `RepositoryChange`
Fork-local branch/file/PR operation with explicit destination and operation ID. Upstream writes are a separate human-authorized capability and cannot be reached through a generic adapter.

## 9. Boundaries the harness should not cross in its first implementation

1. Do not rewrite deterministic parser steps as LLM prompts.
2. Do not invent an anomaly/suspicion selector that decides which tokens are reviewed; `review-automatic-parsing` requires every token in the column.
3. Do not give an agent unrestricted access to historical `fix_*` scripts merely because they exist.
4. Do not make `reviewed/**` publication an automatic consequence of a parser/evaluator result outside the explicit skill workflow.
5. Do not treat a skill as only a prompt; preserve references/scripts/metadata, completion semantics and knowledge dependencies.
6. Do not make LangGraph/LangChain/Langfuse types part of morphology, evaluation, migration, path or column-run domain models.
7. Do not use report text as the source of truth when structured scorer/comparator results exist.
8. Do not let a graph guess local database/source paths; resolve and record them explicitly.
9. Do not equate workflow failure with failing tests; HARN-000 demonstrated `blocked-execution` as a distinct state.
10. Do not publish in-place output from a failed/interrupted regeneration; the regeneration skill already requires clean staging.
11. Do not assume `.agents/skills` or `agent/scripts` alone exhaust the current executable agent surface; aliases, the Claude-only skill, and bundled skill scripts are real inputs.
12. Do not conflate scholarly expert feedback on parsing with clean-context adversarial PR review in the development controller.

## 10. Findings that feed later tickets

### HARN-002
Completed: framework-neutral development-loop contracts around task/research/plan/tests/evals/changes/review and durable gate semantics. Do not reuse `RunState` as the column parser state merely because it already exists.

### HARN-003
Call `reviewed_evaluation` directly where practical; `score_reviewed_morphology.py --json` is already a stable CLI façade. Do not duplicate its metric implementation.

### HARN-014
Correct architecture/backlog around two distinct controllers and the skill-defined systematic parsing workflow.

### HARN-008
Skill packaging already has a useful human/executable structure. Extract minimal manifest/version/permission/alias/dependency/work-unit/completion metadata rather than redesigning skills. `review-automatic-parsing` is the first authoritative runtime workflow to formalize.

### HARN-018
Define column-run state, every-token cursor/completion, evidence/provenance, alternative-reading and resume/revisit semantics before LangGraph owns the parsing loop.

### HARN-015
Define output, agent and efficiency evals plus attributable expert feedback. Reuse deterministic scorers and keep evaluation gold out of model context where necessary.

### HARN-004
The LangGraph spike must exercise a real complete-column/every-token vertical slice derived from HARN-008/HARN-018. It is not a stub research-plan-TDD parser graph. The domain parser and skills remain framework-independent.

### HARN-005
Trace parsing runs and development runs as distinct trace schemas; ingest deterministic CUC metrics without changing them.

### HARN-016
Compare models on identical complete columns, repository/data revision, skills/prompts/tools/permissions and completion/eval protocol.

### HARN-017
Classify systematic findings from parsing/evals/expert feedback into parser/linter/skill/tool/harness/eval development issues; avoid generalizing from isolated local readings.

### HARN-009
Capability permissions need more granularity than "filesystem write": generated-output publication, curated reviewed-data edits, local cache/index materialization, fork GitHub writes, and upstream GitHub writes have different policies. Bundled skill executables need the same classification.

### HARN-010
The bounded autonomous research-plan-TDD-review controller consumes GitHub development issues. It does not parse corpus tokens.

## 11. HARN-001 validation gate

This research ticket was documentation-only, so its test gate is an explicit inventory/consistency check rather than executable product code.

- [x] Root repository surfaces enumerated from the branch tree.
- [x] All 11 canonical `.agents/skills` top-level packages accounted for by name and effect class.
- [x] `.claude/skills` alias topology inspected; the unique `parse-ugaritic-passive-participle` package recorded separately.
- [x] All 29 `agent/scripts` files accounted for by name and operational role.
- [x] All 15 currently discovered skill-bundled executable/helper files accounted for.
- [x] `agent/prompts/**` authoritative knowledge files recorded as explicit versioned dependencies.
- [x] Tracked `agent/data_sources/*.tsv` evidence/config tables recorded.
- [x] Major reusable packages under `agent/` accounted for.
- [x] Generated vs curated artifact authority recorded.
- [x] Git hook and GitHub execution side effects recorded.
- [x] Test-root/runtime/import assumptions recorded, including HARN-000 failure evidence.
- [x] Candidate adapter seams identified without adding LangGraph/LangChain/Langfuse dependencies.
- [x] No parser, data, workflow, dependency, or upstream repository state changed by HARN-001.

HARN-014 subsequently corrected the interpretation of the agentic parsing workflow without changing the underlying inventory: independent review of the correction must compare the architecture against the actual skills, especially complete-column scope, every-token traversal, completion semantics, and escalation rules.
