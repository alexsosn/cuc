# HARN-020 — Safe uv lock applier for fork PR branches

Issue: #33

## Research

HARN-019 intentionally solved dependency resolution as a read-only operation: pinned uv produces `pyproject.toml`, `uv.lock`, and provenance metadata; a deterministic verifier rejects stale/wrong/tampered artifacts. HARN-004 exposed the remaining transport gap: the chat GitHub connector can download and inspect the generated artifact, but it cannot attach a local 267 KB file directly to a Git blob or Contents API update without retransmitting the entire file as text.

The correct response is not to hand-edit `uv.lock`, bypass `uv sync --locked`, or add write authority to the read-only generator. The write operation should be a separate, narrowly constrained capability.

GitHub's workflow permission model permits a job to request only `contents: write`; unspecified scopes are `none`. `GITHUB_TOKEN` is repository-scoped and short-lived. GitHub also suppresses ordinary workflow recursion for most events caused by that token, so a bot-authored lock commit must not itself be treated as an independent CI run; a later connector/human-authored commit must trigger ordinary CI on the resulting head.

References:
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions
- https://docs.github.com/en/actions/concepts/security/github_token
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

## Security boundary

The applier is not a generic repository writer.

It may only:

1. run on `pull_request` events targeting `agent-harness-safety`;
2. act on a same-repository PR whose head branch matches `^harn-[A-Za-z0-9._-]+$`;
3. reject `main`, `agent-harness-safety`, fork heads, and any upstream repository;
4. capture the event head SHA and require the checked-out commit to equal it;
5. run pinned uv from a clean checkout with `persist-credentials: false`;
6. generate/check `agent/uv.lock` from the PR's `agent/pyproject.toml`;
7. prove that the only working-tree change is exactly `agent/uv.lock` (or no change);
8. use GitHub's Git Data API with a hard-coded tree path `agent/uv.lock` and parent `expected_head_sha`;
9. update only `refs/heads/<validated harn branch>` with `force=false` so a concurrent branch move causes a non-fast-forward failure rather than a stale write;
10. never execute repository scripts from the PR head and never persist credentials during generation.

Using Git Data rather than Contents API matters for compare-and-swap semantics: the generated commit has the event head as its sole parent, and the non-force ref update fails if the PR branch advanced concurrently.

## Planned workflow

`.github/workflows/dependency-lock-apply.yml`

- trigger: `pull_request` to `agent-harness-safety`, types `opened`, `synchronize`, `reopened`;
- job-level guard: same repository + `harn-*` branch;
- `permissions: contents: write` only;
- checkout exact `github.event.pull_request.head.sha`, no persisted credentials;
- pinned `actions/checkout`, `actions/setup-python`, `astral-sh/setup-uv`;
- preflight clean-tree/head/branch assertions;
- `uv lock` then `uv lock --check` in `agent/`;
- changed-path assertion (`agent/uv.lock` only);
- if unchanged: exit without write;
- if changed: inline trusted Python calls GitHub REST Git Data endpoints to create blob/tree/commit and non-force update the validated head ref.

No arbitrary destination path, repository, ref, commit parent, command, or workflow input exists.

## TDD plan

### RED

Add `agent/tests/test_dependency_lock_applier_workflow.py` before the workflow exists. Tests must assert:

- workflow exists under the exact path;
- no `pull_request_target`, schedule, push, workflow-dispatch, or arbitrary inputs;
- base branch is exactly `agent-harness-safety`;
- only `contents: write` is granted;
- same-repository and `harn-*` branch guards are present;
- checkout credentials are not persisted;
- action and uv versions are pinned;
- hard-coded output path is `agent/uv.lock`;
- explicit rejection of `main` and `agent-harness-safety`;
- checked-out HEAD is compared to event head SHA;
- post-`uv lock` changed paths are restricted to the lock file;
- Git Data commit parent is event head SHA and ref update is non-force;
- upstream `DT-UCPH/cuc` does not appear anywhere.

Expected RED: missing workflow only; all existing tests remain green.

### GREEN

Implement only the minimal workflow required by the tests. Run full `agent/tests`.

### Integration proof

After merge into `agent-harness-safety`, use the already-open HARN-004 PR as a deliberate stale-lock case:

1. trigger a new PR synchronize event with `langgraph==1.2.11` still present and stale `uv.lock`;
2. confirm the trusted-base applier writes one lock-only commit to `harn-004-langgraph-column-slice`;
3. inspect changed files for that commit/branch and confirm no other path changed;
4. make a connector-authored documentation/provenance commit on HARN-004 so ordinary CI runs on a head containing the generated lock;
5. require `uv sync --locked` to pass and semantic HARN-004 tests to return to the intended runtime RED.

### Independent adversarial review

Attack:

- PR head branch substitution / repository substitution;
- race between generation and ref update;
- force-update or stale-parent acceptance;
- arbitrary path/tree injection;
- token scope broader than contents;
- credential persistence;
- PR-controlled script execution;
- self-modifying workflow risk;
- fork/upstream write path;
- recursion / bot commit incorrectly accepted as test evidence.

Review findings enter a new RED→fix cycle before merge.