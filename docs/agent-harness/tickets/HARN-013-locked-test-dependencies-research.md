# HARN-013 — Locked test dependencies research

## Scope

Issue #17 asks to remove the second CI dependency resolver/install step and make the agent project lock the authoritative test environment.

Current state on `agent-harness-safety`:

- `agent/pyproject.toml` has runtime dependencies and an `observability` extra, but no development dependency group;
- ordinary CI runs `uv sync --locked --no-install-project` and then separately runs `uv pip install pytest==9.1.1 iniconfig==2.3.0 pluggy==1.6.0`;
- the Langfuse smoke job repeats the same second test-runner install;
- this makes the environment reproducible enough, but creates two dependency authorities and a second resolver invocation outside `agent/uv.lock`.

## Current uv semantics checked 2026-09-12

Official uv documentation:

- https://docs.astral.sh/uv/concepts/projects/dependencies/
- https://docs.astral.sh/uv/concepts/projects/sync/
- https://docs.astral.sh/uv/reference/settings/

Relevant conclusions:

1. Standardized development dependencies belong in the top-level `[dependency-groups]` table (PEP 735). The older `tool.uv.dev-dependencies` form is legacy and not preferred.
2. The `dev` group is special-cased and is included by default in `uv sync` unless explicitly disabled.
3. `uv sync --locked` validates/uses the existing lock rather than re-locking; therefore the existing ordinary CI command can remain `uv sync --locked --no-install-project` and will install the dev group once it is declared and locked.
4. `--no-install-project` is still appropriate: tests import the repository code from the checkout and the current project layout does not need an editable/package installation merely to install dependencies.

## Direct-vs-transitive declaration

Declare only the intentional direct test dependency:

```toml
[dependency-groups]
dev = [
    "pytest==9.1.1",
]
```

Do **not** promote `iniconfig` or `pluggy` into direct project dependencies merely because HARN-011 pinned them in an ad-hoc CI command. They are pytest implementation dependencies and should be resolved/pinned by `agent/uv.lock`. The lock is the authoritative complete environment; `pyproject.toml` should describe intentional direct requirements.

This preserves the existing direct pytest version while eliminating duplicate dependency ownership.

## Lock-generation boundary

`agent/uv.lock` remains generated data. Never hand-edit it.

The repository already has the HARN-019/HARN-021 trusted dependency-lock path:

1. connector-authored `agent/pyproject.toml` change;
2. read-only dependency-lock workflow resolves the exact PR head using pinned uv;
3. trusted default-branch applier commits only `agent/uv.lock` to the PR branch;
4. ordinary `Agent tests` then proves `uv sync --locked --no-install-project` succeeds from the resulting exact lock.

HARN-013 must use that path rather than serializing or manually editing lock bytes.

## Desired CI contract

Both jobs should have one environment installation step only:

- ordinary full suite: `uv sync --locked --no-install-project`;
- Langfuse smoke: `uv sync --locked --extra observability --no-install-project`.

Neither job should run `uv pip install` or carry separate pytest/iniconfig/pluggy pins.

## TDD assertions

Before implementation add a stdlib-only contract test that requires:

- `[dependency-groups].dev` exists;
- exact direct requirement `pytest==9.1.1` is present;
- `iniconfig` and `pluggy` are not declared as direct dev requirements;
- `agent/uv.lock` contains the locked pytest package;
- `.github/workflows/agent-tests.yml` retains `uv sync --locked` for both jobs;
- workflow no longer has `Install pinned test runner` or `uv pip install`.

The first test-only commit must be RED against the current metadata/workflow, while the rest of the suite remains unaffected.

## Independent review rubric

Reject if:

- CI still invokes a second dependency resolver/install path;
- pytest is absent from the lock-backed project metadata;
- the lock is hand-edited;
- the dev group is accidentally excluded from ordinary `uv sync`;
- Langfuse smoke loses pytest while selecting the observability extra;
- transitive pytest implementation packages are unnecessarily treated as first-class direct project requirements;
- local/CI setup documentation still instructs a separate pytest install;
- any upstream repository is written or notified.
