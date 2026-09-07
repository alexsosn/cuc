# Seed Tickets: CUC Agent Harness

Architecture correction: HARN-014 separates the **agentic parsing harness** from the **GitHub-issue-driven research-plan-TDD-review development controller**.

The current skills/prompts are the source of truth for parsing semantics. In particular, a complete column is the parsing context/work unit and every token is reviewed in order. Worklists and audits guide evidence collection; they never select only "suspicious" tokens for review.

## Development-ticket gate

Every implementation ticket in the development controller follows:

1. research evidence recorded;
2. implementation plan written;
3. tests/acceptance criteria specified before implementation;
4. implementation performed in `alexsosn/cuc` only;
5. targeted tests pass;
6. full/relevant regression and eval gates pass;
7. logically independent adversarial review from a clean context;
8. findings fixed or explicitly escalated;
9. exact final revision re-tested when the final artifact changes;
10. no upstream write without explicit human approval.

This gate is **not** the token-parsing loop.

---

## Completed foundations

### HARN-000 — Prove fork-local execution substrate

Completed. Fork-local PR-triggered GitHub Actions can provide attributable RED/GREEN execution. The permanent agent-test gate was subsequently repaired under HARN-011.

### HARN-001 — Inventory current agentic entry points and deterministic boundaries

Completed. `docs/agent-harness/current-system-map.md` inventories domain packages, skills, prompts, scripts, generated/curated artifacts, and side effects.

### HARN-002 — Define framework-neutral development harness contracts and gate semantics

Completed. The contracts cover development-loop task/research/plan/test/eval/change/review state and durable transition invariants. They are **development-controller contracts**, not the eventual column-parsing state model.

---

## HARN-003 — Establish a small reviewed-morphology regression fixture

**Type:** research + TDD infrastructure

**Goal**

Select a small, deliberately non-trivial subset of reviewed morphology data for fast deterministic evaluation.

**Coverage**

- unambiguous case;
- genuine ambiguity;
- DULAT-not-found / unresolved case;
- ordered linguistic heuristic case;
- overgeneration-sensitive cases.

**Acceptance**

- existing `score_reviewed_morphology.py` remains authoritative;
- no duplicate metric implementation;
- fixture/manifest retains source provenance;
- runtime is suitable for frequent harness experiments;
- independent review attacks selection bias and trivially easy cases.

**Depends on:** HARN-001.

---

## HARN-014 — Align architecture with the skill-defined parsing workflow

**Type:** research + architecture correction

**Goal**

Make the documented architecture and backlog match the workflow already specified by the skills.

**Required semantics**

- complete column = context/work unit;
- every token reviewed in textual order;
- full-column context available throughout;
- evidence/worklists prioritize investigation but cannot narrow token scope;
- ambiguity/corpus parallels/provenance/completion follow existing skills;
- `auto_parsing/**` remains generated and never hand-edited;
- systematic/generalizable problems may feed GitHub development issues;
- research-plan-TDD-review is a separate development controller.

**Acceptance**

Docs, issue bodies and execution order contain no anomaly-selection parsing loop and no conflation of scholarly expert feedback with PR adversarial review.

**Depends on:** HARN-001/HARN-002 knowledge; can run alongside HARN-003.

---

## HARN-008 — Extract existing skills into versioned harness capabilities

**Type:** research + design

**Goal**

Formalize the **existing** skill workflows without redesigning their scholarly procedure.

The first target must include `review-automatic-parsing`, because it defines the real parsing runtime semantics.

**Extract at minimum**

- context/work unit;
- ordered workflow stages;
- evidence/reference dependencies;
- executable tools/helpers;
- mutation/effect class;
- completion criteria;
- escalation rules;
- authoritative prompt/convention dependencies;
- skill/version/provenance identity.

**TDD requirement**

Manifest/contract parser tests before changing real skill packages.

**Acceptance**

- `SKILL.md` remains the primary human-readable workflow source where possible;
- at least two structurally different skills map cleanly;
- `review-automatic-parsing` retains complete-column/every-token semantics exactly;
- existing scripts/references remain usable outside the harness;
- no vendor-specific domain rewrite;
- independent review compares the formalization directly against the skills.

**Depends on:** HARN-001, HARN-002, HARN-014.

---

## HARN-018 — Define column-run state and completion semantics

**Type:** research + TDD contracts

**Goal**

Create framework-neutral contracts for the agentic parsing runtime derived from the skills.

**Candidate contracts**

- `ColumnTask`
- `ColumnSnapshot`
- `TokenCursor`
- `TokenDecision`
- `EvidenceRecord`
- `ColumnRunState`
- `ColumnCompletion`
- `CorpusReconciliationFinding`

