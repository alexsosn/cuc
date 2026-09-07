# HARN-000 — ChatGPT → GitHub execution substrate

Status: validated experimental substrate; production CI repair remains separate work.

## Goal

Prove that ChatGPT chat mode can drive a real RED → GREEN test cycle in the fork, inspect the resulting GitHub Actions evidence, and bind every result to an exact development commit without touching `DT-UCPH/cuc` or relying on a local shell.

## Selected execution primitive

Use an internal pull request whose head and base are both in `alexsosn/cuc`.

The existing `.github/workflows/python-app.yml` runs on `pull_request` events targeting `main`. Opening or synchronizing such an internal PR creates a new GitHub Actions run. ChatGPT can create/update the branch and PR, then inspect run metadata, jobs, and logs through the connected GitHub interface.

This is preferable to relying on workflow re-runs for TDD because a new commit on the PR branch creates a new run with a new `head_sha`. It therefore avoids silently re-testing an earlier commit.

`workflow_dispatch` was not selected because the current ChatGPT/GitHub tool surface does not expose an operation to dispatch a new workflow run. GitHub supports manual dispatch through the UI, CLI, and REST API when a workflow declares `workflow_dispatch`, but that is not presently callable from this chat execution surface.

## Experiment

Internal draft PR: `alexsosn/cuc#12`

Base commit: `ad69400f5446e1c8217af01659c7c10ab00c015b` (`alexsosn/cuc:main` during the experiment).

### Attempt 1 — blocked execution, not RED

Head commit: `83b8cf3fdb3d5a7c166fe45f31711454ba0f2e5c`

Actions run: `34120481133`

The branch contained an intentionally failing test, but root `pytest` aborted during collection with 105 pre-existing errors before the probe could execute. Representative failures were unresolved top-level imports such as `pipeline`, `scripts`, `linter`, and `morph_features`, plus a missing `natsort` dependency.

Classification: **blocked execution**. This run is not evidence of a TDD RED state.

This distinction is important for the future harness: infrastructure/collection failure must not be represented as a failing product test.

### Attempt 2 — valid RED

Head commit: `a955dba211e0cf904682740e74d9fe11dc801bfb`

Actions run: `34120753823`

A temporary experiment-only `pytest.ini` narrowed `python_files` to `test_harn_000_execution_substrate.py` so the unchanged workflow could reach the probe without collecting the broken baseline suite.

Observed pytest result:

- one test collected;
- `tests/test_harn_000_execution_substrate.py` executed;
- failure was `AssertionError: HARN-000 intentional RED probe`;
- pytest exited non-zero;
- Actions run conclusion was `failure`.

This is the valid RED evidence.

### Attempt 3 — valid GREEN on a new SHA

Head commit: `3f8855d52487071c8ae3285024ed47f4e75dea0e`

Actions run: `34120849392`

Only the probe expectation was changed from the intentional failure to a passing assertion. The PR synchronization created a distinct Actions run whose metadata reports the new head SHA.

Observed result:

- Actions run `34120849392` reports `head_sha = 3f8855d52487071c8ae3285024ed47f4e75dea0e`;
- the pytest step completed successfully;
- the workflow completed with conclusion `success`.

This proves the RED and GREEN results were produced by different branch heads rather than by re-running stale code.

## Exact-SHA semantics

For `pull_request` workflows, GitHub's default checkout does not check out the raw head commit. It checks out the PR merge ref (`refs/pull/<number>/merge`) and tests the synthetic merge commit produced from the PR head and base.

During the RED run, checkout used merge commit `36cbacb673924b2206293fb70b30a38d46ba4be1`, whose commit message recorded the merge of head `a955dba211e0cf904682740e74d9fe11dc801bfb` into base `ad69400f5446e1c8217af01659c7c10ab00c015b`.

This behavior is desirable for an integration gate because it tests the proposed merged result. The harness must nevertheless record both:

- the PR `head_sha`, identifying the development revision;
- the actual checked-out merge SHA, identifying the code executed by CI.

If a future gate needs to test the head commit alone, the workflow must explicitly check out `github.event.pull_request.head.sha`; that is a separate design decision and should not be inferred from run metadata.

## Safety evidence

The experimental PR has both head and base in `alexsosn/cuc`.

The Actions job reported `GITHUB_TOKEN` permissions:

- `Contents: read`
- `Metadata: read`

No upstream PR, issue, comment, review request, mention, workflow, branch, or repository setting was created or modified.

No `pull_request_target` event is used.

## Limitations discovered

### Existing root CI is not a usable general test gate

The current workflow executes bare `pytest` at repository root. On the experiment base this fails during collection before targeted tests run. The HARN-000 probe used a temporary `pytest.ini` only to isolate the execution-substrate experiment.

That file must **not** be merged because it would suppress normal test discovery.

A separate CI repair ticket should define a real, persistent test command/environment for harness development.

### Python/runtime drift

The existing workflow installs Python 3.10. Current `agent/pyproject.toml` on the harness/review line requires Python >=3.13. A future harness CI gate must use the project-supported interpreter rather than silently exercising an older environment.

### Action version drift

The experiment logs warn that `actions/checkout@v3` and `actions/setup-python@v3` target deprecated Node.js 20 and are being forced onto Node.js 24. Updating these actions belongs in CI maintenance rather than the execution-substrate proof.

### Internal PRs are intentionally visible inside the fork

Each synchronization of an internal PR to `main` creates a workflow run. This is acceptable as an explicit test gate, but it should not become the inner edit loop. Agents should commit freely to development branches and use the PR execution gate when tests need to be executed.

## Operational contract for the harness

Until a better callable execution API is available, a test execution requested from ChatGPT should follow this contract:

1. operate only in `alexsosn/cuc`;
2. ensure the development branch has an internal fork PR that triggers the relevant test workflow;
3. record the intended branch head SHA before execution;
4. cause a PR synchronization by committing the candidate test/code revision;
5. identify the newly created workflow run whose `head_sha` equals that branch head;
6. inspect the job and logs rather than trusting PR badge state alone;
7. record the actual checkout/merge SHA when available;
8. classify failures as at least `test-failure`, `regression`, or `blocked-execution` rather than treating every red workflow as the same state;
9. never use an old workflow re-run as proof for a newer branch head;
10. never target `DT-UCPH/cuc` for execution or writes without explicit human authorization.

## HARN-000 disposition

The transport/execution question is answered: ChatGPT can drive a fork-local, exact-head-attributed RED → GREEN cycle through internal PR synchronization and inspect the evidence.

The experiment also showed that CUC's existing general CI configuration is not suitable as the persistent harness test gate. That is follow-up CI infrastructure work and should be resolved before the first LangGraph implementation ticket that depends on reliable automated test execution.

PR #12 is experimental evidence and must be closed without merge after independent adversarial review because its temporary `pytest.ini` intentionally narrows test discovery.
