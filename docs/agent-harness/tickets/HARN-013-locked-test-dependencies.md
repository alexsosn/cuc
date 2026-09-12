# HARN-013 — Locked test dependencies

## Goal

Move the authoritative agent test dependency into the project dependency contract so local/dev and CI use the same `agent/pyproject.toml` + `agent/uv.lock` environment. Remove CI-only package installation without changing test discovery or the optional Langfuse smoke environment.

## Research

Current repository state on `agent-harness-safety` (`05c89beec2fab01a304cf9b0bf6ac646175ef929`):

- `agent/pyproject.toml` has runtime dependencies and an `observability` extra, but no development dependency group.
- both `agent-tests` and `langfuse-sdk-smoke` first run `uv sync --locked ...` and then separately run `uv pip install pytest==9.1.1 iniconfig==2.3.0 pluggy==1.6.0`.
- therefore test execution has two dependency authorities/resolution paths: the project lock and an ad-hoc CI install.

Current uv documentation (checked 2026-09-12) recommends standardized PEP 735 `[dependency-groups]` for development dependencies. The `dev` group is special-cased and included by default by `uv sync`; `--no-install-project` does not disable dependency groups. `tool.uv.dev-dependencies` is legacy and not recommended.

Decision:

```toml
[dependency-groups]
dev = [
    "pytest==9.1.1",
]
```

Declare only pytest directly. `iniconfig` and `pluggy` are pytest implementation dependencies and remain reproducibly pinned transitively by `uv.lock`; making them direct project policy would unnecessarily couple the project to pytest internals.

No new CI flag is required: both existing `uv sync --locked --no-install-project` and `uv sync --locked --extra observability --no-install-project` include the default `dev` group.

## Lock safety

`agent/uv.lock` is generated data and must not be hand-edited. Adding the dependency group to `agent/pyproject.toml` intentionally makes ordinary `uv sync --locked` fail until the trusted HARN-021 lock workflow generates and applies the new lock. The final connector-authored follow-up commit must then prove ordinary CI consumes the resulting lock without a second resolver/install step.

## TDD plan

1. RED first: add a stdlib-only contract test that requires:
   - `[dependency-groups].dev` exists;
   - `pytest==9.1.1` is declared exactly once there;
   - `iniconfig` and `pluggy` are not direct dev requirements;
   - `agent-tests.yml` contains no `uv pip install` / `Install pinned test runner` step;
   - both jobs still use `uv sync --locked`.
2. Run the full PR CI and capture a clean RED caused only by the missing project declaration / duplicated CI install.
3. Add the `dev` dependency group to `agent/pyproject.toml`; do not touch `uv.lock` manually.
4. Let trusted HARN-021 update only `agent/uv.lock`.
5. Remove both ad-hoc CI install steps.
6. Connector-authored follow-up commit triggers ordinary `Agent tests` + Langfuse smoke against the trusted lock.
7. Require exact-head GREEN for full suite and SDK smoke.
8. Fresh logically independent adversarial review attacks dependency duplication, accidental omission of pytest, hidden resolver paths, optional-extra behavior, and manual lock edits.

## Acceptance

- one authoritative direct test dependency declaration: `dependency-groups.dev = ["pytest==9.1.1"]`;
- test dependencies are present in the generated lock;
- ordinary CI does not invoke `uv pip install` or another test resolver;
- full `agent/tests` discovery remains green on Python 3.13;
- Langfuse SDK smoke remains green with `--extra observability` plus the default dev group;
- `agent/uv.lock` is generated only through trusted lock machinery;
- no upstream writes or notifications.
