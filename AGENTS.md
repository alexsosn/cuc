# CUC-Origin — Agent Instructions

## Fork safety / upstream isolation

This repository is an isolated development fork of `DT-UCPH/cuc`.

All iterative development MUST happen inside `alexsosn/cuc` unless the human user explicitly authorizes an upstream action.

### Hard safety rules

Agents MUST NOT, unless explicitly instructed by the human user:

- create a pull request against `DT-UCPH/cuc`;
- create or modify issues in `DT-UCPH/cuc`;
- comment on upstream pull requests or issues;
- request reviews from upstream maintainers;
- mention or notify upstream collaborators;
- push commits or branches to `DT-UCPH/cuc`;
- modify upstream repository settings, Actions, rulesets, releases, or metadata;
- use upstream GitHub Actions as an inner development or test loop.

Reads from upstream are allowed for synchronization, research, comparison, and review.
Writes to upstream are release actions and require explicit human authorization.

Before every GitHub write, classify the destination:

- `alexsosn/cuc` -> allowed;
- `DT-UCPH/cuc` read -> allowed;
- `DT-UCPH/cuc` write -> STOP and require explicit human authorization.

Do not use comments, mentions, review requests, issue creation, upstream PR creation, or upstream workflow triggering merely to test whether something works.

### Development loop

Use the following default workflow:

1. Research and inspect.
2. Create or update a development branch in `alexsosn/cuc`.
3. Write tests before or together with implementation.
4. Implement changes in the fork.
5. Run available tests and validation.
6. Perform a logically independent adversarial review.
7. Revise and repeat until all gates pass.
8. Keep all RED/GREEN iteration inside the fork.

Pushes to development branches are preferred over repeatedly opening or updating upstream pull requests.
An internal pull request may be created from a development branch to `alexsosn/cuc:main` when fork-local CI or integrated review is useful.

### Upstream submission gate

Creating a PR from `alexsosn/cuc` to `DT-UCPH/cuc` is a release action, not part of the development loop.

Before an upstream PR may be created, all of the following must be true:

- implementation is complete;
- relevant tests pass;
- generated artifacts are consistent;
- no prohibited hand-edits to generated data occurred;
- the branch is synchronized or rebased against current upstream;
- a logically independent adversarial review has completed;
- review findings have been resolved or explicitly documented;
- the final diff has been inspected for accidental files, generated noise, credentials, and unrelated changes;
- the human user has explicitly authorized upstream submission.

Without explicit human authorization, stop after preparing the branch in `alexsosn/cuc`.

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
