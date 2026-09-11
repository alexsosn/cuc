# HARN-007 — Deep Agents evaluation plan

## Scope

Evaluate Deep Agents against the existing HARN-004 complete-column review runtime without changing scholarly semantics. Secondary comparison: whether Deep Agents adds useful launcher/context ergonomics around the already framework-neutral HARN-006 development reviewer.

## Research gate

Research is recorded in `HARN-007-deep-agents-research.md` and fixes the comparison baseline at integration revision `935864fca6510aeb4ce02e70a6cf69b2461e298e`.

## Prototype strategy

Run an **executable, deterministic, no-provider-key spike** against the latest published Deep Agents release (`0.7.13`). Use a scripted/fake chat model with tool calling, following upstream's own deterministic integration-test pattern.

The decisive experiment uses the same 3-token complete-column requirement as HARN-004:

1. expose a token-review tool whose calls are observable;
2. script the Deep Agent model to call it for only token `t1`;
3. script the next model turn to return a normal final response;
4. assert the Deep Agent run terminates normally despite `t2` and `t3` never being reviewed;
5. contrast that with HARN-004, whose `ColumnRunState`/transition contract cannot complete with a skipped token.

This is not a claim that Deep Agents cannot be customized. It measures whether its **primary default orchestration** supplies the CUC invariant. If preserving the invariant requires embedding HARN-004's deterministic state machine/tool wrapper, Deep Agents is no longer replacing that layer.

A second probe records isolated-subagent ergonomics only if it can be done deterministically without expanding scope. HARN-006 remains the correctness boundary regardless.

## Dependency handling

The spike dependency is temporary:

1. add exact `deepagents==0.7.13` to `agent/pyproject.toml` on the spike branch;
2. let trusted HARN-021 generate `agent/uv.lock`; never hand-edit it;
3. run the full existing suite plus HARN-007 spike tests;
4. record exact head, synthetic merge, package/version and test evidence;
5. remove the Deep Agents dependency and executable spike code before final merge if the ADR does not adopt Deep Agents as a runtime dependency;
6. regenerate the lock through HARN-021 and prove the final branch remains clean/green.

The final repository should not retain a rejected runtime dependency merely to preserve a one-time experiment.

## TDD gates

### RED 1 — comparison artifact contract

Before adding comparison implementation, add tests requiring a framework-neutral HARN-007 decision artifact that records:

- fixed baseline revision;
- Deep Agents package/version tested;
- parsing-equivalence criteria;
- observed early-termination result;
- subagent/skills/context/dependency observations;
- final disposition enum: `adopt-primary | adopt-selected-components | keep-langgraph | reject`;
- rationale and evidence refs.

The test must fail while the artifact/loader does not exist.

### RED 2 — executable Deep Agents behavior probe

After the temporary dependency is lock-generated, add the scripted-model test first. It must fail until the spike harness exists. The probe must not use network/model credentials.

Required observation: a normal Deep Agents run can finish after only one of three token-review calls. The test should fail if the run unexpectedly enforces full traversal.

### GREEN — minimal spike implementation

Implement only enough code to run the deterministic probe and produce the comparison evidence. Do not port the parser or build a second production harness.

### ADR

Write `docs/agent-harness/adr/ADR-HARN-007-deep-agents.md` from observed evidence. The decision must explicitly answer:

- parsing primary runtime;
- skills handling;
- clean-context reviewer/subagent usage;
- context management;
- filesystem/permissions;
- checkpointing;
- observability;
- dependency surface/churn;
- what, if anything, HARN-010 should borrow.

## Independent adversarial review

Fresh review receives issue #8, final diff, research/plan, exact test evidence, HARN-004/HARN-006 invariants and the Deep Agents source/version references. It must attack:

- semantic mismatch between compared workflows;
- a straw-man Deep Agents configuration;
- confusing default behavior with impossibility of customization;
- accepting a framework because of generic demos/marketing;
- overlooking HARN-004 guarantees already supplied by LangGraph;
- overlooking genuine Deep Agents advantages (isolated subagents, skills, context management);
- permanent dependency bloat after a reject/limited-adoption decision;
- relying on unreleased upstream behavior while testing a different package version.

Any blocker becomes a tests-first RED revision.

## Acceptance

- same real complete-column invariant is the comparison target;
- at least one executable Deep Agents observation, not documentation-only reasoning;
- explicit measured/observed trade-offs;
- ADR chooses one issue-approved disposition and explains selected-component reuse separately;
- HARN-004 deterministic state/domain code unchanged;
- HARN-006 review correctness unchanged;
- no upstream CUC writes;
- no rejected spike dependency left in final production dependency surface;
- exact final-head full-suite GREEN plus logically independent review before merge.
