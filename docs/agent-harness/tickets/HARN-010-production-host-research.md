# HARN-010 follow-up — durable production host research

Date: 2026-09-12
Issue: #53

## Goal

Provide the concrete trusted production composition that HARN-010 deliberately left outside the pure controller: durable restart state, durable host-owned human approval authority, and deterministic uncertain-effect reconciliation around the canonical HARN-023 GitHub effect gateway.

The host must preserve HARN-010's research -> plan -> RED -> implement -> verify -> independent review semantics while ensuring that a process crash cannot downgrade the GitHub safety boundary to in-memory state.

## Current architecture after HARN-023 and HARN-010

The original #53 terminology predates HARN-023. There is no longer a separate `OperationJournal` / `HumanApprovalRegistry` production path. The canonical contracts are:

- `DevelopmentControllerState` in `development_controller.py` is the complete controller envelope. It already serializes the exact base/current revision, HARN-002 core state, RED evidence, pending implementation/index, budgets, provenance/audit data, terminal reason, and `GitHubEffectJournal`.
- `GitHubEffectGateway` in `github_effects.py` is the sole write authority. It checkpoints an uncertain marker before provider dispatch, checkpoints a durable receipt after success, replays receipts without re-dispatch, and refuses to execute an already-quarantined uncertain operation.
- `GitHubEffectGateway.reconcile_uncertain(...)` is the canonical recovery path. It calls a trusted non-dispatch reconciler and converts a proven EXECUTED outcome into a receipt or a proven NOT_EXECUTED outcome into a cleared quarantine.
- `HumanApprovalAuthority` is deliberately host-owned. Approval-shaped `HumanApproval` values present in controller/journal state are inert unless the exact value is also registered in the trusted authority.
- HARN-010 accepts a persistence callback but cannot prove that an arbitrary callback is durable. Adding a `durable=True` flag to the controller would be security theatre rather than an enforceable guarantee.

No reusable production runtime/host or atomic store currently exists under `agent/harness/`.

## Boundary decision

Implement a separate trusted host/bootstrap layer rather than changing the pure controller's authority model.

The intended composition is:

`AtomicHostStateStore`
-> `ProductionHostEnvelope(controller_state, trusted_approvals)`
-> restored `HumanApprovalAuthority`
-> canonical `GitHubEffectGateway(task_policy, trusted_adapter, approval_authority, trusted_reconciler)`
-> `BoundedDevelopmentController(..., persist=host checkpoint)`
-> trusted host run/resume/reconcile methods.

The controller and model-facing ports never receive the store, raw write adapter, reconciler, or approval-authority registration capability.

## Durable host envelope

The persisted unit must contain both controller state and the approvals that constitute trusted host authority. Keeping approvals in a second independent file would create an avoidable cross-file transaction problem: after a crash one file could advance while the other remained stale.

The envelope therefore contains:

- host schema version;
- optional `DevelopmentControllerState` payload;
- ordered trusted `HumanApproval` records.

Validation is strict and fail-closed:

- wrong/missing schema is rejected;
- malformed JSON or non-object payload is rejected;
- controller state is reconstructed through `DevelopmentControllerState.from_dict` so HARN-010 invariants remain authoritative;
- approvals are reconstructed through `HumanApproval.from_dict`;
- approval IDs and operation IDs must remain unique through the authority/journal contracts;
- a persisted trusted approval does not silently rewrite a different request digest.

No best-effort partial recovery is appropriate for a security boundary. A truncated/corrupt envelope blocks startup and requires operator repair/recovery from a known-good snapshot.

## Atomic file durability

The initial concrete store should be a small stdlib JSON file store because the state is compact and HARN-010 already produces JSON-safe dictionaries. Introducing SQLite or an orchestration framework is unnecessary for a single-envelope transactional write.

Save sequence:

1. create a temporary file in the same directory as the target;
2. serialize canonical JSON bytes;
3. flush and `fsync` the temporary file;
4. atomically replace the target with `os.replace`;
5. `fsync` the containing directory where the platform supports directory fsync;
6. only then report persistence success.

A failure before replace must leave the prior valid target untouched. A failure after replace is conservatively surfaced to the caller; the new target may already be durable, and the next startup must parse the actual on-disk envelope rather than assume the old one.

The store must require an explicit non-empty path. Production construction must not silently substitute an in-memory/no-op callback.

