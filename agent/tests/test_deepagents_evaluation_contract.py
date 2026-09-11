from __future__ import annotations

import importlib

import pytest


def _runtime():
    try:
        return importlib.import_module("harness.deepagents_evaluation")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-007 experiment contract is not implemented yet: {exc}")


def _observation(runtime, scenario_id: str, *, bounded_termination: bool = False):
    return runtime.ExperimentObservation(
        scenario_id=scenario_id,
        candidate=runtime.CandidateArchitecture.HYBRID,
        framework="deepagents",
        framework_version="0.7.13",
        cuc_contracts=("HARN-004", "HARN-006"),
        execution_mode=runtime.ExecutionMode.DETERMINISTIC,
        visible_context_fields=("development_run_id", "head_sha"),
        exposed_tools=(),
        exposed_permissions=(),
        checkpoint_observations=("CUC durable state remains authoritative",),
        bounded_termination=bounded_termination,
        framework_steps=None,
        tool_calls=None,
        qualitative_notes=("No live model required for this observation",),
        verdict=runtime.RoleVerdict.KEEP_TYPED_PORT,
    )


def test_experiment_observation_roundtrips_without_inventing_unmeasured_metrics():
    runtime = _runtime()
    observation = _observation(runtime, "reviewer-isolation")
    restored = runtime.ExperimentObservation.from_dict(observation.to_dict())
    assert restored == observation
    assert restored.framework_steps is None
    assert restored.tool_calls is None
    assert restored.execution_mode is runtime.ExecutionMode.DETERMINISTIC


def test_experiment_observation_requires_explicit_framework_version_and_contracts():
    runtime = _runtime()
    with pytest.raises(ValueError, match="framework_version"):
        runtime.ExperimentObservation(
            scenario_id="reviewer-isolation",
            candidate=runtime.CandidateArchitecture.HYBRID,
            framework="deepagents",
            framework_version="",
            cuc_contracts=("HARN-006",),
            execution_mode=runtime.ExecutionMode.DETERMINISTIC,
            visible_context_fields=("head_sha",),
            exposed_tools=(),
            exposed_permissions=(),
            checkpoint_observations=("restartable",),
            bounded_termination=False,
            framework_steps=None,
            tool_calls=None,
            qualitative_notes=("fixture",),
            verdict=runtime.RoleVerdict.KEEP_TYPED_PORT,
        )

    with pytest.raises(ValueError, match="cuc_contracts"):
        runtime.ExperimentObservation(
            scenario_id="reviewer-isolation",
            candidate=runtime.CandidateArchitecture.HYBRID,
            framework="deepagents",
            framework_version="0.7.13",
            cuc_contracts=(),
            execution_mode=runtime.ExecutionMode.DETERMINISTIC,
            visible_context_fields=("head_sha",),
            exposed_tools=(),
            exposed_permissions=(),
            checkpoint_observations=("restartable",),
            bounded_termination=False,
            framework_steps=None,
            tool_calls=None,
            qualitative_notes=("fixture",),
            verdict=runtime.RoleVerdict.KEEP_TYPED_PORT,
        )


def test_measured_counters_reject_boolean_or_negative_values():
    runtime = _runtime()
    base = _observation(runtime, "reviewer-isolation").to_dict()
    for field, value in (("framework_steps", True), ("framework_steps", -1), ("tool_calls", -1)):
        payload = dict(base)
        payload[field] = value
        with pytest.raises(ValueError, match=field):
            runtime.ExperimentObservation.from_dict(payload)


def test_adr_requires_all_representative_scenarios_and_budget_termination():
    runtime = _runtime()
    observations = (
        _observation(runtime, "parsing-wrapper"),
        _observation(runtime, "reviewer-isolation"),
        _observation(runtime, "hitl-presentation"),
        _observation(runtime, "budget-termination", bounded_termination=True),
    )
    adr = runtime.DeepAgentsDecision(
        decision=runtime.CandidateArchitecture.HYBRID,
        parsing_runtime="explicit-harn-004-langgraph",
        development_worker_direction="optional-deepagents-behind-typed-ports",
        harn009_recommendation="HITL presentation only; authorization remains external",
        harn010_recommendation="deterministic controller owns budgets and termination",
        production_dependency=False,
        observations=observations,
        rationale=("Semantic fidelity and authorization safety are veto criteria",),
    )
    restored = runtime.DeepAgentsDecision.from_dict(adr.to_dict())
    assert restored == adr

    with pytest.raises(ValueError, match="hitl-presentation"):
        runtime.DeepAgentsDecision(
            decision=runtime.CandidateArchitecture.HYBRID,
            parsing_runtime="explicit-harn-004-langgraph",
            development_worker_direction="optional",
            harn009_recommendation="external authorization",
            harn010_recommendation="deterministic budgets",
            production_dependency=False,
            observations=tuple(item for item in observations if item.scenario_id != "hitl-presentation"),
            rationale=("fixture",),
        )

    with pytest.raises(ValueError, match="bounded termination"):
        runtime.DeepAgentsDecision(
            decision=runtime.CandidateArchitecture.HYBRID,
            parsing_runtime="explicit-harn-004-langgraph",
            development_worker_direction="optional",
            harn009_recommendation="external authorization",
            harn010_recommendation="deterministic budgets",
            production_dependency=False,
            observations=tuple(
                _observation(runtime, item.scenario_id, bounded_termination=False)
                for item in observations
            ),
            rationale=("fixture",),
        )


def test_parsing_runtime_cannot_silently_move_out_of_harn004():
    runtime = _runtime()
    observations = (
        _observation(runtime, "parsing-wrapper"),
        _observation(runtime, "reviewer-isolation"),
        _observation(runtime, "hitl-presentation"),
        _observation(runtime, "budget-termination", bounded_termination=True),
    )
    with pytest.raises(ValueError, match="HARN-004|parsing"):
        runtime.DeepAgentsDecision(
            decision=runtime.CandidateArchitecture.DEEP_AGENTS,
            parsing_runtime="deepagents-token-loop",
            development_worker_direction="deepagents-primary",
            harn009_recommendation="external authorization",
            harn010_recommendation="deepagents-primary",
            production_dependency=True,
            observations=observations,
            rationale=("convenience",),
        )
