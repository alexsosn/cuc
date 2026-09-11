# HARN-009 — implementation and validation plan

## Architecture

Add a framework-neutral `agent/harness/github_side_effects.py` module. It will expose only typed GitHub intents and a guarded executor; no model-facing generic URL/method escape hatch.

Core contracts:

- `GitHubOperationKind`: closed read/write operation enum;
- `GitHubTarget`: canonical owner/repo plus optional branch/ref;
- `GitHubOperationIntent`: mandatory operation ID, operation kind, target, canonical JSON-scalar payload, deterministic fingerprint;
- `HumanApprovalGrant`: auditable approval ID/approver bound to the exact operation fingerprint;
- `OperationJournal`: serializable prepared/completed records keyed by operation ID;
- `PolicyDecision`: allow / approval-required / deny with deterministic reason;
- `GuardedGitHubSideEffects`: policy evaluation, dry-run, replay/reconciliation, and trusted-adapter dispatch.

The module remains independent of LangGraph, Deep Agents, provider SDKs, and live GitHub credentials.

## Policy

- reads of `alexsosn/cuc` and `DT-UCPH/cuc`: allowed;
- controlled fork-local development writes: allowed;
- fork merge into `agent-harness-safety`: exact human approval required;
- explicit workflow trigger: exact human approval required;
- upstream writes: exact human approval required and never silently downgraded to an autonomous fork permission;
- writes to any other repository: denied;
- unknown/generic operation: unrepresentable/denied.

Feature-branch writes must name a ref and must not target `main` or `agent-harness-safety` through a generic branch-update operation. Integration goes through the dedicated merge operation and approval gate.

## Replay protocol

For writes:

1. validate/canonicalize intent;
2. evaluate policy and approval;
3. if dry-run, return an inspection artifact and stop;
4. check the operation journal;
5. changed fingerprint under an existing operation ID -> reject;
6. completed identical operation -> replay stored result without transport;
7. prepared identical operation -> call adapter reconciliation;
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

## Tests to write first

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
- target is taken from the validated intent, not from payload URLs;
- upstream-looking URL/data in a fork payload cannot change the classified target;
- adapter receives the validated intent and cannot be chosen by the model;
- dry-run performs no transport/reconciliation and no completion write.

### Regression

- existing `agent/tests/test_repository_safety.py` remains green;
- HARN-002/HARN-006 contracts remain unchanged and green.

## Non-goals

- actually performing or approving an upstream write;
- storing GitHub credentials;
- implementing the full HARN-010 controller;
- replacing HARN-002 state transitions;
- relying on prompt instructions for enforcement;
- claiming exactly-once provider execution without reconciliation.