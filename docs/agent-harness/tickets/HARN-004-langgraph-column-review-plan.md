# HARN-004 — LangGraph real column-review vertical slice

## Status

Research and implementation plan, refreshed after HARN-019 was merged. The branch is based on the current `agent-harness-safety` integration head.

## Source of truth

This graph orchestrates the **existing agentic parsing workflow**; it does not invent a research-plan-TDD parsing loop.

Authoritative semantics come from `.agents/skills/review-automatic-parsing/SKILL.md`, HARN-008 capability manifests, HARN-018 column-state contracts, and HARN-015 evaluation contracts:

- one complete column is the context/work unit;
- every token is reviewed in textual order;
- worklists/lints/alignments prioritize evidence but never narrow token scope;
- the whole column remains visible as context throughout the run;
- decisions preserve evidence provenance and defensible alternatives;
- reconciliation may create explicit revisit requests without erasing prior decisions;
- completion requires the exact capability verifier list;
- `auto_parsing/**` remains generated and is never hand-edited;
- systematic parser/linter/skill problems are escalated into the separate GitHub development loop.

The current `review-automatic-parsing` capability requires, in order:

1. `review-status-clean`
2. `lint-error-delta-no-regression`
3. `report-token-count`

The orchestration layer must construct `ColumnTask.required_completion_gates` from the exact loaded capability manifest. Caller/model-supplied gate lists are forbidden.

## Framework decision

Use LangGraph **Graph API** for the vertical slice.

Reasons:

- `ColumnRunState` is already an explicit shared durable state;
- token traversal, revisit routing and completion are real loops/branches;
- acceptance requires inspectable checkpoint/resume boundaries;
- explicit nodes/edges make skipped-token and false-completion bugs observable;
- Graph API checkpoints at super-step boundaries and can use `InMemorySaver` for tests.

Functional API is rejected as the primary API for this slice because its main advantage—minimally wrapping procedural control flow—does not help when CUC already has an explicit domain state machine.

Research checked against current LangGraph 1.2.x documentation and Python 3.13 compatibility on 2026-09-08. Re-check exact pin immediately before dependency integration.

## Dependency/reproducibility boundary

HARN-019 is complete, and the HARN-021 trusted default-branch lock transport has now passed its live HARN-004 integration proof.

Ordinary `Agent tests` uses:

```text
uv sync --locked --no-install-project
```

so stale project metadata fails before tests. Dependency-bearing work must:

1. add the dependency to `agent/pyproject.toml`;
2. let the read-only `Dependency lock artifact` workflow run `uv lock` with pinned uv;
3. let the trusted default-branch `workflow_run` resolve again under `contents: read`, hand off a provenance-bound same-run artifact, and commit only `agent/uv.lock` from a separate `contents: write` job;
4. verify the bot commit has the triggering HARN head as its exact parent and changes only `agent/uv.lock`;
5. make a connector/human-authored follow-up commit so ordinary `Agent tests` independently evaluates the resulting head through `uv sync --locked`.

Live proof on 2026-09-08/09:

- triggering connector-authored HARN-004 head: `e4a2a4e062773503b0375e80c2d6dea1d1acd8d6`;
- trusted workflow run: `34280422765`, with read-only `resolve-lock` and separate write-only `apply-lock`, both successful;
- generated lock commit: `d50d615b4adaad3cfca27854942e87738c968f58`;
- exact parent: `e4a2a4e062773503b0375e80c2d6dea1d1acd8d6`;
- changed path: only `agent/uv.lock`;
- author: `github-actions[bot]`.

For this slice, `langgraph==1.2.11` is directly pinned in project metadata and represented by uv-generated lock bytes; the lock is never hand-edited.

## Runtime boundary

Proposed module: `agent/harness/langgraph_column_review.py`.

LangGraph imports are allowed only in orchestration/runtime code. `column_state.py`, `skill_capabilities.py`, `parsing_evaluation.py`, the scorer, parser and linter remain framework-neutral.

### Capability → task construction

