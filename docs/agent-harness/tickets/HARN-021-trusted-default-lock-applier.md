# HARN-021 — Trusted default-branch uv lock applier

Issue: #37

## Problem

The harness integration line deliberately uses `uv sync --locked`: a dependency metadata change must not silently run with a stale lock. HARN-019 added a safe read-only PR workflow that proves the dependency change can be resolved by pinned uv and emits a provenance-bound lock artifact.

HARN-004 then exposed a transport limitation in the chat GitHub connector: a downloaded 267 KB artifact can be verified locally, but the connector's Git blob/Contents write APIs accept literal content rather than a mounted file reference. Re-transmitting a truncated artifact is unacceptable.

A same-PR writer was researched in HARN-020 and rejected. A `pull_request` workflow is part of the PR merge ref, so a same-repository PR can modify the very workflow that would receive write authority. Fixed-path/branch/CAS checks inside such a self-modifiable writer are not a trust boundary.

## GitHub trust boundary

GitHub documents two properties that make `workflow_run` materially different:

1. A `workflow_run` workflow runs only when its workflow file exists on the repository's **default branch**, and its `GITHUB_SHA`/`GITHUB_REF` are the default branch.
2. A `workflow_run` job may receive write-capable `GITHUB_TOKEN` permissions even when the triggering workflow is unprivileged.

References:
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions

The security warning on `workflow_run` is taken literally: do not checkout, import, execute, or trust code/artifacts from the triggering PR. Treat the PR only as immutable data addressed by its exact head SHA.

## Design

Install two default-branch infrastructure files on fork `main`:

- `.github/workflows/trusted-dependency-lock-apply.yml`
- `.github/scripts/apply_dependency_lock.py`

The workflow is triggered only after successful completion of the exact workflow name `Dependency lock artifact`. It validates from event data that:

- the triggering event was `pull_request`;
- the run belongs to `alexsosn/cuc` / current repository;
- exactly one associated PR exists;
- PR base is `agent-harness-safety`;
- PR head repo is the current repository;
- head branch matches `^harn-[A-Za-z0-9._-]+$`;
- destination is not `main` or `agent-harness-safety`.

The trusted workflow checks out **only `${{ github.sha }}`**, which for `workflow_run` is the default-branch SHA, with `persist-credentials: false`. It never checks out the PR head.

The trusted script then:

1. validates repository/branch/head inputs again;
2. GETs the current `refs/heads/<head-branch>` and requires exact equality with the event head SHA;
3. fetches only hard-coded `agent/pyproject.toml` and `agent/uv.lock` from the exact SHA using the GitHub Contents API;
4. writes only those two files into a fresh temporary directory;
5. runs pinned `uv lock` and `uv lock --check` there; no PR scripts or repository checkout are executed;
6. if lock bytes are unchanged, exits without write;
7. otherwise creates a Git blob from generated lock bytes;
8. reads the exact parent commit tree;
9. creates a tree changing one hard-coded path only: `agent/uv.lock`;
10. creates a commit whose sole parent is the event head SHA;
11. GETs the branch ref a second time and requires it still equals the event head SHA;
12. PATCHes the branch ref with `force: false`.

A concurrent branch update therefore fails closed. A generated-token commit is not accepted as independent CI evidence: GitHub generally suppresses recursive workflow triggers from `GITHUB_TOKEN`, and a subsequent connector/human-authored HARN-004 commit is required before ordinary `Agent tests` evidence is considered.

## Permissions

Trusted writer workflow requests exactly:

```yaml
permissions:
  contents: write
```

All unspecified permissions are `none` under GitHub workflow permission semantics. No actions/artifact read permission is needed because the trusted writer **regenerates** the lock and does not consume the PR artifact.

## TDD strategy

`main` has only a legacy broad Python workflow and no current harness CI. Add a separate read-only PR test workflow:

- `.github/workflows/default-branch-infra-tests.yml`
- `permissions: contents: read`
- pinned checkout/setup-python actions;
- run only `.github/tests/test_trusted_dependency_lock_applier.py` with Python stdlib `unittest`.

### RED

Commit the dedicated test workflow + tests before writer workflow/script exist. The dedicated gate must fail only because those trusted files are absent. Legacy `python-app.yml` is classified separately and is not allowed to substitute for this focused gate.

Tests cover:

- `workflow_run` exact workflow name/type and no PR/push/schedule/manual writer triggers;
- only `contents: write` token scope;
- trusted checkout uses `${{ github.sha }}`, not workflow-run head SHA, and does not persist credentials;
- same-repo / exact base / HARN branch job guards;
- no artifact download and no PR checkout;
- fixed dependency paths and strict branch regex;
- exact-head compare-and-swap before generation and immediately before ref update;
- isolated temp directory and pinned uv execution;
- one fixed Git tree path only;
- exact parent SHA and non-force ref update;
- no upstream repository reference/write.

### GREEN

Implement the minimal workflow/script. Run dedicated exact-head infra tests. Any legacy-main CI failures are documented separately; they do not weaken the focused gate.

### Independent adversarial review

Review from the ticket, final diff, exact test output and GitHub docs, excluding implementation rationale. Attack:

- spoofed workflow-run/PR association;
- malicious triggering workflow or artifact;
- PR repository/base/head substitution;
- branch-name/path injection;
- stale-head race before/after uv generation;
- arbitrary tree paths/parents/force updates;
- accidental execution/import of PR-controlled code;
- broader token permissions;
- writer self-modification;
- bot commit being misclassified as test evidence.

Every blocking finding becomes an isolated RED test before a fix.

## Post-merge integration proof

Only after the reviewed writer is merged to fork `main`:

1. make a connector-authored HARN-004 metadata/provenance commit to trigger a fresh read-only `Dependency lock artifact` run;
2. observe trusted `workflow_run` from `main`;
3. verify it creates exactly one lock-only commit on `harn-004-langgraph-column-slice` with parent equal to the triggering head;
4. verify no other path changed and the lock contains `langgraph==1.2.11` resolution;
5. make a connector-authored follow-up commit on HARN-004;
6. require ordinary `Agent tests` to pass `uv sync --locked` and reach the semantic HARN-004 runtime RED.

Close HARN-021 only after this integration proof. No writes or notifications to `DT-UCPH/cuc`.