# HARN-007 — implementation and validation plan

## Decision gate

The ticket will produce an ADR backed by executable repository evidence. It will not add Deep Agents to production dependencies merely to run a framework demo.

## Executable comparison model

Add `agent/scripts/evaluate_harn007_architecture.py` that uses Python AST/source inspection over the current repository to produce a deterministic JSON report. The report must establish, from actual code rather than hard-coded claims:

- the explicit HARN-004 graph contains the accepted complete-column stages;
- token progression is driven through `ColumnRunState.next_token_id` rather than a model-selected worklist;
- completion gates are looped from `required_completion_gates`;
- HARN-004 imports LangGraph but deterministic `column_state.py` does not;
- HARN-006 exists as a separate clean-context development-review boundary and imports no Deep Agents runtime;
- the core environment contains no `deepagents` dependency today.

The report then records the framework research facts from the dated ADR as external observations and maps the combined evidence to two separate recommendations: parser orchestration and development-controller execution.

## TDD order

1. Commit this research and plan.
2. Add RED tests for the architecture evaluator before the evaluator exists.
3. Confirm the full CI gate fails for the intended missing module/command.
4. Implement the evaluator with no runtime dependency changes.
5. Run the complete agent test suite and Langfuse smoke gate.
6. Update the ADR with the measured report and final decision.
7. Perform logically independent adversarial review from issue #8 + final diff + test evidence only. Attack semantic inequivalence, hard-coded conclusions, framework-marketing assumptions, and hidden HARN-009 safety dependencies.
8. If review finds a blocker, add a failing regression test before changing the implementation, then repeat the full gate and independent review.

## Acceptance mapping

- Representative real workflow: the evaluator inspects HARN-004, not a toy graph.
- Complete-column/every-token semantics: verified from actual graph/state source.
- HARN-008 skill preservation: ADR explicitly retains authenticated capability binding rather than replacing it with generic Deep Agents skills.
- Clean-context reviewer: HARN-006 considered independently.
- Checkpoint/observability: recognized as shared/composable LangGraph capabilities, not credited as Deep Agents-only advantages.
- Permissions/HITL: considered useful ergonomics but not a substitute for HARN-009 capability enforcement.
- Decision: ADR selects parser and development-controller recommendations separately.

## Non-goals

- paid/provider model benchmarking;
- replacing HARN-004;
- implementing HARN-009 or HARN-010 in this ticket;
- adding a shell/filesystem write surface to the parser;
- treating line-count reduction as architectural evidence.