# Adversarial Review: Preliminary Harness Research and Seed Backlog

Date: 2026-09-07
Scope: `docs/agent-harness/research.md`, `seed-tickets.md`, current CUC branch state, and current LangGraph / Deep Agents / Langfuse documentation.

## Disposition

**Approve as a research seed backlog with one blocking correction:** execution substrate must be solved before claiming an autonomous TDD loop. HARN-000 was added for this reason.

## Findings

### F1 — BLOCKER — No proven RED/GREEN execution path from ChatGPT/GitHub-only mode

The original backlog assumed tests could be run but did not define how. Current ChatGPT GitHub access exposes workflow inspection and re-run capabilities, but no direct arbitrary workflow-dispatch operation is currently visible. Development-branch pushes intentionally do not trigger CI.

**Resolution:** added HARN-000. HARN-004 and later implementation tickets should not be considered executable until HARN-000 demonstrates a real failing-test -> fix -> passing-test cycle on the current branch HEAD.

### F2 — MEDIUM — Do not let LangGraph become a parser rewrite

CUC already has a large deterministic pipeline and reviewed-data evaluator. Moving linguistic logic into graph nodes or LLM prompts would reduce reproducibility and make regression reasoning harder.

**Required constraint:** LangGraph orchestrates existing deterministic functions/scripts behind framework-neutral adapters. Domain modules must not import LangGraph/LangChain/Langfuse types.

### F3 — MEDIUM — Deep Agents is useful but still an architecture risk

Current Deep Agents documentation explicitly positions it as a batteries-included harness with planning, subagents, filesystem/context management, permissions, HITL, and skills. It is therefore relevant to CUC, but its convenience could obscure the explicit research-plan-test-review state machine we need.

The package is currently marked Beta on PyPI. It supports Python 3.13, so compatibility with CUC's Python floor is not the concern; control surface and churn are.

**Resolution:** keep HARN-007 as a measured comparison after the explicit LangGraph vertical slice, not a foundational dependency.

### F4 — MEDIUM — Reviewer independence must be architectural, not rhetorical

A prompt saying "review independently" inside the implementer's accumulated context does not provide meaningful logical independence.

**Resolution:** HARN-006 requires a clean reviewer context, structured findings, independent restartability, and tests that prohibited implementer-only context is absent.

### F5 — MEDIUM — Langfuse must remain optional telemetry/evaluation infrastructure

Langfuse's current SDK is OpenTelemetry-based and its datasets/experiments/evaluators are a strong fit for CUC's reviewed morphology scores. But allowing telemetry availability, IDs, prompt storage, or experiment state to become required domain inputs would couple parser correctness to an external service.

**Resolution:** HARN-005 requires fail-open telemetry and forwards existing deterministic scores instead of reimplementing them.

### F6 — MEDIUM — Langfuse deployment/privacy decision is deliberately unresolved

Cloud vs self-hosted affects operations, data handling, credentials, maintenance burden, and cost. This should not block the first tracing adapter if the adapter is backend-optional, but it must be decided before sustained use with research traces or corpus context.

**Resolution:** retain as explicit research question; no credentials or deployment assumptions in early contracts.

### F7 — LOW — Framework compatibility is currently acceptable

Current package metadata indicates LangGraph supports Python >=3.10 including 3.13; Deep Agents supports Python >=3.11 including 3.13; current Langfuse Python SDK documentation states Python 3.9+ for v4. CUC currently requires Python >=3.13 in `agent/pyproject.toml`, so there is no immediate Python-version blocker.

This must be rechecked when dependencies are actually pinned.

### F8 — LOW — Seed backlog is intentionally not yet a GitHub Issues backlog

`alexsosn/cuc` currently has GitHub Issues disabled. Keeping seed tickets under version control is safer than enabling repository features just to host preliminary planning.

**Resolution:** acceptable. Convert to real Issues only after the architecture/backlog stabilizes or Issues are intentionally enabled.

## Recommended priority after review

1. HARN-000 — execution substrate
2. HARN-001 — current-system inventory
3. HARN-002 — framework-neutral contracts
4. HARN-003 — small reviewed regression fixture
5. HARN-004 — minimal LangGraph vertical slice
6. HARN-006 — clean-context independent reviewer
7. HARN-005 — Langfuse adapter
8. HARN-007 — Deep Agents comparison
9. HARN-008 / HARN-009 — skills and side-effect capability model
10. HARN-010 — bounded autonomous controller

The order intentionally delays full autonomous orchestration until test execution, deterministic gates, and reviewer independence have all been proven separately.
