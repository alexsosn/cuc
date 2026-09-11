"""Bounded, framework-neutral research-plan-TDD-review development controller.

HARN-002 remains the authoritative development phase state machine.  This module adds
controller-specific RED evidence, budgets, persistence, clean-review orchestration, and
HARN-009 GitHub side-effect dispatch without exposing raw provider transport.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Any, Callable, Mapping

from .contracts import (
    ChangeSet,
    EvalResult,
    GateOutcome,
    PlanArtifact,
    ResearchArtifact,
    RunPhase,
    RunState,
    TaskSpec,
    TestIntent,
    TestKind,
    TestResult,
)
from .development_reviewer import (
    IndependentReviewer,
    build_development_review_context,
    run_independent_development_review,
)
from .github_side_effects import (
    ApprovalRequired,
    GitHubOperationIntent,
    GuardedGitHubSideEffects,
    JournalStatus,
    SideEffectDenied,
)
from .state_machine import (
    ChangeRecorded,
    EvalRecorded,
    PlanRecorded,
    ResearchRecorded,
    ResumeRequested,
    ReviewRecorded,
    TestRecorded,
    TestsDeclared,
    VerificationPassed,
    apply_event,
)


_STATE_SCHEMA = 1
_FALLBACK_CATEGORIES = frozenset(
    {"performance", "stability", "ergonomics", "documentation", "edge-cases"}
)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _text_tuple(values: object, field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be an iterable of strings")
    try:
        items = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an iterable of strings") from exc
    normalized = tuple(_required_text(item, field) for item in items)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


def _text_sequence(values: object, field: str) -> tuple[str, ...]:
    """Normalize an ordered text log where repeated events are meaningful."""
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must be an iterable of strings")
    try:
        items = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an iterable of strings") from exc
    return tuple(_required_text(item, field) for item in items)


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _nonnegative_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a non-negative finite number")
    result = float(value)
    if result < 0 or not math.isfinite(result):
        raise ValueError(f"{field} must be a non-negative finite number")
    return result


class ControllerStopCode(str, Enum):
    COMPLETE = "complete"
    BLOCKED_EXECUTION = "blocked-execution"
    POLICY_BLOCK = "policy-block"
    NEEDS_HUMAN = "needs-human"
    REVISION_BUDGET_EXHAUSTED = "revision-budget-exhausted"
    VERIFICATION_BUDGET_EXHAUSTED = "verification-budget-exhausted"
    REVIEW_BUDGET_EXHAUSTED = "review-budget-exhausted"
    GITHUB_WRITE_BUDGET_EXHAUSTED = "github-write-budget-exhausted"
    COST_BUDGET_EXHAUSTED = "cost-budget-exhausted"
    STEP_BUDGET_EXHAUSTED = "step-budget-exhausted"


@dataclass(frozen=True)
class DevelopmentControllerPolicy:
    max_revision_attempts: int
    max_verification_executions: int
    max_review_attempts: int
    max_github_writes: int
    max_cost_units: float | None = None
    require_red: bool = True
    allow_no_feature_fallback: bool = False
    production_mode: bool = True

    def __post_init__(self) -> None:
        for field in (
            "max_revision_attempts",
            "max_verification_executions",
            "max_review_attempts",
            "max_github_writes",
        ):
            object.__setattr__(
                self, field, _nonnegative_int(getattr(self, field), field)
            )
        if self.max_cost_units is not None:
            object.__setattr__(
                self,
                "max_cost_units",
                _nonnegative_float(self.max_cost_units, "max_cost_units"),
            )
        for field in ("require_red", "allow_no_feature_fallback", "production_mode"):
            if not isinstance(getattr(self, field), bool):
                raise ValueError(f"{field} must be boolean")

    def fallback_allowed(self, category: str) -> bool:
        if not isinstance(category, str):
            return False
        return self.allow_no_feature_fallback and category.strip().casefold() in _FALLBACK_CATEGORIES


@dataclass(frozen=True)
class RedGateEvidence:
    intent_id: str
    baseline_sha: str
    outcome: GateOutcome
    exit_code: int
    passed_tests: int
    failed_tests: int
    summary: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _required_text(self.intent_id, "intent_id"))
        object.__setattr__(
            self, "baseline_sha", _required_text(self.baseline_sha, "baseline_sha")
        )
        try:
            outcome = self.outcome if isinstance(self.outcome, GateOutcome) else GateOutcome(self.outcome)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid RED outcome: {self.outcome!r}") from exc
        if outcome not in {
            GateOutcome.SUCCESS,
            GateOutcome.TEST_FAILURE,
            GateOutcome.REGRESSION,
        }:
            raise ValueError("RED evidence must represent a completed test execution")
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("exit_code must be an integer")
        passed = _nonnegative_int(self.passed_tests, "passed_tests")
        failed = _nonnegative_int(self.failed_tests, "failed_tests")
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "passed_tests", passed)
        object.__setattr__(self, "failed_tests", failed)
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "baseline_sha": self.baseline_sha,
            "outcome": self.outcome.value,
            "exit_code": self.exit_code,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RedGateEvidence":
        return cls(
            payload["intent_id"],
            payload["baseline_sha"],
            GateOutcome(payload["outcome"]),
            payload["exit_code"],
            payload.get("passed_tests", 0),
            payload.get("failed_tests", 0),
            payload["summary"],
            tuple(payload.get("evidence_refs", ())),
        )


@dataclass(frozen=True)
class ImplementationResult:
    change: ChangeSet
    head_sha: str
    github_operations: tuple[GitHubOperationIntent, ...] = ()
    cost_units: float = 0.0
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.change, ChangeSet):
            raise ValueError("change must be ChangeSet")
        object.__setattr__(self, "head_sha", _required_text(self.head_sha, "head_sha"))
        if isinstance(self.github_operations, (str, bytes, Mapping)):
            raise ValueError("github_operations must be an iterable of GitHubOperationIntent")
        operations = tuple(self.github_operations)
        if any(not isinstance(item, GitHubOperationIntent) for item in operations):
            raise ValueError("github_operations must contain only GitHubOperationIntent")
        operation_ids = tuple(item.operation_id for item in operations)
        if operation_ids != self.change.operation_ids:
            raise ValueError(
                "change.operation_ids must exactly match ordered GitHub operation intents"
            )
        object.__setattr__(self, "github_operations", operations)
        object.__setattr__(self, "cost_units", _nonnegative_float(self.cost_units, "cost_units"))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, object]:
        return {
            "change": self.change.to_dict(),
            "head_sha": self.head_sha,
            "github_operations": [item.to_dict() for item in self.github_operations],
            "cost_units": self.cost_units,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ImplementationResult":
        return cls(
            ChangeSet.from_dict(payload["change"]),
            payload["head_sha"],
            tuple(GitHubOperationIntent.from_dict(item) for item in payload.get("github_operations", ())),
            payload.get("cost_units", 0.0),
            tuple(payload.get("evidence_refs", ())),
        )


@dataclass(frozen=True)
class DevelopmentControllerPorts:
    research: Callable[[TaskSpec, tuple[str, ...]], ResearchArtifact]
    plan: Callable[[RunState], PlanArtifact]
    declare_tests: Callable[[RunState], tuple[TestIntent, ...]]
    run_red: Callable[[TestIntent, str], RedGateEvidence]
    implement: Callable[[RunState, int], ImplementationResult]
    run_test: Callable[[TestIntent, ChangeSet, str], TestResult]
    run_evals: Callable[[RunState, ChangeSet, str], tuple[EvalResult, ...]]
    final_diff: Callable[[str, str], str]

    def __post_init__(self) -> None:
        for field in (
            "research",
            "plan",
            "declare_tests",
            "run_red",
            "implement",
            "run_test",
            "run_evals",
            "final_diff",
        ):
            if not callable(getattr(self, field)):
                raise ValueError(f"{field} port must be callable")


@dataclass(frozen=True)
class DevelopmentControllerState:
    schema_version: int
    base_sha: str
    core: RunState
    provenance_refs: tuple[str, ...] = ()
    red_evidence: tuple[RedGateEvidence, ...] = ()
    pending_implementation: ImplementationResult | None = None
    pending_operation_index: int = 0
    resume_approval_id: str | None = None
    current_head_sha: str | None = None
    evaluated_change_ids: tuple[str, ...] = ()
    revision_attempts: int = 0
    verification_executions: int = 0
    review_attempts: int = 0
    github_writes: int = 0
    cost_units: float = 0.0
    last_implementation_cost_units: float = 0.0
    stop_code: ControllerStopCode | None = None
    stop_reason: str | None = None
    audit_events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != _STATE_SCHEMA:
            raise ValueError(f"schema_version must be {_STATE_SCHEMA}")
        if not isinstance(self.core, RunState):
            raise ValueError("core must be RunState")
        object.__setattr__(self, "base_sha", _required_text(self.base_sha, "base_sha"))
        object.__setattr__(self, "provenance_refs", _text_tuple(self.provenance_refs, "provenance_refs"))
        if isinstance(self.red_evidence, (str, bytes, Mapping)):
            raise ValueError("red_evidence must be an iterable")
        reds = tuple(self.red_evidence)
        if any(not isinstance(item, RedGateEvidence) for item in reds):
            raise ValueError("red_evidence must contain RedGateEvidence")
        red_ids = tuple(item.intent_id for item in reds)
        if len(red_ids) != len(set(red_ids)):
            raise ValueError("RED intent IDs must be unique")
        object.__setattr__(self, "red_evidence", reds)
        if self.pending_implementation is not None and not isinstance(
            self.pending_implementation, ImplementationResult
        ):
            raise ValueError("pending_implementation must be ImplementationResult")
        index = _nonnegative_int(self.pending_operation_index, "pending_operation_index")
        if self.pending_implementation is None and index:
            raise ValueError("pending_operation_index requires pending implementation")
        if self.pending_implementation is not None and index > len(
            self.pending_implementation.github_operations
        ):
            raise ValueError("pending_operation_index exceeds pending operation count")
        object.__setattr__(self, "pending_operation_index", index)
        object.__setattr__(
            self,
            "resume_approval_id",
            _optional_text(self.resume_approval_id, "resume_approval_id"),
        )
        object.__setattr__(
            self,
            "current_head_sha",
            _optional_text(self.current_head_sha, "current_head_sha"),
        )
        object.__setattr__(
            self,
            "evaluated_change_ids",
            _text_tuple(self.evaluated_change_ids, "evaluated_change_ids"),
        )
        for field in (
            "revision_attempts",
            "verification_executions",
            "review_attempts",
            "github_writes",
        ):
            object.__setattr__(self, field, _nonnegative_int(getattr(self, field), field))
        object.__setattr__(self, "cost_units", _nonnegative_float(self.cost_units, "cost_units"))
        object.__setattr__(
            self,
            "last_implementation_cost_units",
            _nonnegative_float(
                self.last_implementation_cost_units,
                "last_implementation_cost_units",
            ),
        )
        if self.stop_code is not None and not isinstance(self.stop_code, ControllerStopCode):
            object.__setattr__(self, "stop_code", ControllerStopCode(self.stop_code))
        object.__setattr__(self, "stop_reason", _optional_text(self.stop_reason, "stop_reason"))
        object.__setattr__(self, "audit_events", _text_sequence(self.audit_events, "audit_events"))

    @property
    def red_gate_complete(self) -> bool:
        targeted = tuple(
            intent for intent in self.core.test_intents if intent.kind is TestKind.TARGETED
        )
        if not targeted:
            return False
        evidence = {item.intent_id: item for item in self.red_evidence}
        return all(
            intent.intent_id in evidence
            and evidence[intent.intent_id].baseline_sha == self.base_sha
            and evidence[intent.intent_id].outcome is GateOutcome.TEST_FAILURE
            and evidence[intent.intent_id].exit_code != 0
            and evidence[intent.intent_id].failed_tests > 0
            for intent in targeted
        )

    @property
    def pending_operation_id(self) -> str | None:
        pending = self.pending_implementation
        if pending is None or self.pending_operation_index >= len(pending.github_operations):
            return None
        return pending.github_operations[self.pending_operation_index].operation_id

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "base_sha": self.base_sha,
            "core": self.core.to_dict(),
            "provenance_refs": list(self.provenance_refs),
            "red_evidence": [item.to_dict() for item in self.red_evidence],
            "pending_implementation": None
            if self.pending_implementation is None
            else self.pending_implementation.to_dict(),
            "pending_operation_index": self.pending_operation_index,
            "resume_approval_id": self.resume_approval_id,
            "current_head_sha": self.current_head_sha,
            "evaluated_change_ids": list(self.evaluated_change_ids),
            "revision_attempts": self.revision_attempts,
            "verification_executions": self.verification_executions,
            "review_attempts": self.review_attempts,
            "github_writes": self.github_writes,
            "cost_units": self.cost_units,
            "last_implementation_cost_units": self.last_implementation_cost_units,
            "stop_code": None if self.stop_code is None else self.stop_code.value,
            "stop_reason": self.stop_reason,
            "audit_events": list(self.audit_events),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DevelopmentControllerState":
        pending = payload.get("pending_implementation")
        return cls(
            payload["schema_version"],
            payload["base_sha"],
            RunState.from_dict(payload["core"]),
            tuple(payload.get("provenance_refs", ())),
            tuple(RedGateEvidence.from_dict(item) for item in payload.get("red_evidence", ())),
            None if pending is None else ImplementationResult.from_dict(pending),
            payload.get("pending_operation_index", 0),
            payload.get("resume_approval_id"),
            payload.get("current_head_sha"),
            tuple(payload.get("evaluated_change_ids", ())),
            payload.get("revision_attempts", 0),
            payload.get("verification_executions", 0),
            payload.get("review_attempts", 0),
            payload.get("github_writes", 0),
            payload.get("cost_units", 0.0),
            payload.get("last_implementation_cost_units", 0.0),
            None if payload.get("stop_code") is None else ControllerStopCode(payload["stop_code"]),
            payload.get("stop_reason"),
            tuple(payload.get("audit_events", ())),
        )


class BoundedDevelopmentController:
    """One-step-at-a-time bounded driver over HARN-002 durable transitions."""

    def __init__(
        self,
        *,
        policy: DevelopmentControllerPolicy,
        ports: DevelopmentControllerPorts,
        reviewer: IndependentReviewer,
        side_effects: GuardedGitHubSideEffects,
        persist: Callable[[dict[str, object]], None],
        approval_state_persist: Callable[[dict[str, object]], None] | None,
        policy_refs: tuple[str, ...],
        review_rubric: tuple[str, ...],
        implementer_id: str,
    ) -> None:
        if not isinstance(policy, DevelopmentControllerPolicy):
            raise ValueError("policy must be DevelopmentControllerPolicy")
        if not isinstance(ports, DevelopmentControllerPorts):
            raise ValueError("ports must be DevelopmentControllerPorts")
        if not isinstance(reviewer, IndependentReviewer):
            raise ValueError("reviewer must be IndependentReviewer")
        if not isinstance(side_effects, GuardedGitHubSideEffects):
            raise ValueError("side_effects must be GuardedGitHubSideEffects")
        if not callable(persist):
            raise ValueError("controller persist must be callable")
        if approval_state_persist is not None and not callable(approval_state_persist):
            raise ValueError("approval_state_persist must be callable or None")
        self.policy = policy
        self.ports = ports
        self.reviewer = reviewer
        self.side_effects = side_effects
        self._persist_callback = persist
        self._approval_state_persist = approval_state_persist
        self.policy_refs = _text_tuple(policy_refs, "policy_refs")
        self.review_rubric = _text_tuple(review_rubric, "review_rubric")
        if not self.policy_refs or not self.review_rubric:
            raise ValueError("policy_refs and review_rubric must not be empty")
        self.implementer_id = _required_text(implementer_id, "implementer_id")
        if reviewer.implementer_id is not None and reviewer.implementer_id != self.implementer_id:
            raise ValueError("reviewer implementer identity does not match controller")

        if policy.production_mode:
            journal_persist = getattr(side_effects.journal, "_persist", None)
            if not callable(journal_persist):
                raise ValueError(
                    "production controller requires durable GitHub operation journal persistence"
                )
            if not callable(approval_state_persist):
                raise ValueError(
                    "production controller requires durable approval-state persistence"
                )

    def _persist(self, state: DevelopmentControllerState) -> DevelopmentControllerState:
        self._persist_callback(state.to_dict())
        return state

    def _persist_approval_registry(self) -> None:
        if self._approval_state_persist is None:
            return
        registry = getattr(self.side_effects, "_approvals", None)
        if registry is None or not hasattr(registry, "to_dict"):
            raise RuntimeError("trusted approval registry is unavailable")
        self._approval_state_persist(registry.to_dict())

    @staticmethod
    def _audit(state: DevelopmentControllerState, event: str) -> tuple[str, ...]:
        return state.audit_events + (_required_text(event, "audit event"),)

    def start(
        self,
        *,
        run_id: str,
        task: TaskSpec,
        base_sha: str,
        provenance_refs: tuple[str, ...] = (),
    ) -> DevelopmentControllerState:
        if not isinstance(task, TaskSpec):
            raise ValueError("task must be TaskSpec")
        state = DevelopmentControllerState(
            _STATE_SCHEMA,
            _required_text(base_sha, "base_sha"),
            RunState(_required_text(run_id, "run_id"), task),
            _text_tuple(provenance_refs, "provenance_refs"),
            audit_events=("controller-started",),
        )
        if self.policy.production_mode:
            self._persist_approval_registry()
        return self._persist(state)

    def _pause(
        self,
        state: DevelopmentControllerState,
        phase: RunPhase,
        code: ControllerStopCode,
        reason: str,
    ) -> DevelopmentControllerState:
        reason = _required_text(reason, "stop reason")
        current = state.core.phase
        if current in {RunPhase.BLOCKED, RunPhase.AWAITING_HUMAN, RunPhase.COMPLETE}:
            core = state.core
        else:
            core = replace(
                state.core,
                phase=phase,
                resume_phase=current,
                pause_reason=reason,
            )
        return self._persist(
            replace(
                state,
                core=core,
                stop_code=code,
                stop_reason=reason,
                audit_events=self._audit(state, code.value),
            )
        )

    def _block(
        self,
        state: DevelopmentControllerState,
        code: ControllerStopCode,
        reason: str,
    ) -> DevelopmentControllerState:
        return self._pause(state, RunPhase.BLOCKED, code, reason)

    def _completed(self, state: DevelopmentControllerState) -> DevelopmentControllerState:
        return self._persist(
            replace(
                state,
                stop_code=ControllerStopCode.COMPLETE,
                stop_reason="development run completed after successful independent review",
                audit_events=self._audit(state, "complete"),
            )
        )

    def _next_missing_red(self, state: DevelopmentControllerState) -> TestIntent | None:
        existing = {item.intent_id for item in state.red_evidence}
        targeted = tuple(
            intent for intent in state.core.test_intents if intent.kind is TestKind.TARGETED
        )
        if self.policy.require_red and not targeted:
            raise ValueError("TDD RED gate requires at least one targeted test intent")
        return next((intent for intent in targeted if intent.intent_id not in existing), None)

    def _record_red(
        self,
        state: DevelopmentControllerState,
        intent: TestIntent,
        evidence: RedGateEvidence,
    ) -> DevelopmentControllerState:
        if not isinstance(evidence, RedGateEvidence):
            raise ValueError("RED port must return RedGateEvidence")
        if evidence.intent_id != intent.intent_id:
            raise ValueError("RED evidence intent does not match targeted test")
        if evidence.baseline_sha != state.base_sha:
            raise ValueError("RED evidence baseline revision/SHA does not match controller baseline")
        if (
            evidence.outcome is not GateOutcome.TEST_FAILURE
            or evidence.exit_code == 0
            or evidence.failed_tests == 0
        ):
            raise ValueError("targeted RED evidence must be a real failing test execution")
        return self._persist(
            replace(
                state,
                red_evidence=state.red_evidence + (evidence,),
                audit_events=self._audit(state, f"red:{intent.intent_id}"),
            )
        )

    def _cost_would_exceed(self, state: DevelopmentControllerState) -> bool:
        limit = self.policy.max_cost_units
        if limit is None:
            return False
        reserve = state.last_implementation_cost_units
        return state.cost_units + reserve > limit

    def _begin_implementation(self, state: DevelopmentControllerState) -> DevelopmentControllerState:
        if state.revision_attempts >= self.policy.max_revision_attempts:
            return self._block(
                state,
                ControllerStopCode.REVISION_BUDGET_EXHAUSTED,
                "maximum implementation/revision attempts exhausted",
            )
        if self._cost_would_exceed(state):
            return self._block(
                state,
                ControllerStopCode.COST_BUDGET_EXHAUSTED,
                "estimated next implementation would exceed cost budget",
            )
        attempt = state.revision_attempts + 1
        try:
            result = self.ports.implement(state.core, attempt)
        except RuntimeError as exc:
            return self._block(state, ControllerStopCode.BLOCKED_EXECUTION, str(exc))
        if not isinstance(result, ImplementationResult):
            raise ValueError("implementation port must return ImplementationResult")
        limit = self.policy.max_cost_units
        new_cost = state.cost_units + result.cost_units
        if limit is not None and new_cost > limit:
            return self._block(
                replace(
                    state,
                    revision_attempts=attempt,
                    cost_units=new_cost,
                    last_implementation_cost_units=result.cost_units,
                ),
                ControllerStopCode.COST_BUDGET_EXHAUSTED,
                "implementation result exceeded cost budget before side-effect dispatch",
            )
        return self._persist(
            replace(
                state,
                pending_implementation=result,
                pending_operation_index=0,
                current_head_sha=result.head_sha,
                revision_attempts=attempt,
                cost_units=new_cost,
                last_implementation_cost_units=result.cost_units,
                audit_events=self._audit(state, f"implementation:{attempt}"),
            )
        )

    def _dispatch_pending_effect(
        self,
        state: DevelopmentControllerState,
    ) -> DevelopmentControllerState:
        pending = state.pending_implementation
        assert pending is not None
        index = state.pending_operation_index
        if index >= len(pending.github_operations):
            core = apply_event(state.core, ChangeRecorded(pending.change))
            return self._persist(
                replace(
                    state,
                    core=core,
                    pending_implementation=None,
                    pending_operation_index=0,
                    resume_approval_id=None,
                    audit_events=self._audit(state, f"change:{pending.change.change_id}"),
                )
            )

        intent = pending.github_operations[index]
        journal_entry = self.side_effects.journal.entry(intent)
        may_write = journal_entry is None or journal_entry.status is not JournalStatus.COMPLETED
        if may_write and state.github_writes >= self.policy.max_github_writes:
            return self._block(
                state,
                ControllerStopCode.GITHUB_WRITE_BUDGET_EXHAUSTED,
                "GitHub write budget exhausted before provider dispatch",
            )

        try:
            if self.policy.production_mode:
                self._persist_approval_registry()
            result = self.side_effects.execute(
                intent,
                approval=state.resume_approval_id,
            )
        except ApprovalRequired as exc:
            return self._pause(
                state,
                RunPhase.AWAITING_HUMAN,
                ControllerStopCode.NEEDS_HUMAN,
                f"human approval required for operation {intent.operation_id}: {exc}",
            )
        except SideEffectDenied as exc:
            return self._block(
                state,
                ControllerStopCode.POLICY_BLOCK,
                f"GitHub operation {intent.operation_id} denied: {exc}",
            )
        except RuntimeError as exc:
            return self._block(
                state,
                ControllerStopCode.BLOCKED_EXECUTION,
                f"GitHub operation {intent.operation_id} blocked: {exc}",
            )

        writes = state.github_writes + (1 if result.status == "executed" else 0)
        return self._persist(
            replace(
                state,
                pending_operation_index=index + 1,
                resume_approval_id=None,
                github_writes=writes,
                audit_events=self._audit(
                    state,
                    f"github:{intent.operation_id}:{result.status}",
                ),
            )
        )

    def _verify(self, state: DevelopmentControllerState) -> DevelopmentControllerState:
        core = state.core
        if not core.changes or state.current_head_sha is None:
            raise ValueError("VERIFY requires current change and head SHA")
        change = core.changes[-1]
        current = {
            result.intent_id: result
            for result in core.test_results
            if result.change_id == change.change_id
        }
        missing = next(
            (intent for intent in core.test_intents if intent.intent_id not in current),
            None,
        )
        if missing is not None:
            if state.verification_executions >= self.policy.max_verification_executions:
                return self._block(
                    state,
                    ControllerStopCode.VERIFICATION_BUDGET_EXHAUSTED,
                    "verification execution budget exhausted before test port call",
                )
            try:
                result = self.ports.run_test(missing, change, state.current_head_sha)
            except RuntimeError as exc:
                return self._block(state, ControllerStopCode.BLOCKED_EXECUTION, str(exc))
            if not isinstance(result, TestResult):
                raise ValueError("test port must return TestResult")
            if result.intent_id != missing.intent_id or result.change_id != change.change_id:
                raise ValueError("test result is bound to the wrong intent/change")
            if result.head_sha != state.current_head_sha or result.executed_sha != state.current_head_sha:
                raise ValueError("test result head/executed revision does not match proposed head")
            next_core = apply_event(core, TestRecorded(result))
            return self._persist(
                replace(
                    state,
                    core=next_core,
                    verification_executions=state.verification_executions + 1,
                    audit_events=self._audit(
                        state, f"verify:{missing.intent_id}:{result.outcome.value}"
                    ),
                )
            )

        if change.change_id not in state.evaluated_change_ids:
            if state.verification_executions >= self.policy.max_verification_executions:
                return self._block(
                    state,
                    ControllerStopCode.VERIFICATION_BUDGET_EXHAUSTED,
                    "verification execution budget exhausted before eval port call",
                )
            try:
                evals = tuple(self.ports.run_evals(core, change, state.current_head_sha))
            except RuntimeError as exc:
                return self._block(state, ControllerStopCode.BLOCKED_EXECUTION, str(exc))
            next_core = core
            for result in evals:
                if not isinstance(result, EvalResult):
                    raise ValueError("eval port must return EvalResult values")
                if result.change_id != change.change_id:
                    raise ValueError("evaluation result is bound to the wrong change")
                if result.head_sha != state.current_head_sha or result.executed_sha != state.current_head_sha:
                    raise ValueError("evaluation head/executed revision does not match proposed head")
                next_core = apply_event(next_core, EvalRecorded(result))
                if next_core.phase is not RunPhase.VERIFY:
                    break
            return self._persist(
                replace(
                    state,
                    core=next_core,
                    evaluated_change_ids=state.evaluated_change_ids + (change.change_id,),
                    verification_executions=state.verification_executions + 1,
                    audit_events=self._audit(state, f"evals:{change.change_id}"),
                )
            )

        next_core = apply_event(core, VerificationPassed())
        return self._persist(
            replace(
                state,
                core=next_core,
                audit_events=self._audit(state, f"verified:{change.change_id}"),
            )
        )

    def _review(self, state: DevelopmentControllerState) -> DevelopmentControllerState:
        if state.review_attempts >= self.policy.max_review_attempts:
            return self._block(
                state,
                ControllerStopCode.REVIEW_BUDGET_EXHAUSTED,
                "independent review budget exhausted before reviewer call",
            )
        if state.current_head_sha is None:
            raise ValueError("REVIEW requires current head SHA")
        try:
            final_diff = self.ports.final_diff(state.base_sha, state.current_head_sha)
            context = build_development_review_context(
                state.core,
                base_sha=state.base_sha,
                head_sha=state.current_head_sha,
                final_diff=final_diff,
                policy_refs=self.policy_refs,
                rubric=self.review_rubric,
            )
            report = run_independent_development_review(context, self.reviewer)
        except RuntimeError as exc:
            return self._block(state, ControllerStopCode.BLOCKED_EXECUTION, str(exc))
        next_core = apply_event(state.core, ReviewRecorded(report.review))
        updated = replace(
            state,
            core=next_core,
            review_attempts=state.review_attempts + 1,
            audit_events=self._audit(
                state, f"review:{report.disposition.value}:{state.current_head_sha}"
            ),
        )
        if next_core.phase is RunPhase.COMPLETE:
            return self._completed(updated)
        if next_core.phase is RunPhase.AWAITING_HUMAN:
            return self._persist(
                replace(
                    updated,
                    stop_code=ControllerStopCode.NEEDS_HUMAN,
                    stop_reason=next_core.pause_reason,
                )
            )
        return self._persist(updated)

    def step(self, state: DevelopmentControllerState) -> DevelopmentControllerState:
        if not isinstance(state, DevelopmentControllerState):
            raise ValueError("state must be DevelopmentControllerState")
        if state.stop_code is not None:
            return state

        phase = state.core.phase
        if phase is RunPhase.COMPLETE:
            return self._completed(state)
        if phase in {RunPhase.BLOCKED, RunPhase.AWAITING_HUMAN}:
            return state

        if phase is RunPhase.RESEARCH:
            try:
                artifact = self.ports.research(state.core.task, state.provenance_refs)
            except RuntimeError as exc:
                return self._block(state, ControllerStopCode.BLOCKED_EXECUTION, str(exc))
            core = apply_event(state.core, ResearchRecorded(artifact))
            return self._persist(
                replace(state, core=core, audit_events=self._audit(state, "research"))
            )

        if phase is RunPhase.PLAN:
            try:
                artifact = self.ports.plan(state.core)
            except RuntimeError as exc:
                return self._block(state, ControllerStopCode.BLOCKED_EXECUTION, str(exc))
            core = apply_event(state.core, PlanRecorded(artifact))
            return self._persist(
                replace(state, core=core, audit_events=self._audit(state, "plan"))
            )

        if phase is RunPhase.TEST_DESIGN:
            try:
                intents = tuple(self.ports.declare_tests(state.core))
            except RuntimeError as exc:
                return self._block(state, ControllerStopCode.BLOCKED_EXECUTION, str(exc))
            core = apply_event(state.core, TestsDeclared(intents))
            return self._persist(
                replace(state, core=core, audit_events=self._audit(state, "tests-declared"))
            )

        if phase is RunPhase.IMPLEMENT:
            if self.policy.require_red and not state.red_gate_complete:
                missing = self._next_missing_red(state)
                if missing is None:
                    raise ValueError("targeted RED gate is incomplete")
                evidence = self.ports.run_red(missing, state.base_sha)
                return self._record_red(state, missing, evidence)
            if state.pending_implementation is None:
                return self._begin_implementation(state)
            return self._dispatch_pending_effect(state)

        if phase is RunPhase.VERIFY:
            return self._verify(state)

        if phase is RunPhase.REVIEW:
            return self._review(state)

        raise ValueError(f"unsupported controller phase: {phase.value}")

    def resume(
        self,
        state: DevelopmentControllerState,
        *,
        approval_id: str | None = None,
    ) -> DevelopmentControllerState:
        if not isinstance(state, DevelopmentControllerState):
            raise ValueError("state must be DevelopmentControllerState")
        if state.core.phase not in {RunPhase.BLOCKED, RunPhase.AWAITING_HUMAN}:
            raise ValueError("resume requires BLOCKED or AWAITING_HUMAN state")
        if state.core.phase is RunPhase.BLOCKED:
            raise ValueError("budget/policy/blocked-execution state is not autonomously resumable")
        if self.policy.production_mode:
            self._persist_approval_registry()
        core = apply_event(state.core, ResumeRequested())
        return self._persist(
            replace(
                state,
                core=core,
                resume_approval_id=_optional_text(approval_id, "approval_id"),
                stop_code=None,
                stop_reason=None,
                audit_events=self._audit(state, "human-resume"),
            )
        )

    def run_until_stop(
        self,
        state: DevelopmentControllerState,
        *,
        max_steps: int | None = None,
    ) -> DevelopmentControllerState:
        if not isinstance(state, DevelopmentControllerState):
            raise ValueError("state must be DevelopmentControllerState")
        if max_steps is None:
            # Finite by construction.  The ceiling intentionally over-approximates one
            # full bounded run including RED, side effects, verification and reviews.
            max_steps = (
                12
                + max(1, self.policy.max_revision_attempts)
                * (
                    4
                    + self.policy.max_github_writes
                    + self.policy.max_verification_executions
                    + self.policy.max_review_attempts
                )
            )
        max_steps = _nonnegative_int(max_steps, "max_steps")
        current = state
        for _ in range(max_steps):
            if current.stop_code is not None or current.core.phase in {
                RunPhase.COMPLETE,
                RunPhase.BLOCKED,
                RunPhase.AWAITING_HUMAN,
            }:
                if current.core.phase is RunPhase.COMPLETE and current.stop_code is None:
                    return self._completed(current)
                return current
            current = self.step(current)
        if current.stop_code is not None:
            return current
        return self._block(
            current,
            ControllerStopCode.STEP_BUDGET_EXHAUSTED,
            "finite controller step ceiling exhausted",
        )