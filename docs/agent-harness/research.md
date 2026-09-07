# Preliminary Research: Agent Harness for CUC

Status: seed research, not an implementation decision.

## Problem

CUC already contains a substantial deterministic morphological parsing pipeline, domain-specific audit/parse skills, reviewed gold data, linting, and morphology agreement metrics. The missing piece is a coherent agent harness that can orchestrate research, planning, TDD, implementation, regression evaluation, and logically independent adversarial review without replacing the deterministic parser or using GitHub CI as the inner development loop.

The target development environment is the isolated fork `alexsosn/cuc`; upstream `DT-UCPH/cuc` remains read-only until explicit human authorization for final submission.

## Current CUC assets to preserve

- Deterministic parsing pipeline under `agent/pipeline/` and `agent/scripts/`.
- DULAT lookups, context reranking, provenance and attestation signals.
- Ordered linguistic heuristics and safeguards.
- `.agents/skills/*` packages with domain instructions, references, scripts, and some agent metadata.
- `reviewed/**` as curated gold data.
- `auto_parsing/**` as generated data that must never be hand-edited.
- `scripts/score_reviewed_morphology.py` and its per-token / aggregate agreement metrics.
- Lint and step-change safeguards.
- Fork-safety policy and repository-safety regression test.

## Candidate stack

### LangGraph: orchestration runtime

LangGraph is the strongest fit for the outer workflow because CUC needs a mixed deterministic + agentic process rather than a single model/tool loop. Relevant capabilities are checkpointed state, resumable execution, explicit branching, human-in-the-loop interrupts, fault tolerance, and parallelizable tasks.

A plausible state machine is:

```
research
  -> plan
  -> write_tests
  -> implement
  -> run_targeted_tests
       fail -> revise -> run_targeted_tests
       pass -> run_regression_eval
                  fail -> revise
                  pass -> independent_review
                             reject -> revise
                             approve -> finalize
```

The graph state should store structured artifacts rather than an opaque chat transcript: task specification, research findings, plan, test intent, changed files, test results, regression metrics, reviewer findings, unresolved risks, iteration number, and final disposition.

The LangGraph Functional API deserves an early spike because it can add checkpointing and interrupts around existing Python control flow with less restructuring than a full graph rewrite. A Graph API implementation may still be preferable once state transitions stabilize.

### LangChain: optional inside nodes

LangChain should not be a prerequisite for the deterministic parser. It is useful where a node genuinely needs a model/tool loop: research, investigation, change proposal, or review. The harness should keep the LLM-facing layer replaceable and avoid leaking LangChain message/tool types into the CUC domain layer.

### Deep Agents: benchmark, not default dependency

Deep Agents already provides planning, subagents, filesystem/context management, permissions, skills, and human approval on top of LangGraph. Before building these pieces ourselves, we should implement the same narrow CUC spike both with minimal LangGraph primitives and with Deep Agents (or otherwise inspect a representative implementation) and compare control, complexity, testability, context isolation, permissions, and dependency cost.

The likely outcome is hybrid: explicit LangGraph for the deterministic research-plan-test-review lifecycle, with selected Deep Agents ideas or components for context isolation/subagents if they are demonstrably useful. This remains a hypothesis until the spike.

### Langfuse: observability and evaluation layer

Langfuse is a good fit as a sidecar rather than the runtime. It can trace model calls/tool invocations, attach metadata and scores, manage datasets, and run/compare offline experiments. It should not own CUC's execution semantics.

CUC has a natural evaluation bridge already: reviewed token analyses are gold data and `score_reviewed_morphology.py` emits exact-set accuracy, precision/recall/F1/Jaccard, coverage, ambiguity error, and per-token details. Those metrics should remain authoritative deterministic evaluators; Langfuse can ingest or visualize them rather than reimplement them.

Initial Langfuse integration should be optional and fail-open for ordinary parser operation: missing Langfuse credentials or an unavailable Langfuse service must not prevent deterministic parsing/tests. Secrets must never be committed.

## Proposed architecture boundary

