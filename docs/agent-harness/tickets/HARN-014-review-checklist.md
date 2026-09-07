# HARN-014 independent review checklist

Review the architecture correction against repository evidence, not author intent.

Reject if any item fails:

- `review-automatic-parsing` is represented as complete-column work with every token reviewed in order.
- Worklists/lints/audits guide attention/evidence only; no suspicious-token selector controls scope.
- Whole-column context remains part of the parsing workflow.
- Skills/prompts are treated as authoritative workflow/domain knowledge rather than optional prompt suggestions.
- `auto_parsing/**` remains generated and never hand-edited.
- Scholarly expert feedback is distinct from development PR adversarial review.
- Research-plan-TDD-review is attached to GitHub development issues, not inserted into token parsing.
- HARN-004 is a real column-review LangGraph slice.
- HARN-008/HARN-018 precede HARN-004 so runtime semantics are extracted before framework orchestration.
- Evals explicitly cover parsed output, agent behavior, expert feedback and fair model comparison.
- Model comparison requires identical complete-column workload, revision, skills/tools/permissions and eval protocol.
- Systematic parser/linter/skill/tool/harness/eval findings can become reproducible GitHub issues; isolated local readings are not automatically generalized.
- Primary deliverable remains morphologically parsed CUC, followed by reproducible automatic parsing and supporting harness/eval/dev infrastructure.
- No upstream write or generated-data mutation is introduced by HARN-014.
