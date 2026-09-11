# HARN-007 — Deep Agents vs explicit LangGraph: research

## Question

Should CUC replace the explicit HARN-004 complete-column LangGraph runtime with Deep Agents, reuse selected Deep Agents components, or keep the current runtime unchanged?

The comparison target is the **real `review-automatic-parsing` workflow**: one complete column, every token in textual order, whole-column context, explicit reconciliation/revisits, exact completion verifiers, checkpoint/resume, HARN-015 evaluation binding, and no hand-editing of generated `auto_parsing/**`.

## Fixed CUC baseline

Baseline revision: `935864fca6510aeb4ce02e70a6cf69b2461e298e` (`agent-harness-safety`).

HARN-004 already provides:

- explicit Graph API nodes for skill-context initialization, evidence, adjudication, reconciliation, revisits, completion gates and evaluation;
- the framework-neutral `ColumnRunState` as the only token cursor/durable scholarly state;
- every-token traversal enforced by `apply_column_event`, not by model obedience;
- exact HARN-008 capability/provenance and verifier admission checks;
- checkpoint/resume with already-completed effects not repeated;
- evaluation artifacts bound to the exact completed run/revision/workload;
- narrow adapters instead of unrestricted filesystem/GitHub mutation.

HARN-006 independently provides a clean, restartable development-review packet. Its correctness does not depend on a particular model/provider/subagent framework.

## Deep Agents snapshot checked on 2026-09-11

Released package used for the executable spike: `deepagents==0.7.13` (PyPI release 2026-09-02, Python >=3.11,<4). A `0.7.14` release PR is open upstream, so the spike uses the latest published release rather than unreleased `main`.

Primary sources inspected:

- package/repository overview: https://github.com/langchain-ai/deepagents
- architecture: https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md
- subagents middleware: https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/subagents.py
- skills middleware: https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/skills.py
- filesystem middleware: https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/filesystem.py
- PyPI release metadata: https://pypi.org/project/deepagents/

Deep Agents is itself built on LangChain `create_agent` and LangGraph. It adds an opinionated middleware stack: todo/planning, filesystem/backends, subagent delegation, context management/summarization, skills, permissions/HITL and provider profiles.

## Feature-by-feature fit

### Complete-column deterministic traversal

Deep Agents' normal control loop is model-directed tool use. Nothing in `create_deep_agent` makes a CUC token cursor, requires a tool call for every token, or prevents a model from returning a final message early. CUC currently has those guarantees in deterministic domain transitions plus explicit graph routing.

This is the central spike hypothesis: a scripted model should be able to review token 1 of a 3-token column and then end normally. If observed, primary adoption would require wrapping/recreating HARN-004's deterministic state machine, eliminating the claimed simplification.

### Skills

Deep Agents `SkillsMiddleware` matches the general Agent Skills directory pattern and supports progressive disclosure. That is compatible with CUC's human-readable `.agents/skills/*/SKILL.md` packages.

However CUC HARN-008 additionally authenticates exact capability manifests, helper/authoritative resource digests, mutation classes, completion verifiers and safety permissions. Deep Agents' default skill discovery (including source layering/last-wins behavior) must not replace that stronger admission/provenance contract.

Useful pattern to borrow: skill discovery/progressive disclosure for model context. Keep HARN-008 as authority.

### Subagents / clean contexts

Deep Agents subagents are isolated by default; `mode="fork"` carries parent conversation and is marked experimental upstream. The isolated mode is a good ergonomic fit for launching a clean HARN-006 reviewer context.

But HARN-006 already enforces the important boundary structurally: allowlisted serialized context, distinct context identity, exact revision/test binding, no implementer scratchpad, and deterministic disposition projection. Deep Agents could later be one **launcher/transport** for that packet; it should not own reviewer correctness semantics.

### Context management

Automatic summarization/offloading is valuable for long development-controller sessions (future HARN-010). It is a poor default for the parsing core because `review-automatic-parsing` requires the complete column to remain available as context and exact state/provenance already lives outside chat history.

Potential reuse: development-controller conversational/context ergonomics only, with durable HARN contracts outside the model context.

### Filesystem and permissions

Deep Agents includes a broad filesystem/backend abstraction and path permissions/HITL. CUC intentionally uses narrow effect adapters and separate fork/upstream policy because scholarly data and GitHub writes have different trust classes.

Using the general filesystem surface for HARN-004 would expand authority without solving a missing parsing requirement. Upstream has also had recent permission/configuration footgun reports, so CUC should not weaken its existing explicit side-effect boundary merely to reduce glue.

### Checkpointing / persistence

Deep Agents inherits LangGraph persistence/checkpointing. This is not a differentiator against HARN-004, which already uses LangGraph checkpoints around deterministic state transitions.

### Observability

Deep Agents emphasizes LangSmith. CUC already has optional HARN-005 Langfuse telemetry and HARN-015 deterministic evaluation contracts. Switching observability stacks would add coupling without a parsing benefit.

### Dependency/churn surface

Current core project dependencies are `langgraph==1.2.11` plus spaCy; Langfuse is an optional extra. Deep Agents adds the higher-level LangChain agent/middleware harness and its transitive surface. The research spike will temporarily pin `deepagents==0.7.13` and record the resulting lock/test behavior, then remove it if the ADR does not adopt Deep Agents as a runtime dependency.

## Risks / recent upstream evidence

Recent upstream reports worth treating as risk evidence rather than automatic blockers:

- tool-body exceptions can escape the agent graph rather than becoming model-visible recoverable tool errors;
- checkpointed skills/memory have had stale-source reports;
- filesystem permission customization has had reported ways to accidentally drop deny rules;
- some subagent/runtime combinations are still actively changing.

These reinforce the need to keep CUC correctness in framework-neutral contracts even if Deep Agents is used for ergonomics.

## Research hypothesis

Expected outcome unless the executable spike disproves it:

1. **Keep explicit LangGraph as the parsing orchestration authority.** Deep Agents does not replace deterministic every-token/completion semantics without wrapping the same state machine.
2. **Borrow selected patterns, not the primary runtime:** isolated subagent launching for HARN-006/HARN-010 and optional skill/context ergonomics are potentially useful.
3. Do **not** add Deep Agents as a mandatory CUC parsing dependency solely for filesystem, checkpointing, skills or observability; those requirements are already covered with narrower authority.

The ADR remains provisional until the same-workload executable spike and independent review are complete.
