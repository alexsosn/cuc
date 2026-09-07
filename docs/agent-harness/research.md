# Preliminary Research: Agent Harness for CUC

Status: architecture corrected by HARN-014; still preliminary until the LangGraph/Deep Agents spikes are measured.

## Primary objective

The system exists to produce and improve a **morphologically parsed CUC** while keeping the automatic parser reproducible and the scholarly/agentic review process auditable.

Deliverable priority:

1. morphologically parsed CUC;
2. reproducible automatic parsing;
3. agentic parsing tools and harness;
4. evaluation + expert-feedback infrastructure;
5. autonomous development loop supporting the above.

The harness and development automation are infrastructure around the corpus, not the research deliverable themselves.

## Two different loops

The architecture must keep two workflows separate.

### 1. Agentic parsing harness

This formalizes the workflow that already exists in the current skills and prompts.

The authoritative semantics are defined primarily by `.agents/skills/review-automatic-parsing/SKILL.md` plus the morphology/tagging guides it references:

- **one complete column is the working/context unit**;
- **every token is reviewed in order**;
- the whole column remains available as context while each token is adjudicated;
- worklists, lints, alignments and audits identify useful evidence but **never decide which tokens are processed**;
- evidence is collected from DULAT, Tropper, EUPT, translations, legacy review, corpus parallels, conventions and specialist audit skills as required;
- repeated/formulaic parallels are reconciled within the column and against the corpus;
- defensible ambiguity is preserved as alternatives rather than collapsed for convenience;
- completion is explicit: reviewing only flagged rows does not complete a column;
- `reviewed/**` is curated scholarly gold;
- `auto_parsing/**` is generated and must never be hand-edited.

A conceptual runtime is therefore:

```text
load complete column
    -> establish worklist/evidence indexes
    -> token 1: inspect -> gather evidence -> adjudicate -> record provenance
    -> token 2: inspect -> gather evidence -> adjudicate -> record provenance
    -> ...
    -> token N
    -> column reconciliation
    -> status/lint/reconstruction checks
    -> corpus-parallel reconciliation where required
    -> persist reviewed result / completion record
```

This is **not** an anomaly-detection loop. Every token is part of the job.

### 2. Autonomous development controller

The research -> plan -> TDD -> implementation -> tests/evals -> logically independent adversarial review lifecycle applies to **development issues**.

It consumes GitHub issues such as:

- parser/config defects or missing productive rules;
- linter defects;
- skill/procedure failures;
- repeated evidence procedures that should become deterministic tools;
- harness/runtime defects;
- eval/benchmark defects;
- performance/stability/ergonomics/documentation/edge-case work when feature tickets are exhausted or blocked.

Its conceptual flow remains:

```text
GitHub issue
  -> research
  -> plan
  -> write tests / RED
  -> implement
  -> targeted + full tests
  -> regression/eval gates
  -> clean-context independent adversarial review
       reject -> revise -> tests/evals -> review
       approve -> finalize/merge
```

Parsing findings may create development issues when they are systematic/generalizable, but the parsing runtime itself is not a TDD graph.

## Current CUC assets to preserve

- deterministic parsing pipeline under `agent/pipeline/` and supporting scripts;
- DULAT lookups, context reranking, provenance and attestation signals;
- ordered linguistic heuristics and safeguards;
- `.agents/skills/*` as existing operational workflow specifications, not merely prompt fragments;
- `agent/prompts/**` and tagging conventions as authoritative morphology/notation knowledge where skills defer to them;
- `reviewed/**` as curated gold data;
- `auto_parsing/**` as generated data that must never be hand-edited;
- `scripts/score_reviewed_morphology.py` and its token/aggregate agreement metrics;
- lint, reconstruction and step-change safeguards;
- fork-safety policy and repository-safety regression tests.

## Candidate stack

### LangGraph: orchestration runtime

LangGraph remains the strongest candidate for durable orchestration because both long column-review runs and the development controller require checkpointed state, resumable execution, explicit branching, human interrupts, and inspectable transitions.

However, there should be **separate graphs/state machines** for parsing and development.

Parsing graph responsibilities:

