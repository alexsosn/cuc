# HARN-010 — bounded development controller implementation plan

## Architecture

Implement `agent/harness/development_controller.py` as a framework-neutral deterministic orchestration layer around HARN-002 state, HARN-006 independent review, and the canonical post-HARN-023 `github_effects.GitHubEffectGateway`.

HARN-010 does not replace `RunState`/`apply_event`; it owns controller-specific RED evidence, budgets, persistence, pending side-effect sequencing, and port invocation.

## HARN-023 integration delta

The original plan named `github_side_effects.GuardedGitHubSideEffects`. HARN-023 (#54) subsequently removed that competing authority and made `github_effects.GitHubEffectGateway` canonical. The implementation must migrate rather than reintroduce the retired module.

Controller GitHub integration therefore uses:

- `GitHubEffectRequest` as the structured pending operation;
- `GitHubEffectGateway` as the only write authority;
- `GitHubEffectJournal` embedded in durable controller state;
- gateway `checkpoint(journal)` calls mapped to synchronous controller-state persistence;
- trusted `HumanApproval` values carried in state only as data, with authority remaining host-owned inside the gateway;
- gateway uncertainty/reconciliation semantics rather than a controller-side raw adapter.

No controller import or runtime path may restore `github_side_effects.py` or expose `GitHubEffectGateway`'s underlying adapter/approval authority.

## Durable controller envelope

`DevelopmentControllerState` contains:

- schema version;
- exact baseline SHA;
- embedded HARN-002 `RunState`;
- issue/provenance refs;
- pre-change `RedGateEvidence`;
- pending `ImplementationResult` and operation index;
- canonical `GitHubEffectJournal`;
- implementation/revision, verification, review, GitHub-write, and cost counters;
- chronological audit events;
- optional terminal code/reason.

It supports strict `to_dict` / `from_dict`. The host persistence callback is invoked after every accepted controller transition and by every gateway journal checkpoint.

## Policy and ports

`DevelopmentControllerPolicy` bounds:

- `max_revision_attempts`;
- `max_verification_executions`;
- `max_review_attempts`;
- `max_github_writes`;
- optional `max_cost_units`;
- `require_red`;
- `allow_no_feature_fallback`;
- production/test configuration as needed by the host contract.

Inject deterministic ports for research, planning, test declaration, baseline RED execution, implementation, post-change tests, evals, final diff, HARN-006 independent reviewer, canonical GitHub gateway, and controller-state persistence.

## Implementation result

An implementation port returns:

- HARN-002 `ChangeSet`;
- exact proposed `head_sha`;
- zero or more canonical `GitHubEffectRequest` write requests;
- cost units;
- evidence refs.

The ordered request IDs must exactly equal `ChangeSet.operation_ids`. Requests used in the side-effect sequence must be write actions; reads belong in research/implementation ports, not the mutation journal.

Before any gateway call, the controller checks that the pending `change_id` and every pending operation ID are new relative to already recorded changes. Reuse terminates with an explicit policy block before replay/provider dispatch.

## TDD/RED semantics

After HARN-002 reaches `IMPLEMENT`, HARN-010 still blocks the first implementation call until every required targeted RED probe has executed against the exact baseline SHA and produced a real failing execution (`TEST_FAILURE`, non-zero exit, failed_tests > 0). Model assertions are not execution evidence.

Reviewer/test-driven revisions preserve the original baseline RED but require fresh post-change verification and fresh clean-context review on each new head.

## Canonical GitHub checkpoint/replay semantics

For one pending write request:

1. Check run-scoped GitHub-write budget and operation/change uniqueness before gateway dispatch.
2. Call `GitHubEffectGateway.execute_write(request, journal, checkpoint=...)`.
3. The checkpoint callback persists a controller snapshot containing the supplied journal synchronously.
4. The gateway checkpoints uncertainty before provider dispatch and receipt completion after successful provider mutation.
5. When the controller successfully consumes the pending request, increment `github_writes` exactly once and advance the pending-operation index.
6. If restart restores a snapshot whose journal already contains the pending request receipt but whose index/counter are stale, gateway replay performs no provider write; consuming that still-pending replay nevertheless increments the run-scoped write counter exactly once before the controller can attempt another request.
7. A quarantined/unknown outcome is preserved in the durable journal and cannot be blindly redispatched.

This closes the crash-window budget bypass found by independent review.

## Human approval

For upstream or sensitive fork writes, the canonical gateway raises `HumanApprovalRequired` before provider dispatch. Controller state becomes `AWAITING_HUMAN` while preserving the exact pending request.

Resume may attach a structured `HumanApproval` to the controller journal, but that value is not authority by itself. The trusted host must separately register the exact approval in the gateway's `HumanApprovalAuthority`; otherwise the gateway pauses again. HARN-010 never receives authority-registration capability.

Concrete durable authority/reconciler/provider host wiring remains tracked by #53.

## Verification and review

`VERIFY` runs one missing test/eval action at a time and binds each result to the current proposed/executed SHA. Real failures route through HARN-002 to bounded implementation. Only after all required current evidence is successful may `VerificationPassed` advance to `REVIEW`.

`REVIEW` builds HARN-006's allowlisted context from exact verified evidence and final diff. APPROVE completes; REQUEST_CHANGES returns to bounded implementation; ESCALATE pauses for human. Every new candidate requires fresh verification and a fresh review packet.

## Budgets and safe termination

Before relevant calls, enforce revision, verification, review, GitHub-write, and cost limits. `run_until_stop()` has a finite step ceiling. Stop reasons are persisted codes/data, including budget exhaustion, blocked execution, policy block, needs-human, and complete.

Programming/invariant violations fail closed; externally meaningful stop conditions should be represented as explicit persisted controller states where practical.

## Scenario gates

Required scenarios include:

1. happy path ordering and real RED before implementation;
2. model-only/false/stale RED cannot unlock implementation;
3. repeated test failure stops at revision budget;
4. reviewer rejection causes bounded new implementation/verification/review;
5. blocked dependency has explicit reason;
6. stale head/executed verification evidence cannot advance;
7. cost/revision/verification/review/GitHub-write budgets stop before excess call;
8. human approval pause/resume preserves exact operation identity;
9. serialization/restart does not repeat completed research/plan/RED;
10. durable receipt replay cannot duplicate provider mutation **or** bypass GitHub-write budget;
11. reused operation/change IDs are blocked before gateway/provider dispatch;
12. every terminal state/reason and issue provenance round-trip;
13. no-feature fallback is policy gated;
14. source/runtime uses only the canonical HARN-023 GitHub authority.

## Review strategy

After exact-head full-suite GREEN, conduct a logically independent review from issue #11, final diff, HARN-023 canonical contract, and CI evidence. Attack termination/budget off-by-one errors, fabricated/stale evidence, restart crash windows, duplicate/reused operation IDs, forged approval-shaped data, uncertain outcomes, alternative GitHub authority paths, provenance drift, and fallback scope expansion.

Every confirmed blocker gets a RED regression before its fix, followed by full suite and a fresh clean-context re-review.
