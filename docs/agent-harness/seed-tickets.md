# Seed Tickets: CUC Agent Harness

These are seed tickets for the research -> plan -> TDD -> implementation -> independent review loop. They are deliberately small and ordered to avoid committing prematurely to LangChain/Deep Agents abstractions.

Each implementation ticket follows the same gate:

1. research evidence recorded;
2. implementation plan written;
3. tests/acceptance criteria specified before code changes;
4. implementation performed in `alexsosn/cuc` only;
5. targeted tests pass;
6. relevant regression/evaluation gates pass;
7. logically independent adversarial review runs from a clean context;
8. findings are fixed or explicitly escalated;
9. no upstream write without explicit human approval.

---

## HARN-001 — Inventory current agentic entry points and deterministic boundaries

**Type:** research

**Goal**

Map the current `agent/`, `.agents/skills/`, scripts, tests, generated artifacts, reviewed data, and GitHub hooks into three categories: deterministic domain logic, agent/tool-facing capabilities, and orchestration/side effects.

**Questions**

- Which scripts are pure/reusable functions versus CLI-only entry points?
- Which current skills already have enough structure to become harness capabilities?
- Which operations mutate generated data or repository state?
- Which operations are nondeterministic or external-source-dependent?
- Where are the current test/evaluation gates actually enforced?

**Deliverable**

`docs/agent-harness/current-system-map.md` with a dependency/side-effect table and candidate adapter seams.

**Acceptance**

- Every current agent-facing script/skill is accounted for.
- Generated vs curated data invariants are explicit.
- No implementation dependencies are added.
- Independent review checks that important side effects or hidden orchestration were not omitted.

**Depends on:** none

---

## HARN-002 — Define harness contracts and gate semantics

**Type:** research + design

**Goal**

Define framework-neutral typed contracts for the state carried by the loop before introducing LangGraph types into the codebase.

**Candidate contracts**

- `TaskSpec`
- `ResearchArtifact`
- `PlanArtifact`
- `TestIntent`
- `TestResult`
- `EvalResult`
- `ChangeSet`
- `ReviewFinding`
- `ReviewResult`
- `RunState`

**Required decisions**

- explicit state transition/gate semantics;
- serializability requirements;
- operation/run IDs for idempotent side effects;
- failure/retry semantics;
- distinction between failing test, blocked execution, regression, and human escalation.

**TDD requirement**

Contract/schema validation tests must be written before any LangGraph adapter.

**Acceptance**

- Domain contracts import no LangGraph, LangChain, Deep Agents, or Langfuse types.
- State is JSON-serializable or has an explicit serialization contract.
- Invalid transitions and malformed review findings have tests.
- Independent review attacks resume/retry and partial-failure cases.

**Depends on:** HARN-001

---

## HARN-003 — Establish a small reviewed-morphology regression fixture

**Type:** research + TDD infrastructure

**Goal**

Select a small, representative subset of existing reviewed morphology data for rapid harness experiments.

**Coverage targets**

Include at least:

- one unambiguous token;
- one genuinely ambiguous token;
- one DULAT-not-found or weak-evidence case;
- one case touched by an ordered linguistic heuristic;
- one case where overgeneration matters.

**Deliverable**

A deterministic fixture/manifest that can invoke the existing scorer and produce a stable JSON result suitable for a graph gate and later Langfuse score ingestion.

**TDD requirement**

Tests assert the fixture selection, scorer invocation contract, and expected metric fields before harness integration.

**Acceptance**

- Existing `score_reviewed_morphology.py` remains authoritative.
- No duplicate metric implementation is introduced.
- Fixture runtime is small enough for frequent use.
- Independent review verifies that fixture selection is not trivially easy or biased toward perfect cases.

**Depends on:** HARN-001

---

## HARN-004 — LangGraph minimal vertical-slice spike

**Type:** research spike + implementation

**Goal**

