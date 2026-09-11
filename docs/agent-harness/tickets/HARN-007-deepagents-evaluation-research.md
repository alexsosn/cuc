# HARN-007 — Deep Agents vs explicit LangGraph: research

## Scope

Evaluate Deep Agents as a possible worker/orchestration layer for the two distinct CUC agent loops without changing domain semantics:

1. scholarly parsing: complete-column, every-token review driven by `review-automatic-parsing` and HARN-018 state;
2. software development: issue-driven research → plan → TDD → implementation → verification → logically independent review → bounded finalize/human approval.

This is a framework-fit study, not permission to collapse the two loops or to replace deterministic contracts with model discretion.

## Repository baseline after HARN-006

Current `agent-harness-safety` head is `171a826772c59d719ed7a5c6644e210e7a18cdb9`.

The boundaries to preserve are now concrete:

- HARN-004: explicit LangGraph `StateGraph` around framework-neutral `ColumnRunState`; exact complete-column traversal/revisit/completion semantics.
- HARN-008: canonical skills are versioned capabilities; framework-native skill discovery is not authoritative for identity, write scopes, provenance, or completion gates.
- HARN-015/016: evaluation and model comparison remain framework/provider neutral.
- HARN-006: finalized clean, serializable `DevelopmentReviewContext` + structured `DevelopmentReviewReport`; exact verified-head/executed-revision evidence binding; HARN-002 remains the only development-review disposition state machine.
- HARN-009: will own human authorization and GitHub side-effect boundaries.
- HARN-010: will own budgets, retries, termination and the bounded development controller.

A higher-level agent harness is acceptable only if it composes behind these contracts.

## Upstream snapshot — refreshed 2026-09-11

Primary sources:

- Deep Agents repository and releases: https://github.com/langchain-ai/deepagents
- Deep Agents overview/customization/subagents/skills/HITL/backends: https://docs.langchain.com/oss/python/deepagents/
- exact release source for `deepagents==0.7.13`.

GitHub's latest-release endpoint still reports `deepagents==0.7.13`, published 2026-09-02. A 0.7.14 release PR exists upstream but is not a published release, so 0.7.13 remains the reproducible spike target.

Deep Agents is an opinionated harness built above LangChain/LangGraph. Relevant capabilities include isolated/custom subagents, distinct model/tool/skill configuration, structured output, LangGraph persistence, filesystem backends, and tool-call HITL.

Important source/default behavior from 0.7.13 remains relevant to negative-capability testing:

- default backend/middleware are installed unless overridden;
- a general-purpose subagent may be added by the harness profile;
- custom subagents can inherit parent tools/permissions unless explicitly constrained;
- filesystem, summarization and patch-tool-call middleware are part of the general harness shape;
- model-specific profile behavior can add prompt/middleware behavior;
- security guidance treats tool/sandbox boundaries—not prompts—as the hard authorization layer.

These defaults are useful for general long-horizon work but mean CUC must test what is *absent*, not only what works.

## Fit analysis

### Complete-column scholarly parsing

**Retain explicit HARN-004 LangGraph.**

Correctness depends on an inspectable deterministic state machine: one complete `ColumnSnapshot`, every token in textual order, one durable cursor, explicit revisit queue, exact completion verifier tuple, no false completion, checkpoint/resume without duplicate controlled effects, and evaluation bound to completed state.

A model-directed Deep Agents outer loop would weaken inspectability. Deep Agents may invoke the already-compiled HARN-004 graph as one bounded capability, but it must never choose token scope/order or become a second traversal authority.

### Skill loading

Deep Agents may expose `.agents/skills` text to a worker, but HARN-008 remains authoritative. Model-facing skill selection must not determine capability provenance, effects, permissions, or completion gates.

### Independent development reviewer

**Best candidate for a narrow experiment.**

HARN-006 now supplies the exact framework-neutral input/output contract. An isolated worker could receive only serialized `DevelopmentReviewContext` and return `DevelopmentReviewReport`.

The experiment must prove that default/parent prompt history, tools, filesystem state, implementation narrative and unrelated skills are absent. Framework output must be revalidated by HARN-006 exactly as any other reviewer adapter is.

### Human approval / GitHub side effects

Deep Agents HITL can be useful for presentation/pause/resume. It is not authorization. HARN-009 must still bind approval to exact operation, repository/ref/path/effect identity and enforce replay/fail-closed rules before any connector call.

### Bounded development controller

A hybrid remains plausible:

```text
bounded deterministic development controller
  ├─ research worker       -> optional Deep Agent
  ├─ implementation worker -> optional Deep Agent
  ├─ independent reviewer  -> optional isolated Deep Agent
  └─ approval/effect port  -> deterministic HARN-009 boundary

complete-column parsing
  └─ explicit HARN-004 LangGraph
       └─ model/evidence adapters may use worker harnesses behind typed ports
```

Deep Agents can add worker ergonomics (context management, delegation, long-horizon planning), but it should not decide verified-head truth, test success, retry budgets, termination, parsing traversal, or authorization.

## Risks to measure

1. Semantic drift: framework planning must not replace HARN-004/HARN-002 transitions.
2. Hidden context: default prompts, summarization, filesystem/memory state or inheritance can break HARN-006 clean-context review.
3. Permission widening: inherited filesystem/shell/MCP tools must not exceed declared experiment scopes.
4. Duplicate state authority: framework checkpoint state must not compete with CUC durable state.
5. Dependency/API churn: 0.7.x is still evolving; any adoption needs a narrow adapter.
6. Testability: exact resume/termination/isolation properties must be provable without live-model nondeterminism where possible.
7. Default-stack opacity: GP subagent, inherited tools/permissions, filesystem middleware and profile behavior require explicit negative tests.

## Falsifiable hypothesis

Default hypothesis: **hybrid**, with explicit LangGraph retained as the authoritative parsing state machine and Deep Agents adopted only if executable experiments show material value behind typed development-worker/reviewer/HITL-presentation ports.

The spike should reject Deep Agents for a role when the wrapper adds no measurable behavior beyond existing contracts or requires weakening isolation/determinism. Production dependency is not justified by API convenience alone.