# HARN-014 — Align harness architecture with the skill-defined parsing workflow

Status: research/architecture correction.

## Why this correction exists

The preliminary harness plan over-centered the research-plan-TDD-review development lifecycle and treated it as if it were also the parsing runtime. That is not the current CUC workflow.

The existing skills are authoritative on parsing semantics. In particular, `.agents/skills/review-automatic-parsing/SKILL.md` defines a complete column as the bounded working unit and requires **every token, in order** to be reviewed. Worklists, lints, alignments, and audits identify where evidence is especially valuable; they do not decide which tokens are in scope.

The corrected architecture therefore separates two controllers:

1. **Agentic parsing harness** — systematic scholarly parsing/review over complete columns, with corpus reconciliation and reproducible evidence/provenance.
2. **Autonomous development controller** — GitHub-issue-driven research -> plan -> TDD -> implementation -> tests/evals -> logically independent adversarial review.

Systematic findings from parsing/evals/expert feedback may create development issues, but they do not turn the token parsing loop itself into a TDD loop.

## Source-of-truth parsing semantics

The harness must preserve these existing rules unless a separate reviewed change explicitly modifies the skills/conventions:

- working/context unit: one complete column;
- traversal: every token, in textual order;
- whole-column context remains visible while adjudicating each token;
- evidence sources are consulted according to the current skills and prompt/convention hierarchy;
- repeated/formulaic parallels inside the column and across the corpus are part of adjudication;
- defensible ambiguity is preserved as alternative analyses rather than forcibly collapsed;
- column completion is explicit and auditable; a handful of adjudicated flagged rows never counts as a reviewed column;
- `reviewed/**` is curated scholarly gold;
- `auto_parsing/**` is generated and must never be hand-edited;
- parser/linter/general-rule defects are fixed at their source with tests and regeneration;
- specialist audit skills remain the escalation route for recurring linguistic phenomena.

## Corrected system boundary

```text
CUC / TF / evidence sources
          |
          v
reproducible deterministic automatic parser
          |
          v
agentic parsing harness
  input: complete column
  loop: every token in order
  actions: inspect -> gather evidence -> adjudicate -> record provenance
  then: column reconciliation -> corpus reconciliation -> completion gates
          |
          +--------------------------+
          |                          |
          v                          v
morphologically parsed CUC     evals + expert feedback
                                     |
                         systematic/generalizable finding
                                     |
                                     v
                               GitHub issue
                                     |
                                     v
autonomous development controller
  research -> plan -> TDD/RED -> implementation -> GREEN/evals
      -> clean-context independent review -> merge
                                     |
                                     v
                        parser/linter/tool/skill/harness improvements
                                     |
                                     +------> back into parsing system
```

## Deliverable priority

1. **Morphologically parsed CUC** — primary research deliverable.
2. **Reproducible automatic parsing** — deterministic baseline/materialization from pinned inputs and parser revision.
3. **Agentic tools and parsing harness** — scale systematic column review and corpus reconciliation.
4. **Evaluation + expert-feedback infrastructure** — measure output/agent/system quality and compare models/skills/tools.
5. **Autonomous development loop** — convert systematic findings into durable software/procedure improvements.

Infrastructure is justified by how well it improves the corpus and its reproducibility, not as an end in itself.

## Eval layers

### Output quality

- reviewed-morphology exact-set agreement and precision/recall/F1/Jaccard;
- ambiguity preservation / incorrect collapse;
- unsupported extra analyses;
- unresolved coverage;
- reconstruction and lint validity;
- column/corpus consistency.

### Agent behavior

- expert acceptance/correction rate;
- unsupported edits or re-opened settled readings;
- evidence/provenance quality;
- completion correctness (no skipped tokens);
- successful reconciliation of parallels and repeated forms.

### System/model comparison

Run the **same complete columns** with identical repository revision, skills/prompts, evidence/tool permissions, completion criteria, and evaluators. Record exact model/provider/version plus skill/tool versions. Compare quality first, then cost/latency/tool-call/retry efficiency.

## Feedback-to-development boundary

A local scholarly reading remains a corpus decision. A recurring/generalizable finding may become a development issue when evidence supports a parser/config rule, linter correction, skill/procedure improvement, deterministic-tool opportunity, harness defect, or eval defect.

Issue creation must carry reproducible examples and avoid generalizing from one token without evidence. The development controller consumes those issues; it does not infer its own parsing scope.

## Consequences for existing tickets

- HARN-003 remains valid as the small deterministic morphology-eval fixture.
- HARN-008 moves earlier: it must extract/formalize the existing skills as workflow contracts before LangGraph owns parsing semantics.
- HARN-018 defines column-run state/completion semantics from the skills.
- HARN-004 becomes a **real column-review LangGraph vertical slice**, not a research-plan-TDD graph.
- HARN-005 traces parsing runs/evals and development runs without conflating them.
- HARN-006 remains the clean-context reviewer for development PR finalization; it is distinct from scholarly expert feedback on parsed data.
- HARN-010 remains the bounded GitHub-issue-driven autonomous development controller.
- HARN-015 defines parsing eval + expert-feedback protocol.
- HARN-016 compares model backends on identical column workloads.
- HARN-017 bridges systematic parsing/eval findings into development issues.

## Revised critical path

```text
HARN-003  small deterministic eval fixture
    |
HARN-014  architecture/backlog correction
    |
HARN-008  formalize existing skill contracts
    |
HARN-018  column-run state + completion semantics
    |
HARN-015  parsing eval + expert feedback protocol
    |
HARN-004  LangGraph real column-review vertical slice
    |
HARN-005  tracing / Langfuse adapter
    |
HARN-016  model comparison benchmark
    |
HARN-017  systematic finding -> GitHub issue bridge

Development-controller track can progress in parallel after the parsing contracts stabilize:
HARN-006 -> HARN-009 -> HARN-007 -> HARN-010
```

HARN-012/HARN-013 remain independent CI/dependency hardening work.
