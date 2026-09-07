# HARN-011 — Agent CI test gate

This document defines the fork-local test execution contract used by the CUC agent harness development line.

## Authoritative suite

The agent/harness suite is the pytest suite configured by `agent/pyproject.toml`:

- Python: `>=3.13`;
- working directory: `agent/`;
- pytest discovery: `testpaths = ["tests"]`;
- command in CI: `.venv/bin/python -m pytest -q`;
- no repository-root `pytest.ini`, test exclusion, or discovery narrowing is used to obtain GREEN.

The legacy `.github/workflows/python-app.yml` remains a separate root-CUC workflow. Its historical Python 3.10/root-pytest contract is not authoritative for `agent/tests`.

## Dependency contract

The dedicated `.github/workflows/agent-tests.yml` job:

1. checks out the PR synthetic merge ref with credentials not persisted;
2. provisions Python 3.13;
3. provisions uv 0.12.7;
4. runs `uv sync --frozen --no-install-project` from `agent/`, so project runtime dependencies come from `agent/uv.lock`;
5. installs the CI-only test packages `pytest==9.1.1`, `iniconfig==2.3.0`, and `pluggy==1.6.0` into `agent/.venv`; pytest's `packaging`/`pygments` dependencies are already supplied by the frozen project environment;
6. runs the complete pytest suite from `agent/`.

Workflow actions are pinned to exact release commits:

- `actions/checkout` v7.0.1 — `3d3c42e5aac5ba805825da76410c181273ba90b1`;
- `actions/setup-python` v7 — `5fda3b95a4ea91299a34e894583c3862153e4b97`;
- `astral-sh/setup-uv` v10.0.1 — `20cfd1bf945f4377ade1205e4dbc17946fc9a30d`.

The workflow has `permissions: contents: read`, uses `pull_request` rather than `pull_request_target`, and runs for every pull request whose base is `agent-harness-safety`. A path filter is deliberately not used: a permanent gate must not silently disappear because a future change touches a path that an older allowlist failed to anticipate.

## Execution identity

GitHub `pull_request` workflows execute a synthetic merge commit by default. For every harness test result, record both:

- the PR head SHA that contains the proposed code;
- the actual checked-out synthetic merge SHA from the Actions log/run.

A result is stale or non-attributable if either identity is unknown.

## Failure classification

The controller must distinguish at least these outcomes:

### `blocked-execution`

The intended tests did not execute normally. Examples:

- dependency installation failed;
- Python/runtime setup failed;
- pytest aborted during collection/import;
- required local/generated evidence was unavailable before the intended assertion could run;
- workflow configuration prevented the intended job from running.

Do not count this as TDD RED and do not infer product correctness from missing test output.

### `test-failure`

The intended test executed and an assertion/expected test contract failed. This is valid RED evidence.

### `regression`

The authoritative full suite executes normally and one or more existing tests fail after a proposed change. Fix the defect or the invalid test at its authoritative source; do not hide the test.

### `success`

The authoritative full suite executes normally and all collected tests pass.

## HARN-011 RED→GREEN evidence

### Intentional runtime-contract RED

- PR: `#15` (`harn-011-ci-gate` → `agent-harness-safety`)
- head SHA: `45d8f7f7bf5cb4649575be7210029c0d9c845a0b`
- Actions run: `34124925246`
- executed synthetic merge SHA: `78ce10e8df2d7c77a0ac8aa1da7ab36d87fa4592`
- result: one test collected and failed with `agent CI must use Python >=3.13; got 3.10`
- classification: `test-failure`

The contract test itself was committed first at `e02d4f14ceb09c529d2fe17ac260108f600aa459`; the intentionally wrong 3.10 workflow was added afterward.

### Focused GREEN

- head SHA: `34c0939e08312722c6711e8298be0b1cef75bc7f`
- Actions run: `34125004333`
- change from RED: runtime changed to Python 3.13;
- result: focused CI contract test passed.

### Full-suite regression surfaced by the real gate

After locked project dependencies were installed and discovery was broadened to the complete suite:

- head SHA: `a0eaf91a5e9d1b8c7070dfbecebea3705d7ea50d`
- Actions run: `34125079508`
- executed synthetic merge SHA: `8f9885a96558b8be7f318ad9a97f40c36aead286`
- result: `1 failed, 1044 passed`;
- failing test: `SeedReviewedColumnRangeTests.test_columnless_tablet_can_be_seeded_in_dry_run`.

The failure exposed a hidden dependency on ignored `agent/generated_sources/**`: a no-op dry run over a fully reviewed tablet required a generated sign-span TSV even though it had no row to append.

A second focused RED was added before the fix:

- head SHA: `79bab74225d741c38c1c6f6a42aac03625054410`
- Actions run: `34125546351`
- executed synthetic merge SHA: `c05a9e0938b3e70b8bb018e68e690f9ab160bfaa`
- result: `2 failed, 1044 passed`;
- new assertion required seed planning to omit section headers that contain no new rows.

The implementation then made generated sign-span input lazy (required only when there are new seed rows), introduced deterministic seed-order filtering, and made a no-op non-dry run perform no write.

### Full-suite GREEN

- head SHA: `3b7ff34a0656c41af66cdc54cc2963331cd59c09`
- Actions run: `34125707555`
- executed synthetic merge SHA: `2aade7d1bbefbd8aa497f05590ac2741a7ac93bc`
- result: `1046 passed in 15.44s`.

This GREEN used the complete `agent/tests` discovery configured by `agent/pyproject.toml`; no tests were excluded to obtain it.

## Maintenance rules

- Keep the test command rooted at `agent/` unless package/import layout is intentionally redesigned with tests proving the new contract.
- Keep Python aligned with `agent/pyproject.toml` and `agent/uv.lock`.
- Update pinned action/tool versions deliberately and record the new provenance.
- If pytest and its CI-only dependencies become locked development dependencies, remove the separate CI installation rather than maintaining duplicate dependency declarations.
- Do not add a path filter to the permanent PR gate merely to save a small amount of CI time.
- A failing full-suite test is work to investigate, not a reason to narrow discovery.
- Generated-data policy still applies inside tests and fixes: never hand-edit `auto_parsing/**` to obtain GREEN.
