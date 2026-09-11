# HARN-010 follow-up — durable production host implementation plan

Issue: #53

## Architecture

Add `agent/harness/production_host.py` as the trusted composition layer around the already-merged HARN-010 controller and the canonical HARN-023 GitHub effect gateway.

The module must not introduce a second mutation authority. It owns persistence/bootstrap only:

- `ProductionHostEnvelope`: strict serializable host state containing optional `DevelopmentControllerState` plus trusted `HumanApproval` records;
- `AtomicHostStateStore`: explicit-path JSON store using same-directory temporary file, file flush/fsync, atomic `os.replace`, and directory fsync where supported;
- `ProductionDevelopmentHost`: restores the envelope, reconstructs `HumanApprovalAuthority`, creates one `GitHubEffectGateway` from trusted adapter/reconciler dependencies, creates one `BoundedDevelopmentController`, and offers narrow trusted start/load/step/resume/reconcile operations.

## Envelope contract

Schema version starts at 1. The envelope serializes:

```text
{
  "schema_version": 1,
  "controller_state": <DevelopmentControllerState | null>,
  "trusted_approvals": [<HumanApproval>, ...]
}
```

`from_dict` is strict:

- payload must be an object;
- schema must equal the supported version;
- controller state must parse through `DevelopmentControllerState.from_dict`;
- approval records must parse through `HumanApproval.from_dict`;
- approval IDs and operation IDs must be unique;
- malformed, truncated, or semantically invalid payloads raise and do not produce a partial runtime state.

The host envelope is the transaction boundary so controller state and trusted approvals cannot diverge across independent files.

## Atomic store contract

`AtomicHostStateStore(path)` requires an explicit filesystem path. It provides:

- `load() -> ProductionHostEnvelope | None`: missing file means no prior run; existing invalid file raises;
- `save(envelope) -> None`: write canonical UTF-8 JSON to a same-directory temp file, flush/fsync, atomically replace target, then fsync parent directory where supported.

The implementation must clean up an uncommitted temp file on failure. A failure before `os.replace` must preserve the previous valid target. There is no in-memory fallback in the production bootstrap.

For directory fsync portability, unsupported directory-open/fsync errors may be treated as platform limitations only when they are the known unsupported class; arbitrary persistence failures remain visible.

## Host construction

`ProductionDevelopmentHost` receives only trusted application dependencies:

- explicit `AtomicHostStateStore`;
- `DevelopmentControllerPolicy`;
- `DevelopmentControllerPorts`;
- `IndependentReviewer`;
- `GitHubTaskPolicy`;
- write adapter implementing `execute(request)`;
- reconciler implementing `reconcile(request)`;
- controller policy refs/review rubric/implementer identity.

Construction order:

1. load the durable envelope;
2. instantiate a new `HumanApprovalAuthority`;
3. register only `trusted_approvals` from the host envelope;
4. construct canonical `GitHubEffectGateway` with that authority and trusted reconciler;
5. construct `BoundedDevelopmentController` with a persistence callback that atomically replaces only `controller_state` inside the current host envelope and saves the whole envelope.

No raw adapter/reconciler/authority/store object is passed into `DevelopmentControllerPorts`.

## State ownership

The host keeps the most recently durable envelope as its trusted state. Controller methods remain stateless with respect to the current run, so host operations explicitly load/use/update the current `DevelopmentControllerState`.

Public narrow operations should include the minimum needed production lifecycle:

- `state` or `load_state()` returning the reconstructed controller state as data;
- `start(...)` delegating to controller start and relying on the durable callback;
- `step()` operating on the current controller state;
- `run_until_stop(...)` if needed as a convenience over the controller's bounded method;
- `resume(approval=...)` for trusted human resume;
- `reconcile_uncertain()` for the current pending quarantined request.

Do not expose the underlying gateway, adapter, reconciler, approval authority, or a generic store-mutation API.

## Trusted approval transaction

A host resume with human approval follows a strict transaction:

1. require current controller state and pending operation;
2. validate `approval.operation_id` equals the pending operation ID;
3. validate `approval.request_sha256` equals the exact pending request digest;
4. reject approval-ID or operation-ID rebinding;
5. create an envelope including the approval;
6. save it atomically;
7. only after save succeeds, register approval in live `HumanApprovalAuthority`;
8. delegate to `BoundedDevelopmentController.resume(..., approval=approval)`.

If step 6 fails, the authority remains unchanged and execution cannot consume a volatile approval. If the process dies between 6 and 7, restart restores and registers the persisted approval.

Journal approval-shaped values alone must never be imported into authority.

## Reconciliation transaction

`reconcile_uncertain()`:

1. require current controller state and pending implementation/request;
2. require that exact operation be present in `github_journal.uncertain_requests`;
3. call canonical `GitHubEffectGateway.reconcile_uncertain(request, journal, checkpoint=...)`;
4. checkpoint callback atomically saves the controller snapshot with the supplied journal while preserving all other state fields and trusted approvals;
5. return the updated controller state.

EXECUTED produces a durable receipt; NOT_EXECUTED removes quarantine. Reconciliation never calls the write adapter. The normal subsequent controller step decides whether to consume the receipt or dispatch a NOT_EXECUTED request under ordinary budget/policy rules.

## RED phase

Before implementing `production_host.py`, add `agent/tests/test_production_host.py` covering at least:

1. module/API absence as initial RED;
2. envelope/store round-trip;
3. wrong schema and corrupt/truncated JSON rejection;
4. prior snapshot survives simulated pre-replace failure;
5. explicit durable store required;
6. approval persists before authority becomes usable;
7. restart restores authority from host approvals only;
8. journal-only approval remains untrusted;
9. uncertainty checkpoint survives restart;
10. reconciliation EXECUTED/NOT_EXECUTED uses reconciler only;
11. receipt replay after restart does not duplicate write;
12. exact request identity mismatch fails closed;
13. privileged dependencies are not exposed through model-facing ports/public data API.

The first committed test state must fail for the intended missing production-host capability, not because of a malformed fixture.

## Implementation sequence

1. Implement strict envelope and atomic store until storage tests pass.
2. Implement bootstrap/restore with private trusted dependencies.
3. Implement durable approval transaction.
4. Implement uncertain reconciliation wrapper.
5. Exercise HARN-010/HARN-023 crash windows through the host.
6. Run focused tests, then the complete `agent` test suite and Langfuse gate on the exact head.
7. Open a draft PR to `agent-harness-safety` only after meaningful implementation exists.

## Adversarial review

After full exact-head GREEN, perform a logically independent review from issue #53, the final diff, and the HARN-010/HARN-023 contracts. Attack:

- temp-file/replace/fsync crash windows;
- corrupt or schema-downgraded state acceptance;
- approval persisted after rather than before live registration;
- forged journal approval promoted into authority;
- approval ID/digest/operation rebinding;
- uncertain request blind redispatch after restart;
- reconciler accidentally calling or sharing the write adapter path;
- receipt replay duplicate mutation;
- state/request identity drift;
- model-facing access to adapter/reconciler/authority/store;
- in-memory production fallback;
- accidental second GitHub mutation authority;
- upstream writes/notifications.

Every confirmed blocker becomes a new RED regression before any fix, followed by full exact-head GREEN and a fresh independent re-review.

## Completion

#53 is complete only when the trusted host path has real durable storage/restart tests, exact-head full CI is GREEN, independent review reports no blockers, and the reviewed PR is merged into `agent-harness-safety`. Unit/offline HARN-010 construction remains available for tests, but the documented production path must use this durable host.