# HARN-009 — implementation and validation plan

## Architecture

Add a framework-neutral `agent/harness/github_side_effects.py` module. It exposes only typed GitHub intents and a guarded executor; there is no model-facing generic URL/method escape hatch.

Core contracts:

- `GitHubOperationKind`: closed read/write operation enum;
- `GitHubTarget`: canonical owner/repo plus optional branch/ref;
- `GitHubOperationIntent`: mandatory operation ID, operation kind, target, type-preserving canonical JSON payload, deterministic fingerprint;
- `HumanApprovalGrant`: auditable approval ID/approver bound to the exact operation fingerprint;
- `HumanApprovalRegistry`: trusted host-owned authority recording which exact grants were actually issued by the human gate;
- `OperationJournal`: serializable prepared/completed records keyed by operation ID;
- `PolicyDecision`: allow / approval-required / deny with deterministic reason;
- `GuardedGitHubSideEffects`: policy evaluation, dry-run, replay/reconciliation, trusted approval lookup, and typed adapter dispatch.

The module remains independent of LangGraph, Deep Agents, provider SDKs, and live GitHub credentials. The model-facing guard has no approval-registration method; registry construction/restoration belongs to trusted orchestration code.

## Policy

- reads of `alexsosn/cuc` and `DT-UCPH/cuc`: allowed;
- controlled fork-local development writes: allowed;
- fork merge into `agent-harness-safety`: exact trusted human approval required;
- explicit workflow trigger: exact trusted human approval required;
- upstream writes: exact trusted human approval required and never silently downgraded to an autonomous fork permission;
- writes to any other repository: denied;
- unknown/generic operation: unrepresentable/denied.

Feature-branch writes must name a ref and must not target `main` or `agent-harness-safety` through a generic branch-update operation. Integration goes through the dedicated merge operation and approval gate.

A caller-constructed `HumanApprovalGrant` is not authorization by itself. The exact grant must already exist in the trusted registry. This prevents the controller from minting its own human approval using a public operation fingerprint.

## Replay protocol

For writes:

1. validate/canonicalize intent without losing JSON container types;
2. evaluate destination/action policy;
3. if dry-run, return an inspection artifact and stop;
4. inspect operation journal and reject changed fingerprints under an existing operation ID;
5. for approval-required operations, resolve an exact grant only through the trusted registry, including a previously recorded approval ID on prepared restart;
6. completed identical operation -> replay stored result without a second write;
7. prepared identical operation -> call adapter reconciliation only after approval validation;
8. if reconciliation finds an existing provider result, record/return it without a new write;
9. otherwise keep/write `prepared`, dispatch exactly one typed operation, then record completion.

Read operations do not require operation-journal idempotency but still pass destination/action classification.

## TDD / RED order

1. Commit research and this plan separately.
2. Add contract/policy tests before `github_side_effects.py` exists.
3. Add replay/restart/adversarial bypass tests before implementation.
4. Open a draft fork-local PR to exercise CI and confirm failures are isolated to the missing HARN-009 implementation.
5. Implement the smallest framework-neutral boundary satisfying those tests.
6. Run the complete agent suite plus Langfuse smoke.
7. Perform a logically independent adversarial review from issue #10 + final diff + actual CI evidence only.
8. For every review blocker, add a RED regression first, then fix minimally and rerun the full suite.
9. Fresh final clean-context re-review before merge.

## Initial tests

### Policy / approval

- classify fork, upstream, unknown repository;
- allow fork/upstream reads;
- allow fork feature-branch/issue/PR writes;
- reject unsafe fork branch updates to integration/main refs;
- require exact approval for harness merge and workflow trigger;
- require exact approval for every upstream write;
- reject approval replay across another operation ID, action, repo, ref, or payload;
- reject caller attempts to smuggle approval through payload fields.

### Idempotency / restart

- identical completed operation returns recorded result and makes zero new adapter writes;
- reused operation ID with changed fingerprint raises before transport;
- prepared operation reconciles provider state before retry;
- reconciliation hit prevents duplicate issue/PR creation;
- reconciliation miss executes one retry;
- journal round-trip preserves these semantics;
- malformed/tampered serialized journal is rejected.

### Boundary / bypass

- there is no generic request operation;
- arbitrary/unknown operation strings fail closed;
- target is taken from validated intent, not payload URLs;
- upstream-looking URL/data in a fork payload cannot change the classified target;
- adapter receives validated intent and cannot be chosen by the model;
- dry-run performs no transport/reconciliation/journal completion.

## Independent-review regressions

The first clean-context review added tests before fixes for two blockers:

1. a caller-fabricated grant with a perfectly matching public fingerprint must still fail unless registered by the trusted host authority;
2. JSON `[]` and `{}` — including nested empty containers — must remain distinct through immutable normalization, round-trip serialization, and fingerprinting.

The fix also tests trusted registry round-trip, same-ID altered-grant rejection, and restart of a prepared sensitive operation: journal state alone is insufficient; restored trusted approval state is required before reconciliation or retry.

## Regression gates

- existing `agent/tests/test_repository_safety.py` remains green;
- HARN-002/HARN-006/HARN-007 contracts remain unchanged and green;
- complete agent suite and Langfuse SDK smoke pass on the exact final head.

## Non-goals

- actually performing or approving an upstream write;
- storing GitHub credentials;
- implementing the full HARN-010 controller;
- replacing HARN-002 state transitions;
- relying on prompt instructions for enforcement;
- claiming exactly-once provider execution without provider reconciliation;
- cryptographically protecting trusted local persistence against a malicious host/storage layer.
