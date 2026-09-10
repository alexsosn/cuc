# HARN-006 — Clean-context independent development reviewer: research

## Question

What is the smallest framework-neutral addition that makes development review structurally independent from the implementation context while preserving the HARN-002 development state machine as the authority?

## Repository evidence

### HARN-002 already owns durable review semantics

`agent/harness/contracts.py` already defines:

- `ReviewFinding` with severity, evidence references and blocking status;
- `ReviewResult` with `review_id`, `reviewer_id`, distinct `review_context_id`, exact `inspected_sha`, disposition and structured findings;
- `RunState.review`, `RunPhase.REVIEW`, verified-head requirements and complete-state invariants.

`agent/harness/state_machine.py` already enforces:

- review can be recorded only from `RunPhase.REVIEW`;
- `ReviewResult.inspected_sha` must equal `RunState.verified_head_sha`;
- `APPROVE` moves to `COMPLETE`;
- `REQUEST_CHANGES` moves back to `IMPLEMENT`, clears verified head, and retains the structured review for the revision loop;
- `ESCALATE` moves to `AWAITING_HUMAN` with an explicit resume phase.

Therefore HARN-006 must not create a second review disposition enum, a second revision counter, or a parallel review state machine.

### HARN-005 already exposes development-review telemetry

`agent/harness/telemetry.py` projects `development_review_id`, `development_reviewer_id`, `development_review_context_id`, inspected head, disposition, finding count and blocking count from the HARN-002 state. No telemetry identifier is required by durable state.

Therefore HARN-006 does not need a Langfuse/OpenTelemetry dependency. Its data contracts should be transport-neutral and automatically remain observable through the existing development projection after `ReviewRecorded` is applied.

## Threat model

The independence problem is primarily a context-construction and admission problem, not a prompt-style problem.

A reviewer must not receive, by default:

- implementer chain-of-thought or scratchpad;
- implementation-agent conversation history;
- implementation self-justification / rationale supplied after the fact;
- hidden model state or previous reviewer output used as persuasion;
- scholarly expert feedback/corrected morphology treated as PR/code approval evidence.

A reviewer does need a bounded, auditable packet containing only:

- the GitHub task/specification and acceptance criteria;
- exact base/head/executed revision identities;
- final diff/snapshot references or supplied final diff text;
- actual test and eval evidence for the current change/head;
- applicable repository/invariant policy references;
- an explicit review rubric.

The key rule is allowlisting: arbitrary mappings from an implementation agent must not be merged into reviewer context. A blacklist is insufficient because aliases/nesting can smuggle the same content under new keys.

## Existing evidence that should be reused, not trusted blindly

`RunState` is useful for task, declared tests, current change, test/eval results and verified head. However the context builder must independently validate that the state is genuinely review-ready and that current successful test/eval evidence refers to the verified head/executed revision. The existing HARN-002 state constructor and verification transition already enforce most of this; HARN-006 should fail closed if asked to build a context from another phase.

External final-diff material is necessarily supplied by a repository adapter in a later controller integration. HARN-006 should make its identity explicit (`base_sha`, `head_sha`, diff digest/content) and bind `head_sha` to `RunState.verified_head_sha`.

## Proposed minimal contracts

### `DevelopmentReviewContext`

A deterministic, serializable reviewer packet with explicit fields only:

- schema version;
- `review_context_id` derived from canonical packet content;
- development `run_id` and task projection (`task_id`, title, objective, acceptance criteria);
- exact `base_sha`, `head_sha`, `executed_sha`;
- current `change_id` and changed paths;
- final diff text (or a bounded canonical final-diff payload) plus digest;
- current successful test/eval projections and evidence refs;
- policy/invariant references;
- explicit rubric items.

Do not include `ResearchArtifact.summary`, `PlanArtifact.summary`, implementation conversation, self-justification, scholarly expert feedback, or prior review narrative by default. Research/plan may contain useful facts, but including their prose also carries implementer framing; the task/spec + final artifact + objective gate evidence is the cleaner independent default.

### Context builder

`build_development_review_context(...)` should:

1. require `RunPhase.REVIEW` and a non-null verified head/current change;
2. require caller `head_sha == state.verified_head_sha` and an exact `base_sha`;
3. derive one `executed_sha` from current successful verification evidence and reject mixed/stale evidence;
4. copy only allowlisted task/change/test/eval scalar/provenance data;
5. accept policy/invariant references and rubric as explicit string tuples;
6. accept final diff as a dedicated argument, not a generic context mapping;
7. calculate deterministic SHA-256 identities for diff and canonical context;
8. reject empty rubric/policy evidence and impossible review-ready state.

### Reviewer adapter and runner

A callable receives only `DevelopmentReviewContext`, not `RunState` or implementation state. It returns the existing HARN-002 `ReviewResult`.

`run_independent_development_review(...)` validates:

- returned value is `ReviewResult`;
- reviewer id equals the requested reviewer identity;
- result `review_context_id` equals the packet's exact id;
- result `inspected_sha` equals context head;
- `review_id` is attributable and not equal to the implementation `run_id`/context id;
- HARN-002 disposition/finding invariants remain intact.

It returns the validated `ReviewResult`; state mutation is a separate helper that delegates to `apply_event(state, ReviewRecorded(result))`.

## Independence semantics

“Logically independent” cannot be proven from a string saying “independent.” The harness can enforce structural preconditions:

- new context id derived only from clean allowlisted inputs;
- reviewer receives no `RunState` object and no generic implementation-context mapping;
- no research/plan/self-justification text in default packet;
- reviewer id is explicit and distinct from an optional implementer id when supplied;
- reviewer execution is restartable from the serialized context alone;
- prior reviewer output is not automatically inserted into a retry context.

A model/provider-specific clean-session/subagent launcher belongs in later orchestration integration; HARN-006 correctness must not depend on one provider or LangGraph/Deep Agents.

## Synthetic-defect strategy

HARN-006 cannot prove that an arbitrary LLM catches every code defect. Deterministic tests can prove the pipeline exposes a seeded defect to a reviewer adapter without implementation framing:

- construct a final diff containing a known unsafe pattern;
- use a deterministic reviewer fixture that reads only the context and returns a blocking `ReviewFinding` when the pattern is visible;
- assert the finding persists in the existing `ReviewResult` and `ReviewRecorded` routes the run back to `IMPLEMENT`.

This tests the review transport/context/routing mechanics, not model intelligence.

## Non-goals

- scholarly expert review of morphology (HARN-015);
- GitHub write authorization/HITL (HARN-009);
- bounded whole development loop (HARN-010);
- model/provider selection or Deep Agents decision (HARN-007);
- Langfuse transport (already optional HARN-005 sidecar);
- automatic merge/finalization by the reviewer.

## Risks to attack in adversarial review

- generic metadata kwargs reintroduce implementer prose;
- nested arbitrary JSON fields bypass allowlisting;
- stale/mixed executed SHA enters the packet;
- review result is accepted for another context/head/reviewer;
- reviewer sees prior scholarly expert feedback and treats it as code approval;
- restarting review requires implementation conversation state;
- REQUEST_CHANGES is handled by custom mutation instead of HARN-002 transition;
- caller can forge a context id instead of deriving it canonically;
- raw diff or evidence is omitted from the context while only hashes remain, making review impossible;
- telemetry/provider dependencies become correctness-critical.
