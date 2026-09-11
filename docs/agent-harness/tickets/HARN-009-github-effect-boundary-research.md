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

`agent/tests/test_repository_safety.py` enforces workflow/static source invariants; HARN-009 adds the runtime adapter boundary.

## Existing contracts to reuse

HARN-002 already owns durable development operation identity:

- each `ChangeSet` may declare `operation_ids`;
- operation IDs must be unique within a change and across one `RunState`;
- `ChangeRecorded` rejects reuse after retry/resume.

HARN-009 therefore does not invent a competing operation-identity namespace. The effect boundary requires the same operation IDs and adds authorization, approval and durable execution/quarantine state keyed by them.

HARN-007's accepted ADR keeps HARN-009 authoritative even if Deep Agents is later used for open-ended development helpers. Security lives at the tool/gateway boundary, not in prompts or model instructions.

## Threat model / bypasses

The boundary must fail closed against:

1. direct upstream write requests without approval;
2. a generic/raw GitHub adapter whose method or endpoint is caller-controlled;
3. URL/repository spelling tricks that avoid exact fork/upstream classification;
4. operation-ID reuse with a changed action/payload/repository;
5. replay after a successful write causing the external action twice;
6. approval for operation A being reused for operation B or for a mutated payload;
7. a task policy allowing one fork action being used to perform another;
8. process or adapter failure after an external write may already have occurred;
9. failure to persist a success receipt after the remote write returned success;
10. serialized journal tampering or scalar/shape confusion;
11. generic direct GitHub transport added elsewhere under the development harness;
12. treating branch protection (HARN-012) as equivalent to runtime authorization.

## Scope decision

Implement a framework-neutral typed gateway under `agent/harness`; do not add Deep Agents/LangGraph/Langfuse dependencies.

The gateway has four distinct concepts:

- **repository classification**: exact canonical `owner/repo` identities for the fork and upstream;
- **task policy**: exact fork `(operation_id, action)` permissions plus separately declared upstream operation IDs;
- **human approval**: exact upstream request authorization data;
- **effect journal**: successful receipts and uncertain requests, plus approvals, all keyed by HARN-002 operation identity.

Only an allowlisted `GitHubAction` enum may be executed. No public `raw`, `generic`, arbitrary REST endpoint, or caller-supplied HTTP method capability is provided.

## Human approval model

An upstream write never executes on first encounter. Authorization produces a structured `HumanApprovalRequired` interrupt/challenge containing the operation ID and exact request digest. This is orchestration data, not prose or a prompt convention.

A `HumanApproval` binds:

- approval ID;
- approver identity;
- operation ID;
- exact request digest.

A mismatched/stale approval is rejected. Approval persistence alone does **not** make an ambiguous failed write retryable: after dispatch the runtime must distinguish provably-not-executed from outcome-unknown. HARN-010 is responsible for creating approval records only from a real human interaction and for mapping these structured interrupts into durable controller state.

No real upstream write is needed to validate this ticket; all upstream execution tests use deterministic fake adapters.

## Repository/action model

Canonical repositories:

- fork: `alexsosn/cuc`;
- upstream: `DT-UCPH/cuc`.

Unknown repositories are fail-closed for writes. Repository identifiers are accepted only after strict normalization; arbitrary endpoint URLs are not an execution interface.

Fork permissions bind each declared operation ID to one exact action. This avoids a cross-product capability bug where a task that happened to allow actions A and B could repurpose the operation ID intended for A to execute B. Legacy single-action policy can be normalized safely; ambiguous multi-action legacy policy must fail closed.

The write-action vocabulary covers development-controller operations without pretending to be a full GitHub SDK: branch/ref writes, contents writes/deletes, issue/comment writes, PR create/update/merge/review-request, and workflow dispatch. Reads use a separate authorization path and never inherit write authority.

## Durable replay and uncertain outcomes

A write request contains an explicit `operation_id` and deterministic digest of repository, action and JSON-safe payload. A successful receipt records the same identity plus an adapter result reference safe for persistence.

At-most-once behavior cannot rely only on a post-write receipt: the process can die after the remote write but before that receipt is saved. The gateway therefore persists an `uncertain` marker **before** invoking a write-capable adapter.

Consequences:

- if the pre-dispatch checkpoint fails, no adapter call occurs;
- if the adapter or process fails after dispatch begins, restored state remains uncertain and automatic replay is blocked;
- only a trusted `AdapterEffectNotExecuted` assertion may clear uncertainty automatically, and the cleared state must itself be durably checkpointed;
- after adapter success, the completed receipt state must be checkpointed before success is returned;
- if that final checkpoint fails, the runtime exposes `GitHubEffectOutcomeUnknown` carrying the earlier durable uncertainty state rather than a retryable persistence error;
- exact replay of a successfully persisted receipt returns the prior receipt without invoking the adapter again.

The effect journal validates:

- unique approval IDs;
- at most one successful receipt or one uncertainty marker per operation ID;
- no operation may be both receipted and uncertain;
- receipt/request identity consistency;
- no malformed nested contracts or scalar-as-collection coercion.

This complements HARN-002: HARN-002 proves operation IDs are unique in the development run; HARN-009 proves a given GitHub operation is authorized and cannot be automatically replayed across an ambiguous external-effect window.

## Static bypass guard

The development harness is scanned for recognizable direct GitHub REST/CLI transport so future controller code cannot silently bypass the typed gateway. The check is deliberately GitHub-specific: generic `subprocess.run` and non-GitHub HTTP remain legal because HARN-010 must execute local tests/evals and later observability/provider integrations may use ordinary HTTP.

Static scanning is defense-in-depth, not a hostile-code sandbox. The trusted controller must not hand the untrusted model the raw adapter or unrestricted GitHub transport object.

## Non-goals

- repository branch/ruleset enforcement (HARN-012);
- bounded full development controller semantics (HARN-010);
- automatic upstream release submission;
- credentials/secrets management for arbitrary GitHub SDKs;
- cryptographic authentication of a hostile persistence store or hostile in-process Python caller;
- any write to `DT-UCPH/cuc` during tests or implementation.

## Research conclusion

HARN-009 is unblocked after HARN-002 and HARN-007. The review-hardened minimal implementation is a framework-neutral, typed, fail-closed effect gateway with exact operation/action authority, request-digest-bound human approval, durable uncertainty-before-dispatch, receipt-before-success, and a GitHub-specific static bypass guard. HARN-010 can map `HumanApprovalRequired` and `GitHubEffectOutcomeUnknown` into durable controller states while exposing only this gateway—not a raw write-capable GitHub tool—to any model-driven helper.
