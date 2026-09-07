# HARN-000 — Establish a ChatGPT/GitHub-only execution substrate

**Type:** blocking research + infrastructure spike

## Goal

Prove how the agentic development loop can actually execute RED/GREEN tests and deterministic evaluators when development is performed through ChatGPT with GitHub access, without relying on local Codex and without creating upstream notification noise.

This ticket blocks claims of a working TDD harness. A graph that can plan and edit code but cannot execute tests on demand is not an autonomous development harness.

## Current constraint

The current ChatGPT GitHub integration can read/write repository content, inspect workflow runs/jobs/logs/artifacts, and re-run existing workflow jobs/runs. In the currently exposed tool surface, there is no direct operation for starting an arbitrary new `workflow_dispatch` run.

The existing repository workflow triggers on pushes/PRs involving `main`; development-branch pushes intentionally do not trigger CI.

## Research options

### A. Internal fork PR as execution loop

Open a PR entirely inside `alexsosn/cuc` and use `pull_request` synchronization to trigger tests after pushes.

Pros:
- automatic RED/GREEN after every push;
- no upstream repository activity.

Cons:
- current fork `main` is not the desired harness base, so this may produce a misleading/huge PR unless the fork base strategy is cleaned up first;
- runs on every push rather than only when the controller wants a gate;
- creates persistent PR activity, though only in the fork.

### B. Fork-only manual workflow, then reusable re-runs

Create a safe workflow on the fork default branch with `workflow_dispatch` and a branch/ref input. A human performs the initial dispatch once for the harness development branch. Subsequent agent iterations re-run the existing job through the GitHub integration.

The spike must verify whether `actions/checkout` with a branch-name input resolves the latest branch HEAD on each re-run rather than the original commit. If it does, this provides a low-noise reusable runner with only one manual bootstrap action per branch/session.

Pros:
- fork-only;
- on-demand rather than every push;
- ChatGPT can inspect logs and re-run jobs after the seed run;
- compatible with explicit TDD gates.

Risks/questions:
- exact GitHub re-run semantics must be tested, not assumed;
- workflow file must exist on the default branch for manual dispatch;
- changing fork `main` needs care because it is currently behind the harness base;
- the workflow must never accept an upstream write target.

### C. Dedicated runner service / plugin

Research whether an already available connected execution service can run a checked-out fork branch and return test artifacts without local Codex.

Do not introduce a new hosted service merely to avoid researching A/B. Any service must have explicit permissions, cost, secret handling, and reproducibility analysis.

### D. Coarse milestone CI only

Continue editing through GitHub and run CI only at milestone PRs.

This is acceptable for research/documentation but does **not** satisfy the target TDD loop, so it is a fallback, not the desired result.

## Safety requirements

- No workflow or runner may write to `DT-UCPH/cuc`.
- No `pull_request_target`, broad scheduled runner, or issue/PR write permission is introduced merely for test execution.
- Development runs must remain in `alexsosn/cuc`.
- Secrets are minimal and repository-scoped; tests should prefer read-only GitHub permissions.
- The execution mechanism must be idempotent under retry/re-run.

## TDD for the runner itself

Before relying on the runner, demonstrate:

1. a deliberately failing tiny test produces RED;
2. a follow-up fix on the development branch produces GREEN;
3. the second run executed the new branch HEAD;
4. logs identify the tested commit SHA;
5. no upstream workflow run/PR/issue/comment was created;
6. repeated re-run does not mutate repository data;
7. repository-safety tests remain green.

## Deliverable

`docs/agent-harness/execution-substrate-decision.md` containing:

- options tested;
- exact GitHub semantics observed;
- chosen mechanism;
- permission model;
- trigger/re-run procedure;
- failure modes;
- evidence from RED/GREEN runs;
- whether any one-time human bootstrap remains necessary.

## Acceptance

- We can execute tests for a named fork development branch and retrieve logs/results from ChatGPT.
- The tested commit SHA is explicit and auditable.
- A RED -> code change -> GREEN cycle is demonstrated.
- No upstream collaborator-facing activity is generated.
- Independent adversarial review attempts to make the runner test stale code, the wrong branch, or bypass fork isolation.

**Depends on:** fork safety layer already present.

**Blocks:** HARN-004 and every ticket that claims an actual TDD implementation loop.
