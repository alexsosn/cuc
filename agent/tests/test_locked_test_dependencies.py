from __future__ import annotations

from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "agent" / "pyproject.toml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-tests.yml"


def test_pytest_is_owned_by_the_locked_dev_dependency_contract() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    groups = data.get("dependency-groups", {})
    dev = groups.get("dev")

    assert isinstance(dev, list), "pyproject must declare [dependency-groups].dev"
    assert dev.count("pytest==9.1.1") == 1
    assert not any(item.startswith("iniconfig") for item in dev)
    assert not any(item.startswith("pluggy") for item in dev)


def test_agent_ci_uses_only_the_locked_project_environment() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "Install pinned test runner" not in workflow
    assert "uv pip install" not in workflow
    assert workflow.count("uv sync --locked") == 2
    assert "uv sync --locked --no-install-project" in workflow
    assert "uv sync --locked --extra observability --no-install-project" in workflow
