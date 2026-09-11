# HARN-010 — bounded development controller research

Date: 2026-09-11

## Goal

Build the development controller for issue-driven research -> plan -> tests/RED -> implementation -> verification/evals -> logically independent adversarial review. The controller must remain bounded, restartable, auditable, fork-safe, and separate from the corpus parsing graph.

## Existing contracts to reuse

### HARN-002 owns durable development phases

`agent/harness/contracts.py` and `state_machine.py` already define the authoritative phase model:

`RESEARCH -> PLAN -> TEST_DESIGN -> IMPLEMENT -> VERIFY -> REVIEW -> COMPLETE`

with explicit `BLOCKED` and `AWAITING_HUMAN` exceptional states and deterministic `ResumeRequested` semantics.

The state machine already enforces:

- research before plan;
- plan before test declaration;
- declared tests before implementation;
- unique change IDs and operation IDs;
- fresh verification evidence bound to one proposed head and one executed revision;
- failed tests/regressions route back to implementation;
- reviewer `REQUEST_CHANGES` routes back to implementation;
- reviewer escalation routes to `AWAITING_HUMAN`;
- stale review revisions are rejected.

HARN-010 should orchestrate these contracts, not introduce a parallel phase state machine.

### Strict TDD/RED needs one controller-level gate

HARN-002 transitions from `TEST_DESIGN` to `IMPLEMENT` immediately when tests are declared. That phase means implementation is *permitted*, not that code has already changed. Therefore HARN-010 can preserve HARN-002 unchanged and insert a controller-level pre-change RED gate before calling the implementation port.

The RED artifact should record the declared targeted test(s), baseline revision, exit/failure evidence, and evidence refs. The controller must refuse the first implementation attempt until:

- every required targeted RED probe has executed against the pre-change revision; and
- at least one targeted test demonstrates the missing/broken behavior with a real non-zero test result.

A model assertion such as “this should fail” is not RED evidence.

Subsequent review-driven revisions do not require re-proving the original baseline RED; they require fresh verification and fresh review on the new head.

### HARN-006 owns independent development review

`development_reviewer.py` already provides:

- allowlisted `DevelopmentReviewContext` packets;
- exact base/head/executed revision binding;
- diff digest binding;
- only successful fresh test/eval evidence in the review packet;
- structured review findings and disposition;
- independent reviewer identity constraints;
- routing back through HARN-002 `ReviewRecorded`.

HARN-010 should call this boundary rather than passing arbitrary controller/model history to the reviewer.

### HARN-009 owns GitHub write authority

The authoritative production write capability is `github_side_effects.GuardedGitHubSideEffects`:

- closed typed operations;
- fork/upstream classification;
- exact trusted human approval registry;
- durable PREPARED/COMPLETED journal protocol;
- reconciliation before retry after uncertain outcomes;
- operation fingerprints and replay conflict detection;
- explicit refs for sensitive integration/publication actions.

Issue #51 records the production wiring constraint: HARN-010 must provide atomic durable journal persistence, deterministic provider reconciliation, and host-owned approval-registry persistence; model-controlled code must not receive approval registration or raw provider transport.

### Legacy `github_effects.py`

The repository also contains an older `GitHubEffectGateway`. Its tests show useful historical invariants: explicit task policy, operation IDs, pre-dispatch checkpoint, ambiguous-outcome blocking, and no raw generic GitHub action. It is not obviously unsafe by itself.

However, it is a *second effect authority* with older approval/journal semantics. HARN-010 must not expose both gateways. Production controller writes should flow only through `GuardedGitHubSideEffects`. The legacy module may remain for compatibility/tests, but it is not a controller port and must not be imported by the new controller implementation.

## Controller-owned durable envelope

HARN-002 `RunState` captures semantic workflow state but not controller budgets, baseline RED evidence, or terminal controller reasons. HARN-010 therefore needs a small serializable envelope around `RunState`, containing at least:

- schema version and controller run ID;
- exact base revision;
- HARN-002 `RunState`;
- pre-change RED evidence;
- revision/implementation attempt count;
- test/eval execution count;
- review count;
- GitHub write count or side-effect operation references;
- accumulated cost units when supplied by ports;
- explicit terminal reason/code;
- audit records/evidence refs sufficient to reconstruct decisions.

The envelope must serialize deterministically and be persisted after every semantic transition/counter update. A restart loads the envelope and resumes from the HARN-002 phase plus controller gate state; it must not replay completed GitHub effects because HARN-009 separately owns effect idempotency.

## Bounds and termination

Controller policy should make limits explicit and non-negative/positive as appropriate:

- maximum implementation/revision attempts;
- maximum test/eval executions;
- maximum review attempts;
- maximum GitHub writes;
- optional maximum cost units;
- optional no-feature fallback permission.

The controller checks the relevant budget **before** invoking an expensive or side-effecting port. Exhaustion is terminal/blocked with an explicit reason; there is no implicit unbounded while-loop.

A reviewer rejection consumes a revision attempt and can return to implementation only while budget remains. Repeated test failure likewise consumes bounded attempts. Safe termination includes `COMPLETE`, `BLOCKED`, and `AWAITING_HUMAN`, each with an auditable reason.

## Failure classification

Normalize external port outcomes into the existing HARN-002 categories where possible:

- successful execution;
- test failure;
- regression;
- blocked execution/dependency;
- human escalation/approval required;
- policy/budget block.

Programming errors/invariant violations should fail closed rather than be converted into success-like model text.

## Revision identity

Every implementation result must identify the resulting change and proposed head. Verification ports must report both proposed head and actually executed revision. HARN-002 already rejects mixed/stale evidence at `VerificationPassed`; HARN-006 repeats the binding for review context. HARN-010 should not weaken either check.

The pre-change RED evidence is separately bound to the controller's exact baseline revision, so a RED run from another branch/revision cannot unlock implementation.

## Human approval and interrupts

Current LangGraph documentation (checked 2026-09-11) confirms that interrupts use a checkpointer/thread ID and that resuming an interrupt restarts the interrupted node from the beginning rather than continuing at the exact source line. The docs explicitly require idempotent side effects before interrupts and recommend durable checkpointers in production:

- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://docs.langchain.com/oss/python/langgraph/persistence

This reinforces the architecture already chosen in HARN-009: GitHub idempotency/approval lives below any orchestration framework. HARN-010 core semantics should stay framework-neutral. A future LangGraph host can map `AWAITING_HUMAN` to `interrupt()` and persist the controller envelope with a durable checkpointer, but correctness cannot depend on LangGraph replay behavior.

## No-feature fallback

The issue requires fallback work only when explicitly enabled by controller policy. Therefore the controller must not silently invent feature scope after an issue is complete/blocked. When enabled, a fallback selector may choose only from:

- performance;
- stability;
- ergonomics;
- documentation;
- edge cases.

The fallback itself should produce a normal TaskSpec/issue provenance and pass the same research-plan-RED-implementation-verification-review gates.

## HARN-017 provenance compatibility

HARN-010 should treat issue provenance as structured opaque refs attached to the task/run envelope. It must preserve corpus/run/expert references supplied by future HARN-017 issues rather than reinterpret them or manufacture parser scope. Parser/eval gates are attachable ports when the issue explicitly requires them.

## Scope decision

Implement a small synchronous/framework-neutral bounded controller around HARN-002, HARN-006, and HARN-009. Do not add Deep Agents or a second LangGraph graph. Do not wire live GitHub credentials in unit tests. The implementation should make host ports explicit and testable with deterministic fakes.

The production-host contract must make durable persistence mandatory for GitHub side effects; default/in-memory HARN-009 journal mode is allowed only in explicit offline/test configuration.
