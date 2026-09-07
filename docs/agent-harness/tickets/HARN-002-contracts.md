# HARN-002 — Framework-neutral harness contracts

## Research basis

HARN-001 shows a deterministic/domain-heavy system with orchestration and side effects layered above it. The harness boundary therefore carries descriptions of work and evidence, not parser internals, framework objects, open handles, model clients, or mutable repository objects.

HARN-011 established an execution vocabulary that HARN-002 preserves: `blocked-execution`, `test-failure`, `regression`, and `success` are materially different outcomes. HARN-002 adds `human-escalation` as an explicit gate result rather than prose.

Existing CUC data-model style uses frozen standard-library dataclasses with explicit `to_dict()` methods. Reusing that style keeps these contracts independent of LangGraph, LangChain, Deep Agents, Langfuse, Pydantic, and runtime services.

## Design decisions

### 1. Pure standard-library domain package

`agent/harness/` contains no orchestration-framework imports. Contracts use frozen dataclasses, `Enum`, normalized tuples, JSON-safe scalar mappings, and explicit runtime validation.

Type annotations are not treated as runtime validation. `RunState` rejects wrong nested contract types before they can become a durable checkpoint, so malformed state fails at construction rather than later during serialization or replay. Deserialization preserves that same validation boundary: `from_dict()` validates mappings and passes collection-shaped values through to constructors instead of coercing malformed scalar strings into character tuples.

### 2. Durable identity is explicit but side-effect execution is out of scope

Every run has a `run_id`. Changes have `change_id`s and may carry `operation_id`s. IDs are non-empty opaque strings and are never generated implicitly by the contract layer.

Within one `RunState`, operation IDs must be unique across all changes. HARN-002 supplies stable identity for downstream idempotency logic; it does **not** claim exactly-once external effects. Persisting attempted/completed side-effect status, closing crash windows between an external write and checkpoint persistence, and retry-after-approval behavior belong to HARN-009/HARN-004.

### 3. State phases model orchestration progress

Normal phases are:

`research -> plan -> test-design -> implement -> verify -> review -> complete`

Exceptional phases are `blocked` and `awaiting-human`.

Retry loops are explicit:

- verification `test-failure` -> `implement`;
- verification `regression` -> `implement`;
- review `request-changes` -> `implement`;
- execution blockage -> `blocked` with the interrupted phase captured in `resume_phase`;
- human escalation -> `awaiting-human` with the interrupted phase captured in `resume_phase`.

Resume is allowed only to the captured non-exceptional phase. Durable snapshots also validate phase prerequisites, so serialized state cannot claim `review` or `complete` without the evidence required to reach those phases.

`implement` checkpoints are also constrained to reducer-reachable forms. Before the first change, `implement` represents the initial implementation phase. Once a change exists, an `implement` checkpoint must retain either current `test-failure`/`regression` evidence for the latest change or a `request-changes` review backed by the verified evidence for the inspected revision. A restored checkpoint therefore cannot skip verification merely by declaring `phase=implement` after recording a change.

### 4. Gate outcomes and review dispositions are separate

`GateOutcome`:

- `success`
- `test-failure`
- `regression`
- `blocked-execution`
- `human-escalation`

`ReviewDisposition`:

- `approve`
- `request-changes`
- `escalate`

Review results retain reviewer identity, independent review-context identity, findings/evidence, and the exact inspected head SHA. A review cannot be collapsed into a generic successful test.

### 5. Evidence is referential and revision-bound

Test/eval/review contracts carry concise summaries plus optional stable evidence references. Full logs and large artifacts remain outside `RunState`.

`TestResult` records both the proposed revision (`head_sha`) and the revision actually executed (`executed_sha`). Verification requires all current successful test/eval evidence to describe one proposed head **and one executed revision**. This prevents a false GREEN assembled from different GitHub synthetic merge commits for the same PR head.

### 6. Transition semantics are pure

`harness/state_machine.py` is a reducer from immutable `RunState` + typed event to immutable `RunState`. It performs no file, network, model, or GitHub side effects. Invalid event/phase combinations raise `InvalidTransition`.

Event wrappers validate their contract payloads at construction time, and `apply_event()` rejects non-`RunState` inputs before dereferencing state fields. Events cover research, plan, test declaration, change recording, test/eval evidence, verification, review, and resume. HARN-004 can wrap this reducer with LangGraph without importing LangGraph into the domain layer.

