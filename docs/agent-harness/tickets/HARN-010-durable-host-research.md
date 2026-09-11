# HARN-010 follow-up — Durable trusted host research

Issue: #53

Baseline: `4fbe56c75a6c7b5f034fd07038fdde371f0d637e`

## Question

How should production code compose the merged HARN-010 controller with the canonical HARN-023 GitHub effect boundary so controller state, trusted human approvals, and uncertain GitHub operations survive process restart without introducing a second mutation authority?

## Existing contracts inspected

### `DevelopmentControllerState` is already the complete controller snapshot

`agent/harness/development_controller.py` already serializes the framework-neutral controller state, including:

- exact base/current head identity;
- HARN-002 core `RunState`;
- RED evidence;
- pending implementation and pending operation index;
- budget counters/cost;
- provenance/audit events;
- terminal reason;
- canonical `GitHubEffectJournal`.

`BoundedDevelopmentController` deliberately accepts only a `persist(dict)` callback. The controller does not claim that callback is durable. Its GitHub journal checkpoint path uses the same callback before/after canonical gateway transitions.

**Implication:** do not add another controller state model or a `durable=True` flag. Durability belongs to a trusted host/store.

### `GitHubEffectGateway` must remain the only mutation authority

`agent/harness/github_effects.py` already owns:

- repository/action/operation authorization;
- exact request digest/ref binding;
- upstream/fork safety;
- uncertainty checkpoint before write dispatch;
- receipt checkpoint after successful dispatch;
- replay/idempotency;
- trusted reconciliation through `reconcile_uncertain(...)`;
- registered human approval checks for sensitive actions.

The gateway constructor receives the write adapter, optional `HumanApprovalAuthority`, and optional reconciler. Those dependencies are intentionally private to the gateway.

**Implication:** the production host composes the existing gateway; it must not expose the raw adapter/reconciler or create a second journal/executor.

### Human approval authority is intentionally volatile and host-owned

`HumanApprovalAuthority` contains an in-memory private registry and exposes `register(...)` / `resolve(...)`. Approval-shaped values in `GitHubEffectJournal` are insufficient by themselves.

Controller `resume(..., approval=...)` can persist an approval into the journal, but the gateway will still reject a sensitive effect unless the same exact approval has been registered by the trusted authority.

**Implication:** the durable host must persist trusted approval records separately from controller/journal state, restore the authority from those records after restart, and persist a newly issued approval *before* live authority registration.

## Durable envelope

Use one versioned host envelope, not multiple independently committed files:

```text
HostEnvelope v1
├─ controller_state: DevelopmentControllerState | null
└─ trusted_approvals: [HumanApproval ...]
```

Properties:

- strict top-level schema/version; unknown/missing fields fail closed;
- `DevelopmentControllerState.from_dict(...)` remains the canonical controller validator;
- `HumanApproval.from_dict(...)` remains the canonical approval validator;
- approval IDs must be unique and an ID cannot bind different content;
- canonical JSON encoding (`sort_keys`, compact separators, UTF-8, no NaN) gives deterministic persisted bytes;
- no raw adapter/reconciler/credentials are serializable members.

A single envelope matters because approval registration and controller/journal state must have an unambiguous durable ordering across crashes.

## Atomic filesystem store

Target a stdlib-only store so HARN-010 does not gain a persistence framework dependency.

Preferred write algorithm:

1. serialize/validate the complete envelope before touching the current snapshot;
2. create a secure temporary file **in the destination directory** (`tempfile.mkstemp(dir=...)`);
3. write bytes, flush, `os.fsync(file_fd)`;
4. close the temp file;
5. `os.replace(temp, destination)` on the same filesystem;
6. fsync the containing directory where the platform supports directory descriptors;
7. clean up an un-replaced temp file on failure.

Python documents `mkstemp()` as race-safe and allows choosing the directory. Python documents successful same-filesystem replacement/rename as atomic on POSIX and recommends `replace()` for overwrite semantics.

References checked 2026-09-11:

- https://docs.python.org/3/library/tempfile.html#tempfile.mkstemp
- https://docs.python.org/3/library/os.html#os.replace
- https://docs.python.org/3/library/os.html#os.fsync

