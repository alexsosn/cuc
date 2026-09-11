# HARN-007 — Deep Agents vs explicit LangGraph research

Date: 2026-09-11

## Question

Should CUC replace, wrap, or selectively complement the explicit LangGraph parsing harness with LangChain Deep Agents? The comparison must use the real HARN-004 complete-column workflow and, secondarily, HARN-006 development review rather than a generic agent demo.

## Repository evidence

### HARN-004 parsing workflow

`agent/harness/langgraph_column_review.py` owns an explicit Graph API state machine around the scholarly `ColumnRunState`:

1. initialize authenticated skill context;
2. collect evidence for the deterministic `next_token_id`;
3. adjudicate that token;
4. repeat until the initial pass has visited every token;
5. reconcile the complete column;
6. execute explicit revisit requests;
7. close reconciliation;
8. execute every capability-defined completion gate;
9. mark the column complete and evaluate the exact completed revision.

The graph does not let model output choose token scope, remove completion verifiers, or mark deterministic gates as passed. The durable scholarly state is outside LangGraph and can be tested independently.

### HARN-006 development review

`agent/harness/development_reviewer.py` establishes an allowlisted, serializable clean-context boundary for development review. The reviewer sees the task projection, final diff/snapshot evidence, exact head/executed revision, successful tests/evals, policy references, and rubric. It excludes implementer conversational state and uses HARN-002 for disposition routing.

This means subagent isolation is already an application invariant, not a missing framework primitive.

## Current Deep Agents architecture checked on 2026-09-11

Primary sources:

- https://github.com/langchain-ai/deepagents
- https://github.com/langchain-ai/deepagents/blob/main/README.md
- https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md
- https://docs.langchain.com/oss/python/deepagents/overview

Observed properties relevant to CUC:

- `create_deep_agent` is an opinionated harness layered on LangChain `create_agent`; LangGraph remains the runtime underneath.
- The bundled harness targets long-horizon tool-using work and includes planning/todos, filesystem/context offloading, subagents with isolated context, memory/skills, and optional shell/tool surfaces.
- Deep Agents supports human-in-the-loop/tool approval patterns and custom tools/middleware, but its own security guidance states that boundaries must be enforced at the tool/sandbox level: the model can do anything exposed by its tools.
- The project explicitly recommends dropping to LangGraph when the ordinary agent loop is not the right workflow shape and a custom graph is needed.
- A compiled LangGraph graph can itself be used as a subagent, so Deep Agents and explicit LangGraph are composable rather than mutually exclusive runtimes.

## Semantic comparison

| Requirement | Explicit HARN-004 LangGraph | Deep Agents as primary parser orchestrator | Deep Agents outside/around HARN-004 |
|---|---|---|---|
| Complete column is the fixed work unit | encoded in `ColumnTask` / `ColumnRunState` | requires CUC-specific tool/middleware/state contract | preserved by HARN-004 |
| Every token visited in textual order | deterministic cursor and transition guards | generic agent loop does not supply this invariant; must be reimplemented outside model choice | preserved by HARN-004 |
| Whole-column context | explicit snapshot in state | feasible | feasible |
| Capability-defined completion gates | exact manifest binding and gate loop | would still require CUC-specific deterministic verifier wrapper | preserved by HARN-004 |
| Explicit revisit semantics | state-machine events and queue | would require CUC-specific state/tool protocol | preserved by HARN-004 |
| Model cannot widen/narrow token scope | structural | would require wrapping scope in deterministic tools | preserved by HARN-004 |
| Checkpoint/resume | LangGraph checkpointer boundary | available because Deep Agents uses LangGraph | same runtime capability |
| Skills | authenticated HARN-008 capability package | Deep Agents has skills, but HARN-008 provenance/completion contract would still need CUC binding | can expose authenticated capability through wrapper |
| Observability | HARN-005 sidecar, framework-neutral | possible through LangGraph/LangSmith ecosystem | possible |
| Filesystem/context offloading | deliberately absent from parser graph | bundled feature | can remain outside scholarly state |
| Subagents | not needed for deterministic token scheduler | bundled feature | potentially useful for research/evidence helpers |
| HITL | application policy required | tool-call approval primitive available | useful only after HARN-009 wraps side effects |
| Fork/upstream write safety | not part of parsing graph | not supplied by framework | must remain HARN-009 application capability policy |
| Clean-context development review | explicit HARN-006 packet | subagent isolation is ergonomically useful but does not replace exact packet/revision binding | possible wrapper around HARN-006 |

## Key architectural observation

Replacing HARN-004 with a Deep Agents loop does not remove CUC-specific orchestration. To preserve the accepted semantics, CUC would still need an external deterministic cursor, authenticated capability binding, revisit queue, completion-verifier loop, evaluator boundary, and exact state transitions. At that point the existing HARN-004 graph is the deterministic component being reconstructed.

Deep Agents offers more leverage for open-ended development-controller phases: research, planning, bounded implementation delegation, filesystem/context management, and isolated helper/subagents. Those are not sufficient safety boundaries for HARN-009, and they should only receive explicit CUC capabilities after the side-effect policy is implemented.

## Research conclusion to test in the architecture model

1. **Parsing runtime:** retain explicit HARN-004 LangGraph as the primary orchestrator. Do not add Deep Agents to the parsing correctness path.
2. **Development controller:** keep HARN-010 contracts framework-neutral. After HARN-009 exists, Deep Agents may be evaluated as a selective execution harness for open-ended research/implementation helpers, while HARN-002/HARN-006/HARN-009 remain the authoritative state, review, and side-effect boundaries.
3. **Dependency:** do not add `deepagents` to the core agent environment for this research ticket. A dependency is justified only by a later concrete implementation that uses it.

The next gate is an executable repository model that checks this conclusion against the current HARN-004/HARN-006 source shape rather than leaving the ADR as prose.