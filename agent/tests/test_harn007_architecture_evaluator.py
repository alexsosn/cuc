from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


AGENT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = AGENT_ROOT / "scripts" / "evaluate_harn007_architecture.py"
DEEPAGENTS_REVISION = "54696577caf3dfcefb662db08b4a8034ec6a35cd"


def _report() -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=AGENT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


def test_architecture_evaluator_is_bound_to_real_harn004_and_harn006_sources() -> None:
    report = _report()
    evidence = report["repository_evidence"]

    assert evidence["column_review_source"] == "harness/langgraph_column_review.py"
    assert evidence["column_state_source"] == "harness/column_state.py"
    assert evidence["development_reviewer_source"] == "harness/development_reviewer.py"
    assert evidence["pyproject_source"] == "pyproject.toml"

    assert evidence["column_review_imports_langgraph"] is True
    assert evidence["column_state_imports_langgraph"] is False
    assert evidence["development_reviewer_imports_deepagents"] is False
    assert evidence["core_declares_deepagents_dependency"] is False


def test_evaluator_recovers_complete_column_control_invariants_from_source() -> None:
    report = _report()
    evidence = report["repository_evidence"]

    assert set(evidence["graph_nodes"]) == {
        "initialize",
        "initial_evidence",
        "initial_adjudicate",
        "reconcile",
        "revisit_evidence",
        "revisit_adjudicate",
        "close_reconciliation",
        "completion_gate",
        "complete",
    }
    assert evidence["uses_next_token_id_cursor"] is True
    assert evidence["loops_required_completion_gates"] is True
    assert evidence["binds_capability_completion_verifiers"] is True
    assert evidence["has_clean_context_development_review"] is True


def test_decision_model_distinguishes_parser_and_development_controller() -> None:
    report = _report()
    decision = report["decision"]

    assert decision["parser_orchestrator"] == "retain-explicit-langgraph"
    assert decision["development_controller"] == "defer-deepagents-until-harn009"
    assert decision["core_dependency"] == "do-not-add-deepagents-now"
    assert decision["parser_primary_deepagents_fit"] == "reject"
    assert decision["development_helper_deepagents_fit"] == "conditional"


def test_external_framework_observations_are_dated_and_source_backed() -> None:
    report = _report()
    observations = report["external_observations"]

    assert observations["checked_on"] == "2026-09-11"
    assert observations["deepagents_revision"] == DEEPAGENTS_REVISION
    assert observations["deepagents_runtime"] == "langgraph"
    assert observations["compiled_langgraph_can_be_subagent"] is True
    assert observations["tool_boundary_required_for_security"] is True
    assert observations["sources"]
    assert all(source.startswith("https://") for source in observations["sources"])

    github_sources = [
        source for source in observations["sources"]
        if source.startswith("https://github.com/langchain-ai/deepagents/")
    ]
    assert github_sources
    assert all(DEEPAGENTS_REVISION in source for source in github_sources)
    assert all("/blob/main/" not in source for source in github_sources)


def test_report_is_deterministic() -> None:
    assert _report() == _report()