Wrap one narrow CUC task in a minimal LangGraph workflow using existing parser/test/eval code rather than rewriting the parser.

**Spike flow**

```
research_stub -> plan_stub -> test_gate -> implementation_stub/adapter
    -> targeted_test -> regression_eval -> review_stub -> final_state
```

The first spike may use deterministic/stub research and implementation nodes; the point is to validate state, checkpointing, branching, retry behavior, and adapter boundaries.

**Research question**

Compare LangGraph Functional API and Graph API for this vertical slice. Record the choice and rejected alternative.

**TDD requirement**

Tests cover:

- pass path;
- failed targeted test -> revise path;
- regression failure -> revise path;
- reviewer reject -> revise path;
- serialization/checkpoint resume;
- idempotent re-entry for side-effecting adapter stubs.

**Acceptance**

- Deterministic CUC domain modules do not import LangGraph.
- Graph execution can resume from a checkpoint without duplicating completed side effects.
- State transitions are inspectable and testable.
- No GitHub write or upstream action is part of the spike.
- Independent review compares implementation against HARN-002 contracts rather than author rationale.

**Depends on:** HARN-002, HARN-003

---

## HARN-005 — Langfuse optional tracing adapter

**Type:** research + implementation

**Goal**

Instrument the vertical slice with Langfuse while keeping ordinary execution independent of Langfuse availability.

**Trace schema**

Capture at minimum:

- harness run/ticket ID;
- base commit/branch;
- node name and iteration;
- model/provider when applicable;
- tool/capability name;
- test/eval gate result;
- reviewer context identity;
- final disposition.

**TDD requirement**

Tests must cover:

- telemetry disabled;
- missing credentials;
- telemetry backend failure;
- trace metadata construction;
- deterministic evaluator scores forwarded without changing their values.

**Acceptance**

- Parser/test correctness is unaffected by Langfuse being unavailable.
- Secrets are environment/config only and never committed.
- Existing CUC metrics are ingested, not reimplemented.
- No Langfuse ID is required by domain state.
- Independent review checks accidental hard dependency and data leakage risk.

**Research note**

Re-check current Langfuse Python SDK/API before implementation; current docs use Python SDK v4 and OpenTelemetry-based tracing.

**Depends on:** HARN-004

---

## HARN-006 — Clean-context independent reviewer

**Type:** research + implementation

**Goal**

Make adversarial review structurally independent from the implementation agent rather than merely asking the same context to "review itself".

**Reviewer input contract**

Default input contains only:

- task/specification;
- acceptance criteria;
- relevant repository snapshot/diff;
- tests and actual outputs;
- applicable invariants/policies;
- explicit review rubric.

It excludes implementer chain-of-thought, self-justification, and conversational momentum.

**Structured output**

- disposition: `approve | reject | needs-human`;
- findings with severity/evidence/location;
- missing tests;
- invariant risks;
- regression risks.

**TDD requirement**

Tests verify context construction and that prohibited implementer-context fields are absent. Add synthetic changes with seeded defects and require the reviewer pipeline to expose them at the contract/evaluator level where deterministic checking is possible.

**Acceptance**

- Reviewer context has a distinct run/context ID.
- Review is restartable independently of implementation state.
- Reject routes deterministically back to revision.
- Findings are persisted as structured artifacts.
- Independent meta-review checks whether the reviewer can accidentally see implementer-only context.

**Depends on:** HARN-004

---

## HARN-007 — Evaluate Deep Agents against explicit LangGraph harness

**Type:** research spike

**Goal**

Determine whether Deep Agents should be adopted, partially reused, or rejected for CUC's coding/research harness.

**Compare**

- planning/task decomposition;
- subagent/context isolation;
- filesystem/context management;
- permissions/HITL;
- deterministic state-machine control;
- testability;
- checkpoint/resume semantics;
- dependency surface;
- ability to preserve CUC skills;
- observability integration;
- amount of CUC-specific glue code.

