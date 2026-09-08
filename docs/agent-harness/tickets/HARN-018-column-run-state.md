# HARN-018 — Column-run state and completion semantics

## Research question

What is the smallest framework-neutral state model that can execute the **existing** `review-automatic-parsing` workflow faithfully, survive checkpoint/replay, and later become LangGraph state without turning the development-controller `RunState` into parser state?

## Source semantics

The state model is derived from the current parsing architecture and `review-automatic-parsing` capability, not from LangGraph:

- one complete **column** is the work/context unit;
- every token is visited in textual order on the initial review pass;
- worklists/lints/alignments can prioritize evidence gathering but cannot select only a subset of tokens;
- a token decision retains its evidence provenance and all defensible alternative analyses;
- explicit revisits may amend a token after column/tablet/corpus reconciliation, but prior decisions remain in history;
- completion happens only after the full initial traversal, reconciliation, and the capability's completion gates (`review-status-clean`, lint delta, report/token-count) succeed;
- a decision-changing revisit makes earlier completion-gate observations stale and requires fresh gate evidence;
- `auto_parsing/**` remains generated. This module must have no direct filesystem/GitHub mutation primitive; parser regeneration is a separate controlled capability/adapter.

HARN-002's `RunState` is deliberately **not reused**. It models the GitHub issue development lifecycle (`research -> plan -> TDD -> implement -> verify -> review`). HARN-018 models the scholarly parsing runtime. Keeping these states separate is an HARN-014 architectural invariant.

## Contract design

Create a standard-library-only `agent/harness/column_state.py` containing immutable serializable contracts and pure transition events.

### Identity and input snapshot

`CapabilityRef`
- canonical capability name;
- contract version;
- provenance digest for the exact loaded capability/prompt/tool package.

`ColumnTask`
- stable run/task id;
- corpus/tablet/column identity;
- repository/input revision;
- capability ref;
- required completion-gate ids copied from the capability manifest at task creation;
- evidence-priority token ids produced by worklists/audits. These are hints only and never participate in cursor construction or completion coverage.

`ColumnToken`
- stable token id;
- ordinal in the column;
- line/reference;
- surface form.

`ColumnSnapshot`
- stable snapshot id;
- ordered tuple of every `ColumnToken` in the complete column;
- source reference and source provenance digest/version.

The snapshot validates contiguous ordinals and unique token ids so cursor position has one deterministic meaning after resume.

### Evidence and decisions

`EvidenceRecord`
- stable evidence id;
- source id/kind;
- exact source reference;
- required provenance reference/version/digest;
- concise claim/summary.

`TokenDecision`
- stable decision id;
- token id;
- one or more analyses, preserving alternatives rather than collapsing them;
- evidence ids;
- rationale/summary;
- `revisit_of` pointing to the previous decision when this is an explicit revisit.

Decisions are append-only. Revisit never overwrites the initial decision; the latest decision is derived from history.

### Cursor, reconciliation, and completion

`TokenCursor`
- `next_index` only for the mandatory initial pass;
- derived `initial_pass_complete` / next token from the immutable snapshot.

`RevisitRequest`
- stable request id;
- token id;
- reason;
- optional reconciliation finding id.

`CorpusReconciliationFinding`
- stable finding id;
- scope (`column`, `tablet`, `corpus`);
- affected token ids;
- evidence ids;
- summary;
- whether it requires an explicit revisit before reconciliation can close.

`CompletionGateResult`
- gate id;
- pass/fail outcome;
- evidence refs;
- `decision_revision`: the exact decision history revision observed by the gate.

`ColumnCompletion`
- completed decision revision;
- exact required gate ids and gate evidence used for completion.

`ColumnRunState`
- task + complete snapshot;
- cursor;
- evidence, decision history, revisit requests, reconciliation findings, gate history;
- reconciliation-closed flag;
- completion record;
- event receipts for idempotent replay.

Every accepted token decision increments `decision_revision`. Gate results are valid for completion only when their revision equals the current decision revision.

## Pure event model and replay rule

Use pure `apply_column_event(state, event) -> state` transitions. No event performs filesystem, model, GitHub, parser, or Langfuse work.

Seed events:

1. `EvidenceRecorded`
2. `TokenReviewed` — mandatory initial pass; must target exactly the current cursor token.
3. `RevisitRequested`
4. `TokenRevisited` — requires an unresolved explicit request and keeps prior decision history.
5. `ReconciliationFindingRecorded`
6. `ReconciliationClosed`
7. `CompletionGateRecorded`
8. `ColumnCompleted`

Every event has an `event_id`. State stores an event receipt containing a deterministic payload fingerprint:

- replaying the **same event id + same payload** is a no-op;
- reusing an event id with a different payload is rejected;
- this gives checkpoint/retry idempotency without hiding conflicting replays.

## Completion invariant

A column can enter completed state only when all of the following hold:

1. cursor reached the end of the immutable complete-column snapshot;
2. each snapshot token has exactly one initial decision, in snapshot order;
3. every required-revisit reconciliation finding has a resolved explicit revisit;
4. reconciliation is explicitly closed after the latest decision-changing revisit;
5. every task-required completion gate has a latest successful result at the current `decision_revision`;
6. no anomaly/suspicion/worklist selector participates in token coverage.

A later decision-changing event is not accepted after completion; a new reconciliation/review pass must be a new run/task, preserving the completed run as provenance.

## Serialization

All persisted contracts provide deterministic `to_dict`/`from_dict`; `ColumnRunState.to_json()` uses sorted JSON keys and compact separators. Round-trip equality is part of the contract tests.

No object stores LangGraph messages, checkpoints, runnables, LangChain tools, Langfuse spans, or provider-specific model objects.

## TDD plan

Write tests before `column_state.py` exists.

RED tests must prove:

1. complete snapshot + evidence-priority hint still starts at token 1 and cannot review token 2 first;
2. skipped-token completion is impossible;
3. initial pass reviews every token once in order;
4. checkpoint serialization/resume preserves the exact next token;
5. exact event replay is idempotent while conflicting event-id reuse is rejected;
6. evidence provenance is required and survives serialization;
7. alternative readings survive round-trip unchanged;
8. revisit without an explicit request is rejected and an explicit revisit preserves decision history;
9. reconciliation cannot close with an unresolved required revisit;
10. required completion gates must all pass at the current decision revision;
11. a revisit makes earlier gate results/reconciliation closure stale until refreshed;
12. no LangGraph/LangChain/Langfuse type leaks into the module;
13. full existing `agent/tests` suite remains green.

## Independent adversarial review rubric

The final reviewer starts from #24 plus the actual current `review-automatic-parsing` skill and HARN-008 manifest, not this design rationale. Reject if it can demonstrate any of:

- a completion path with an unvisited token;
- priority/worklist hints changing token inclusion/order;
- a silent or implicit revisit;
- overwrite/loss of earlier token decisions or evidence;
- checkpoint replay skipping/duplicating a decision;
- stale gate evidence completing a post-revisit state;
- invented anomaly-selection semantics;
- direct mutation of generated parsing data;
- LangGraph/LangChain/Langfuse types in domain contracts;
- conflation with the development-controller state machine.

## Non-goals

- LangGraph graph construction (HARN-004);
- model/tool execution;
- actual corpus file writes;
- parser regeneration adapters;
- Langfuse tracing;
- expert-feedback/eval protocol beyond representing exact evidence/gate provenance.