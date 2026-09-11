# HARN-017 — Systematic parsing findings to development issues

## Status

Research and implementation plan. No production code exists yet on this branch.

## Problem

The parsing/evaluation track now produces durable complete-column state, evaluation records, model-comparison evidence and expert feedback, while the autonomous development track consumes `TaskSpec` plus HARN-023-authorized GitHub effects. What is missing is a narrow bridge that decides when a parsing-side finding is sufficiently systematic to become a development ticket.

This bridge must **not** become another parsing runtime loop and must **not** perform GitHub I/O itself. A local scholarly reading stays in the scholarly workflow. A general parser/linter/skill/tool/harness/eval problem may become a fork-local development issue only after reproducible evidence and deduplication.

## Existing contracts reused

- HARN-014 architecture: parsing production loop and software-development loop are separate.
- HARN-015 evaluation: parsing run/model/capability/evidence identities and metric/expert-feedback references are evaluator-side evidence, not model workload context.
- HARN-010 development controller: `TaskSpec` is the canonical development-task input contract.
- HARN-023 GitHub effects: `GitHubEffectRequest` + `GitHubEffectGateway` are the only GitHub mutation authority; the bridge may construct a request but never dispatch it.
- Fork safety: issue targets are `alexsosn/cuc` only; no upstream operation is emitted.

## Research conclusions

### 1. Classification is explicit and closed

Use the issue taxonomy verbatim:

- `local-scholarly-reading` — never creates a development issue;
- `parser-config-defect`;
- `linter-defect`;
- `skill-procedure-defect`;
- `deterministic-tool-opportunity`;
- `harness-runtime-defect`;
- `eval-benchmark-defect`.

The bridge does not infer a class from free text. Classification must be present before eligibility is evaluated.

### 2. One differing token is not enough

A development issue is eligible only when there is evidence that the problem is systematic. Two paths are acceptable:

1. at least two **distinct loci** reproduce the same problem signature; or
2. one or more typed systematic signals exist, such as a complete-column eval regression, repeated expert-feedback pattern, cross-model comparison, or repeated workflow-cost/tooling signal.

A second record for the same token/locus does not satisfy recurrence by itself.

### 3. Generalization needs boundary evidence where the class implies a rule

Parser/config, linter and skill/procedure defects are especially prone to over-generalization. Before an issue can be proposed, they require at least one positive case, one negative case and one boundary case. Other categories may carry that matrix but are not universally blocked by its absence.

### 4. Dedup identity must not depend on observed examples

A new example arriving later must not create a duplicate issue. Therefore the stable fingerprint is derived from:

- finding classification;
- a normalized subsystem/component identity;
- a normalized caller-supplied `problem_key` describing the general defect/opportunity.

Occurrence order, titles, summaries, model names and metrics are evidence, not identity. The issue body carries an exact hidden fingerprint marker so a trusted read-side can discover existing tickets and pass their fingerprints to the bridge.

### 5. Output is a proposal, not a mutation

Eligible novel findings yield:

- deterministic issue fingerprint;
- `TaskSpec` suitable for HARN-010;
- rendered title/body with reproducibility evidence;
- one `GitHubEffectRequest` using `GitHubAction.CREATE_ISSUE`, repository `alexsosn/cuc`, and an operation ID derived from the fingerprint.

The caller/controller must still authorize and dispatch that request through HARN-023. The bridge has no adapter, token, connector or raw endpoint.

## Proposed framework-neutral contracts

Module: `agent/harness/systematic_findings.py`.

### Finding taxonomy

`FindingClassification` closed enum.

### Reproducible occurrence

`FindingOccurrence` records:

- stable occurrence ID;
- corpus/tablet/column/token IDs;
- summary;
- run ID;
- provider/model/version;
- skill capability name/version/provenance digest;
- ordinary evidence refs;
- metric refs;
- expert-feedback refs.

This deliberately records run/model/skill versions in the issue evidence while keeping held-out evaluation targets outside parsing/model context.

### Systematic signal

