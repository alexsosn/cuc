# HARN-021 — Trusted default-branch uv lock applier

Issue: #37

## Problem

The harness integration line deliberately uses `uv sync --locked`: dependency metadata changes must never silently run against a stale lock. HARN-019 added a safe read-only PR workflow that proves pinned uv can resolve the change and emits provenance-bound lock evidence, but the chat GitHub connector cannot safely attach the resulting 267 KB downloaded file to a Git write without retransmitting it.

HARN-020 explored a same-PR writer and was rejected because a PR can modify the workflow that would carry `contents: write`. A self-modifiable writer is not a trust boundary.

A trusted `workflow_run` consumer installed on fork `main` solves the workflow-definition problem, but independent adversarial review found a second boundary problem: a **single privileged job must not both resolve PR-controlled dependency metadata and own a write credential**. Sanitizing only the direct `uv` subprocess environment is not sufficient isolation for descendant dependency/build processes on the same runner.

## Revised trust model

The default-branch workflow is split into two jobs with different authorities:

```text
Dependency lock artifact success
        |
trusted workflow_run from main
        |
validate same-repo HARN PR -> agent-harness-safety
        |
resolve-lock                 apply-lock
contents: read               contents: write
        |                         ^
exact-head dependency data       |
pinned uv lock                   |
uv lock --check                  |
        |                         |
fixed artifact + provenance -----+
        |
current trusted workflow run only
        |
verify head/digests -> one-path Git Data commit -> second CAS -> non-force ref
```

The resolver is the only job allowed to run uv. The applier treats the generated lock as inert bytes and never executes PR-controlled code or dependency tooling.

## Resolver contract

`resolve-lock`:

1. runs only after the trusted `workflow_run` guard succeeds;
2. has `contents: read` only;
3. checks out only `${{ github.sha }}` (the trusted default-branch revision) with `persist-credentials: false`;
4. validates same repository, base `agent-harness-safety`, HARN branch policy and exact head SHA;
5. CAS-checks the current HARN branch head before reading;
6. fetches only hard-coded `agent/pyproject.toml` and `agent/uv.lock` at the exact expected head;
7. writes those bytes into a fresh temporary directory;
8. runs pinned uv 0.12.7 using an allowlisted child environment, then `uv lock --check`;
9. CAS-checks the branch again before publishing evidence;
10. writes only `trusted-lock-artifact/uv.lock` and `trusted-lock-artifact/metadata.json`;
11. metadata binds schema version, repository, base/head branch, expected head, uv version, pyproject SHA-256, prior lock SHA-256, and generated lock SHA-256;
12. uploads the fixed artifact with a pinned action and fixed name derived from exact head SHA.

The resolver performs no Git writes.

## Privileged applier contract

`apply-lock`:

1. requires successful `resolve-lock` via `needs`;
2. has `contents: write` and no dependency-resolution responsibility;
3. checks out only trusted `${{ github.sha }}` with credentials disabled;
4. downloads only the fixed-name artifact from the **current trusted workflow run** using a pinned action; it supplies no cross-run ID, repository or alternate token;
5. validates repository/base/head inputs again;
6. CAS-checks the HARN branch before any write preparation;
7. fetches only current exact-head `agent/pyproject.toml` and `agent/uv.lock` as inert bytes;
8. verifies artifact metadata and all digests against those exact-head bytes before any POST/PATCH;
9. exits without write if the verified generated lock equals the current lock;
10. otherwise creates one Git blob and one tree entry for hard-coded `agent/uv.lock`;
11. creates a commit whose sole parent is the exact triggering head;
12. CAS-checks the branch immediately before ref update;
13. PATCHes the HARN branch ref with `force: false`.

The applier contains no subprocess/uv execution path.

## Workflow security invariants

- trigger only `workflow_run` for exact workflow name `Dependency lock artifact`, `types: [completed]`;
- require successful triggering conclusion, triggering event `pull_request`, same repository, PR base `agent-harness-safety`, same-repo head and `harn-*` branch;
- never checkout workflow-run head SHA as repository code;
- no `pull_request_target`, push, schedule, workflow_dispatch or repository_dispatch writer trigger;
- exactly two jobs: `resolve-lock` then `apply-lock`;
- resolver permission is `contents: read`; applier permission is `contents: write`;
- resolver owns pinned uv setup and has cache/token hardening; applier contains no uv setup or command;
- same-run artifact handoff only;
- fixed artifact paths and fixed destination tree path;
- no upstream repository reference or write.

## TDD history and strategy

Initial test-first RED established the default-branch workflow/script contract. Initial implementation reached focused GREEN, but this did **not** count as approval.

Independent adversarial review rejected the single-job design because the privileged process still owned a write token during dependency resolution and because too many invariants were protected only by string-presence tests.

Review findings were converted to tests before fixes:

- behavioral pre-CAS drift must fetch/write nothing;
- unchanged lock must create no Git writes;
- post-preparation drift must never update the ref;
- success must create exactly one lock tree path, exact parent and non-force update;
- untrusted repository/base/branch/head inputs fail closed;
- artifact metadata/head/digest mismatch fails before writes;
- resolver child environment is allowlisted rather than inheriting runner credentials;
- workflow must split read-only resolution from privileged byte-only apply;
- privileged job must consume only same-run fixed-name artifact and never run uv.

The dedicated `Default branch infra tests` workflow remains read-only and pinned. Legacy root Python CI is classified separately because it has a known unrelated root-level collection failure.

## Independent review gate

Final review must be clean-context and attack at least:

- spoofed workflow-run/PR association;
- cross-run or substituted artifact;
- job-level permission inheritance mistakes;
- credential exposure to dependency/build descendants;
- branch/repository/path injection;
- stale head before resolution, before write preparation and before ref update;
- arbitrary tree paths/parents/force updates;
- accidental execution/import of PR-controlled code;
- writer self-modification;
- bot-created lock commit incorrectly treated as ordinary CI evidence.

Every blocking finding enters the RED -> fix -> GREEN sub-loop.

## Post-merge integration proof

Only after exact-final-head GREEN and independent approval, merge this infrastructure to fork `main`. Then:

1. make a connector-authored HARN-004 metadata/provenance commit to trigger a fresh read-only `Dependency lock artifact` run;
2. observe the trusted default-branch `workflow_run`;
3. verify resolver has read-only permissions and applier has write permissions;
4. verify exactly one bot commit changes only `agent/uv.lock`, parented to the triggering HARN-004 head;
5. verify the lock contains the `langgraph==1.2.11` resolution;
6. make a connector-authored follow-up commit on HARN-004;
7. require ordinary `Agent tests` to pass `uv sync --locked` and reach the semantic HARN-004 runtime RED.

Close HARN-021 only after this integration proof. No writes or notifications to `DT-UCPH/cuc`.
