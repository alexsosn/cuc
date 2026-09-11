# HARN-009 — GitHub side-effect boundary research

Date: 2026-09-11

## Goal

Turn CUC's fork/upstream safety convention into a framework-neutral runtime capability boundary for the future development controller. The model or orchestration framework must not be able to bypass destination classification, approval, or replay safety by choosing a different GitHub tool spelling.

## Current repository state

### Durable controller contracts

HARN-002 already provides framework-neutral development state and explicit `operation_ids` on `ChangeSet`. It also has `AWAITING_HUMAN` / `HUMAN_ESCALATION` semantics. HARN-009 should complement those contracts rather than create a second development state machine.

### Existing safety policy

`AGENTS.md` is the current normative fork-safety text:

- development happens in `alexsosn/cuc`;
- reads from `DT-UCPH/cuc` are allowed;
- upstream writes are release actions and require explicit human authorization;
- every GitHub write must classify its destination;
- comments, mentions, review requests, issue creation, upstream PR creation, and workflow triggering must not be used against upstream as test probes.

`agent/tests/test_repository_safety.py` adds static CI checks. It rejects dangerous workflow triggers, broadened workflow push scope, workflow issue/PR write permissions, and obvious automation strings that target upstream writes. These tests are useful defense-in-depth, but they do not gate runtime calls made by a development controller.

Issue #10 refers to `.agents/skills/repository-safety/SKILL.md`, but that path does not exist on `agent-harness-safety` as of this research. The authoritative repository policy is therefore `AGENTS.md` plus the static safety tests. HARN-009 must not assume a hidden skill supplies enforcement.

### HARN-007 architecture result

HARN-007 deliberately kept HARN-010 framework-neutral and deferred any Deep Agents development execution until this runtime boundary exists. Therefore HARN-009 must not depend on Deep Agents, LangGraph, provider SDKs, or prompt compliance.

## Threat model

The boundary protects against an agent/controller that is buggy, retries after interruption, supplies malformed or misleading operation metadata, chooses an alternative operation name, or attempts to route a write through a generic GitHub adapter. It also protects against stale approval grants and operation-ID reuse.

The boundary does **not** attempt to make a deliberately malicious trusted transport implementation safe if that transport ignores the validated target and secretly writes elsewhere. Trusted adapter code remains part of the trusted computing base; the model never receives the raw transport.

## Required policy model

### Destinations

Classify every operation target before execution:

- `alexsosn/cuc` -> fork;
- `DT-UCPH/cuc` -> upstream;
- any other repository -> denied unless a future policy explicitly adds it.

Owner/repository comparison must be canonicalized case-insensitively, while the original target remains in the audit artifact.

### Operation classes

The boundary should expose a closed enum rather than arbitrary HTTP methods/URLs.

Read-only examples:

- repository/issue/PR/branch/workflow state reads;
- upstream synchronization/research reads.

Controlled fork-local writes:

- create/update a feature branch;
- create/update a fork-local issue;
- open/update a fork-local PR;
- add fork-local issue/PR comments or review artifacts;
- push/update explicitly allowed development refs.

Sensitive writes:

- merge into the harness integration branch;
- trigger write-capable workflows;
- any upstream write.

Unknown/generic operations are denied, not interpreted heuristically.

### Approval

Approval must be a typed, auditable artifact bound to the exact operation fingerprint (operation ID + action + target + ref + canonical payload digest). It is not a boolean supplied by the model and not a phrase in a prompt.

A stale grant, grant for another action/target/payload, or caller-supplied `approved=true` flag must not authorize anything.

Current policy permits autonomous controlled writes only in the fork. Upstream writes remain denied without an exact human grant. Merge into `agent-harness-safety` and explicit workflow triggering should also require an exact human grant because they cross an integration/publication boundary even within the fork.

### Replay safety

`operation_id` is mandatory and immutable. The boundary needs a serializable operation journal:

1. canonicalize and fingerprint the intent;
2. record `prepared` before invoking a write transport;
3. on retry/resume, reject reuse of the operation ID with a different fingerprint;
4. if an identical operation is already completed, return the recorded result without a second write;
5. if an identical operation is only `prepared`, ask the trusted transport to reconcile whether the write already happened before issuing it again;
6. only then execute and record `completed`.

For issue/PR creation, the concrete transport used by HARN-010 should make reconciliation possible by persisting the operation ID in provider-visible metadata/body or by another deterministic lookup convention. HARN-009 can define the reconciliation protocol without making live GitHub calls.

This is stronger than an in-memory "seen ID" set and specifically addresses a crash between a successful provider write and local completion recording.

### Dry run

Dry-run must return the same policy/approval decision and canonical intended side effect but never invoke the transport or mark a write as completed. It is inspection, not execution.

## Adapter boundary

The controller should receive a `GuardedGitHubSideEffects` capability, not a generic REST/GraphQL/CLI executor. The guard dispatches a validated closed operation kind to a trusted adapter. There is deliberately no `request(method, url, body)` escape hatch.

A generic-adapter bypass attempt therefore fails at the intent parser/policy boundary before any transport method is selected.

## TDD implications

Tests must cover at least:

- fork/upstream/unknown destination classification;
- allowed fork-local reads and controlled writes;
- upstream reads;
- upstream write without approval -> explicit approval-required outcome and zero transport calls;
- exact upstream approval -> allowed by policy (without using a live upstream transport in CI);
- approval mismatch by operation ID, target, ref, action, or payload -> denied;
- sensitive fork merge/workflow actions require approval;
- operation-ID reuse with changed payload -> denied;
- completed replay -> no duplicate write;
- prepared/unknown-outcome replay -> reconcile first, then avoid duplicate if found;
- prepared replay with no provider-side result -> exactly one retry;
- dry-run -> zero writes/journal completion;
- unknown/generic adapter action -> denied;
- serialization/restart of journal preserves replay behavior;
- static `test_repository_safety.py` remains green.

## Scope decision

Implement the policy, intent/approval/journal contracts, and trusted-adapter dispatch in HARN-009. Do not yet wire live GitHub credentials or the full HARN-010 loop. That separation keeps the safety boundary testable without network access and makes HARN-010 consume a proven capability rather than reimplement permissions.