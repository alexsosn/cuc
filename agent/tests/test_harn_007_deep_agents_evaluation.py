from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def _runtime():
    try:
        return importlib.import_module("harness.deep_agents_evaluation")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-007 comparison contract is not implemented yet: {exc}")


def _record(
    *,
    disposition: str = "keep-langgraph",
    observed_token_ids: tuple[str, ...] = ("t1",),
    completed_normally: bool = True,
):
    runtime = _runtime()
    return runtime.DeepAgentsDecisionRecord(
        schema_version=1,
        baseline_revision="935864fca6510aeb4ce02e70a6cf69b2461e298e",
        deepagents_package="deepagents",
        deepagents_version="0.7.13",
        required_token_ids=("t1", "t2", "t3"),
        observed_token_ids=observed_token_ids,
        completed_normally=completed_normally,
        equivalence_criteria=(
            "complete-column-context",
            "every-token-in-order",
            "checkpoint-resume",
            "exact-completion-gates",
        ),
        observations=(
            "default model-directed loop can return a final answer before complete traversal",
            "isolated subagents are useful as a launcher but not a replacement for HARN-006 contracts",
        ),
        disposition=runtime.DeepAgentsDisposition(disposition),
        selected_components=("isolated-subagent-launcher-pattern", "skills-progressive-disclosure-pattern"),
        rationale="Keep deterministic HARN-004 parsing orchestration; borrow selected ergonomics only.",
        evidence_refs=("harn-007:spike:early-termination", "harn-004:column-state"),
    )


def test_decision_record_round_trips_and_exposes_early_termination() -> None:
    runtime = _runtime()
    record = _record()
    assert record.early_termination_observed is True
    restored = runtime.DeepAgentsDecisionRecord.from_dict(record.to_dict())
    assert restored == record
    assert restored.disposition is runtime.DeepAgentsDisposition.KEEP_LANGGRAPH


def test_primary_adoption_is_rejected_when_probe_skips_required_tokens() -> None:
    with pytest.raises(ValueError, match="primary|token|equivalent"):
        _record(disposition="adopt-primary")


def test_primary_adoption_requires_successful_complete_equivalence_probe() -> None:
    with pytest.raises(ValueError, match="primary|successful|complete|equivalent"):
        _record(
            disposition="adopt-primary",
            observed_token_ids=("t1", "t2", "t3"),
            completed_normally=False,
        )


def test_primary_adoption_accepts_only_successful_complete_probe() -> None:
    runtime = _runtime()
    record = _record(
        disposition="adopt-primary",
        observed_token_ids=("t1", "t2", "t3"),
        completed_normally=True,
    )
    assert record.disposition is runtime.DeepAgentsDisposition.ADOPT_PRIMARY
    assert record.early_termination_observed is False


def test_selected_component_decision_requires_named_components() -> None:
    runtime = _runtime()
    with pytest.raises(ValueError, match="selected_components"):
        runtime.DeepAgentsDecisionRecord(
            schema_version=1,
            baseline_revision="935864fca6510aeb4ce02e70a6cf69b2461e298e",
            deepagents_package="deepagents",
            deepagents_version="0.7.13",
            required_token_ids=("t1", "t2", "t3"),
            observed_token_ids=("t1", "t2", "t3"),
            completed_normally=True,
            equivalence_criteria=("every-token-in-order",),
            observations=("fixture",),
            disposition=runtime.DeepAgentsDisposition.ADOPT_SELECTED_COMPONENTS,
            selected_components=(),
            rationale="fixture",
            evidence_refs=("fixture:evidence",),
        )


def test_decision_record_rejects_observed_tokens_outside_fixed_workload() -> None:
    runtime = _runtime()
    with pytest.raises(ValueError, match="observed_token_ids"):
        runtime.DeepAgentsDecisionRecord(
            schema_version=1,
            baseline_revision="935864fca6510aeb4ce02e70a6cf69b2461e298e",
            deepagents_package="deepagents",
            deepagents_version="0.7.13",
            required_token_ids=("t1", "t2", "t3"),
            observed_token_ids=("t1", "outside"),
            completed_normally=True,
            equivalence_criteria=("every-token-in-order",),
            observations=("fixture",),
            disposition=runtime.DeepAgentsDisposition.KEEP_LANGGRAPH,
            selected_components=(),
            rationale="fixture",
            evidence_refs=("fixture:evidence",),
        )


def test_committed_harn_007_evidence_is_machine_valid_and_matches_decision() -> None:
    runtime = _runtime()
    path = REPO_ROOT / "docs/agent-harness/tickets/HARN-007-deep-agents-evidence.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    record = runtime.DeepAgentsDecisionRecord.from_dict(payload)

    assert record.disposition is runtime.DeepAgentsDisposition.KEEP_LANGGRAPH
    assert record.deepagents_version == "0.7.13"
    assert record.required_token_ids == ("t1", "t2", "t3")
    assert record.observed_token_ids == ("t1",)
    assert record.early_termination_observed is True
    assert "git:5920d3758442e7d775c7af37f3acb4f89b2606dd" in record.evidence_refs
    assert "synthetic-merge:7b376228f3653aa8639b97619c2795ed31105da6" in record.evidence_refs