**Critical invariants**

- a completed column cannot contain an unvisited token;
- every-token traversal order and explicit revisits are auditable;
- whole-column context belongs to the run snapshot;
- checkpoint/resume cannot skip work;
- ambiguity alternatives and evidence provenance survive serialization;
- worklist priority never becomes scope selection;
- generated automatic parsing is changed only through controlled parser/regeneration adapters.

**Depends on:** HARN-008, HARN-014.

---

## HARN-015 — Define parsing eval and expert-feedback protocol

**Type:** research + evaluation design

**Goal**

Measure how well the system improves the parsed corpus and provide comparable feedback for models, skills, tools and parser changes.

**Eval layers**

1. **Output quality** — morphology agreement, ambiguity preservation, extra/missing options, unresolved coverage, reconstruction/lint validity, column/corpus consistency.
2. **Agent quality** — expert acceptance/correction rate, skipped-token failures, unsupported edits, evidence/provenance quality, consistency gains/regressions.
3. **Efficiency/system** — tool calls, retries, latency and cost where available.

**Expert feedback**

Feedback must be attached to exact corpus/token/column, repository/data revision, model/provider/version, skill/tool versions and run identity.

**Acceptance**

- deterministic CUC evaluators remain authoritative where applicable;
- expert feedback is durable and attributable;
- evaluator/gold leakage risks are explicit;
- protocol supports but does not depend on Langfuse;
- independent review attacks benchmark bias and incomparable-run risks.

**Depends on:** HARN-003, HARN-014; should align with HARN-018.

---

## HARN-004 — LangGraph real column-review vertical slice

**Type:** research spike + implementation

**Goal**

Wrap a narrow **real agentic parsing column workflow** in LangGraph. Do not use a research-plan-TDD stub graph as the parsing spike.

**Vertical slice**

```text
load complete column
  -> initialize skill-defined worklist/evidence context
  -> token cursor loop: every token in order
       inspect -> evidence/tool calls -> decision/provenance
  -> column reconciliation
  -> completion/status/reconstruction/lint gates
  -> evaluation artifact
  -> final column-run state
```

A tiny fixture column may be used for tests, but completion semantics must be identical to a real column run.

**Research question**

Compare LangGraph Functional API and Graph API against this workflow.

**TDD requirement**

Tests cover:

- every-token traversal;
- no skipped-token completion;
- checkpoint/resume at token cursor;
- explicit revisit semantics;
- preserved column context/evidence provenance;
- controlled effect adapter replay;
- completion gate failure;
- deterministic eval forwarding.

**Acceptance**

- skills remain source of workflow semantics;
- deterministic CUC packages import no LangGraph;
- no anomaly/suspicion selector controls token scope;
- resume does not duplicate effects or skip tokens;
- no unrestricted GitHub/upstream action in the parsing graph;
- independent review compares graph behavior to HARN-008/HARN-018 and the original skills.

**Depends on:** HARN-003, HARN-008, HARN-018, preferably HARN-015.

---

## HARN-005 — Add optional Langfuse tracing/evaluation adapter

**Type:** research + implementation

**Goal**

Instrument both parsing runs and development runs without making Langfuse part of domain correctness.

**Parsing trace**

- corpus/tablet/column and data revision;
- model/provider/version;
- skill/prompt/tool versions;
- token cursor and tool/evidence calls;
- completion state;
- deterministic eval scores;
- expert-feedback references.

**Development trace**

- GitHub issue/branch/base/head/executed revision;
- dev-loop phase and iteration;
- test/eval results;
- independent reviewer context/result;
- final disposition.

**Acceptance**

- telemetry disabled/missing/backend failure is fail-open;
- existing metrics are forwarded unchanged;
- no Langfuse ID is required by parser/domain state;
- secrets are never committed;
- independent review checks leakage and accidental coupling.

**Depends on:** HARN-004 and HARN-015.

---

## HARN-016 — Benchmark model backends on identical column-review workloads

**Type:** research + benchmark implementation

**Goal**

Compare different model backends on the same real systematic parsing task.

**Fair-comparison contract**

Every model receives:

- the same complete columns;
- the same initial corpus/repository revision;
- the same skills/prompts;
- the same evidence tools/permissions;
- the same every-token traversal/completion criteria;
- the same evaluation protocol.

Held-out evaluator/expert gold must not leak into model context.

**Measure**

Morphology quality, ambiguity preservation, expert acceptance/correction, unsupported edits, column/corpus consistency, unresolved outcomes, tool calls, retries, latency and cost.

