# CUC Agent Harness Architecture

This document is the canonical architecture summary after HARN-014. Existing skills/prompts remain authoritative for detailed scholarly procedure.

## Deliverables

1. **Morphologically parsed CUC** — primary deliverable.
2. **Reproducible automatic parsing** — deterministic parser/materialization from pinned inputs and revision.
3. **Agentic parsing tools and harness** — systematic complete-column review and corpus reconciliation.
4. **Evaluation and expert-feedback infrastructure** — output/agent/system quality and model comparisons.
5. **Autonomous development controller** — durable software/skill/tool improvements from GitHub issues.

## System

```text
CUC / Text-Fabric / evidence sources
                |
                v
    reproducible automatic parser
    rules | DULAT | context | lint
    reconstruction | provenance
                |
                v
       AGENTIC PARSING HARNESS
       -----------------------
       input = one complete column
       whole column stays in context
                |
       for every token, in order:
         inspect current parse
         gather required evidence
         adjudicate / preserve ambiguity
         record decision + provenance
                |
       column reconciliation
       completion/status/lint checks
       corpus-parallel reconciliation
                |
                v
        morphologically parsed CUC
                |
        +-------+------------------+
        |                          |
        v                          v
 deterministic evals          expert feedback
        |                          |
        +-------------+------------+
                      |
           systematic/generalizable finding
                      |
                      v
                 GitHub issue
                      |
                      v
       AUTONOMOUS DEVELOPMENT CONTROLLER
       ---------------------------------
       research -> plan -> TDD/RED
       -> implementation -> GREEN/evals
       -> clean-context adversarial review
       -> merge/finalize under repo policy
                      |
                      v
        parser / linter / script / skill /
        harness / eval improvements
                      |
                      +-----> parsing system
```

There is no anomaly-detection gate deciding which tokens the parser reviews. Worklists, lint findings, alignments and specialist audits affect **evidence priority**, not **token scope**.

## Parsing runtime source of truth

`.agents/skills/review-automatic-parsing/SKILL.md` defines the core workflow:

- complete column is the bounded working/context unit;
- every token is reviewed in order;
- the line/clause and column context are read during token adjudication;
- DULAT/Tropper and the other skill-defined evidence sources are reconciled;
- parallels are checked;
- defensible alternative readings are preserved;
- completion is measured explicitly, not inferred from resolving flagged rows;
- recurring phenomena use specialist audit skills;
- parser/linter defects are escalated rather than hidden in corpus data.

HARN-008/HARN-018 formalize these semantics for runtime use; they do not redefine them.

## Generated and curated data

```text
TF/source
   |
   v
generated_sources/**
   |
   v
parser revision + config/evidence
   |
   v
auto_parsing/**          reviewed/**
(generated; never         (curated scholarly gold;
 hand-edited)              explicit review/migration writes)
```

If automatic parsing is wrong, change generator/config/code with tests and regenerate. Never patch `auto_parsing/**` manually.

## Evaluation plane

### Output quality

- exact-set morphology agreement;
- precision/recall/F1/Jaccard;
- ambiguity preservation and option-count error;
- unsupported extras / missing analyses;
- unresolved coverage;
- reconstruction/lint validity;
- column/corpus consistency.

### Agent quality

- expert acceptance/correction rate;
- skipped-token/completion failures;
- unsupported edits;
- evidence/provenance quality;
- consistency gains/regressions;
- unnecessary reopening of settled readings.

### Model/system comparison

Different models must process the **same complete columns** under the same:

- repository/data revision;
- skills/prompts;
- tools/evidence permissions;
- every-token completion contract;
- deterministic evaluators;
- expert-feedback protocol.

Record exact model/provider/version, skill/tool versions, tool calls, retries, latency and cost where available. Gold/evaluator information that would leak answers must remain outside model context.

Langfuse may store traces/datasets/experiments/scores for these runs, but it does not define parser correctness.

## Systematic feedback -> development

A finding is classified before becoming a GitHub issue:

```text
local scholarly reading ---------------> corpus decision only
parser/config general rule ------------> dev issue
linter defect --------------------------> dev issue
skill/procedure weakness ---------------> dev issue
deterministic-tool opportunity ---------> dev issue
harness/runtime defect -----------------> dev issue
eval/benchmark defect ------------------> dev issue
```

Repeated expensive agent behavior is a signal that a deterministic script/tool may be warranted. Repeated quality failures associated with a skill are evidence for skill improvement. Both are implemented through the development controller, with measured before/after evals where possible.

## Orchestration boundaries

### Parsing graph

Target LangGraph state is defined by HARN-018 and should cover column snapshot, token cursor, decisions, evidence, completion and reconciliation. It must support checkpoint/resume without skipped tokens or lost provenance.

### Development graph

HARN-002/HARN-010 cover GitHub issue, research, plan, tests, change set, executed revision, evals, review and bounded retry state. It does not own corpus-token traversal.

### Independent review vs expert feedback

- **expert feedback** evaluates scholarly parsing output and feeds evals/systematic findings;
- **clean-context adversarial review** evaluates development changes/PRs before finalization.

They are separate evidence channels and must not substitute for one another.

## Corrected critical path

```text
HARN-003  deterministic morphology fixture
HARN-014  architecture correction
    |
HARN-008  formalize existing skills
    |
HARN-018  column-run state/completion
    |
HARN-015  eval + expert feedback protocol
    |
HARN-004  LangGraph real column-review slice
    |
HARN-005  optional tracing/Langfuse
    |
HARN-016  model benchmark
    |
HARN-017  systematic finding -> GitHub issue

Development-controller track:
HARN-006 -> HARN-009 -> HARN-007 -> HARN-010
```