## Approval ordering

A trusted human approval is authority, not ordinary model state. The crash-safe ordering is therefore:

1. validate the approval against the currently pending exact operation ID and request digest;
2. create a new host envelope containing the approval;
3. durably persist that envelope;
4. only after persistence succeeds, register the exact approval in the live `HumanApprovalAuthority`;
5. resume controller execution.

If persistence fails, live authority must remain unchanged. If the process crashes after persistence but before live registration, restart restores the approval and registers it before constructing/using the gateway. This ordering prevents volatile-only approval authority.

Approval-shaped data that appears only in `GitHubEffectJournal` remains insufficient. Restoration registers only approvals from the trusted host envelope's authority records.

## Restart and checkpoint sequencing

The host-owned controller persistence callback replaces only the controller-state portion of the current envelope while preserving trusted approvals, then atomically saves the whole envelope.

This composes correctly with HARN-023's gateway ordering:

- before provider dispatch, the gateway's uncertainty checkpoint calls the controller persistence callback, which atomically writes an envelope containing the updated uncertain journal;
- after provider success, the receipt checkpoint atomically writes an envelope containing the receipt;
- if the process dies before HARN-010 advances its pending index/write counter, restart sees the durable receipt and HARN-010's existing replay logic consumes it without another provider call while still charging the write budget exactly once.

A crash after durable uncertainty but before the provider call is intentionally indistinguishable from a crash after an ambiguous provider call. Both restore as quarantined and require trusted reconciliation rather than blind dispatch.

## Deterministic reconciliation

Reconciliation remains a `GitHubEffectGateway` responsibility. The host supplies a trusted reconciler dependency and exposes a narrow trusted recovery method that:

- requires a currently pending operation whose exact request identity matches the quarantined journal entry;
- invokes `gateway.reconcile_uncertain`, never the write adapter;
- persists every journal change through the same durable envelope callback;
- on EXECUTED, retains the resulting durable receipt for normal HARN-010 replay;
- on NOT_EXECUTED, clears the quarantine so a later ordinary controller step may dispatch under normal policy/budget rules.

No generic `execute_raw`, endpoint URL, adapter handle, or arbitrary reconciliation mutation is added.

## Host surface and privilege separation

The production host is trusted application code, not a model tool. Its constructor owns privileged dependencies. The `DevelopmentControllerPorts` object remains the only model/provider extension surface used by HARN-010 and receives none of those dependencies.

Privileged objects should be stored as private implementation fields. Public methods should operate on structured HARN contracts (start/load/step/resume with a human approval/reconcile current uncertain operation), not expose the underlying adapter, reconciler, approval authority, or store mutation callback.

The host must continue to use only `GitHubEffectGateway`; no second mutation gateway or compatibility path is introduced.

## TDD scenarios

The first RED suite should prove the missing production boundary rather than restate existing HARN-010 unit behavior:

1. durable envelope/store round-trip restores controller state and trusted approvals;
2. wrong schema, malformed JSON, malformed controller payload, and malformed approval payload fail closed;
3. simulated write/replace failure leaves the previous valid snapshot readable;
4. bootstrap with no explicit durable store/path is rejected;
5. restart rebuilds trusted approval authority from host approvals;
6. approval-shaped journal data without a trusted host approval remains unauthorized;
7. trusted approval persistence happens before live authority registration/resume;
8. pre-dispatch uncertainty survives restart;
9. trusted reconciliation for EXECUTED and NOT_EXECUTED never calls the write adapter;
10. receipt checkpoint survives restart before controller pending-index persistence and remains provider-idempotent;
11. request identity mismatch after restart fails closed;
12. public/controller-port surfaces do not expose raw adapter/reconciler/authority registration capabilities.

## Scope exclusions

- No new LangGraph/Deep Agents persistence dependency.
- No database/service deployment layer.
- No changes to corpus parsing or evaluation semantics.
- No replacement for HARN-023 authorization/reconciliation.
- No automatic upstream write or notification path.
- No actual GitHub network I/O in unit tests; fake adapters/reconcilers only.

## Research conclusion

#53 should implement one small trusted production-host module plus adversarial tests and documentation. The security property comes from composing already-reviewed HARN-010/HARN-023 contracts with a concrete atomic persistence boundary and durable trusted approval records, not from creating another side-effect subsystem.