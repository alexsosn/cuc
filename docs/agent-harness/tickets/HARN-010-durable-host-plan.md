# HARN-010 follow-up — Durable trusted host plan

Issue: #53

Research: `docs/agent-harness/tickets/HARN-010-durable-host-research.md`

## Goal

Add the trusted production composition layer that makes merged HARN-010 controller state and HARN-023 approval/effect recovery genuinely durable across process restart, without creating another GitHub mutation authority or giving model-facing ports access to privileged host dependencies.

## Proposed module

`agent/harness/development_host.py`

Framework-neutral / stdlib-only.

### 1. `DevelopmentHostEnvelope`

Frozen versioned value object:

- `schema_version: int` (v1 only);
- `controller_state: DevelopmentControllerState | None`;
- `trusted_approvals: tuple[HumanApproval, ...]`.

Required validation:

- exact schema/version; reject bool-as-int;
- strict top-level fields on `from_dict` (missing/unknown fail closed);
- canonical nested `DevelopmentControllerState.from_dict` / `HumanApproval.from_dict`;
- unique approval IDs;
- deterministic `to_dict`/JSON round trip.

### 2. `AtomicJsonDevelopmentHostStore`

Constructed with an explicit filesystem path. No default/in-memory mode.

API:

- `load() -> DevelopmentHostEnvelope | None` (`None` only when path does not exist);
- `save(envelope) -> None`.

Write sequence:

- serialize before touching old file;
- `mkstemp` in destination directory;
- write/flush/file-fsync;
- close;
- same-directory `os.replace`;
- directory fsync where supported;
- unlink leftover temp file on failure.

Load rejects:

- invalid UTF-8/JSON;
- non-object JSON;
- malformed/wrong-version envelope;
- invalid nested controller/approval state.

### 3. `TrustedDevelopmentHost`

Host-owned composition of:

- `DevelopmentControllerPolicy`;
- `DevelopmentControllerPorts`;
- `IndependentReviewer`;
- `GitHubTaskPolicy`;
- trusted write adapter;
- trusted reconciler;
- explicit `AtomicJsonDevelopmentHostStore`;
- controller policy refs/rubric/implementer identity.

On bootstrap:

1. load envelope (or create empty v1 envelope only if file absent);
2. instantiate fresh `HumanApprovalAuthority`;
3. register only `envelope.trusted_approvals`;
4. construct canonical `GitHubEffectGateway` with host-owned adapter/reconciler/authority;
5. construct `BoundedDevelopmentController` whose persistence callback validates `DevelopmentControllerState.from_dict(...)`, updates the envelope, and atomically saves it.

Public API stays narrow:

- `state` / `envelope` read access;
- `start(...)`;
- `step()`;
- `run(max_steps=...)` or equivalent bounded driver;
- `register_human_approval(approval)`;
- `resume_with_approval(approval)` (or registration + resume as one trusted method);
- `reconcile_uncertain()` for the exact current pending request.

Do not expose raw adapter/reconciler/authority/store mutation methods.

## TDD gates

### RED A — envelope/store

Create `agent/tests/test_development_host.py` first.

Tests:

1. envelope round-trip preserves full `DevelopmentControllerState` + trusted approvals;
2. wrong schema, unknown fields, malformed JSON, truncated JSON fail closed;
3. missing state file is distinguishable from corrupted state (`None` only for missing);
4. store uses same-directory temp + atomic replace and saves canonical valid JSON;
5. simulated write/fsync/replace failure before replace leaves prior valid snapshot intact and cleans temporary file;
6. production store requires an explicit path and cannot be configured as memory/no-op.

Expected RED: import/module missing only; existing suite otherwise green.

### GREEN A

Implement only envelope + atomic store. Run focused tests and full suite.

### RED B — trusted composition/restart

Add adversarial tests only after GREEN A:

1. bootstrap restores trusted approvals into a fresh authority;
2. journal-carried approval absent from host envelope cannot authorize sensitive effect;
3. approval persistence happens before live authority registration (persistence failure leaves live authority unauthorized);
4. persistence callback validates controller dict before replacing envelope;
5. host public surface does not expose raw adapter/reconciler/approval registration/store mutation;
6. restart with an uncertain operation routes through trusted `reconcile_uncertain`, never write adapter;
7. crash after receipt checkpoint but before controller pending-index persistence: restart/replay returns receipt without duplicate adapter call;
8. request digest/operation/repository/action/ref mismatch after restart fails closed;
9. production bootstrap requires explicit durable store/path and never silently creates volatile persistence;
10. exact controller terminal/provenance/audit state survives restart.

### GREEN B

Implement minimal host wrapper. Reuse canonical HARN-010/HARN-023 methods; do not duplicate gateway authorization/replay code.

### Full verification

- focused `test_development_host*.py`;
- existing `test_development_controller*.py`;
- existing `test_github_effects*.py` + reconciliation/security regression tests;
- full `agent/tests` under locked environment;
- any existing Langfuse smoke remains non-regressed.

## Independent adversarial review rubric

Review in clean logical context and reject if:

- a new GitHub mutation/journal authority exists outside `GitHubEffectGateway`;
- controller receives a fake durability flag rather than an actual durable store;
- approval can become live before durable persistence;
- journal approval alone restores trusted authority;
- uncertain effect can be blindly redispatched;
- receipt replay can call provider twice;
- host exposes raw adapter/reconciler/authority registration through public API/model-facing ports;
- corrupted state becomes a new empty run;
- direct non-atomic overwrite can destroy valid state;
- store writes temp file on another filesystem;
- unknown schema/fields are accepted;
- upstream repository writes are enabled or safety markers weakened.

Every blocking review finding must be converted to a failing regression test before its fix.

## Non-goals

- multi-process locking;
- network/database backend;
- secret storage;
- distributed transactions with GitHub;
- new model/runtime framework;
- changing parsing runtime semantics.

## Completion evidence

Before merge record:

- exact final head SHA;
- exact synthetic merge SHA tested by GitHub Actions;
- focused/full test counts;
- restart/crash scenario evidence;
- independent review disposition;
- confirmation that only fork-local branches/PRs were written.
