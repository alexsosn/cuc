# HARN-009 — GitHub side-effect boundary plan

Issue: #10
Research commit: `03265267a7853cc0f71ef6bc4add2bc45de1728c`

## Design

Add a standard-library-only module `agent/harness/github_effects.py`.

### Contracts

`RepositoryClass`
- `FORK`
- `UPSTREAM`
- `UNKNOWN`

`GitHubAction`
- explicit read action(s);
- explicit development write actions only;
- no generic/raw endpoint action.

`GitHubTaskPolicy`
- explicit fork-write action allowlist;
- upstream writes are never autonomous regardless of fork policy;
- reads from fork/upstream allowed;
- unknown-repository writes denied.

`GitHubEffectRequest`
- `operation_id`;
- canonical repository;
- action;
- JSON-safe payload;
- deterministic request SHA-256 over canonical repository/action/payload.

`HumanApprovalChallenge`
- operation ID;
- request SHA-256;
- repository/action;
- reason.

`HumanApproval`
- approval ID;
- approver ID;
- exact operation ID + request SHA-256.

`GitHubEffectReceipt`
- operation ID;
- request SHA-256;
- repository/action;
- safe result reference.

`GitHubEffectJournal`
- approvals;
- successful receipts;
- strict `to_dict`/`from_dict` round-trip validation.

### Runtime API

`classify_repository(value)`
- strict canonicalization of `owner/repo` only;
- known fork/upstream classified exactly;
- unknown identity remains unknown rather than falling through to fork.

`authorize_read(request, policy)`
- fork/upstream reads allowed;
- write action passed to read path rejected.

`GitHubEffectGateway(policy, adapter)`
- adapter is private to gateway and receives only authorized typed requests;
- `execute_write(request, journal)`:
  - validates write action and task policy;
  - returns prior persisted receipt on exact replay without adapter call;
  - rejects operation-ID reuse with changed digest;
  - fork write: execute if task policy explicitly allows action;
  - upstream write with no matching approval: raise structured `HumanApprovalRequired` carrying challenge, without adapter call;
  - upstream write with exact approval: execute;
  - unknown repository: deny;
  - adapter error: propagate/sanitize as execution failure but do not append receipt;
  - success: return updated journal + receipt.

The gateway does not own HARN-002 run transitions. HARN-010 will map the structured approval interrupt into durable controller state.

## TDD order

Create `agent/tests/test_github_effects.py` before implementation.

### A. Destination classification / reads
- exact fork and upstream classification;
- case/whitespace normalization is deterministic without accepting arbitrary URLs as repository identities;
- unknown repository stays unknown;
- upstream read allowed;
- write action cannot enter read path.

### B. Fork writes
- explicit task policy allows selected fork action;
- non-allowlisted fork action denied before adapter call;
- request carries explicit non-empty operation ID;
- success creates attributable receipt.

### C. Upstream approval interrupt
- upstream write without approval raises `HumanApprovalRequired`;
- challenge binds repository/action/operation/digest;
- adapter is not called before approval;
- mismatched operation/digest approval rejected;
- exact approval allows fake-adapter execution;
- no real upstream API action occurs in tests.

### D. Replay / resume
- serialize + restore journal after successful fake write;
- replay exact request returns same receipt and adapter call count stays one;
- same operation ID with mutated payload/action/repository is rejected;
- adapter failure records no success receipt, so retry remains possible;
- upstream approval survives journal serialization and retry after transient adapter failure.

### E. Generic-adapter bypass
- action enum has no raw/generic/endpoint member;
- gateway rejects non-enum action construction/deserialization;
- adapter receives typed authorized request only after policy/approval checks;
- extend `test_repository_safety.py` so future development-controller modules cannot introduce obvious direct upstream-mutating REST/CLI calls outside the boundary.

### F. Malformed persistence
- scalar where tuple/list expected rejected;
- duplicate approval IDs rejected;
- duplicate receipt operation IDs rejected;
- receipt with malformed digest rejected;
- contradictory/mutated nested payload rejected.

## Implementation constraints

- Python standard library only;
- no GitHub SDK/network call in this module;
- injected adapter is deterministic in tests;
- no writes to `DT-UCPH/cuc`;
- no changes under `auto_parsing/**` or `reviewed/**`;
- no Deep Agents dependency;
- preserve existing HARN-002/HARN-006/HARN-007 contracts unchanged unless a review-driven test proves a required compatibility fix.

## Verification gates

1. Initial focused RED: new HARN-009 tests fail only because module/contracts are absent.
2. Minimal implementation to focused GREEN.
3. Full `agent/tests` GREEN and existing repository-safety tests GREEN.
4. Fresh logically independent adversarial review against issue #10 + policy files, attacking:
   - alternate repository spelling;
   - generic adapter/raw endpoint escape;
   - approval substitution;
   - operation replay and mutation;
   - exception/partial-execution handling;
   - serialization tampering;
   - accidental real upstream write surface.
5. Every blocking review finding becomes test-first RED before repair.
6. Final exact-head full-suite GREEN + fresh review before marking PR ready/merging.

## Acceptance mapping

- explicit operation IDs: `GitHubEffectRequest.operation_id`, tied to HARN-002 identity;
- upstream generic bypass prevention: typed action allowlist + gateway-only adapter execution + static safety extension;
- explicit human gate: structured `HumanApprovalRequired` challenge, exact-digest approval contract;
- fork attribution/replay safety: task-policy allowlist + durable receipts;
- existing safety suite: retained and extended;
- adversarial bypass review: mandatory final gate.
