# HARN-023 — canonical GitHub effect boundary plan

## Outcome

Converge on `agent/harness/github_effects.py` as the sole write-authority module and port the strongest reviewed invariants from the duplicate `github_side_effects.py` before deleting it.

## Canonical API extensions

### 1. First-class target ref

Extend `GitHubEffectRequest`:

```text
operation_id
repository
action
target_ref: str | None
payload
request_sha256
```

`request_sha256` binds repository + action + target_ref + payload.

Rules:

- `UPDATE_REF` requires a non-empty `target_ref`;
- `MERGE_PULL_REQUEST` requires a non-empty `target_ref` identifying the intended integration/base ref;
- `DISPATCH_WORKFLOW` requires a non-empty `target_ref` identifying the workflow execution ref;
- generic `UPDATE_REF` on fork rejects `main` and `agent-harness-safety` case-insensitively;
- other actions may omit ref unless future action-specific policy requires it.

The ref is not inferred from payload. Conflicting payload text does not change authorization identity.

### 2. Host-owned human approval authority

Add `HumanApprovalAuthority`:

- host/controller boundary registers actual human approvals;
- registration rejects ID rebinding;
- resolution succeeds only for a previously registered exact `HumanApproval` value;
- gateway receives the authority privately in its constructor;
- upstream write requires both a matching journal approval **and** matching authority registration;
- a fabricated journal approval with correct operation ID/digest is insufficient.

The authority is intentionally separate from model-facing task policy and from serialized `GitHubEffectJournal`. On process restart, trusted host code must restore/provide authority before an upstream uncertain operation can be reconciled or executed.

### 3. Typed uncertain reconciliation

Add:

```text
ReconciliationDisposition = executed | not-executed
GitHubReconciliationResult(disposition, result_ref?)
```

Trusted reconciler protocol:

```text
reconcile(request) -> GitHubReconciliationResult
```

`GitHubEffectGateway.reconcile_uncertain(...)` is allowed only when the journal contains the exact uncertain request identity.

- `executed` requires a durable non-empty result ref; gateway converts uncertainty to receipt and checkpoints it;
- `not-executed` clears uncertainty and checkpoints the safe journal;
- malformed/exceptional reconciliation leaves uncertainty intact;
- reconciliation itself never calls `execute()`;
- normal `execute_write()` still raises `GitHubEffectOutcomeUnknown` for persisted uncertainty and never blind-retries.

For upstream requests, reconciliation also requires the exact registered human approval because learning/reconciling a sensitive write must not make an untrusted fabricated approval authoritative.

## Preserve canonical A invariants

Do not weaken:

- exact task-local fork operation -> action permission;
- separately declared upstream operation IDs;
- closed action vocabulary;
- unknown repositories denied;
- durable uncertainty checkpoint before dispatch;
- safe `AdapterEffectNotExecuted` path;
- post-write receipt-checkpoint failure -> outcome unknown;
- receipt replay without adapter execution;
- operation-ID request identity conflict rejection;
- static direct-GitHub-transport guard.

## TDD RED

Create `agent/tests/test_github_effect_boundary_reconciliation.py` first.

Required tests:

1. **single authority** — repository must not contain both `github_effects.py` and a second write-capable `github_side_effects.py` after implementation. Initial RED expects the duplicate to exist.
2. **approval forgery** — exact fabricated `HumanApproval` inserted into journal but absent from authority cannot authorize upstream write; adapter call count remains zero.
3. **registered approval** — exact authority-registered approval + journal approval authorizes once.
4. **ref digest** — same repo/action/payload with different `target_ref` produces different request digest.
5. **required refs** — UPDATE_REF, MERGE_PULL_REQUEST, DISPATCH_WORKFLOW reject missing ref before adapter dispatch.
6. **protected fork refs** — generic UPDATE_REF to `main` or `agent-harness-safety` denied even when operation/action is task-authorized.
7. **uncertain executed reconciliation** — no second execute; reconciliation records receipt and subsequent execute replays it.
8. **uncertain not-executed reconciliation** — clears quarantine durably; a later execute may perform one real dispatch.
9. **reconciliation failure** — exception/malformed result preserves uncertainty and prevents execute retry.
10. **operation/action capability** — after all ports, a task-authorized operation cannot be reused with another action.
11. **round trip** — target_ref and request digest survive request serialization.

The initial test commit should fail only for the not-yet-ported API/duplicate authority, with existing suite otherwise green.

## Implementation order

1. Extend request serialization/digest with `target_ref` while keeping old positional construction compatible where practical.
2. Add action/ref policy validation before any journal mutation/checkpoint/adapter invocation.
3. Add host-owned approval authority and require registry resolution for upstream approval.
4. Add typed reconciliation protocol/result and gateway method.
5. Port/adjust canonical tests.
6. Delete duplicate module and its implementation-specific tests only after equivalent canonical regressions are green.
7. Update HARN-010 research/plan to reference only canonical `github_effects.py` and the new authority/reconciliation contracts.
8. Full suite.
9. Fresh independent adversarial review.

## Compatibility policy

There are no repository production consumers of `github_side_effects.py` at decision time. Do not retain a write-capable compatibility alias. If a new consumer appears before merge, pause and migrate it explicitly to canonical contracts.

`GitHubEffectRequest` should preserve backward construction for current HARN-009 tests by making `target_ref` optional in the constructor; action-specific validation belongs in the gateway, not value construction, so read/issue/content actions remain unaffected.

## Adversarial review

Attack:

- fabricated approval inserted only into journal;
- authority grant ID rebound to altered digest;
- same op/action/payload with changed ref;
- ref case variants for protected refs;
- payload fields attempting to contradict first-class ref;
- uncertain request changed before reconciliation;
- reconcile `executed` without result ref;
- reconcile callback throwing after external read/check;
- receipt checkpoint failure during reconciliation;
- deletion leaving another importable write-capability class/module;
- regression of task-local operation/action permissions.