`build_column_task_from_capability(...)` receives an already-loaded `SkillCapabilityManifest` plus `SkillProvenance` and corpus/task identity. It must derive:

- `CapabilityRef.canonical_name`;
- `CapabilityRef.contract_version`;
- the capability provenance digest;
- `required_completion_gates=manifest.completion_verifiers` preserving exact order.

Hard errors:

- capability is not `review-automatic-parsing`;
- capability `work_unit != "column"`;
- manifest/provenance identity mismatch;
- caller attempts to supply, omit, reorder or substitute completion gates.

## Effect adapters

The graph itself does not mutate corpus files, invoke unrestricted shell, or call GitHub.

Inject narrow adapters for:

- evidence collection for the current token;
- scholarly adjudication producing a `TokenDecision`;
- reconciliation producing findings/revisit requests;
- named completion verifiers;
- evaluation artifact construction.

Tests use deterministic fake adapters. Model/tool integration comes later.

Controlled effect calls use deterministic operation IDs derived from task/run + phase + token/request/gate identity. Results are persisted in orchestration state so replay/resume returns the prior result instead of repeating an already completed effect.

## Proposed graph

```text
START
  -> initialize
  -> review_next_token
       -> review_next_token     while initial cursor incomplete
       -> reconcile             after every initial token
  -> route_reconciliation
       -> revisit_next          while explicit revisit work remains
       -> completion_gate       when reconciliation is closable/closed
  -> completion_gate
       -> completion_gate       until exact capability verifier list exhausted
       -> evaluate              when all gates pass
  -> finalize
  -> END
```

The graph stores/forwards the existing `ColumnRunState`; it must not invent a second token cursor.

## TDD gates

Write tests before LangGraph implementation.

### A. Capability binding

- exact manifest creates exact `CapabilityRef` and gate list;
- weakened/reordered/substituted gates are impossible;
- manifest/provenance mismatch fails before graph execution;
- a different skill capability cannot be substituted.

### B. Every-token scope/order

- three-token column adjudicates `t1`, `t2`, `t3` exactly once and in order;
- priority/worklist IDs do not change inclusion or order;
- reconciliation/completion cannot occur with a skipped initial token;
- adapters always receive the complete immutable column snapshot as context.

### C. Checkpoint/resume/replay

- fail after token 2, resume same `thread_id`, and prove already completed controlled effects are not duplicated;
- resume follows `ColumnRunState.cursor`, not a separate orchestration counter;
- a different `thread_id` starts a fresh independent run.

### D. Revisit/reconciliation

- reconciliation can enqueue an explicit revisit;
- revisit produces a linked new decision without re-running the initial pass;
- unresolved required findings prevent completion.

### E. Completion/evaluation

- verifier calls occur in exact manifest order;
- failed verifier prevents evaluation/finalization;
- evaluation forwards HARN-015 measurements/artifact identity rather than recomputing morphology scores;
- final state requires every token, every required revisit and every required verifier.

### F. Isolation/dependency

- deterministic domain modules contain no LangGraph imports;
- orchestration imports the locked LangGraph version successfully;
- no unrestricted filesystem/GitHub adapter exists;
- full existing suite remains green.

## Independent adversarial review rubric

Reject if any of these are possible:

- flagged/worklist tokens become the processing scope;
- caller/model weakens completion gates;
- capability provenance is caller-authored rather than derived from loaded HARN-008 data;
- graph maintains a second token cursor;
- resume duplicates already completed controlled effects;
- revisit loses request/prior-decision provenance;
- reviewed gold or expert correction enters model workload context;
- graph hand-edits `auto_parsing/**`;
- deterministic domain modules acquire LangGraph/LangChain/Langfuse types;
- dependency lock is hand-edited or ordinary CI resolves stale dependencies.

## Non-goals

- model-backend comparison (HARN-016);
- Langfuse transport (HARN-005);
- Deep Agents comparison (HARN-007);
- production checkpoint database choice;
- autonomous GitHub development controller;
- changing scholarly parsing rules or source skill semantics.
