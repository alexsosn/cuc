from __future__ import annotations

import math

from harness.contracts import (
    ChangeSet,
    EvalResult,
    GateOutcome,
    PlanArtifact,
    ResearchArtifact,
    RunState,
    TaskSpec,
    TestIntent as HarnessTestIntent,
    TestKind as HarnessTestKind,
    TestResult as HarnessTestResult,
)
from harness.development_controller import (
    BoundedDevelopmentController,
    ControllerActionKind,
    ControllerCheckpoint,
    ControllerLimits,
)
from harness.state_machine import (
    ChangeRecorded,
    EvalRecorded,
    PlanRecorded,
    ResearchRecorded,
    TestRecorded as HarnessTestRecorded,
    TestsDeclared as HarnessTestsDeclared,
    apply_event,
)


HEAD = "d" * 40


def _verify_state(*, two_tests: bool = False) -> RunState:
    state = RunState(
        "run-adversarial",
        TaskSpec(
            "HARN-010-adversarial",
            "Controller restart adversarial case",
            "Exercise partial durable verification progress",
            ("restart without duplicate logical attempts",),
        ),
    )
    state = apply_event(
        state,
        ResearchRecorded(ResearchArtifact("research", "done")),
    )
    state = apply_event(
        state,
        PlanRecorded(PlanArtifact("plan", "done", ("test", "implement"))),
    )
    intents = [
        HarnessTestIntent(
            "unit-1",
            HarnessTestKind.TARGETED,
            ("python", "-m", "pytest", "tests/test_development_controller.py"),
            "agent",
            "first verification shard",
        )
    ]
    if two_tests:
        intents.append(
            HarnessTestIntent(
                "unit-2",
                HarnessTestKind.REGRESSION,
                ("python", "-m", "pytest", "-q"),
                "agent",
                "second verification shard",
            )
        )
    state = apply_event(state, HarnessTestsDeclared(tuple(intents)))
    return apply_event(
        state,
        ChangeRecorded(
            ChangeSet(
                "change-adversarial",
                "candidate",
                ("agent/harness/development_controller.py",),
            )
        ),
    )


def _checkpoint() -> ControllerCheckpoint:
    return ControllerCheckpoint(
        run_id="run-adversarial",
        limits=ControllerLimits(
            max_steps=10,
            max_implementation_attempts=3,
            max_review_attempts=3,
        ),
    )


def test_restart_after_partial_verify_progress_keeps_same_logical_attempt():
    controller = BoundedDevelopmentController()
    state = _verify_state(two_tests=True)
    issued = controller.step(state, _checkpoint())
    assert issued.action.kind is ControllerActionKind.VERIFY

    progressed = apply_event(
        state,
        HarnessTestRecorded(
            HarnessTestResult(
                "unit-1",
                "change-adversarial",
                GateOutcome.SUCCESS,
                HEAD,
                HEAD,
                0,
                1,
                0,
                "first shard passed",
            )
        ),
    )
    assert progressed.phase is state.phase

    resumed = controller.step(progressed, issued.checkpoint)

    assert resumed.action.kind is ControllerActionKind.VERIFY
    assert resumed.action.ordinal == issued.action.ordinal
    assert resumed.action.state_sha256 != issued.action.state_sha256
    assert resumed.checkpoint.steps_used == issued.checkpoint.steps_used
    assert resumed.checkpoint.implementation_attempts == issued.checkpoint.implementation_attempts
    assert resumed.checkpoint.review_attempts == issued.checkpoint.review_attempts
    assert resumed.checkpoint.pending_action == resumed.action


def test_controller_can_fingerprint_every_valid_runstate_including_nonfinite_metrics():
    controller = BoundedDevelopmentController()
    state = _verify_state()
    state = apply_event(
        state,
        EvalRecorded(
            EvalResult(
                "quality",
                "change-adversarial",
                GateOutcome.SUCCESS,
                HEAD,
                HEAD,
                "metric recorded",
                (("diagnostic", math.nan),),
            )
        ),
    )

    decision = controller.step(state, _checkpoint())

    assert decision.action.kind is ControllerActionKind.VERIFY
    assert len(decision.action.state_sha256) == 64
