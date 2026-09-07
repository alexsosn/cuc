# CUC-Origin — Agent Instructions

## Fork safety / upstream isolation

All iterative development MUST stay inside `alexsosn/cuc`.

Upstream `DT-UCPH/cuc` is read-only for agents unless the human user explicitly authorizes a release action.

Without explicit human authorization, agents MUST NOT create or modify upstream pull requests, issues, comments, review requests, mentions, branches, workflow runs, releases, repository settings, or other upstream state.

Before every GitHub write:

- `alexsosn/cuc` -> allowed;
- `DT-UCPH/cuc` read -> allowed;
- `DT-UCPH/cuc` write -> STOP and require explicit human authorization.

Keep research, RED/GREEN development, CI iteration, and logically independent adversarial review inside the fork. An upstream PR is a final release action only after tests, synchronization, diff inspection, adversarial review, and explicit human approval.

See `.github/AGENT_SAFETY.md` and `AGENTS.md` for the full policy.

## Never hand-edit generated data — fix the generator instead

Auto-parsing output (`auto_parsing/**`) and every other intermediate or
generated artifact is produced by the pipeline. It may change **only** by
regenerating it. Do **not** apply "surgical" manual edits — row deletions,
substitutions, patches, or find-and-replace — to generated data, not even to
make it match a result you know is correct.

If generated output is wrong, the **generator** is wrong. Debug and fix the
parser/pipeline (and add a test), then regenerate. Surgery hides the defect
instead of fixing it and silently diverges the committed data from what the
pipeline actually produces, so the next regeneration undoes or contradicts the
edit.

If regeneration is blocked — non-determinism, drift versus the committed
baseline, a tripped step-change safeguard — **that blocker is the bug**. Stop
and fix it; do not route around it by editing the output.

Reviewed data (`reviewed/**`) is the opposite case: it is curated by hand and
is never overwritten by regeneration.
