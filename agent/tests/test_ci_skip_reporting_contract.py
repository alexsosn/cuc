from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-tests.yml"


def _job_block(workflow: str, job: str, next_job: str | None = None) -> str:
    marker = f"  {job}:\n"
    assert marker in workflow, f"missing job {job!r}"
    block = workflow.split(marker, 1)[1]
    if next_job is not None:
        end = f"  {next_job}:\n"
        assert end in block, f"missing following job {next_job!r}"
        block = block.split(end, 1)[0]
    return block


def test_full_suite_reports_skip_reasons_without_changing_smoke_selection() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    full = _job_block(workflow, "agent-tests", "langfuse-sdk-smoke")
    smoke = _job_block(workflow, "langfuse-sdk-smoke")

    assert "run: .venv/bin/python -m pytest -q -rs\n" in full
    assert "run: .venv/bin/python -m pytest -q tests/test_langfuse_sdk_smoke.py\n" in smoke
    assert " -rs" not in smoke