**Depends on:** HARN-004, HARN-008, HARN-015, HARN-005 for full tracing.

---

## HARN-017 — Turn systematic parsing findings into development issues

**Type:** feedback bridge + implementation

**Goal**

Convert reproducible, generalizable findings from parsing/evals/expert feedback into deduplicated GitHub issues for the development controller.

**Classification before issue creation**

- local scholarly reading — no dev issue;
- parser/config defect or missing general rule;
- linter defect;
- skill/procedure defect;
- deterministic-tool opportunity;
- harness/runtime defect;
- eval/benchmark defect.

**Acceptance**

- issue contains reproducible examples/run/model/skill/tool/eval provenance;
- one token alone does not automatically justify a general rule;
- repetitive evidence work can become script/tool optimization tickets;
- skill weaknesses can become measured skill-improvement tickets;
- creation is fork-local, idempotent and deduplicated;
- issue format is consumable by HARN-010.

**Depends on:** HARN-009, HARN-010 contracts, HARN-015; benefits from HARN-016.

---

# Development-controller track

These tickets implement the separate research-plan-TDD-review loop that consumes GitHub issues.

## HARN-006 — Clean-context independent development reviewer

**Goal**

Make PR/code review structurally independent from the implementing context.

Reviewer input contains task/spec, relevant final diff/state, actual tests/evals and policies. It excludes implementer hidden rationale/self-justification by default.

**Acceptance**

Structured approve/reject/needs-human findings, distinct context identity, restartability, reject->revision routing, and adversarial meta-review of context leakage.

**Depends on:** development contracts from HARN-002; can be implemented without the parsing graph.

---

## HARN-009 — Human approval and GitHub side-effect boundary

**Goal**

Enforce fork/upstream side-effect policy with explicit operation IDs and human interrupt for upstream writes.

**Acceptance**

Destination classification, idempotent retries, no generic-tool bypass, fork-local development writes, upstream read allowance, upstream-write human gate, repository-safety tests.

**Depends on:** HARN-002 and chosen orchestration adapter.

---

## HARN-007 — Evaluate Deep Agents against explicit LangGraph harness

**Goal**

Compare Deep Agents against explicit LangGraph on a representative CUC workflow, preferably the real HARN-004 column slice plus relevant development-controller capabilities.

**Compare**

Context isolation, skills, subagents, permissions, checkpoint/resume, explicit state control, testability, dependency surface, observability and CUC-specific glue.

**Acceptance**

Evidence-based ADR: adopt, partially adopt, borrow patterns, or reject.

**Depends on:** HARN-004; preferably HARN-006/HARN-008.

---

## HARN-010 — Bounded autonomous research-plan-TDD-review development controller

**Type:** design + integration

**Goal**

Consume GitHub development issues and autonomously execute the bounded dev lifecycle. This controller does **not** parse corpus tokens.

**Flow**

```text
GitHub issue
 -> research
 -> plan
 -> TDD/RED
 -> implementation
 -> targeted/full tests
 -> relevant parser/eval/regression gates
 -> independent review
      reject -> bounded revision loop
      approve -> finalize/merge under repository policy
```

**Controls**

Maximum revisions, budgets, failure classification, blocked/needs-human states, artifact retention, branch/fork safety, exact executed revision identity, and safe fallback work when feature work is exhausted/blocked.

**Acceptance**

No unbounded retries; every terminal state has a reason; model output cannot mark tests/evals passed; side effects are policy-gated; run can be reconstructed from durable artifacts; independent review attacks termination and bypass behavior.

**Depends on:** HARN-006, HARN-009, architecture decision from HARN-007; HARN-005 tracing desirable but not correctness-critical.

---

# Independent hardening

## HARN-011 — Correct agent CI gate

Completed. Python 3.13 full agent test suite executes in fork-local PR CI with attributable RED/GREEN evidence.

## HARN-012 — Require the agent test check in branch policy

Repository-policy hardening; independent of the parsing architecture.

## HARN-013 — Move test dependencies into a locked dev dependency set

Dependency/reproducibility hardening; independent of the parsing architecture.

---

# Corrected execution order

Current:

`HARN-003` and `HARN-014`.

Then parsing-harness critical path:

`HARN-008 -> HARN-018 -> HARN-015 -> HARN-004 -> HARN-005 -> HARN-016 -> HARN-017`

Development-controller track can run in parallel after its prerequisites:

`HARN-006 -> HARN-009 -> HARN-007 -> HARN-010`

Use HARN-012/HARN-013 or performance/stability/ergonomics/docs/edge-case work when the higher-value feature path is blocked.
