# HARN-002 — Framework-neutral harness contracts

## Research basis

HARN-001 shows a deterministic/domain-heavy system with orchestration and side effects layered above it. The harness boundary should therefore carry *descriptions of work and evidence*, not parser internals, framework objects, open file handles, model clients, or mutable repository objects.

HARN-011 established an execution vocabulary that HARN-002 must preserve: `blocked-execution`, `test-failure`, `regression`, and `success` are materially different outcomes. HARN-002 adds `human-escalation` as an explicit gate result rather than encoding it as prose.

Existing CUC data-model style uses frozen standard-library dataclasses with explicit `to_dict()` methods. Reusing that style keeps these contracts independent of LangGraph, LangChain, Deep Agents, Langfuse, Pydantic, and runtime services.

## Design decisions

### 1. Pure standard-library domain package

Create `agent/harness/` with no orchestration-framework imports. Contracts use frozen dataclasses, `Enum`, tuples, mappings copied into JSON-safe dictionaries, and explicit validation.

### 2. Separate durable identity from human-readable labels

Every run has a `run_id`. Side-effect-capable changes carry a `change_id` and one or more `operation_ids`. Operation IDs are durable idempotency keys: retry/resume code must be able to recognize that an externally visible operation has already been attempted or completed without depending on process-local memory.

IDs are non-empty opaque strings. The contract layer does not generate UUIDs automatically because generation policy belongs to the controller/runtime and implicit generation makes replay tests less deterministic.

### 3. State phases model orchestration progress, not domain parser state

`RunPhase`:

`research -> plan -> test-design -> implement -> verify -> review -> complete`

Exceptional phases are `blocked` and `awaiting-human`.

The normal retry loops are:

- verification `test-failure` -> `implement`;
- verification `regression` -> `implement`;
- review `request-changes` -> `implement`;
- execution blockage -> `blocked` with a recorded resume phase;
- explicit human escalation -> `awaiting-human` with a recorded resume phase.

A state can resume from `blocked`/`awaiting-human` only to the phase captured when it entered the exceptional state. This avoids arbitrary jump-ahead after partial failure.

### 4. Gate outcome and review disposition are distinct

`GateOutcome` is for executable/evaluation gates:

- `success`
- `test-failure`
- `regression`
- `blocked-execution`
- `human-escalation`

`ReviewDisposition` is scholarly/engineering review judgment:

- `approve`
- `request-changes`
- `escalate`

A review cannot be represented as a generic successful test because reviewers need findings, evidence, reviewer identity/context, and an inspected revision SHA.

### 5. Evidence is referential, not unbounded logs

Test/eval/review contracts carry concise summaries plus optional stable evidence references. Full logs and large artifacts remain outside `RunState`; a future Langfuse/checkpoint adapter may store references/hashes. This keeps state serializable and bounded.

### 6. Exact execution identity is first-class

`TestResult` records both proposed revision (`head_sha`) and actually executed revision (`executed_sha`) when known, preserving the HARN-011 synthetic-merge distinction. A `success`, `test-failure`, or `regression` result requires an executed SHA; `blocked-execution` may lack one when execution never started.

### 7. Transition semantics are pure and testable

A small reducer in `harness/state_machine.py` applies typed events to immutable `RunState`. Invalid phase/event combinations raise `InvalidTransition`. No file/network/GitHub effects occur in the reducer.

Events are intentionally coarse:

- record research;
- record plan;
- declare tests;
- record change;
- record verification result;
- record evaluation result;
- record review;
- resume exceptional state.

The first implementation need not execute tools; it only proves contracts and deterministic state semantics for HARN-004 to wrap.

## Contract inventory

Required public contracts:

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
- enums for phase/outcome/severity/disposition/test kind
- transition events and `apply_event`

## Validation invariants

- all public IDs and required text fields are non-empty;
- tuple-like fields normalize to tuples and reject empty required collections;
- `TaskSpec` requires at least one acceptance criterion;
- `PlanArtifact` requires at least one step;
- `TestIntent` requires a command and working directory and has an explicit `targeted`/`regression` kind;
- non-blocked `TestResult` requires execution identity and an exit code;
- `success` requires exit code 0 and zero failed tests;
- `test-failure`/`regression` require a non-zero exit code or positive failed-test count;
- `blocked-execution` requires a blocker summary and must not claim passed tests;
- a blocking `ReviewFinding` cannot use informational severity;
- `ReviewResult(approve)` cannot contain blocking findings;
- `ReviewResult(request-changes)` must contain at least one blocking finding;
- `RunState` JSON round-trip preserves enums, tuples, mappings, optional artifacts, and exceptional resume phase;
- duplicate `operation_id`s within a `ChangeSet` are invalid;
- a run cannot record the same operation ID in two different changes.

## TDD plan

1. Commit contract/state-machine tests while `agent/harness` does not exist. Tests dynamically import the not-yet-existing package and turn absence into an ordinary assertion failure so pytest collection remains healthy. HARN-011 defines collection/import abort as `blocked-execution`; that must **not** be used as TDD RED evidence.
2. Observe the explicit assertion failure on the exact head/run and classify it as `test-failure`.
3. Implement the smallest standard-library contracts satisfying schema/serialization tests.
4. Implement pure transition reducer and retry/resume validation.
5. Run the full HARN-011 agent suite on a fork-only PR and repair any regression without narrowing discovery.
6. Perform a logically independent adversarial review focused on replay/resume, malformed partial state, duplicate side-effect identity, stale execution identity, and illegal transition bypasses.

## Deferred deliberately

- LangGraph state classes/checkpointers — HARN-004;
- deterministic eval adapters — HARN-003;
- GitHub write capability enforcement — HARN-009;
- skill manifests/discovery — HARN-008;
- observability/Langfuse adapters — later ticket;
- persistence backend, UUID generation, clock/timestamps, filesystem/GitHub effects — runtime concerns, not domain contracts.