```
CUC domain
  deterministic parser / scorer / linter / data invariants
          |
          v
Harness adapters
  ParserRun, TestResult, EvalResult, Skill, Tool, ReviewFinding
          |
          v
LangGraph orchestration
  research -> plan -> TDD -> implement -> evaluate -> review -> finalize
          |
          +---- model/tool nodes (LangChain only where useful)
          |
          +---- optional Deep Agents spike/components
          |
          v
Langfuse
  traces / spans / datasets / experiments / scores
```

The key dependency rule is inward: CUC domain code must not depend on LangGraph/LangChain/Langfuse types. Harness code adapts existing CUC functions/scripts into structured interfaces.

## Independent adversarial review requirement

"Independent review" must mean more than a second prompt in the same accumulated context. The reviewer should receive a clean context containing only the task/specification, relevant repository state/diff, tests and outputs, and explicit review criteria. It should not receive the implementer's hidden rationale or proposed self-justification by default.

Review output should be structured, for example:

- disposition: approve / reject / needs-human
- findings: severity, file/location, claim, evidence, suggested verification
- tests_missing
- invariant_risks
- regression_risks

For high-risk changes, two independent review passes may be useful: implementation correctness and domain/data-integrity review.

## TDD and evaluation gates

Every implementation ticket should define tests before implementation. The harness must distinguish:

1. unit/contract tests for new code;
2. targeted parsing fixtures reproducing the motivating bug/feature;
3. repository safety/data invariants;
4. lint/step-change safeguards;
5. reviewed-morphology regression metrics.

A generic "pytest passed" signal is insufficient. The graph should carry typed gate results and explicit acceptance thresholds.

## Checkpointing and side effects

Checkpointing is useful for long agent runs, but side effects require idempotency. Any task that writes files, commits, creates branches, or calls an external service must be isolated and have a deterministic operation identity so resume/retry does not duplicate actions.

Upstream GitHub writes are outside the autonomous graph by policy. If a future graph ever reaches an upstream-submission node, it must interrupt for explicit human approval before the write.

## Observability schema seed

Every harness run should have stable identifiers and metadata:

- run_id / thread_id
- ticket_id
- branch/base commit
- harness version
- skill name/version
- model/provider where applicable
- node name
- iteration
- test/eval gate status
- reviewer identity/context id
- final disposition

Langfuse traces should mirror the graph hierarchy without making Langfuse IDs part of domain state.

## Open research questions

1. Functional API vs Graph API for the first CUC harness implementation.
2. Whether Deep Agents materially reduces code without weakening explicit state-machine control.
3. Best persistence backend for fork-only development (initially in-memory/SQLite-like local state vs durable external service).
4. How to execute tests/builds in ChatGPT/GitHub-only operation without noisy CI and without relying on local Codex.
5. How to represent `.agents/skills` as typed, versioned harness capabilities while preserving their current portability.
6. How to enforce clean-context reviewer independence in the actual execution environment.
7. Which reviewed examples should become the first small deterministic regression dataset for rapid inner-loop evaluation.
8. Langfuse Cloud vs self-hosted vs optional local-only telemetry for this research project; privacy/cost/operational implications need explicit comparison.
9. How to handle stochastic model calls so replay/resume is auditable and does not silently change prior evidence.
10. Whether prompt management belongs in Langfuse initially or should remain repository-versioned until the harness stabilizes.

## Recommended sequence

Do not begin with a broad rewrite. First establish a baseline and a narrow vertical slice:

1. inventory current agentic entry points and deterministic boundaries;
2. define typed harness contracts and gate semantics;
3. choose one small reviewed morphology case as a vertical slice;
4. implement minimal LangGraph orchestration around existing code;
5. instrument that slice with Langfuse without making telemetry mandatory;
6. implement a clean-context independent reviewer;
7. compare against a Deep Agents version/approach;
8. decide architecture from measured complexity, reliability, trace quality, and regression behavior.

## External references consulted (2026-09-07)

- LangGraph overview / persistence / Functional API: current LangChain documentation.
- Deep Agents overview and product-positioning documentation: current LangChain documentation.
- Langfuse evaluation, datasets, experiment runner, and API documentation: current Langfuse documentation.

URLs are intentionally not embedded as execution dependencies; implementation tickets should re-check current APIs before coding because these projects evolve quickly.
