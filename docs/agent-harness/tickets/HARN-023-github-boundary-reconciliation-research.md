# HARN-023 — duplicate GitHub side-effect boundary research

## Trigger

HARN-009 was implemented twice on parallel branches and the two PRs were merged sequentially:

- PR #51 added `agent/harness/github_effects.py` at merge `38c372406d9c59318c8c2d43fb28563813607b3a`;
- PR #50 then added `agent/harness/github_side_effects.py` at merge `98b0af411d66b97a2047ea4a828b2b16ff8ddc0d`.

Both implementations passed their own tests and adversarial reviews, but the combined repository now exposes two independently usable write-authority APIs. HARN-010 cannot safely treat both as authoritative.

## Final implementation A — `github_effects.py` (PR #51)

Strengths:

- explicit task-local `GitHubTaskPolicy`;
- exact fork `operation_id -> GitHubAction` capability binding;
- separately declared upstream operation IDs;
- closed action enum including contents/ref/issue/PR/review/workflow operations;
- request digest binds repository, action and JSON payload;
- structured `HumanApprovalChallenge` / `HumanApprovalRequired` interrupt;
- explicit `GitHubEffectOutcomeUnknown` quarantine;
- durable uncertainty checkpoint happens before adapter dispatch;
- post-write receipt-checkpoint failure remains outcome-unknown instead of pretending success;
- exact receipt replay returns without adapter redispatch;
- generic GitHub transport is statically prohibited in harness code by HARN-009 regressions.

Weaknesses / assumptions:

- `HumanApproval` is a public value object and authority is currently the controller's promise to create it only from a real human interaction; the gateway has no host-owned approval registry/authority;
- request identity has first-class repository/action but target branch/ref is only whatever the action payload encodes;
- no built-in reconcile callback for a restored uncertain request; automatic retry remains prohibited until trusted code resolves it;
- protected fork integration refs are not a first-class policy concept in this module.

The PR #51 final review explicitly deferred the approval-authority integration to HARN-010, requiring trusted controller code to create `HumanApproval` only from real human input.

## Final implementation B — `github_side_effects.py` (PR #50)

Strengths:

- explicit `GitHubTarget(owner, repo, ref)`; operation fingerprint binds target/ref separately from payload;
- host-owned `HumanApprovalRegistry`; caller-created approval-shaped values do not authorize unless registered;
- JSON freezing preserves object/array distinction after review-driven regression fixes;
- durable `OperationJournal` can synchronously persist PREPARED before transport;
- restored PREPARED operations call trusted `adapter.reconcile()` before any retry;
- persistence failure before dispatch rolls back and prevents provider write;
- explicit refs required for branch writes and sensitive merge/workflow actions;
- direct update of protected fork refs (`main`, `agent-harness-safety`) is denied through generic branch-write operations;
- typed per-action adapter protocol has no generic URL/HTTP request method.

Weaknesses / assumptions:

- policy is global by destination/action class rather than task-local exact operation authority;
- ordinary fork writes are allowed by action class without an HARN-010 task-declared operation ID/action capability;
- fork merge/workflow approval is hard-coded globally instead of derived from task policy;
- approval failure is a generic `ApprovalRequired` exception, not a structured challenge carrying exact operation/digest details for durable controller pause state;
- action vocabulary omits some operations present in `github_effects.py` (contents delete/update, request-review, generic ref update semantics).

## Security comparison

| Property | `github_effects.py` | `github_side_effects.py` | Stronger source |
| --- | --- | --- | --- |
| exact task operation -> action authority | yes | no | A |
| host-authenticated approval authority | controller convention only | registry-backed | B |
| structured approval challenge | yes | no | A |
| repository/action/payload digest | yes | yes | tie |
| target/ref first-class in identity | no | yes | B |
| durable pre-dispatch record | yes | yes | tie |
| post-write persistence failure quarantined | yes | PREPARED survives; reconcile on restart | both, different model |
| trusted reconciliation before retry | external/manual | built-in adapter reconcile | B |
| receipt/completed replay | yes | yes | tie |
| protected integration-ref policy | no | yes | B |
| rich closed action vocabulary | yes | narrower | A |
| generic transport absent | yes | yes | tie |
| HARN-010 fit | direct: policy/challenge/outcome-unknown | needs task-policy/challenge layer | A |

## Decision

Use **`agent/harness/github_effects.py` as the canonical HARN-010 authority**, because HARN-010 requires task-local operation capability binding and structured pause/quarantine semantics. However, do not simply delete implementation B: port its stronger reviewed invariants first.

Required ports into canonical A:

1. **Trusted approval authority**
   - add a host-owned approval registry/resolver;
   - the gateway must not accept an arbitrary caller-constructed approval as authority;
   - approval remains bound to exact operation ID + request digest.

2. **First-class target/ref binding**
   - extend `GitHubEffectRequest` with an optional explicit target ref that participates in request digest;
   - ref-required actions must fail closed when ref is absent;
   - fork direct ref-update operations must reject protected integration refs through the generic ref path.

3. **Trusted uncertainty reconciliation**
   - add a narrow trusted reconciliation path for an already persisted uncertain request;
   - it may record a discovered durable result receipt without redispatch, or explicitly clear uncertainty only when trusted reconciliation proves no mutation occurred;
   - ordinary `execute_write()` remains fail-closed and never blindly retries uncertainty.

Keep from A:

- `GitHubTaskPolicy` exact task-local permissions;
- structured `HumanApprovalChallenge`;
- explicit `GitHubEffectOutcomeUnknown`;
- richer action enum;
- explicit checkpoint callback and receipt semantics.

## Retiring implementation B

There are no production exports in `agent/harness/__init__.py` for either module and no HARN-010 implementation exists yet. The B implementation was added only by PR #50 with its own tests/docs.

Preferred migration:

- port the stronger B invariants and regression tests to canonical A;
- delete `github_side_effects.py` and its implementation-specific tests once equivalent canonical tests exist;
- keep historical PR/research docs as repository history; the final HARN-023 decision document records why the duplicate module disappeared.

A compatibility module would be harmful because it would remain a second write-capable surface. If future consumers appear before merge, stop and reassess rather than preserve write dispatch under an alias.

## TDD requirements

RED must demonstrate the current combined base fails all of these:

1. exactly one write-authority module exists;
2. a fabricated unregistered `HumanApproval` cannot authorize upstream mutation;
3. target/ref changes alter request identity and required-ref actions reject missing refs;
4. generic fork ref update cannot target `main` or `agent-harness-safety`;
5. uncertain requests can be reconciled without blind redispatch;
6. task-local operation/action binding remains mandatory after the ports.

Tests must not perform live GitHub writes.

## Independent review rubric

Reject if:

- deleting one module loses any reviewed invariant from the other;
- a model/controller can fabricate its own approval authority;
- task policy becomes a broad action allowlist/cross-product;
- target/ref can be smuggled only in payload and escape authorization identity;
- PREPARED/uncertain state can auto-retry without reconciliation;
- reconcile can clear uncertainty without trusted proof;
- protected fork refs are writable through the generic ref capability;
- an alias/compatibility module still exposes a second dispatch path;
- HARN-010 has more than one authoritative write gateway.
