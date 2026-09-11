# ADR — Deep Agents vs explicit LangGraph for CUC

Status: Accepted

Date: 2026-09-11

Issue: HARN-007 / #8

## Context

CUC already has an explicit HARN-004 LangGraph column-review workflow with deterministic token traversal, explicit revisit semantics, authenticated capability-defined completion gates, and a separately testable scholarly state model. HARN-006 separately provides a clean-context development-review boundary.

HARN-007 evaluates whether LangChain Deep Agents should replace, wrap, or selectively complement this architecture. The Deep Agents evidence used for this decision is pinned to upstream revision `54696577caf3dfcefb662db08b4a8034ec6a35cd`; mutable documentation is not the sole basis for the decision.

The executable comparison is `agent/scripts/evaluate_harn007_architecture.py`. It inspects the real HARN-004/HARN-006 repository sources rather than a toy graph and emits a deterministic report.

## Decision

For parsing, retain explicit HARN-004 LangGraph as the primary orchestrator. Deep Agents is not adopted on the parsing correctness path.

For development-controller execution, defer Deep Agents for development-controller execution until HARN-009 establishes the runtime GitHub side-effect and approval boundary. HARN-010 remains framework-neutral. After HARN-009, Deep Agents may be evaluated as a selective helper harness for open-ended research or implementation tasks, but HARN-002, HARN-006, and HARN-009 remain authoritative state, review, and side-effect boundaries.

For dependencies, do not add `deepagents` to the core agent environment as part of HARN-007.

## Evidence

CUC-side executable observations:

- HARN-004 imports LangGraph while deterministic `column_state.py` does not;
- the graph contains the accepted complete-column stages;
- progression uses the deterministic `next_token_id` cursor;
- required completion gates are looped explicitly;
- capability completion verifiers are bound into the task contract;
- HARN-006 exists as a distinct clean-context development-review boundary;
- the current core environment has no Deep Agents dependency.

Pinned Deep Agents observations at `54696577caf3dfcefb662db08b4a8034ec6a35cd`:

- Deep Agents is an opinionated agent harness built on LangGraph;
- it bundles subagents, filesystem/context management, HITL, skills, and tool surfaces;
- its own guidance recommends LangGraph directly when a custom graph is the correct workflow shape;
- compiled LangGraph graphs can compose with Deep Agents as subagents;
- security boundaries still have to be enforced at the tool/sandbox layer.

These properties mean replacing HARN-004 with a generic Deep Agents loop would not eliminate the CUC-specific deterministic scheduler and completion machinery; it would require reconstructing those semantics outside model choice.

## Consequences

Positive:

- parsing semantics stay explicit, deterministic, and testable;
- no new core dependency or runtime churn is introduced by this research ticket;
- Deep Agents remains available as a later compositional option rather than being rejected categorically;
- HARN-009 can define the actual side-effect boundary before any development helper receives write-capable tools.

Negative / deferred:

- HARN-007 does not produce a live Deep Agents development-controller prototype;
- filesystem/context-management ergonomics are not adopted yet;
- a later bounded prototype is still needed if Deep Agents is reconsidered after HARN-009.

## Revisit criteria

Revisit this ADR if one of the following becomes true:

- HARN-009 is complete and a concrete development-helper use case justifies a Deep Agents dependency;
- HARN-004 semantics materially change so that explicit deterministic graph control is no longer required;
- Deep Agents changes its execution/security model enough that the pinned comparison is no longer representative.
