# HARN-010 — bounded development controller research

Date: 2026-09-11

## Goal

Build the development controller for issue-driven research -> plan -> tests/RED -> implementation -> verification/evals -> logically independent adversarial review. The controller must remain bounded, restartable, auditable, fork-safe, and separate from the corpus parsing graph.

## Existing contracts to reuse

### HARN-002 owns durable development phases

`agent/harness/contracts.py` and `state_machine.py` define the authoritative phase model:

`RESEARCH -> PLAN -> TEST_DESIGN -> IMPLEMENT -> VERIFY -> REVIEW -> COMPLETE`

with explicit `BLOCKED` and `AWAITING_HUMAN` exceptional states. HARN-010 should orchestrate these contracts, not introduce a parallel phase state machine.

The state machine already enforces research/plan/test ordering, unique change and operation IDs, fresh verification evidence bound to one head/executed revision, reviewer routing, and stale-review rejection.

### Strict TDD/RED needs one controller-level gate

HARN-002 moves to `IMPLEMENT` as soon as tests are declared. HARN-010 therefore adds a pre-change RED gate before invoking the implementation port. RED evidence records targeted intent, exact baseline SHA, real exit/failure counts, and evidence refs. Model text cannot substitute for executed RED evidence.

The original baseline RED is not repeated automatically for every revision; every candidate revision still requires fresh verification and fresh independent review.

### HARN-006 owns independent development review

`development_reviewer.py` provides allowlisted clean-context packets, exact base/head/executed revision binding, final-diff digest binding, fresh successful test/eval evidence, independent reviewer identity constraints, and HARN-002 routing. HARN-010 calls this boundary rather than exposing arbitrary controller scratch/history to the reviewer.

## GitHub write authority after HARN-023

### Research delta: HARN-023 superseded the initial HARN-009 integration choice

While HARN-010 was in progress, HARN-023 (#54) reconciled two independently merged HARN-009 implementations. The integration branch now has exactly one canonical development-controller GitHub mutation authority:

`agent/harness/github_effects.py`

`github_side_effects.py` was deliberately removed rather than retained as a compatibility write surface. HARN-010 must follow the new integration contract; restoring or continuing to import the retired module would recreate the duplicate-authority defect HARN-023 fixed.

The canonical boundary retains the strongest reviewed invariants from both earlier implementations:

- a closed `GitHubAction` vocabulary with no raw/generic transport;
- exact fork/upstream repository classification;
- task-local fork operation -> action permissions and separately declared upstream operation IDs;
- first-class `target_ref` bound into request identity;
- protected fork integration refs;
- explicit refs for branch/ref and integration/publication actions;
- trusted host-owned `HumanApprovalAuthority` for every upstream write and sensitive fork integration/publication action;
- durable pre-dispatch uncertainty checkpointing;
- immutable `GitHubEffectJournal` with receipts and uncertain-operation quarantine;
- deterministic trusted reconciliation without blind redispatch;
- post-write receipt-persistence failure quarantine;
- replay from a durable receipt without another provider write.

HARN-010 therefore accepts only `GitHubEffectGateway` as its mutation gateway and stores the current `GitHubEffectJournal` in the durable controller envelope. It never receives the underlying write adapter, reconciler, or approval-authority registration capability.

### Persistence consequence

The canonical gateway receives a synchronous `checkpoint(GitHubEffectJournal)` callback for every write. HARN-010 must implement that checkpoint by durably persisting a controller snapshot containing the updated journal before the gateway is allowed to dispatch the provider write.

After a successful write, the gateway checkpoints the completed receipt before returning. There is still a smaller crash window between that durable receipt checkpoint and the controller snapshot that advances its pending-operation index/write counter. Therefore a pending operation replayed from an already-durable receipt must still consume exactly one run-scoped GitHub-write budget slot when the controller consumes that pending operation after restart.

This is the independent-review blocker captured by `test_development_controller_adversarial.py`.

### Operation-ID reuse must be rejected before dispatch

HARN-002 rejects duplicate operation IDs when a `ChangeSet` is recorded, but GitHub effects are dispatched before `ChangeRecorded`. A later revision that reuses a prior operation ID could therefore replay/dispatch before HARN-002 rejects the new change. HARN-010 must preflight pending `change_id` and `operation_ids` against already recorded changes before any gateway call and terminate with an explicit policy reason.

### Production-host follow-up

Issue #53 remains the host-wiring follow-up. Its terminology predates HARN-023, but the invariant survives: production must durably restore the controller envelope/journal, the trusted human approval authority, and deterministic reconciliation capability without exposing authority registration or raw provider transport to model-controlled code. HARN-010 defines the controller/checkpoint boundary; a concrete production storage/provider host belongs to #53.

## Controller-owned durable envelope

HARN-002 `RunState` captures semantic workflow state but not controller budgets, baseline RED evidence, GitHub effect journal, pending implementation, or terminal controller reasons. HARN-010 therefore needs a serializable envelope containing at least:

- schema version and exact baseline revision;
- HARN-002 `RunState`;
- issue/provenance refs;
- pre-change RED evidence;
- pending implementation and pending operation index;
- canonical `GitHubEffectJournal`;
- revision, verification, review, GitHub-write, and cost counters;
- explicit terminal reason/code;
- chronological audit records.

The envelope is persisted after every accepted transition/counter mutation and from the gateway checkpoint before provider dispatch.

## Bounds and termination

Controller policy explicitly bounds implementation/revision attempts, verification executions, review attempts, GitHub writes, optional cost units, and the overall step loop. Limits are checked before expensive or side-effecting calls. Exhaustion becomes a persisted blocked terminal reason; there is no unbounded retry loop.

Repeated test failures and reviewer rejections route back to implementation only while budgets remain. `COMPLETE`, `BLOCKED`, and `AWAITING_HUMAN` are explicit auditable stop states.

## Revision identity and model-output distrust

Every implementation identifies its `ChangeSet` and proposed head. Verification ports report both proposed head and executed revision. HARN-002 and HARN-006 retain their stale/mixed evidence checks. RED evidence is separately bound to the exact baseline SHA.

The controller treats port/model output as proposed structured evidence; only executed test/eval results and the independent review contract can advance verification/review state.

## Human approval and orchestration framework

LangGraph interrupt/persistence documentation reinforces that side effects preceding an interrupt must be idempotent and durably checkpointed. HARN-010 correctness remains framework-neutral: approval/uncertainty/replay safety belongs below any future LangGraph or Deep Agents host, inside the canonical HARN-023 gateway plus durable controller checkpoint.

A future orchestration host may map `AWAITING_HUMAN` to its interrupt primitive, but HARN-010 does not require another graph/framework dependency.

## No-feature fallback and HARN-017 provenance

Fallback work is legal only when explicitly enabled and only for performance, stability, ergonomics, documentation, or edge cases. It must become a normal task and pass the same gates.

HARN-017 provenance is preserved as opaque structured refs. The controller does not invent parser scope; parser/eval gates are attached only when the issue requires them.

## Scope decision

Implement a small synchronous/framework-neutral bounded controller around HARN-002, HARN-006, and the post-HARN-023 canonical `github_effects.GitHubEffectGateway`. Do not add Deep Agents, a second LangGraph graph, or a second GitHub mutation authority. Unit tests use deterministic fake adapters/checkpoints only and perform no live upstream writes or notifications.