**Method**

Implement or prototype the same narrow HARN-004 scenario using Deep Agents, without changing the deterministic parser.

**Acceptance**

Produce an ADR with measured/observed trade-offs and one of:

- adopt Deep Agents for selected nodes;
- adopt Deep Agents as primary harness;
- keep explicit LangGraph and borrow only patterns;
- reject for now.

No architecture decision may be based only on documentation marketing or line-count aesthetics.

**Depends on:** HARN-004, preferably HARN-006

---

## HARN-008 — Map `.agents/skills` into versioned harness capabilities

**Type:** research + design

**Goal**

Define how existing skill packages are discovered and invoked without rewriting their domain knowledge into framework-specific prompts.

**Questions**

- What is the minimal manifest needed for inputs, outputs, tools, references, tests, and version?
- Can current `SKILL.md` remain the human-readable source while optional machine metadata is added?
- How are skill-specific evaluators and safety permissions represented?
- How do we trace the exact skill/version used for a run?

**TDD requirement**

Manifest/parser contract tests precede migration of any real skill.

**Acceptance**

- At least two structurally different existing skills map cleanly under the proposal.
- Existing scripts/references remain usable outside the harness.
- No mandatory vendor-specific metadata is introduced into domain content.
- Independent review checks portability and backwards compatibility.

**Depends on:** HARN-001, HARN-002

---

## HARN-009 — Human approval and GitHub side-effect boundary

**Type:** safety design + implementation

**Goal**

Make repository writes explicit capabilities and enforce the existing fork/upstream safety policy at runtime.

**Policy seed**

- `alexsosn/cuc` reads: allowed;
- `alexsosn/cuc` controlled development writes: allowed by task policy;
- `DT-UCPH/cuc` reads: allowed;
- `DT-UCPH/cuc` writes: never autonomous; require explicit human authorization at the moment of action.

**TDD requirement**

Tests cover destination classification, fork writes, upstream-read allowance, upstream-write rejection/interrupt, retry after approval, and no duplicate action after resume.

**Acceptance**

- Side-effect capabilities have explicit operation IDs.
- Upstream writes cannot occur through a generic GitHub tool adapter bypass.
- Human approval is represented as a graph interrupt/gate, not a prompt convention.
- Existing `test_repository_safety.py` remains green and is extended if necessary.
- Independent review attempts bypasses via alternative API/tool paths.

**Depends on:** HARN-002, HARN-004

---

## HARN-010 — Define autonomous research-plan-TDD-review controller semantics

**Type:** design + integration

**Goal**

Turn the successful spikes into a bounded autonomous development loop with explicit stop conditions.

**Required controls**

- maximum revision iterations;
- budget/cost limits where available;
- failure classification;
- blocked/needs-human state;
- no-feature-work fallback to performance/stability/ergonomics/docs/edge cases only when explicitly enabled;
- clean-context review after each candidate finalization;
- artifact/log retention sufficient for audit;
- branch/fork safety enforcement.

**TDD requirement**

Scenario tests for success, repeated failure, reviewer rejection loop, blocked dependency, budget exhaustion, human escalation, and safe termination.

**Acceptance**

- No unbounded retry loop.
- Every terminal state has an explicit reason.
- A run can be reconstructed from persisted structured artifacts/traces.
- Model output alone cannot mark tests/evals as passed.
- Independent review verifies termination and side-effect safety under adversarial sequences.

**Depends on:** HARN-004, HARN-005, HARN-006, HARN-009; architecture decision from HARN-007

---

# Suggested first execution order

Start with:

`HARN-001 -> HARN-002 -> HARN-003 -> HARN-004`

Then branch the research:

- observability/evals: `HARN-005`
- review independence: `HARN-006`
- Deep Agents comparison: `HARN-007`
- skill formalization: `HARN-008`
- side-effect safety: `HARN-009`

Only after those results should `HARN-010` assemble the autonomous loop.