## Contract inventory

Public contracts:

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

## Hardened validation invariants

- required IDs/text are non-empty;
- tuple-like fields normalize to tuples; required collections reject empty values;
- scalar strings are rejected for tuple-like fields both in direct construction and through `from_dict()`; deserialization must not pre-coerce malformed scalars into character tuples;
- nested durable values must be instances of their declared contract types, while non-null nested serialized values must be mappings/collections of mappings as appropriate;
- `TaskSpec` requires at least one acceptance criterion;
- `PlanArtifact` requires at least one step;
- `TestIntent` requires a command, working directory, and explicit targeted/regression kind;
- `TestResult(success)` requires `head_sha`, `executed_sha`, exit code 0, zero failed tests, and at least one passed test;
- `TestResult(test-failure|regression)` requires execution identity, a non-zero exit code, and at least one failed test; an import/collection abort with no failed tests is therefore not representable as a test failure;
- `blocked-execution` and `human-escalation` cannot claim a completed test exit/count result;
- `EvalResult(success|test-failure|regression)` requires proposed and executed revision identity;
- eval metric names are unique and metrics are canonicalized by name for stable JSON round-trips;
- a blocking `ReviewFinding` cannot have informational severity;
- `ReviewResult(approve)` cannot contain blocking findings;
- `ReviewResult(request-changes)` requires at least one blocking finding;
- test results must refer to declared intents and known changes; eval results must refer to known changes;
- `implement` with no recorded change is the initial implementation state; with a recorded change it requires current `test-failure`/`regression` evidence or a `request-changes` review backed by complete verified evidence;
- `review`/`complete` checkpoints require fresh successful evidence for every declared test on the latest change;
- verified current test/eval evidence must agree on one proposed head and one executed revision;
- `complete` requires an approving review of the verified head;
- duplicate operation IDs within a change or across a run are invalid;
- explicit `to_dict()` / `from_dict()` gives a lossless JSON round-trip for valid state and rejects malformed serialized shapes rather than silently normalizing them into another valid type.

## TDD and adversarial-review record

1. Contract/state-machine tests were committed before `agent/harness` existed. Missing implementation was converted into ordinary assertion failures so pytest collection stayed healthy; the initial RED was classified as `test-failure`, not HARN-011 `blocked-execution`.
2. The first implementation reached full GREEN, then an independent adversarial review rejected it for mixed executed-revision evidence, forged phase snapshots, and weak executed-test evidence.
3. Regression tests reproduced those three findings before fixes were written.
4. A second independent review of the next GREEN found nested type confusion: malformed values could enter `RunState` and fail only later during serialization. A reviewer-driven RED reproduced that defect before runtime nested-type checks were added.
5. A third fresh review found malformed transition-event payloads, non-`RunState` reducer input, and scalar strings being accepted as tuple-like direct constructor values. Regression tests were committed first; the RED was `9 failed, 1069 passed, 8 subtests passed`. Event-boundary/runtime guards then restored full GREEN.
6. A fourth fresh review of that GREEN found two durable restore/reachability defects: `from_dict()` pre-coercion could bypass the scalar-string guard, and a forged `IMPLEMENT` checkpoint could contain a recorded change without any reducer-valid retry cause. Regression tests were committed first; the exact RED was `5 failed, 1072 passed, 15 subtests passed`. The fix made deserialization preserve constructor validation and constrained `IMPLEMENT` checkpoints to reducer-reachable forms. The resulting full suite was `1073 passed, 19 subtests passed` on code head `2e83b223cc9fd3709a0ef07b21bd3f8b7f0bde6b` / synthetic merge `9c031f457ec370c282caa19956e2394d2cc9d14f`.
7. Final acceptance requires a full fork-local suite on this documentation-final head plus a fresh review of the resulting diff. No generated `auto_parsing/**` data may be edited by this ticket.

## Deferred deliberately

- deterministic eval adapters — HARN-003;
- LangGraph state/checkpoint/runtime integration — HARN-004;
- GitHub side-effect capability enforcement, approval gates, and no-duplicate-write semantics — HARN-009;
- skill manifests/discovery — HARN-008;
- Langfuse/observability adapter — later ticket;
- persistence backend, UUID generation, clocks, filesystem/GitHub effects — runtime concerns outside these domain contracts.
