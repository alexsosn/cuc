from __future__ import annotations

import importlib

import pytest

from harness.contracts import (
    ChangeSet,
    GateOutcome,
    PlanArtifact,
    ResearchArtifact,
    ReviewDisposition,
    ReviewResult,
    RunPhase,
    RunState,
    TaskSpec,
    TestIntent,
    TestKind,
    TestResult,
)
from harness.state_machine import (
    ChangeRecorded,
    PlanRecorded,
    ResearchRecorded,
    ResumeRequested,
    ReviewRecorded,
    TestRecorded,
    TestsDeclared,
    VerificationPassed,
    apply_event,
)


HEAD = "a" * 40


def _runtime():
    try:
        return importlib.import_module("harness.development_controller")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-010 controller runtime is not implemented yet: {exc}")


def _task() -> TaskSpec:
    return TaskSpec(
        "HARN-010",
        "Bounded development controller",
        "Coordinate one bounded development run",
        ("bounded", "restartable", "approval-aware"),
    )


def _states() -> dict[RunPhase, RunState]:
    states: dict[RunPhase, RunState] = {}
    state = RunState("run-1", _task())
    states[state.phase] = state

    state = apply_event(
        state,
        ResearchRecorded(ResearchArtifact("research-1", "research complete", ("repo:contracts",))),
    )
    states[state.phase] = state

    state = apply_event(
        state,
        PlanRecorded(
            PlanArtifact("plan-1", "plan complete", ("write tests", "implement"), ("repo:state-machine",))
        ),
    )
    states[state.phase] = state

    state = apply_event(
        state,
        TestsDeclared(
            (
                TestIntent(
                    "unit",
                    TestKind.TARGETED,
                    ("python", "-m", "pytest", "tests/test_development_controller.py"),
                    "agent",
                    "controller tests",
                ),
            )
        ),
    )
    states[state.phase] = state

    state = apply_event(
        state,
        ChangeRecorded(
            ChangeSet(
                "change-1",
                "controller implementation",
                ("agent/harness/development_controller.py",),
                (),
            )
        ),
    )
    states[state.phase] = state

    state = apply_event(
        state,
        TestRecorded(
            TestResult(
                "unit",
                "change-1",
                GateOutcome.SUCCESS,
                HEAD,
                HEAD,
                0,
                12,
                0,
                "tests passed",
                ("ci:unit",),
            )
        ),
    )
    state = apply_event(state, VerificationPassed())
    states[state.phase] = state

    state = apply_event(
        state,
        ReviewRecorded(
            ReviewResult(
                "review-1",
                "independent-reviewer",
                "context-1",
                HEAD,
                ReviewDisposition.APPROVE,
                "approved",
            )
        ),
    )
    states[state.phase] = state
    return states


def _limits(max_steps=20, max_implementation_attempts=3, max_review_attempts=3):
    runtime = _runtime()
    return runtime.ControllerLimits(
        max_steps=max_steps,
        max_implementation_attempts=max_implementation_attempts,
        max_review_attempts=max_review_attempts,
    )


def _checkpoint(**limit_overrides):
    runtime = _runtime()
    return runtime.ControllerCheckpoint(
        run_id="run-1",
        limits=_limits(**limit_overrides),
    )


@pytest.mark.parametrize(
    ("phase", "kind_name"),
    (
        (RunPhase.RESEARCH, "RESEARCH"),
        (RunPhase.PLAN, "PLAN"),
        (RunPhase.TEST_DESIGN, "DESIGN_TESTS"),
        (RunPhase.IMPLEMENT, "IMPLEMENT"),
        (RunPhase.VERIFY, "VERIFY"),
        (RunPhase.REVIEW, "REVIEW"),
    ),
)
def test_each_normal_phase_schedules_exactly_one_typed_action(phase, kind_name):
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    checkpoint = _checkpoint()

    decision = controller.step(_states()[phase], checkpoint)

    assert decision.stop_reason is None
    assert decision.action.kind is getattr(runtime.ControllerActionKind, kind_name)
    assert decision.action.source_phase is phase
    assert decision.action.ordinal == 1
    assert decision.checkpoint.steps_used == 1
    assert checkpoint.steps_used == 0


def test_same_state_replays_pending_action_without_consuming_more_budget():
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    state = _states()[RunPhase.IMPLEMENT]

    first = controller.step(state, _checkpoint(max_implementation_attempts=1))
    replay = controller.step(state, first.checkpoint)

    assert replay.action == first.action
    assert replay.checkpoint == first.checkpoint
    assert replay.checkpoint.steps_used == 1
    assert replay.checkpoint.implementation_attempts == 1


def test_pending_action_survives_checkpoint_round_trip_and_replays():
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    state = _states()[RunPhase.RESEARCH]
    issued = controller.step(state, _checkpoint())

    restored = runtime.ControllerCheckpoint.from_dict(issued.checkpoint.to_dict())
    replay = controller.step(state, restored)

    assert restored == issued.checkpoint
    assert replay.action == issued.action
    assert replay.checkpoint == restored


def test_changed_state_acknowledges_pending_action_and_schedules_next_phase():
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    states = _states()
    first = controller.step(states[RunPhase.RESEARCH], _checkpoint())

    second = controller.step(states[RunPhase.PLAN], first.checkpoint)

    assert second.action.kind is runtime.ControllerActionKind.PLAN
    assert second.action.ordinal == 2
    assert second.checkpoint.steps_used == 2
    assert second.checkpoint.pending_action == second.action


