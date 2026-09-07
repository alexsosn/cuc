"""Pure state-transition semantics for the framework-neutral harness contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .contracts import (
    ChangeSet,
    EvalResult,
    GateOutcome,
    PlanArtifact,
    ResearchArtifact,
    ReviewDisposition,
    ReviewResult,
    RunPhase,
    RunState,
    TestIntent,
    TestResult,
)


class InvalidTransition(ValueError):
    """Raised when an event is inconsistent with the current durable state."""


@dataclass(frozen=True)
class ResearchRecorded:
    artifact: ResearchArtifact


@dataclass(frozen=True)
class PlanRecorded:
    artifact: PlanArtifact


@dataclass(frozen=True)
class TestsDeclared:
    intents: tuple[TestIntent, ...]

    def __post_init__(self) -> None:
        intents = tuple(self.intents)
        if not intents:
            raise ValueError("at least one test intent is required")
        ids = [intent.intent_id for intent in intents]
        if len(ids) != len(set(ids)):
            raise ValueError("test intent IDs must be unique")
        object.__setattr__(self, "intents", intents)


@dataclass(frozen=True)
class ChangeRecorded:
    change: ChangeSet


@dataclass(frozen=True)
class TestRecorded:
    result: TestResult


@dataclass(frozen=True)
class EvalRecorded:
    result: EvalResult


@dataclass(frozen=True)
class VerificationPassed:
    pass


@dataclass(frozen=True)
class ReviewRecorded:
    result: ReviewResult


@dataclass(frozen=True)
class ResumeRequested:
    pass


def _require_phase(state: RunState, expected: RunPhase, event_name: str) -> None:
    if state.phase is not expected:
        raise InvalidTransition(
            f"{event_name} requires phase {expected.value}; current phase is {state.phase.value}"
        )


def _pause(state: RunState, phase: RunPhase, reason: str) -> RunState:
    return replace(
        state,
        phase=phase,
        resume_phase=state.phase,
        pause_reason=reason,
    )


def _after_gate(state: RunState, outcome: GateOutcome, reason: str) -> RunState:
    if outcome is GateOutcome.SUCCESS:
        return state
    if outcome in {GateOutcome.TEST_FAILURE, GateOutcome.REGRESSION}:
        return replace(state, phase=RunPhase.IMPLEMENT, verified_head_sha=None, review=None)
    if outcome is GateOutcome.BLOCKED_EXECUTION:
        return _pause(state, RunPhase.BLOCKED, reason)
    if outcome is GateOutcome.HUMAN_ESCALATION:
        return _pause(state, RunPhase.AWAITING_HUMAN, reason)
    raise InvalidTransition(f"unsupported gate outcome: {outcome}")


def _latest_results_by_id(results, key):
    latest = {}
    for result in results:
        latest[key(result)] = result
    return latest


def apply_event(state: RunState, event: object) -> RunState:
    """Apply one validated event without performing external side effects."""

    if isinstance(event, ResearchRecorded):
        _require_phase(state, RunPhase.RESEARCH, "ResearchRecorded")
        return replace(state, research=event.artifact, phase=RunPhase.PLAN)

    if isinstance(event, PlanRecorded):
        _require_phase(state, RunPhase.PLAN, "PlanRecorded")
        if state.research is None:
            raise InvalidTransition("plan cannot be recorded before research evidence")
        return replace(state, plan=event.artifact, phase=RunPhase.TEST_DESIGN)

    if isinstance(event, TestsDeclared):
        _require_phase(state, RunPhase.TEST_DESIGN, "TestsDeclared")
        if state.plan is None:
            raise InvalidTransition("tests cannot be declared before a plan")
        return replace(state, test_intents=event.intents, phase=RunPhase.IMPLEMENT)

    if isinstance(event, ChangeRecorded):
        _require_phase(state, RunPhase.IMPLEMENT, "ChangeRecorded")
        prior_operations = {
            operation_id for change in state.changes for operation_id in change.operation_ids
        }
        reused = prior_operations.intersection(event.change.operation_ids)
        if reused:
            raise InvalidTransition(
                "operation IDs cannot be reused after retry/resume: " + ", ".join(sorted(reused))
            )
        if any(change.change_id == event.change.change_id for change in state.changes):
            raise InvalidTransition(f"change ID already recorded: {event.change.change_id}")
        return replace(
            state,
            changes=state.changes + (event.change,),
            phase=RunPhase.VERIFY,
            verified_head_sha=None,
            review=None,
        )

    if isinstance(event, TestRecorded):
        _require_phase(state, RunPhase.VERIFY, "TestRecorded")
        change = state.latest_change
        if change is None:
            raise InvalidTransition("test result requires a recorded change")
        if event.result.change_id != change.change_id:
            raise InvalidTransition(
                f"test result is for stale/unknown change {event.result.change_id}; "
                f"current change is {change.change_id}"
            )
        declared_ids = {intent.intent_id for intent in state.test_intents}
        if event.result.intent_id not in declared_ids:
            raise InvalidTransition(f"undeclared test intent: {event.result.intent_id}")
        updated = replace(state, test_results=state.test_results + (event.result,))
        return _after_gate(updated, event.result.outcome, event.result.summary)

    if isinstance(event, EvalRecorded):
        _require_phase(state, RunPhase.VERIFY, "EvalRecorded")
        change = state.latest_change
        if change is None:
            raise InvalidTransition("evaluation result requires a recorded change")
        if event.result.change_id != change.change_id:
            raise InvalidTransition(
                f"evaluation result is for stale/unknown change {event.result.change_id}; "
                f"current change is {change.change_id}"
            )
        updated = replace(state, eval_results=state.eval_results + (event.result,))
        return _after_gate(updated, event.result.outcome, event.result.summary)

    if isinstance(event, VerificationPassed):
        _require_phase(state, RunPhase.VERIFY, "VerificationPassed")
        change = state.latest_change
        if change is None:
            raise InvalidTransition("verification requires a recorded change")
        if not state.test_intents:
            raise InvalidTransition("verification requires declared tests")

        current_tests = tuple(
            result for result in state.test_results if result.change_id == change.change_id
        )
        latest_tests = _latest_results_by_id(current_tests, lambda result: result.intent_id)
        missing = [
            intent.intent_id for intent in state.test_intents if intent.intent_id not in latest_tests
        ]
        if missing:
            raise InvalidTransition("missing fresh test results: " + ", ".join(sorted(missing)))
        non_success = [
            intent_id
            for intent_id, result in latest_tests.items()
            if result.outcome is not GateOutcome.SUCCESS
        ]
        if non_success:
            raise InvalidTransition(
                "latest test results are not successful: " + ", ".join(sorted(non_success))
            )

        current_evals = tuple(
            result for result in state.eval_results if result.change_id == change.change_id
        )
        latest_evals = _latest_results_by_id(current_evals, lambda result: result.eval_id)
        failed_evals = [
            eval_id
            for eval_id, result in latest_evals.items()
            if result.outcome is not GateOutcome.SUCCESS
        ]
        if failed_evals:
            raise InvalidTransition(
                "latest evaluation results are not successful: " + ", ".join(sorted(failed_evals))
            )

        head_shas = {result.head_sha for result in latest_tests.values()}
        head_shas.update(result.head_sha for result in latest_evals.values())
        head_shas.discard(None)
        if len(head_shas) != 1:
            raise InvalidTransition("verification evidence must refer to one proposed head SHA")
        verified_head = next(iter(head_shas))
        return replace(
            state,
            phase=RunPhase.REVIEW,
            verified_head_sha=verified_head,
            review=None,
        )

    if isinstance(event, ReviewRecorded):
        _require_phase(state, RunPhase.REVIEW, "ReviewRecorded")
        if state.verified_head_sha is None:
            raise InvalidTransition("review requires a verified head SHA")
        if event.result.inspected_sha != state.verified_head_sha:
            raise InvalidTransition(
                f"review inspected stale revision {event.result.inspected_sha}; "
                f"verified revision is {state.verified_head_sha}"
            )
        if event.result.disposition is ReviewDisposition.APPROVE:
            return replace(state, review=event.result, phase=RunPhase.COMPLETE)
        if event.result.disposition is ReviewDisposition.REQUEST_CHANGES:
            return replace(
                state,
                review=event.result,
                phase=RunPhase.IMPLEMENT,
                verified_head_sha=None,
            )
        if event.result.disposition is ReviewDisposition.ESCALATE:
            updated = replace(state, review=event.result)
            return _pause(updated, RunPhase.AWAITING_HUMAN, event.result.summary)
        raise InvalidTransition(f"unsupported review disposition: {event.result.disposition}")

    if isinstance(event, ResumeRequested):
        if state.phase not in {RunPhase.BLOCKED, RunPhase.AWAITING_HUMAN}:
            raise InvalidTransition("ResumeRequested requires blocked or awaiting-human state")
        if state.resume_phase is None:
            raise InvalidTransition("exceptional state has no resume phase")
        return replace(
            state,
            phase=state.resume_phase,
            resume_phase=None,
            pause_reason=None,
        )

    raise InvalidTransition(f"unsupported event type: {type(event).__name__}")
