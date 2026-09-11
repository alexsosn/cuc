# HARN-009 — GitHub side-effect boundary plan

Issue: #10
Research commit: `03265267a7853cc0f71ef6bc4add2bc45de1728c`

## Design

Add a standard-library-only module `agent/harness/github_effects.py`. It performs no network I/O; a trusted controller injects the write adapter.

### Contracts

`RepositoryClass`
- `FORK`
- `UPSTREAM`
- `UNKNOWN`

`GitHubAction`
- explicit read action;
- explicit development write actions only;
- no generic/raw endpoint action.

`GitHubOperationPermission`
- one HARN-002 operation ID;
- one exact fork write action.

`GitHubTaskPolicy`
- fork authority is bound as `operation_id -> exact action`, not a cross-product of operation IDs and actions;
- upstream operation IDs are separately declared and never inherit fork authority;
- legacy single-action fork policy is normalized to exact per-operation permissions; ambiguous multi-action legacy policy fails closed;
- reads from fork/upstream are separately authorized;
- unknown-repository writes are denied.

`GitHubEffectRequest`
- operation ID;
- canonical repository;
- action;
- JSON-safe payload;
- deterministic request SHA-256 over canonical repository/action/payload.

`HumanApprovalChallenge` / `HumanApproval`
- upstream approval is a structured runtime interrupt;
- approval binds approver, operation ID and exact request digest.

`GitHubEffectReceipt`
- operation ID + request digest + repository/action;
- durable adapter result reference.

`GitHubEffectUncertainRequest`
- durable quarantine marker for an adapter dispatch whose external outcome cannot safely be replayed.

`GitHubEffectJournal`
- approvals;
- successful receipts;
- uncertain requests;
- strict round-trip validation and mutually exclusive receipt/uncertainty state for one operation.

### Runtime API

`classify_repository(value)`
- strict canonicalization of `owner/repo` only;
- known fork/upstream classified exactly;
- unknown identity remains unknown rather than falling through to fork.

`GitHubEffectGateway.authorize_read(request)`
- fork/upstream reads allowed;
- unknown reads denied;
- write action passed to read path rejected.

`GitHubEffectGateway.execute_write(request, journal, checkpoint=...)`

Before any adapter dispatch:
1. validate request/action and declared operation ID;
2. exact replay of a persisted receipt returns the receipt without adapter execution;
3. an existing uncertainty marker blocks automatic replay;
4. fork write requires the exact declared `(operation_id, action)` pair;
5. upstream write requires a declared upstream operation ID plus an exact-digest `HumanApproval`; absent approval raises `HumanApprovalRequired` before adapter execution;
6. unknown repository is denied;
7. persist `GitHubEffectUncertainRequest` through the supplied durable checkpoint;
8. only then invoke the injected adapter.

After dispatch:
- `AdapterEffectNotExecuted` is the only adapter failure allowed to clear uncertainty automatically; the cleared journal must itself be checkpointed before retry can be considered safe;
- any ordinary adapter exception becomes `GitHubEffectOutcomeUnknown` and keeps the durable uncertainty marker;
- process-level interruption after the pre-dispatch checkpoint likewise resumes into the persisted uncertainty state and cannot automatically duplicate the write;
- successful adapter return creates a receipt, clears uncertainty and checkpoints the completed journal before success is exposed;
- if that final receipt checkpoint fails after the external write may have succeeded, raise `GitHubEffectOutcomeUnknown` carrying the earlier durable uncertainty journal.

The gateway does not own HARN-002 run transitions. HARN-010 will map approval/uncertainty outcomes into durable controller state.

## TDD / review history

The ticket follows research → plan → TDD/RED → implementation → full-suite GREEN → logically independent adversarial review. Every blocking review finding is converted to a failing test before repair.

Coverage includes:

### Destination / identity
- strict fork/upstream classification;
- URL/repository spelling tricks rejected;
- request digest stable and bound to repository/action/payload;
- operation-ID reuse with mutated request rejected.

### Fork writes
- exact per-operation action permission required;
- cross-product privilege expansion rejected;
- non-allowlisted action denied before adapter call;
- success creates attributable replay receipt.

### Upstream approval
- no matching approval raises structured `HumanApprovalRequired`;
- challenge binds exact operation/digest/repository/action;
- mismatched or stale approval rejected;
- no real upstream API write occurs in tests.

### Durable replay / crash safety
- exact successful replay never repeats adapter call;
- uncertainty is checkpointed before dispatch;
- resume from uncertainty cannot automatically replay;
- ordinary adapter exception remains quarantined;
- process-level interruption remains quarantined after restore;
- `AdapterEffectNotExecuted` supports the explicitly safe retry path;
- final receipt-checkpoint failure is reported as outcome-unknown rather than a retryable storage failure.

### Generic bypass / static safety
- action enum exposes no raw/generic endpoint capability;
- `agent/harness` is statically checked for recognizable direct GitHub REST/CLI transport;
- the static check is deliberately GitHub-specific and does not prohibit local subprocess execution or non-GitHub HTTP required by later controller/evaluation work;
- explicit upstream-mutating patterns remain covered separately by repository safety tests.

### Malformed persistence
- scalar/collection confusion rejected;
- duplicate approvals/receipts/uncertainty IDs rejected;
- malformed digests and contradictory durable state rejected.

## Implementation constraints

- Python standard library only;
- no GitHub SDK/network call in `github_effects.py`;
- injected adapter is trusted infrastructure and deterministic in tests;
- untrusted model code must never receive the raw adapter or unrestricted GitHub transport;
- no writes to `DT-UCPH/cuc` during this ticket;
- no changes under `auto_parsing/**` or `reviewed/**`;
- no Deep Agents dependency;
- preserve HARN-002/HARN-006/HARN-007 contracts unless a review-driven regression proves a required compatibility change.

## Acceptance mapping

- explicit operation IDs: HARN-002 operation identity, with fork operations bound to an exact action;
- upstream generic bypass prevention: typed action vocabulary + gateway-only adapter execution + GitHub-specific static safety guard;
- explicit human gate: `HumanApprovalRequired` challenge and exact request-digest approval;
- replay/crash safety: durable uncertainty-before-dispatch + durable receipt-before-success;
- fork attribution: exact operation/action policy plus receipts;
- existing safety suite: retained and extended;
- adversarial review: mandatory final gate before ready/merge.