def test_complete_and_exceptional_phases_stop_without_consuming_budget():
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    states = _states()
    checkpoint = _checkpoint()
    blocked = RunState(
        "run-1",
        _task(),
        phase=RunPhase.BLOCKED,
        resume_phase=RunPhase.RESEARCH,
        pause_reason="CI unavailable",
    )
    awaiting = RunState(
        "run-1",
        _task(),
        phase=RunPhase.AWAITING_HUMAN,
        resume_phase=RunPhase.RESEARCH,
        pause_reason="approval required",
    )

    cases = (
        (states[RunPhase.COMPLETE], runtime.ControllerStopReason.COMPLETE),
        (blocked, runtime.ControllerStopReason.BLOCKED),
        (awaiting, runtime.ControllerStopReason.AWAITING_HUMAN),
    )
    for state, expected in cases:
        decision = controller.step(state, checkpoint)
        assert decision.action is None
        assert decision.stop_reason is expected
        assert decision.checkpoint == checkpoint


def test_changed_state_to_pause_clears_prior_pending_action():
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    issued = controller.step(_states()[RunPhase.RESEARCH], _checkpoint())
    blocked = RunState(
        "run-1",
        _task(),
        phase=RunPhase.BLOCKED,
        resume_phase=RunPhase.RESEARCH,
        pause_reason="dependency blocked",
    )

    decision = controller.step(blocked, issued.checkpoint)

    assert decision.stop_reason is runtime.ControllerStopReason.BLOCKED
    assert decision.checkpoint.pending_action is None
    assert decision.checkpoint.steps_used == 1


def test_global_step_limit_is_exact_and_does_not_issue_n_plus_one():
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    research = _states()[RunPhase.RESEARCH]
    first = controller.step(research, _checkpoint(max_steps=1))
    plan = _states()[RunPhase.PLAN]

    stopped = controller.step(plan, first.checkpoint)

    assert stopped.action is None
    assert stopped.stop_reason is runtime.ControllerStopReason.STEP_LIMIT_REACHED
    assert stopped.checkpoint.steps_used == 1
    assert stopped.checkpoint.pending_action is None


def test_implementation_limit_counts_new_attempts_after_core_state_progress():
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    states = _states()
    checkpoint = _checkpoint(max_implementation_attempts=1)

    issued = controller.step(states[RunPhase.IMPLEMENT], checkpoint)
    verify = states[RunPhase.VERIFY]
    after_progress = controller.step(verify, issued.checkpoint)

    failed = apply_event(
        verify,
        TestRecorded(
            TestResult(
                "unit",
                "change-1",
                GateOutcome.TEST_FAILURE,
                HEAD,
                HEAD,
                1,
                0,
                1,
                "red",
            )
        ),
    )
    stopped = controller.step(failed, after_progress.checkpoint)

    assert stopped.action is None
    assert stopped.stop_reason is runtime.ControllerStopReason.IMPLEMENTATION_LIMIT_REACHED
    assert stopped.checkpoint.implementation_attempts == 1


def test_review_limit_is_enforced_after_request_changes_cycle():
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    review = _states()[RunPhase.REVIEW]
    checkpoint = _checkpoint(max_review_attempts=1)
    issued = controller.step(review, checkpoint)

    implement = apply_event(
        review,
        ReviewRecorded(
            ReviewResult(
                "review-changes",
                "independent-reviewer",
                "context-2",
                HEAD,
                ReviewDisposition.REQUEST_CHANGES,
                "fix blocker",
            )
        ),
    )
    implementation = controller.step(implement, issued.checkpoint)
    assert implementation.action.kind is runtime.ControllerActionKind.IMPLEMENT

    # A later valid review snapshot represents progress through implementation/verification.
    later_review = review
    stopped = controller.step(later_review, implementation.checkpoint)

    assert stopped.action is None
    assert stopped.stop_reason is runtime.ControllerStopReason.REVIEW_LIMIT_REACHED


def test_checkpoint_is_bound_to_run_id():
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    foreign = RunState("run-2", _task())

    with pytest.raises(ValueError, match="run_id"):
        controller.step(foreign, _checkpoint())


def test_checkpoint_deserialization_rejects_unknown_or_tampered_fields():
    runtime = _runtime()
    payload = _checkpoint().to_dict()

    with pytest.raises(ValueError):
        runtime.ControllerCheckpoint.from_dict({**payload, "unexpected": True})

    with pytest.raises(ValueError):
        runtime.ControllerCheckpoint.from_dict({**payload, "schema_version": 999})

    with pytest.raises(ValueError):
        runtime.ControllerCheckpoint.from_dict({**payload, "steps_used": True})

    with pytest.raises(ValueError):
        runtime.ControllerLimits.from_dict(
            {"max_steps": True, "max_implementation_attempts": 1, "max_review_attempts": 1}
        )


def test_explicit_resume_is_required_before_controller_schedules_again():
    runtime = _runtime()
    controller = runtime.BoundedDevelopmentController()
    blocked = RunState(
        "run-1",
        _task(),
        phase=RunPhase.BLOCKED,
        resume_phase=RunPhase.RESEARCH,
        pause_reason="CI unavailable",
    )
    checkpoint = _checkpoint()

    paused = controller.step(blocked, checkpoint)
    assert paused.stop_reason is runtime.ControllerStopReason.BLOCKED

    resumed = apply_event(blocked, ResumeRequested())
    decision = controller.step(resumed, paused.checkpoint)
    assert decision.action.kind is runtime.ControllerActionKind.RESEARCH


def test_invalid_pending_action_shape_is_rejected_on_restore():
    runtime = _runtime()
    state = _states()[RunPhase.RESEARCH]
    issued = runtime.BoundedDevelopmentController().step(state, _checkpoint())
    payload = issued.checkpoint.to_dict()
    payload["pending_action"] = {
        **payload["pending_action"],
        "kind": runtime.ControllerActionKind.REVIEW.value,
    }

    with pytest.raises(ValueError):
        runtime.ControllerCheckpoint.from_dict(payload)
