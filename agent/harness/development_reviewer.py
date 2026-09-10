"""Clean, restartable context boundary for independent development review.

Durable review findings and dispositions are the existing HARN-002 contracts.  This
module only constructs an allowlisted review packet, validates an independent reviewer
result, and delegates disposition routing back to the authoritative state machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Callable, Mapping

from .contracts import (
    GateOutcome,
    ReviewResult,
    RunPhase,
    RunState,
)
from .state_machine import ReviewRecorded, apply_event


_SCHEMA_VERSION = 1
_JSON_SCALAR = str | int | float | bool | None


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _exact_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _text_tuple(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an iterable of strings")
    try:
        items = tuple(_required_text(item, field) for item in value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an iterable of strings") from exc
    if required and not items:
        raise ValueError(f"{field} must not be empty")
    if len(items) != len(set(items)):
        raise ValueError(f"{field} must not contain duplicates")
    return items


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _exact_keys(payload: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(payload)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {sorted(missing)}")
        if extra:
            details.append(f"unknown fields: {sorted(extra)}")
        raise ValueError(f"invalid {field}: " + "; ".join(details))


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReviewTaskProjection:
    task_id: str
    title: str
    objective: str
    acceptance_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _required_text(self.task_id, "task_id"))
        object.__setattr__(self, "title", _required_text(self.title, "title"))
        object.__setattr__(self, "objective", _required_text(self.objective, "objective"))
        object.__setattr__(
            self,
            "acceptance_criteria",
            _text_tuple(self.acceptance_criteria, "acceptance_criteria", required=True),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "objective": self.objective,
            "acceptance_criteria": list(self.acceptance_criteria),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReviewTaskProjection":
        payload = _mapping(payload, "task projection")
        _exact_keys(
            payload,
            {"task_id", "title", "objective", "acceptance_criteria"},
            "task projection",
        )
        return cls(
            payload["task_id"],
            payload["title"],
            payload["objective"],
            tuple(payload["acceptance_criteria"]),
        )


@dataclass(frozen=True)
class ReviewChangeProjection:
    change_id: str
    changed_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "change_id", _required_text(self.change_id, "change_id"))
        object.__setattr__(
            self,
            "changed_paths",
            _text_tuple(self.changed_paths, "changed_paths", required=True),
        )

    def to_dict(self) -> dict[str, object]:
        return {"change_id": self.change_id, "changed_paths": list(self.changed_paths)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReviewChangeProjection":
        payload = _mapping(payload, "change projection")
        _exact_keys(payload, {"change_id", "changed_paths"}, "change projection")
        return cls(payload["change_id"], tuple(payload["changed_paths"]))


@dataclass(frozen=True)
class ReviewTestEvidence:
    intent_id: str
    kind: str
    command: tuple[str, ...]
    working_directory: str
    head_sha: str
    executed_sha: str
    outcome: str
    exit_code: int
    passed_tests: int
    failed_tests: int
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _required_text(self.intent_id, "intent_id"))
        object.__setattr__(self, "kind", _required_text(self.kind, "kind"))
        object.__setattr__(self, "command", _text_tuple(self.command, "command", required=True))
        object.__setattr__(
            self,
            "working_directory",
            _required_text(self.working_directory, "working_directory"),
        )
        object.__setattr__(self, "head_sha", _required_text(self.head_sha, "head_sha"))
        object.__setattr__(
            self, "executed_sha", _required_text(self.executed_sha, "executed_sha")
        )
        object.__setattr__(self, "outcome", _required_text(self.outcome, "outcome"))
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("exit_code must be an integer")
        for field in ("passed_tests", "failed_tests"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        object.__setattr__(
            self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "kind": self.kind,
            "command": list(self.command),
            "working_directory": self.working_directory,
            "head_sha": self.head_sha,
            "executed_sha": self.executed_sha,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReviewTestEvidence":
        payload = _mapping(payload, "test evidence")
        _exact_keys(
            payload,
            {
                "intent_id", "kind", "command", "working_directory", "head_sha",
                "executed_sha", "outcome", "exit_code", "passed_tests",
                "failed_tests", "evidence_refs",
            },
            "test evidence",
        )
        return cls(
            payload["intent_id"],
            payload["kind"],
            tuple(payload["command"]),
            payload["working_directory"],
            payload["head_sha"],
            payload["executed_sha"],
            payload["outcome"],
            payload["exit_code"],
            payload["passed_tests"],
            payload["failed_tests"],
            tuple(payload["evidence_refs"]),
        )


@dataclass(frozen=True)
class ReviewEvalEvidence:
    eval_id: str
    head_sha: str
    executed_sha: str
    outcome: str
    metrics: tuple[tuple[str, _JSON_SCALAR], ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "eval_id", _required_text(self.eval_id, "eval_id"))
        object.__setattr__(self, "head_sha", _required_text(self.head_sha, "head_sha"))
        object.__setattr__(
            self, "executed_sha", _required_text(self.executed_sha, "executed_sha")
        )
        object.__setattr__(self, "outcome", _required_text(self.outcome, "outcome"))
        try:
            raw_metrics = tuple(self.metrics)
        except TypeError as exc:
            raise ValueError("metrics must be key/value pairs") from exc
        normalized: list[tuple[str, _JSON_SCALAR]] = []
        names: list[str] = []
        for item in raw_metrics:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ValueError("metrics must be key/value pairs")
            name, value = item
            name = _required_text(name, "metric name")
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise ValueError(f"metric {name!r} must be JSON-scalar")
            names.append(name)
            normalized.append((name, value))
        if len(names) != len(set(names)):
            raise ValueError("metric names must be unique")
        normalized.sort(key=lambda item: item[0])
        object.__setattr__(self, "metrics", tuple(normalized))
        object.__setattr__(
            self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "eval_id": self.eval_id,
            "head_sha": self.head_sha,
            "executed_sha": self.executed_sha,
            "outcome": self.outcome,
            "metrics": {name: value for name, value in self.metrics},
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReviewEvalEvidence":
        payload = _mapping(payload, "evaluation evidence")
        _exact_keys(
            payload,
            {"eval_id", "head_sha", "executed_sha", "outcome", "metrics", "evidence_refs"},
            "evaluation evidence",
        )
        metrics = _mapping(payload["metrics"], "metrics")
        return cls(
            payload["eval_id"],
            payload["head_sha"],
            payload["executed_sha"],
            payload["outcome"],
            tuple(metrics.items()),
            tuple(payload["evidence_refs"]),
        )


@dataclass(frozen=True)
class DevelopmentReviewContext:
    schema_version: int
    review_context_id: str
    development_run_id: str
    task: ReviewTaskProjection
    change: ReviewChangeProjection
    base_sha: str
    head_sha: str
    executed_sha: str
    final_diff: str
    diff_sha256: str
    test_evidence: tuple[ReviewTestEvidence, ...]
    eval_evidence: tuple[ReviewEvalEvidence, ...]
    policy_refs: tuple[str, ...]
    rubric: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {_SCHEMA_VERSION}")
        if not isinstance(self.task, ReviewTaskProjection):
            raise ValueError("task must be ReviewTaskProjection")
        if not isinstance(self.change, ReviewChangeProjection):
            raise ValueError("change must be ReviewChangeProjection")
        if any(not isinstance(item, ReviewTestEvidence) for item in self.test_evidence):
            raise ValueError("test_evidence must contain ReviewTestEvidence")
        if any(not isinstance(item, ReviewEvalEvidence) for item in self.eval_evidence):
            raise ValueError("eval_evidence must contain ReviewEvalEvidence")
        tests = tuple(self.test_evidence)
        evals = tuple(self.eval_evidence)
        if not tests:
            raise ValueError("test_evidence must not be empty")
        object.__setattr__(
            self, "development_run_id", _required_text(self.development_run_id, "development_run_id")
        )
        object.__setattr__(self, "base_sha", _required_text(self.base_sha, "base_sha"))
        object.__setattr__(self, "head_sha", _required_text(self.head_sha, "head_sha"))
        object.__setattr__(
            self, "executed_sha", _required_text(self.executed_sha, "executed_sha")
        )
        final_diff = _exact_text(self.final_diff, "final_diff")
        object.__setattr__(self, "final_diff", final_diff)
        expected_diff_digest = _sha256_text(final_diff)
        if self.diff_sha256 != expected_diff_digest:
            raise ValueError("diff digest does not match final_diff")
        object.__setattr__(self, "diff_sha256", expected_diff_digest)
        object.__setattr__(self, "test_evidence", tests)
        object.__setattr__(self, "eval_evidence", evals)
        object.__setattr__(
            self, "policy_refs", _text_tuple(self.policy_refs, "policy_refs", required=True)
        )
        object.__setattr__(self, "rubric", _text_tuple(self.rubric, "rubric", required=True))
        expected_context_id = self._derived_context_id()
        if self.review_context_id != expected_context_id:
            raise ValueError("review context identity does not match canonical packet")
        object.__setattr__(self, "review_context_id", expected_context_id)

    def _identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "development_run_id": self.development_run_id,
            "task": self.task.to_dict(),
            "change": self.change.to_dict(),
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "executed_sha": self.executed_sha,
            "final_diff": self.final_diff,
            "diff_sha256": self.diff_sha256,
            "test_evidence": [item.to_dict() for item in self.test_evidence],
            "eval_evidence": [item.to_dict() for item in self.eval_evidence],
            "policy_refs": list(self.policy_refs),
            "rubric": list(self.rubric),
        }

    def _derived_context_id(self) -> str:
        return "review-context-" + _sha256_text(_canonical_json(self._identity_payload()))

    def to_dict(self) -> dict[str, object]:
        return {"review_context_id": self.review_context_id, **self._identity_payload()}

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DevelopmentReviewContext":
        payload = _mapping(payload, "development review context")
        _exact_keys(
            payload,
            {
                "schema_version", "review_context_id", "development_run_id", "task", "change",
                "base_sha", "head_sha", "executed_sha", "final_diff", "diff_sha256",
                "test_evidence", "eval_evidence", "policy_refs", "rubric",
            },
            "development review context",
        )
        return cls(
            payload["schema_version"],
            payload["review_context_id"],
            payload["development_run_id"],
            ReviewTaskProjection.from_dict(payload["task"]),
            ReviewChangeProjection.from_dict(payload["change"]),
            payload["base_sha"],
            payload["head_sha"],
            payload["executed_sha"],
            payload["final_diff"],
            payload["diff_sha256"],
            tuple(ReviewTestEvidence.from_dict(item) for item in payload["test_evidence"]),
            tuple(ReviewEvalEvidence.from_dict(item) for item in payload["eval_evidence"]),
            tuple(payload["policy_refs"]),
            tuple(payload["rubric"]),
        )

    @classmethod
    def from_json(cls, payload: str) -> "DevelopmentReviewContext":
        if not isinstance(payload, str):
            raise ValueError("serialized review context must be a string")
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("serialized review context must be valid JSON") from exc
        return cls.from_dict(decoded)


@dataclass(frozen=True)
class IndependentReviewer:
    reviewer_id: str
    review: Callable[[DevelopmentReviewContext], ReviewResult]
    implementer_id: str | None = None

    def __post_init__(self) -> None:
        reviewer_id = _required_text(self.reviewer_id, "reviewer_id")
        if not callable(self.review):
            raise ValueError("review must be callable")
        implementer_id = None
        if self.implementer_id is not None:
            implementer_id = _required_text(self.implementer_id, "implementer_id")
            if implementer_id == reviewer_id:
                raise ValueError("independent reviewer_id must differ from implementer_id")
        object.__setattr__(self, "reviewer_id", reviewer_id)
        object.__setattr__(self, "implementer_id", implementer_id)


def _latest_by_key(items: tuple[Any, ...], key: Callable[[Any], str]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for item in items:
        latest[key(item)] = item
    return latest


def build_development_review_context(
    state: RunState,
    *,
    base_sha: str,
    head_sha: str,
    final_diff: str,
    policy_refs: tuple[str, ...],
    rubric: tuple[str, ...],
) -> DevelopmentReviewContext:
    """Build an allowlisted packet from objective review-ready development evidence."""

    if not isinstance(state, RunState):
        raise ValueError("state must be RunState")
    if state.phase is not RunPhase.REVIEW:
        raise ValueError("development review context requires review phase")
    base_sha = _required_text(base_sha, "base_sha")
    head_sha = _required_text(head_sha, "head_sha")
    if state.verified_head_sha != head_sha:
        raise ValueError("review head does not match verified head")
    final_diff = _exact_text(final_diff, "final_diff")
    policy_refs = _text_tuple(policy_refs, "policy_refs", required=True)
    rubric = _text_tuple(rubric, "rubric", required=True)
    if not state.changes:
        raise ValueError("review-ready state has no current change")
    change = state.changes[-1]

    current_tests = tuple(
        item for item in state.test_results if item.change_id == change.change_id
    )
    latest_tests = _latest_by_key(current_tests, lambda item: item.intent_id)
    test_evidence: list[ReviewTestEvidence] = []
    executed_shas: set[str] = set()
    for intent in state.test_intents:
        result = latest_tests.get(intent.intent_id)
        if result is None:
            raise ValueError(f"review verification is missing current test: {intent.intent_id}")
        if result.outcome is not GateOutcome.SUCCESS:
            raise ValueError(f"review verification test is not successful: {intent.intent_id}")
        if result.head_sha != head_sha or result.executed_sha is None:
            raise ValueError(f"review verification test has stale head: {intent.intent_id}")
        executed_shas.add(result.executed_sha)
        test_evidence.append(
            ReviewTestEvidence(
                intent.intent_id,
                intent.kind.value,
                intent.command,
                intent.working_directory,
                result.head_sha,
                result.executed_sha,
                result.outcome.value,
                result.exit_code,
                result.passed_tests,
                result.failed_tests,
                result.evidence_refs,
            )
        )

    current_evals = tuple(
        item for item in state.eval_results if item.change_id == change.change_id
    )
    latest_evals = _latest_by_key(current_evals, lambda item: item.eval_id)
    eval_evidence: list[ReviewEvalEvidence] = []
    for result in latest_evals.values():
        if result.outcome is not GateOutcome.SUCCESS:
            raise ValueError(f"review verification evaluation is not successful: {result.eval_id}")
        if result.head_sha != head_sha or result.executed_sha is None:
            raise ValueError(f"review verification evaluation has stale head: {result.eval_id}")
        executed_shas.add(result.executed_sha)
        eval_evidence.append(
            ReviewEvalEvidence(
                result.eval_id,
                result.head_sha,
                result.executed_sha,
                result.outcome.value,
                result.metrics,
                result.evidence_refs,
            )
        )

    if len(executed_shas) != 1:
        raise ValueError("review verification evidence must share one executed revision")
    executed_sha = next(iter(executed_shas))
    task = ReviewTaskProjection(
        state.task.task_id,
        state.task.title,
        state.task.objective,
        state.task.acceptance_criteria,
    )
    change_projection = ReviewChangeProjection(change.change_id, change.changed_paths)
    diff_digest = _sha256_text(final_diff)

    identity_payload: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "development_run_id": state.run_id,
        "task": task.to_dict(),
        "change": change_projection.to_dict(),
        "base_sha": base_sha,
        "head_sha": head_sha,
        "executed_sha": executed_sha,
        "final_diff": final_diff,
        "diff_sha256": diff_digest,
        "test_evidence": [item.to_dict() for item in test_evidence],
        "eval_evidence": [item.to_dict() for item in eval_evidence],
        "policy_refs": list(policy_refs),
        "rubric": list(rubric),
    }
    context_id = "review-context-" + _sha256_text(_canonical_json(identity_payload))
    return DevelopmentReviewContext(
        _SCHEMA_VERSION,
        context_id,
        state.run_id,
        task,
        change_projection,
        base_sha,
        head_sha,
        executed_sha,
        final_diff,
        diff_digest,
        tuple(test_evidence),
        tuple(eval_evidence),
        policy_refs,
        rubric,
    )


def run_independent_development_review(
    context: DevelopmentReviewContext,
    reviewer: IndependentReviewer,
) -> ReviewResult:
    """Run one clean reviewer call and bind its result to the exact packet/head."""

    if not isinstance(context, DevelopmentReviewContext):
        raise ValueError("context must be DevelopmentReviewContext")
    if not isinstance(reviewer, IndependentReviewer):
        raise ValueError("reviewer must be IndependentReviewer")
    result = reviewer.review(context)
    if not isinstance(result, ReviewResult):
        raise ValueError("independent reviewer must return ReviewResult")
    mismatches: list[str] = []
    if result.reviewer_id != reviewer.reviewer_id:
        mismatches.append("reviewer")
    if result.review_context_id != context.review_context_id:
        mismatches.append("context")
    if result.inspected_sha != context.head_sha:
        mismatches.append("head")
    if mismatches:
        raise ValueError("review result binding mismatch: " + ", ".join(mismatches))
    if result.review_id in {context.development_run_id, context.review_context_id}:
        raise ValueError("review_id must identify a distinct independent review run")
    return result


def apply_development_review(state: RunState, result: ReviewResult) -> RunState:
    """Delegate disposition routing unchanged to the HARN-002 state machine."""

    if not isinstance(state, RunState):
        raise ValueError("state must be RunState")
    if not isinstance(result, ReviewResult):
        raise ValueError("result must be ReviewResult")
    return apply_event(state, ReviewRecorded(result))