`SystematicSignalKind` closed enum and `SystematicSignal` record, for evidence whose unit is broader than one token:

- eval regression;
- expert-feedback pattern;
- model comparison;
- repeated workflow cost/tool opportunity.

Each signal carries stable ID, summary and evidence refs.

### Candidate

`SystematicFindingCandidate` contains classification, subsystem, problem key, title, objective, acceptance criteria, occurrences, optional systematic signals, optional positive/negative/boundary cases and extra evidence refs.

### Decision

`FindingDisposition`:

- `local-no-development-issue`;
- `insufficient-systematic-evidence`;
- `duplicate-existing-issue`;
- `ready-for-development-issue`.

`FindingBridgeDecision` includes the disposition, reason, deterministic fingerprint, and for READY only a `TaskSpec` plus `GitHubEffectRequest`.

## Eligibility algorithm

1. Validate typed candidate and evidence.
2. Compute stable fingerprint from classification + normalized subsystem + normalized problem key.
3. If classification is local scholarly reading: stop with no issue.
4. If fingerprint is already in trusted `existing_issue_fingerprints`: stop as duplicate.
5. Compute distinct loci from corpus/tablet/column/token IDs.
6. Require either >=2 distinct loci or >=1 typed systematic signal.
7. For parser/config, linter or skill/procedure defects require positive + negative + boundary cases.
8. Render deterministic HARN-010-compatible task and fork-local CREATE_ISSUE request.

Dedup happens before rendering mutable evidence details; adding another occurrence leaves the fingerprint unchanged.

## Issue rendering

Body contains:

- hidden marker `<!-- harn-017:fingerprint:<sha256> -->`;
- classification/subsystem/problem key;
- objective and acceptance criteria identical to the emitted `TaskSpec`;
- occurrence table/sections with exact corpus/tablet/column/token IDs, run/model/version and capability identity;
- metric/expert/evidence refs;
- systematic signals;
- positive/negative/boundary cases where supplied;
- explicit note that creation is fork-local and the ticket enters research → plan → TDD → verification → independent review.

No raw held-out gold/evaluator target payload is rendered by this module; only caller-supplied safe evidence references are accepted.

## TDD / RED plan

Before implementation, tests must require:

1. local scholarly readings never create a GitHub request even with repeated occurrences;
2. one token/locus difference alone is insufficient;
3. duplicate observations of the same locus do not fake recurrence;
4. two distinct loci for one problem become eligible;
5. typed systematic signals can justify a ticket even with one observed locus;
6. parser/linter/skill generalization is blocked without positive/negative/boundary cases;
7. fingerprint is stable across occurrence order/additional evidence and changes when class/subsystem/problem key changes;
8. an existing fingerprint suppresses issue creation deterministically;
9. READY output contains a `TaskSpec` and fork-local `CREATE_ISSUE` request with no target ref or upstream authority;
10. issue body contains exact run/model/skill provenance plus metric/expert refs;
11. invalid/underspecified provenance or duplicate IDs fail closed;
12. module performs no network I/O and does not depend on LangGraph/Langfuse/provider SDKs.

## Independent adversarial review rubric

Reject if any of these are possible:

- one token correction can be promoted merely by duplicating the same evidence record;
- local philological disagreement opens a dev ticket;
- occurrence/evidence ordering changes dedup identity;
- adding evidence creates a second issue for the same general defect;
- two unrelated defects collapse because dedup uses only a broad category;
- caller-controlled title/body can alter the canonical task identity or hidden fingerprint;
- missing run/model/capability provenance is silently omitted from an issue;
- held-out evaluation target content is copied into parsing/model context;
- bridge directly dispatches GitHub writes or bypasses HARN-023;
- any generated request can target `DT-UCPH/cuc`;
- output cannot be converted directly into HARN-010 `TaskSpec` input.

## Non-goals

- actually executing GitHub issue creation;
- polling/searching GitHub for duplicates (trusted read-side supplies existing fingerprints);
- changing parser rules or reviewed corpus data;
- model-provider execution;
- HARN-010 durable host persistence (#53).
