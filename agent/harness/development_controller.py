"""Deterministic bounded scheduler for one development-controller run.

The durable development lifecycle belongs to :mod:`harness.state_machine`.  This
module only decides which category of work a trusted host may perform next and keeps
small, serializable scheduling budgets.  It performs no provider, filesystem, shell,
reviewer, or GitHub side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from .contracts import RunPhase, RunState


_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ControllerActionKind(str, Enum):
    RESEARCH = "research"
    PLAN = "plan"
    DESIGN_TESTS = "design-tests"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    REVIEW = "review"


class ControllerStopReason(str, Enum):
    COMPLETE = "complete"
    BLOCKED = "blocked"
    AWAITING_HUMAN = "awaiting-human"
    STEP_LIMIT_REACHED = "step-limit-reached"
    IMPLEMENTATION_LIMIT_REACHED = "implementation-limit-reached"
    REVIEW_LIMIT_REACHED = "review-limit-reached"


_PHASE_ACTION: dict[RunPhase, ControllerActionKind] = {
    RunPhase.RESEARCH: ControllerActionKind.RESEARCH,
    RunPhase.PLAN: ControllerActionKind.PLAN,
    RunPhase.TEST_DESIGN: ControllerActionKind.DESIGN_TESTS,
    RunPhase.IMPLEMENT: ControllerActionKind.IMPLEMENT,
    RunPhase.VERIFY: ControllerActionKind.VERIFY,
    RunPhase.REVIEW: ControllerActionKind.REVIEW,
}

_TERMINAL_PHASES: dict[RunPhase, ControllerStopReason] = {
    RunPhase.COMPLETE: ControllerStopReason.COMPLETE,
    RunPhase.BLOCKED: ControllerStopReason.BLOCKED,
    RunPhase.AWAITING_HUMAN: ControllerStopReason.AWAITING_HUMAN,
}


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _exact_keys(payload: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"extra={extra}")
        raise ValueError(f"invalid {field} fields: " + ", ".join(details))


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _enum(value: object, enum_type: type[Enum], field: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def _digest(value: object, field: str) -> str:
    text = _required_text(value, field).lower()
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return text


def _state_sha256(state: RunState) -> str:
    encoded = json.dumps(
        state.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ControllerLimits:
    max_steps: int
    max_implementation_attempts: int
    max_review_attempts: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_steps", _positive_int(self.max_steps, "max_steps"))
        object.__setattr__(
            self,
            "max_implementation_attempts",
            _positive_int(self.max_implementation_attempts, "max_implementation_attempts"),
        )
        object.__setattr__(
            self,
            "max_review_attempts",
            _positive_int(self.max_review_attempts, "max_review_attempts"),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_steps": self.max_steps,
            "max_implementation_attempts": self.max_implementation_attempts,
            "max_review_attempts": self.max_review_attempts,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ControllerLimits":
        payload = _mapping(payload, "ControllerLimits payload")
        _exact_keys(
            payload,
            {"max_steps", "max_implementation_attempts", "max_review_attempts"},
            "ControllerLimits",
        )
        return cls(
            payload["max_steps"],
            payload["max_implementation_attempts"],
            payload["max_review_attempts"],
        )


@dataclass(frozen=True)
class ControllerAction:
    kind: ControllerActionKind
    source_phase: RunPhase
    ordinal: int
    state_sha256: str

    def __post_init__(self) -> None:
        kind = _enum(self.kind, ControllerActionKind, "kind")
        phase = _enum(self.source_phase, RunPhase, "source_phase")
        ordinal = _positive_int(self.ordinal, "ordinal")
        state_digest = _digest(self.state_sha256, "state_sha256")
        expected = _PHASE_ACTION.get(phase)
        if expected is None:
            raise ValueError(f"source_phase {phase.value!r} cannot issue controller work")
        if kind is not expected:
            raise ValueError(
                f"action {kind.value!r} does not match source phase {phase.value!r}"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "source_phase", phase)
        object.__setattr__(self, "ordinal", ordinal)
        object.__setattr__(self, "state_sha256", state_digest)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source_phase": self.source_phase.value,
            "ordinal": self.ordinal,
            "state_sha256": self.state_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ControllerAction":
        payload = _mapping(payload, "ControllerAction payload")
        _exact_keys(
            payload,
            {"kind", "source_phase", "ordinal", "state_sha256"},
            "ControllerAction",
        )
        return cls(
            payload["kind"],
            payload["source_phase"],
            payload["ordinal"],
            payload["state_sha256"],
        )


@dataclass(frozen=True)
class ControllerCheckpoint:
    run_id: str
    limits: ControllerLimits
    steps_used: int = 0
    implementation_attempts: int = 0
    review_attempts: int = 0
    pending_action: ControllerAction | None = None
    schema_version: int = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {_SCHEMA_VERSION}")
        if not isinstance(self.limits, ControllerLimits):
            raise ValueError("limits must be ControllerLimits")
        run_id = _required_text(self.run_id, "run_id")
        steps = _nonnegative_int(self.steps_used, "steps_used")
        implementations = _nonnegative_int(
            self.implementation_attempts, "implementation_attempts"
        )
        reviews = _nonnegative_int(self.review_attempts, "review_attempts")
        if steps > self.limits.max_steps:
            raise ValueError("steps_used exceeds max_steps")
        if implementations > self.limits.max_implementation_attempts:
            raise ValueError("implementation_attempts exceeds configured limit")
        if reviews > self.limits.max_review_attempts:
            raise ValueError("review_attempts exceeds configured limit")
        if implementations + reviews > steps:
            raise ValueError("phase-specific attempts cannot exceed steps_used")
        pending = self.pending_action
        if pending is not None and not isinstance(pending, ControllerAction):
            raise ValueError("pending_action must be ControllerAction or None")
        if pending is not None:
            if steps == 0 or pending.ordinal != steps:
                raise ValueError("pending_action ordinal must equal non-zero steps_used")
            if (
                pending.kind is ControllerActionKind.IMPLEMENT
                and implementations == 0
            ):
                raise ValueError("pending IMPLEMENT requires an implementation attempt")
            if pending.kind is ControllerActionKind.REVIEW and reviews == 0:
                raise ValueError("pending REVIEW requires a review attempt")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "steps_used", steps)
        object.__setattr__(self, "implementation_attempts", implementations)
        object.__setattr__(self, "review_attempts", reviews)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "limits": self.limits.to_dict(),
            "steps_used": self.steps_used,
            "implementation_attempts": self.implementation_attempts,
            "review_attempts": self.review_attempts,
            "pending_action": (
                None if self.pending_action is None else self.pending_action.to_dict()
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ControllerCheckpoint":
        payload = _mapping(payload, "ControllerCheckpoint payload")
        _exact_keys(
            payload,
            {
                "schema_version",
                "run_id",
                "limits",
                "steps_used",
                "implementation_attempts",
                "review_attempts",
                "pending_action",
            },
            "ControllerCheckpoint",
        )
        pending_payload = payload["pending_action"]
        pending = (
            None
            if pending_payload is None
            else ControllerAction.from_dict(_mapping(pending_payload, "pending_action"))
        )
        return cls(
            run_id=payload["run_id"],
            limits=ControllerLimits.from_dict(_mapping(payload["limits"], "limits")),
            steps_used=payload["steps_used"],
            implementation_attempts=payload["implementation_attempts"],
            review_attempts=payload["review_attempts"],
            pending_action=pending,
            schema_version=payload["schema_version"],
        )


@dataclass(frozen=True)
class ControllerDecision:
    checkpoint: ControllerCheckpoint
    action: ControllerAction | None = None
    stop_reason: ControllerStopReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, ControllerCheckpoint):
            raise ValueError("checkpoint must be ControllerCheckpoint")
        if (self.action is None) == (self.stop_reason is None):
            raise ValueError("decision requires exactly one of action or stop_reason")
        if self.action is not None and not isinstance(self.action, ControllerAction):
            raise ValueError("action must be ControllerAction")
        if self.stop_reason is not None:
            object.__setattr__(
                self,
                "stop_reason",
                _enum(self.stop_reason, ControllerStopReason, "stop_reason"),
            )


class BoundedDevelopmentController:
    """Pure, one-step scheduler over the canonical durable run state."""

    def step(
        self,
        state: RunState,
        checkpoint: ControllerCheckpoint,
    ) -> ControllerDecision:
        if not isinstance(state, RunState):
            raise ValueError("state must be RunState")
        if not isinstance(checkpoint, ControllerCheckpoint):
            raise ValueError("checkpoint must be ControllerCheckpoint")
        if checkpoint.run_id != state.run_id:
            raise ValueError(
                f"checkpoint run_id {checkpoint.run_id!r} does not match state run_id {state.run_id!r}"
            )

        state_digest = _state_sha256(state)
        pending = checkpoint.pending_action
        if pending is not None:
            if pending.state_sha256 == state_digest:
                return ControllerDecision(checkpoint=checkpoint, action=pending)
            if pending.source_phase is state.phase:
                raise ValueError(
                    "RunState changed without leaving the pending action source phase; "
                    "finish the issued action before requesting another controller step"
                )
            checkpoint = replace(checkpoint, pending_action=None)

        terminal = _TERMINAL_PHASES.get(state.phase)
        if terminal is not None:
            return ControllerDecision(checkpoint=checkpoint, stop_reason=terminal)

        if checkpoint.steps_used >= checkpoint.limits.max_steps:
            return ControllerDecision(
                checkpoint=checkpoint,
                stop_reason=ControllerStopReason.STEP_LIMIT_REACHED,
            )

        kind = _PHASE_ACTION.get(state.phase)
        if kind is None:
            raise ValueError(f"unsupported RunPhase for controller scheduling: {state.phase}")

        if (
            kind is ControllerActionKind.IMPLEMENT
            and checkpoint.implementation_attempts
            >= checkpoint.limits.max_implementation_attempts
        ):
            return ControllerDecision(
                checkpoint=checkpoint,
                stop_reason=ControllerStopReason.IMPLEMENTATION_LIMIT_REACHED,
            )
        if (
            kind is ControllerActionKind.REVIEW
            and checkpoint.review_attempts >= checkpoint.limits.max_review_attempts
        ):
            return ControllerDecision(
                checkpoint=checkpoint,
                stop_reason=ControllerStopReason.REVIEW_LIMIT_REACHED,
            )

        ordinal = checkpoint.steps_used + 1
        action = ControllerAction(kind, state.phase, ordinal, state_digest)
        next_checkpoint = replace(
            checkpoint,
            steps_used=ordinal,
            implementation_attempts=(
                checkpoint.implementation_attempts
                + (1 if kind is ControllerActionKind.IMPLEMENT else 0)
            ),
            review_attempts=(
                checkpoint.review_attempts
                + (1 if kind is ControllerActionKind.REVIEW else 0)
            ),
            pending_action=action,
        )
        return ControllerDecision(checkpoint=next_checkpoint, action=action)
