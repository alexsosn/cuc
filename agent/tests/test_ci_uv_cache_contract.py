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


def test_agent_tests_has_exactly_one_shared_uv_cache_writer() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    full = _job_block(workflow, "agent-tests", "langfuse-sdk-smoke")
    smoke = _job_block(workflow, "langfuse-sdk-smoke")

    # The full authoritative suite owns the shared cache write. Leaving save-cache
    # at setup-uv's default true makes that ownership explicit through absence of
    # a false override while avoiding a second cache namespace.
    assert "save-cache: false" not in full
    assert "cache-suffix:" not in full

    # Smoke may restore the shared cache, but it must never race the full job to
    # upload the same key. Do not solve the race by disabling restore or forking a
    # second cache namespace.
    assert "save-cache: false" in smoke
    assert "restore-cache: false" not in smoke
    assert "cache-suffix:" not in smoke

    assert workflow.count("astral-sh/setup-uv@") == 2