Failure semantics:

- serialization/temp-write/fsync/replace failure before replacement leaves the previous destination untouched;
- readers never observe a partially written destination;
- directory fsync is attempted after replacement where supported; unsupported directory fsync is a platform capability limitation, not a reason to fall back to non-atomic overwrite;
- malformed/truncated existing JSON is an error, never interpreted as an empty/new run.

## Trusted host composition

Recommended production object graph:

```text
AtomicJsonHostStore
        │ load HostEnvelope
        ▼
HumanApprovalAuthority  <── restore persisted approvals
        │
trusted write adapter ──┐
trusted reconciler ─────┼─> GitHubEffectGateway
GitHubTaskPolicy ───────┘
        │
        ▼
BoundedDevelopmentController
  persist callback ───────> host persists envelope(controller_state=..., approvals=current trusted set)
```

The host, not model-facing ports, owns all constructors above.

### Approval ordering

For a human-issued approval:

1. validate exact pending operation ID + request digest against controller state;
2. construct new envelope with the approval appended;
3. persist that envelope atomically;
4. only after successful persistence, call `HumanApprovalAuthority.register(approval)`;
5. call controller `resume(..., approval=approval)` so the journal records the same approval;
6. persist controller progress via the host callback.

A crash after step 3 but before step 4 is safe: restart restores the persisted authority. A crash before step 3 cannot authorize the write because the live authority was never changed.

### Controller persistence callback

The controller callback receives a serialized controller dict. The host must:

- parse it back through `DevelopmentControllerState.from_dict(...)` before accepting it;
- replace only `controller_state` in the current host envelope;
- retain trusted approval records unchanged;
- atomically persist the whole envelope.

This keeps controller code framework-neutral while preventing a model-provided dict from becoming trusted state without canonical validation.

## Restart and uncertainty cases

### Crash after uncertainty checkpoint, before provider call

The envelope contains the uncertain request in `GitHubEffectJournal`. After restart the host restores controller state and gateway. It must not call the write adapter. Recovery routes through `GitHubEffectGateway.reconcile_uncertain(...)` using the trusted reconciler.

### Crash after provider success + receipt checkpoint, before controller pending-index snapshot

The durable journal already contains the receipt but controller pending index may still point at the same operation. On restart the controller can replay that operation through the gateway; receipt lookup/idempotency returns the prior receipt and must not call the provider again.

### Approval-shaped journal state without persisted authority

Must remain unauthorized after restart. Only approvals in the trusted host envelope repopulate `HumanApprovalAuthority`.

## Public surface

A production host should expose operations such as:

- `load_state()` / `start(...)` / `step()` / `run_bounded(...)`;
- `register_human_approval(...)`;
- `reconcile_uncertain(...)`;
- read-only inspection of current controller/envelope state.

It must **not** expose:

- the raw GitHub write adapter;
- the reconciler;
- `HumanApprovalAuthority.register`;
- arbitrary store mutation;
- a generic GitHub execute method.

## Non-goals

- remote/distributed database selection;
- cross-process locking/concurrent writers;
- changing HARN-010 phase semantics;
- changing HARN-023 policy or request vocabulary;
- persisting provider secrets/credentials;
- implementing GitHub network I/O inside harness modules.

## Risk findings to drive TDD

1. malformed or wrong-schema envelope silently treated as empty;
2. non-atomic direct write destroys the only valid snapshot;
3. approval registered in memory before durable persistence;
4. journal approval mistaken for trusted authority after restart;
5. host exposes adapter/reconciler/authority registration handles;
6. uncertain write blindly redispatched after restart;
7. receipt persisted but controller pending index stale, causing duplicate external mutation;
8. request digest/operation identity changes across restart;
9. production bootstrap silently substitutes an in-memory/no-op persistence callback;
10. host persistence callback accepts unvalidated controller dictionaries.

## Research decision

Implement a framework-neutral `development_host.py` containing a strict host envelope, stdlib atomic JSON store, and trusted composition/bootstrap wrapper. Keep `development_controller.py` and `github_effects.py` as the existing authorities unless tests expose a missing primitive that cannot be implemented safely in the host layer.
