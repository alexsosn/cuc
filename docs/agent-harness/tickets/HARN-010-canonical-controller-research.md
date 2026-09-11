# HARN-010 — canonical bounded development-controller research

## Scope

HARN-010 is the **software-development controller**, not the Ugaritic parsing runtime. It consumes a development issue and orchestrates:

`research -> plan -> tests/RED -> implement -> tests/evals -> clean independent review -> finalize/merge`

The durable development lifecycle remains HARN-002 `RunState` + `apply_event()`. HARN-010 must not invent a second phase reducer.

## Current dependency state

All blocking architecture dependencies now exist on `agent-harness-safety`:

- HARN-002: framework-neutral development state/reducer;
- HARN-006: clean-context independent development reviewer;
- HARN-007: explicit orchestration remains preferred over Deep Agents for the correctness-critical controller;
- HARN-009 + HARN-023: **one** canonical GitHub write boundary, `agent/harness/github_effects.py`.

HARN-023 matters because three older HARN-010 experiments were based on a tree that still exposed two competing write boundaries. Those branches cannot be finalized unchanged.

## Comparison of existing HARN-010 experiments

### PR #52 — research/plan only

Useful as early design input, but it has no scenario RED or runtime. It should not be the canonical continuation.

### PR #57 — pure bounded scheduler

Strengths:
- small deterministic scheduler;
- serialized checkpoint and exact `RunState` digest;
- explicit step/implementation/review ceilings;
- replay of a pending action without consuming another budget slot;
- no side effects in scheduler code.

Limitation:
- intentionally does not execute research/test/eval/reviewer/GitHub ports, so it satisfies only part of issue #11.

Its useful invariant is retained: **one logical issued action must be replayable without consuming another controller budget slot, and HARN-002 state remains authoritative.**

### PR #56 — full controller experiment

Strengths:
- orchestrates HARN-002 events rather than replacing the reducer;
- explicit pre-implementation RED evidence;
- revision, verification, review, GitHub-write and cost budgets;
- exact head/executed revision checks;
- HARN-006 independent review integration;
- durable controller-state serialization;
- strong adversarial scenarios for crash/replay and budget accounting.

Blocker:
- imports the now-retired `github_side_effects.py` implementation and introspects its embedded journal/approval registry. HARN-023 deliberately removed that API.

Conclusion: use the **behavioral scope/tests of PR #56**, plus the deterministic replay invariant from PR #57, but rebuild the GitHub host integration against canonical `github_effects.py` on the post-HARN-023 base.

## Canonical GitHub integration after HARN-023

`GitHubEffectGateway` is the sole authorization/dispatch boundary. It is intentionally stateless with respect to durable journal ownership:

```text
execute_write(request, journal, checkpoint=...) -> (new_journal, receipt)
reconcile_uncertain(request, journal, checkpoint=...) -> (new_journal, receipt|None)
```

Therefore HARN-010 host/controller wiring must explicitly own:

1. a `GitHubEffectJournal`;
2. an atomic durable journal checkpoint callback used by the gateway **before provider dispatch**;
3. a host-owned `HumanApprovalAuthority` whose restore/registration surface is not model-visible;
4. a trusted reconciler keyed by exact operation identity;
5. the controller's own durable structured state.

The controller may call the gateway but must not receive or expose the raw write adapter, approval registration method, or generic GitHub transport.

## Journal/controller crash model

There are two durability boundaries and they must be ordered safely:

1. gateway checkpoints an uncertain GitHub request before adapter dispatch;
2. provider may mutate GitHub;
3. gateway checkpoints receipt/cleared uncertainty;
4. controller advances its pending-operation cursor and persists controller state.

A crash between 3 and 4 is safe only if restart reloads the **newer durable journal** and the older controller snapshot. Replaying the same pending operation must return the durable receipt without provider redispatch, then advance controller accounting exactly once.

A crash after provider mutation but before receipt persistence leaves durable uncertainty; restart must reconcile, never blindly redispatch.

## Approval durability

`HumanApprovalAuthority` is intentionally separate from serialized model-facing state. HARN-010 production host must restore the trusted authority from a host-owned durable trust store before resuming sensitive operations. Controller state may carry only an approval identifier/request pause record; it must never mint/register approval authority.

This directly closes follow-up #53.

## RED semantics

A real TDD RED is an executed targeted test against the exact baseline revision before implementation. Controller/model text claiming "RED" is not evidence.

Minimum accepted RED evidence:
- targeted intent identity;
- exact baseline revision;
- non-zero exit code;
- at least one expected failing test;
- no unrelated regression classification;
- evidence/log reference.

Implementation cannot begin until all required targeted RED intents have accepted evidence unless policy explicitly disables RED for a non-code task.

## Verification and review

Fresh tests/evals must bind both declared head SHA and executed SHA to the candidate revision. Stale CI/eval evidence is rejected.

HARN-006 constructs a clean review context from durable artifacts + exact base/head diff. The implementer context cannot directly set `ReviewDisposition`; reviewer rejection returns through a bounded revision loop.

## Bounds and stop reasons

The controller requires finite ceilings for at least:
- controller steps;
- implementation/revision attempts;
- verification executions;
- review attempts;
- GitHub writes;
- optional cost units.

Terminal/pause reasons are structured and auditable: complete, blocked execution/dependency, policy block, needs human, specific budget exhausted, or step ceiling exhausted.

No-feature fallback to performance/stability/ergonomics/documentation/edge cases is allowed only when controller policy explicitly enables it.

## Non-goals

- parsing corpus tokens or deciding parsing scope;
- implementing HARN-017 issue discovery;
- model-provider selection/benchmarking;
- exposing raw GitHub connectors or approval-registration tools to the model;
- automatic upstream writes.

## Decision

Build a fresh canonical HARN-010 branch on post-HARN-023 `agent-harness-safety`. Preserve the strongest scenario contracts from PR #56 and pending-action replay semantics from PR #57, but use only `github_effects.py` for GitHub mutation. Retire #52/#56/#57 once the canonical branch has research, plan and RED evidence.