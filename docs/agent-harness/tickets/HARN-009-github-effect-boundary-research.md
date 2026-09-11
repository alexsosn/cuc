# HARN-009 — GitHub side-effect boundary research

Date: 2026-09-11
Issue: #10
Base revision: `5612e43b286268e6d9030cfd455cd57d8e29721a`

## Problem

The repository already has a strong written fork/upstream policy and static workflow checks, but the development-controller runtime has no typed boundary that can stop a write before a generic GitHub/tool adapter executes it.

Existing policy is consistent across `AGENTS.md` and `.github/AGENT_SAFETY.md`:

- `alexsosn/cuc` is the autonomous development fork;
- reads from `DT-UCPH/cuc` are allowed;
- every upstream write is an externally visible release action and requires explicit human authorization;
- upstream comments, issues, PRs, branches, workflow dispatches, review requests and metadata changes must never be used as an autonomous test loop.

`agent/tests/test_repository_safety.py` currently enforces workflow/static source invariants, not runtime adapter calls.

## Existing contracts to reuse

HARN-002 already owns durable development operation identity:

- each `ChangeSet` may declare `operation_ids`;
- operation IDs must be unique within a change and across one `RunState`;
- `ChangeRecorded` rejects reuse after retry/resume.

HARN-009 should therefore not invent a competing operation-identity namespace. The GitHub effect boundary should require the same operation ID and keep only approval/execution receipts keyed by it.

HARN-007's accepted ADR keeps HARN-009 authoritative even if Deep Agents is later used for open-ended development helpers. Security must live at the tool/gateway boundary, not in prompts or model instructions.

## Threat model / bypasses

The boundary must fail closed against:

1. direct upstream write requests without approval;
2. a generic/raw GitHub adapter whose method or endpoint is caller-controlled;
3. URL/repository spelling tricks that avoid exact fork/upstream classification;
4. operation-ID reuse with a changed action/payload;
5. replay after a successful write causing the external action twice;
6. approval for operation A being reused for operation B or for a mutated payload;
7. a task policy allowing one fork action being used to perform another;
8. adapter exceptions being recorded as successful execution;
9. serialized journal tampering or scalar/shape confusion;
10. treating branch protection (HARN-012) as equivalent to runtime authorization. `agent-harness-safety` is currently unprotected, so these are separate controls.

## Scope decision

Implement a framework-neutral typed gateway under `agent/harness`; do not add Deep Agents/LangGraph/Langfuse dependencies.

The gateway has three distinct concepts:

- **repository classification**: exact canonical `owner/repo` identities for the fork and upstream;
- **task policy**: explicit allowlist of fork-local write capabilities; reads are separately classified;
- **effect journal**: human approvals and successful execution receipts keyed by HARN-002 operation IDs and bound to a deterministic request digest.

Only an allowlisted `GitHubAction` enum may be executed. No public `raw`, `generic`, arbitrary REST endpoint, or caller-supplied HTTP method capability is provided.

## Human approval model

An upstream write never executes on first encounter. Authorization produces a structured `HumanApprovalRequired` interrupt/challenge containing the operation ID and exact request digest. This is orchestration data, not prose or a prompt convention.

A `HumanApproval` must bind:

- approval ID;
- approver identity;
- operation ID;
- exact request digest.

A mismatched/stale approval is rejected. A valid approval can survive checkpoint/resume and can be retried after a transient adapter failure because no successful receipt exists yet. Once a successful receipt is persisted, replay of the same operation/digest returns that receipt without re-invoking the adapter.

No real upstream write is needed to validate this ticket; all upstream execution tests use deterministic fake adapters.

## Repository/action model

Canonical repositories:

- fork: `alexsosn/cuc`;
- upstream: `DT-UCPH/cuc`.

Unknown repositories are fail-closed for writes. Repository identifiers are accepted only after strict normalization; arbitrary endpoint URLs are not an execution interface.

The initial write-action allowlist should cover development-controller operations without pretending to be a full GitHub SDK: branch/ref writes, contents writes/deletes, issue/comment writes, PR create/update/merge/review-request, and workflow dispatch. Reads use a separate read request/path and never inherit write authority.

## Replay and attribution

A write request contains an explicit `operation_id` and deterministic digest of repository, action and JSON-safe payload. A successful receipt records the same identity plus an adapter result reference safe for persistence.

The effect journal is serializable and validates:

- unique approval IDs;
- at most one successful receipt per operation ID;
- receipt/request digest consistency;
- no operation ID may be reused for a different request;
- no malformed nested contracts or scalar-as-collection coercion.

This journal complements HARN-002: HARN-002 proves operation IDs are unique in the development run; HARN-009 proves a given GitHub operation is authorized and executed at most once across resume.

## Non-goals

- repository branch/ruleset enforcement (HARN-012);
- bounded full development controller semantics (HARN-010);
- automatic upstream release submission;
- credentials/secrets management for arbitrary GitHub SDKs;
- cryptographic authentication of a hostile persistence store;
- any write to `DT-UCPH/cuc` during tests or implementation.

## Research conclusion

HARN-009 is unblocked after HARN-002 and HARN-007. The safest minimal implementation is a framework-neutral, typed, fail-closed effect gateway with request-digest-bound human approval and replay receipts. HARN-010 can later map `HumanApprovalRequired` to its durable `AWAITING_HUMAN` state, while Deep Agents or any other helper receives only this gateway rather than a raw write-capable GitHub tool.
