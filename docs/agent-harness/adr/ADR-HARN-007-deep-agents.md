# ADR-HARN-007 — Keep explicit LangGraph parsing orchestration; borrow selected Deep Agents patterns

- Status: **Accepted**
- Date: 2026-09-11
- Issue: HARN-007 / #8
- Baseline: `935864fca6510aeb4ce02e70a6cf69b2461e298e`
- Tested Deep Agents release: `deepagents==0.7.13`
- Decision: **keep explicit LangGraph** for parsing; selectively borrow Deep Agents ergonomics outside the parsing correctness boundary.

## Context

CUC already has a real complete-column parsing runtime from HARN-004. Its correctness requirements are stronger than a normal model-directed agent loop:

- a complete column is the work/context unit;
- every token must be reviewed in textual order;
- worklists guide evidence attention but cannot narrow scope;
- `ColumnRunState` is the durable token cursor and scholarly state;
- reconciliation/revisits are explicit and provenance-preserving;
- exact HARN-008 completion verifiers are required;
- checkpoint/resume may not skip or duplicate completed controlled effects;
- HARN-015 evaluation is bound to the exact completed run/workload;
- filesystem/GitHub effects remain narrow and policy-controlled.

HARN-006 separately provides a clean, restartable, allowlisted independent development-review context. HARN-009/HARN-010 will own human approval and the bounded development controller.

Deep Agents is a higher-level agent harness built on LangGraph/LangChain and adds useful middleware for subagents, skills, context management, filesystems/backends, permissions and planning. The question was whether those capabilities should replace the explicit HARN-004 graph, merely supplement it, or be rejected.

## Executable comparison

Documentation review alone was not considered sufficient. A deterministic no-network spike pinned the latest published release available for this evaluation, `deepagents==0.7.13`.

The spike used the same essential invariant as the HARN-004 regression workload: a fixed three-token complete column (`t1`, `t2`, `t3`) and explicit instructions that every token must be reviewed in textual order. The scripted model:

1. called `review_token` for `t1` only;
2. then returned the ordinary final answer `Done after one token.`

Deep Agents returned normally. It did not independently reject completion with `t2` and `t3` unreviewed.

Evidence:

- test-first RED head: `5c7c556a0cf75fd82386a74dc1783c9e89c1a00a` — exactly one new failure because the executable spike module did not yet exist;
- GREEN implementation head: `5920d3758442e7d775c7af37f3acb4f89b2606dd`;
- executed synthetic merge: `7b376228f3653aa8639b97619c2795ed31105da6`;
- GitHub Actions run: `34577769467`;
- full suite: `1228 passed, 1 skipped, 23 subtests passed`;
- structured observation: `docs/agent-harness/tickets/HARN-007-deep-agents-evidence.json`.

This observation concerns the **default primary control loop**, not the theoretical customizability of Deep Agents. Deep Agents can be wrapped around deterministic CUC tools/state transitions. But once HARN-004's cursor, completion gates, reconciliation and admission checks remain the authority, Deep Agents supplements that layer rather than replacing it.

## Decision

### Parsing runtime: keep explicit HARN-004 LangGraph

Do not replace the parsing graph with a Deep Agents primary loop.

The decisive reason is semantic, not stylistic: HARN-004 makes complete traversal and completion validity deterministic and external to model obedience. The executable Deep Agents spike demonstrated normal early termination under the same fixed complete-column requirement. Re-establishing CUC's guarantee inside Deep Agents would require retaining or recreating the deterministic state machine we already have.

### Skills: keep HARN-008 authoritative; optionally borrow progressive disclosure

Deep Agents' skill-loading/progressive-disclosure pattern is useful ergonomically. It must not replace HARN-008 capability manifests, package/resource digests, mutation classes, permissions, completion verifiers or exact provenance identity.

A later controller may expose HARN-008-authenticated skills through a Deep-Agents-like disclosure mechanism without changing the authority model.

### Independent development review: isolated subagent launcher is optional transport

Deep Agents' isolated subagent mode is a plausible launcher for HARN-006 clean review packets. HARN-006 remains the correctness boundary: the serialized allowlist, review context identity, exact revision/test binding and disposition routing are not delegated to the framework.

Do not use conversation-forking as the default independent-review mode because carrying implementer context conflicts with the clean-context requirement.

### Context management: candidate for HARN-010 development ergonomics, not parsing state

Summarization/offloading can be useful for long autonomous development sessions. Parsing state, token completion, provenance and whole-column scope remain durable framework-neutral data and must not depend on conversational summarization.

### Filesystem / permissions: do not broaden HARN-004 effects

Deep Agents' general filesystem/backend abstraction provides capabilities CUC parsing does not currently need and would broaden the authority surface. Keep HARN-004's narrow adapters and the separate repository/upstream safety boundary.

### Checkpointing: no advantage over current HARN-004 requirement

Deep Agents inherits LangGraph persistence. HARN-004 already uses LangGraph checkpoints around deterministic CUC transitions, so checkpointing is not a reason to replace the existing graph.

### Observability: keep HARN-005 optional Langfuse sidecar

No observability-stack migration is justified by HARN-007. The deterministic HARN-015 evaluator remains authoritative and HARN-005 remains optional transport/telemetry.

### Dependency surface: do not retain Deep Agents as a parsing dependency

The temporary pin increased the ordinary locked environment from 68 to 85 installed packages in the observed CI run, adding the higher-level LangChain package plus provider/integration dependencies. Package count is not itself a rejection criterion, but there is no parsing capability gain that justifies retaining that surface after the semantic-equivalence test failed.

The spike dependency and executable spike code must therefore be removed before this ADR is merged. The framework-neutral decision record may remain because it adds no Deep Agents dependency and makes the evidence machine-checkable.

## Consequences

HARN-010 should build the bounded autonomous development controller on the existing framework-neutral contracts and explicit LangGraph/runtime pieces. It may evaluate selected Deep Agents patterns for:

- isolated clean-context subagent launching;
- progressive skill disclosure backed by HARN-008;
- long-session context ergonomics.

Those are implementation conveniences, not correctness authorities. HARN-010 does **not** need to wait for a second parsing-runtime rewrite.

## Alternatives rejected

### Adopt Deep Agents as the primary parsing runtime

Rejected for now. The tested primary loop can terminate normally before complete token traversal. Custom wrappers can restore the invariant, but then the deterministic HARN-004 layer remains necessary.

### Reject Deep Agents entirely

Rejected. Isolated subagents, skill disclosure and context-management patterns may reduce controller glue and are worth reusing selectively where HARN contracts remain authoritative.

### Replace HARN-006 reviewer with Deep Agents subagents

Rejected. A launcher cannot replace the allowlisted clean review packet, exact revision binding and existing review state-machine semantics.

## Revisit criteria

Reconsider primary adoption only if a future Deep Agents release supplies a demonstrable framework-level mechanism that can express and enforce the CUC complete-column state machine without duplicating HARN-004, and it wins the same fixed-workload tests on correctness, resume semantics, side-effect isolation and dependency/operational cost.
