from __future__ import annotations

import importlib

import pytest


def _runtime():
    try:
        return importlib.import_module("harness.deep_agents_evaluation")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-007 comparison contract is not implemented yet: {exc}")


def _record(*, disposition: str = "keep-langgraph"):
    runtime = _runtime()
    return runtime.DeepAgentsDecisionRecord(
        schema_version=1,
        baseline_revision="935864fca6510aeb4ce02e70a6cf69b2461e298e",
        deepagents_package="deepagents",
        deepagents_version="0.7.13",
        required_token_ids=("t1", "t2", "t3"),
        observed_token_ids=("t1",),
        completed_normally=True,
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
