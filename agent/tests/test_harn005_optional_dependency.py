from __future__ import annotations

from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_langfuse_is_exact_optional_extra_not_default_dependency():
    payload = tomllib.loads((REPO_ROOT / "agent/pyproject.toml").read_text(encoding="utf-8"))
    project = payload["project"]

    assert "langfuse==4.15.1" not in project["dependencies"]
    assert project["optional-dependencies"]["observability"] == ["langfuse==4.15.1"]


def test_agent_ci_has_separate_locked_observability_smoke_job():
    workflow = (REPO_ROOT / ".github/workflows/agent-tests.yml").read_text(encoding="utf-8")

    assert "langfuse-sdk-smoke:" in workflow
    assert "uv sync --locked --extra observability --no-install-project" in workflow
    assert ".venv/bin/python -m pytest -q tests/test_langfuse_sdk_smoke.py" in workflow