- complete-column task state;
- stable token cursor and resume semantics;
- evidence/provenance records;
- per-token decisions and preserved alternatives;
- column completion gates;
- corpus reconciliation findings;
- model/skill/tool/run provenance.

Development graph responsibilities:

- issue/task specification;
- research and plan artifacts;
- test intents/results;
- change set and executed revision identity;
- regression/eval results;
- independent review findings;
- bounded retries and GitHub side-effect gates.

The LangGraph Functional API deserves an early comparison with the Graph API, but only after the real skill-derived parsing state is specified. HARN-004 must not invent runtime semantics to make a framework spike convenient.

### LangChain: optional inside model/tool nodes

LangChain should remain optional. It is useful only where a node genuinely needs a model/tool interaction loop. Deterministic parser, linter, scorer, reconstruction and corpus-index logic must remain ordinary CUC code.

No LangChain message/tool types should leak into domain or persisted corpus contracts.

### Deep Agents: benchmark, not default dependency

Deep Agents should be evaluated against the **same real column-review vertical slice**, not against an artificial research-plan-TDD parser graph. Compare planning/subagents, context isolation, skills, permissions, checkpointing, testability, observability and dependency/control cost.

### Langfuse: observability/evaluation plane

Langfuse remains a sidecar rather than the runtime.

It should trace both categories of run without conflating them:

**Parsing run metadata**
- corpus/tablet/column;
- exact input/repository revision;
- model/provider/version;
- skill/prompt/tool versions;
- token cursor / tool calls / evidence calls;
- completion state;
- deterministic eval scores;
- expert feedback references.

**Development run metadata**
- GitHub ticket;
- branch/base/head/executed revision;
- dev-loop phase/iteration;
- test/eval gates;
- reviewer context/result;
- final disposition.

Langfuse availability must never be required for parser/test correctness. Existing CUC deterministic scores stay authoritative; Langfuse stores/compares them rather than reimplementing them.

## Corrected architecture boundary

```text
CUC/TF + evidence sources
          |
          v
reproducible deterministic automatic parser
  parser / rules / linter / scorer / reconstruction / provenance
          |
          v
agentic parsing harness
  existing skills + prompts are source of truth
  complete column -> every token in order -> reconciliation -> completion
          |
          +--------------------------+
          |                          |
          v                          v
morphologically parsed CUC      evals + expert feedback
                                     |
                            systematic finding
                                     |
                                     v
                               GitHub issue
                                     |
                                     v
autonomous development controller
  research -> plan -> TDD -> implement -> tests/evals -> independent review
                                     |
                                     v
                  parser / linter / tool / skill / harness improvements
                                     |
                                     +------> back into parsing system

Langfuse/experiment plane spans parsing runs, evals, feedback and development runs.
```

The dependency rule remains inward: CUC domain code and corpus contracts must not depend on LangGraph/LangChain/Langfuse types.

## Skill-first design rule

HARN-008 is now architectural, not cosmetic. Before wiring parsing into LangGraph, extract the actual contracts already encoded in skills:

- working unit/context scope;
- ordered steps;
- required evidence sources;
- optional/specialist evidence routes;
- executable helpers/tools;
- mutation class;
- completion criteria;
- escalation rules;
- authoritative prompt/reference dependencies;
- provenance/version identity.

`SKILL.md` should remain the human-readable source where possible. Machine metadata should formalize invocation/state/permissions, not duplicate or silently rewrite scholarly procedure.

## Parsing state seed

HARN-018 should define framework-neutral contracts such as:

- `ColumnTask`
- `ColumnSnapshot`
- `TokenCursor`
- `TokenDecision`
- `EvidenceRecord`
- `ColumnRunState`
- `ColumnCompletion`
- `CorpusReconciliationFinding`

Critical invariants:

- no completed column with an unvisited token;
- traversal order and revisits are explicit/auditable;
- full column remains part of the context snapshot;
- evidence and alternatives survive checkpoint/replay;
- resume cannot silently skip a token;
- worklists may prioritize evidence but may not narrow scope;
- generated `auto_parsing/**` cannot be mutated ad hoc.

## Evaluation strategy

Evaluation must answer three separate questions.

### A. Output quality: how good is the parsed CUC?

Seed metrics:

- reviewed morphology exact-set agreement;
- macro/micro precision, recall, F1 and Jaccard;
- ambiguity preservation and option-count errors;
- unsupported extra analyses;
- unresolved coverage;
- reconstruction/lint validity;
- column/corpus consistency.

HARN-003 provides the first fast deterministic fixture; larger held-out column/tablet datasets should follow.

### B. Agent quality: how well does the agent perform systematic review?

Candidate measures:

- expert acceptance/correction rate;
- skipped-token/completion failures;
- unsupported changes;
- unnecessary re-opening of settled readings;
- evidence/provenance completeness;
- consistency improvements/regressions across parallels;
- recovery after expert correction.

Expert feedback must be attached to exact run/model/skill/tool/corpus revision and exact token/column where applicable.

### C. Model/system quality: which model + skills + tools combination is better?

Comparisons must use:

- identical complete columns;
- identical initial data/repository revision;
- identical skills/prompts/evidence tools and permissions;
- identical every-token completion contract;
- evaluator/gold data withheld from model context where required;
- exact model/provider/version metadata;
- the same deterministic + expert-feedback protocol.

Then compare quality first, followed by cost, latency, tool calls, retries and stability.

## Feedback and automatic improvement

Parsing runs, expert feedback and model comparisons may expose recurring problems.

Before a GitHub issue is created, classify the finding:

- local scholarly reading -> corpus decision only;
- parser/config rule problem;
- linter defect;
- skill/procedure weakness;
- deterministic-tool opportunity;
- harness/runtime defect;
- eval/benchmark defect.

This is also how scripts and skills should improve: if agents repeatedly perform the same expensive evidence procedure or make the same class of mistake, measured evidence can justify a deterministic helper or a revised skill. The development controller then implements that change using research-plan-TDD-review.

## Independent review requirement

The clean-context adversarial reviewer remains a **development-controller gate**. It receives task/specification, final diff/repository state, actual test/eval outputs and applicable invariants, without the implementer's accumulated self-justification.

Scholarly expert review of parsing output is a different feedback channel and must not be conflated with PR adversarial review.

## GitHub and side effects

- all autonomous development work stays in `alexsosn/cuc`;
- upstream `DT-UCPH/cuc` remains read-only without explicit human authorization;
- development writes need deterministic operation identities for retry/resume;
- upstream writes remain explicit human-interrupt actions;
- generated `auto_parsing/**` is rebuilt through controlled parser/regeneration capabilities rather than hand editing.

## Revised execution order

Already completed:

`HARN-000 -> HARN-001 -> HARN-002`

Current work:

`HARN-003` — small representative deterministic morphology fixture.

Then the corrected parsing-harness critical path is:

`HARN-014 -> HARN-008 -> HARN-018 -> HARN-015 -> HARN-004 -> HARN-005 -> HARN-016 -> HARN-017`

Development-controller work can progress in parallel once its prerequisites are stable:

`HARN-006 -> HARN-009 -> HARN-007 -> HARN-010`

HARN-012/HARN-013 remain independent CI/dependency hardening tickets and can be taken when the primary path is blocked or after higher-value feature tickets.

## Open research questions

1. Minimal machine-readable representation of the existing skill workflow without duplicating `SKILL.md` semantics.
2. Exact `ColumnRunState` and safe resume/revisit semantics.
3. How reviewed-column writes, automatic-parser regeneration and corpus reconciliation should be separated into controlled effect adapters.
4. Functional API vs Graph API for the real column-review loop.
5. Persistence backend for long parsing runs.
6. How to isolate model context from held-out eval/expert gold while still giving the full input column and legitimate evidence.
7. Expert-feedback format and adjudication/versioning.
8. Representative held-out columns/tablets for fair model comparisons beyond HARN-003's fast fixture.
9. Langfuse Cloud/self-hosted/local-only telemetry trade-offs.
10. Whether Deep Agents improves real column-review orchestration without weakening explicit completion/state control.
11. Rules for converting repeated parsing findings into deduplicated, reproducible development issues.

## External framework references

Framework APIs evolve quickly. LangGraph, Deep Agents and Langfuse versions/APIs must be re-checked in the implementation tickets before pinning dependencies. The architecture above deliberately depends on capabilities and boundaries, not on a particular current helper API.
