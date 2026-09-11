"""Clean, restartable context boundary for independent development review.

Durable disposition routing remains owned by the existing development state machine.
This module constructs an allowlisted review packet, validates a structured independent
review artifact, and projects that artifact onto the existing core review contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Callable, Mapping

from .contracts import (
    FindingSeverity,
    GateOutcome,
    ReviewDisposition,
    ReviewFinding,
    ReviewResult,
    RunPhase,
    RunState,
)
from .state_machine import ReviewRecorded, apply_event


_SCHEMA_VERSION = 1
_REPORT_SCHEMA_VERSION = 1
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
        raise ValueError(f"{field} must be an iterable of strings, not a scalar string")
    try:
        raw = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an iterable of strings") from exc
    items = tuple(_required_text(item, field) for item in raw)
    if required and not items:
        raise ValueError(f"{field} must not be empty")
    if len(items) != len(set(items)):
        raise ValueError(f"{field} must not contain duplicates")
    return items


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _mapping_tuple(value: object, field: str) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError(f"{field} must be an iterable of mappings, not a scalar")
    try:
        items = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an iterable of mappings") from exc
    if any(not isinstance(item, Mapping) for item in items):
        raise ValueError(f"{field} must contain only mappings")
    return items


def _typed_tuple(value: object, cls: type, field: str) -> tuple:
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError(f"{field} must be an iterable of {cls.__name__}")
    try:
        items = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an iterable of {cls.__name__}") from exc
    if any(not isinstance(item, cls) for item in items):
        raise ValueError(f"{field} must contain only {cls.__name__}")
    return items


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


def _enum(value: object, cls: type[Enum], field: str):
    try:
        return value if isinstance(value, cls) else cls(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


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
            payload["acceptance_criteria"],  # type: ignore[arg-type]
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
        return cls(payload["change_id"], payload["changed_paths"])  # type: ignore[arg-type]


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
            self,
            "executed_sha",
            _required_text(self.executed_sha, "executed_sha"),
        )
        object.__setattr__(self, "outcome", _required_text(self.outcome, "outcome"))
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int):
            raise ValueError("exit_code must be an integer")
        for field in ("passed_tests", "failed_tests"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        object.__setattr__(
            self,
            "evidence_refs",
            _text_tuple(self.evidence_refs, "evidence_refs"),
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
            payload["command"],  # type: ignore[arg-type]
            payload["working_directory"],
            payload["head_sha"],
            payload["executed_sha"],
            payload["outcome"],
            payload["exit_code"],
            payload["passed_tests"],
            payload["failed_tests"],
            payload["evidence_refs"],  # type: ignore[arg-type]
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
            self,
            "executed_sha",
            _required_text(self.executed_sha, "executed_sha"),
        )
        object.__setattr__(self, "outcome", _required_text(self.outcome, "outcome"))
        if isinstance(self.metrics, (str, bytes, Mapping)):
            raise ValueError("metrics must be key/value pairs")
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
            self,
            "evidence_refs",
            _text_tuple(self.evidence_refs, "evidence_refs"),
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
            payload["evidence_refs"],  # type: ignore[arg-type]
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
        tests = _typed_tuple(self.test_evidence, ReviewTestEvidence, "test_evidence")
        evals = _typed_tuple(self.eval_evidence, ReviewEvalEvidence, "eval_evidence")
        if not tests:
            raise ValueError("test_evidence must not be empty")
        object.__setattr__(
            self,
            "development_run_id",
            _required_text(self.development_run_id, "development_run_id"),
        )
        object.__setattr__(self, "base_sha", _required_text(self.base_sha, "base_sha"))
        object.__setattr__(self, "head_sha", _required_text(self.head_sha, "head_sha"))
        object.__setattr__(
            self,
            "executed_sha",
            _required_text(self.executed_sha, "executed_sha"),
        )
        final_diff = _exact_text(self.final_diff, "final_diff")
        object.__setattr__(self, "final_diff", final_diff)
        expected_diff_digest = _sha256_text(final_diff)
        if self.diff_sha256 != expected_diff_digest:
            raise ValueError("diff digest does not match final_diff")
        object.__setattr__(self, "diff_sha256", expected_diff_digest)
        object.__setattr__(self, "test_evidence", tests)
        object.__setattr__(self, "eval_evidence", evals)

        for item in tests:
            if item.outcome != GateOutcome.SUCCESS.value:
                raise ValueError(
                    f"review verification test is not successful: {item.intent_id}"
                )
            if item.head_sha != self.head_sha:
                raise ValueError(
                    f"review verification test head does not match context: {item.intent_id}"
                )
            if item.executed_sha != self.executed_sha:
                raise ValueError(
                    "review verification test executed revision does not match context: "
                    f"{item.intent_id}"
                )
        for item in evals:
            if item.outcome != GateOutcome.SUCCESS.value:
                raise ValueError(
                    f"review verification evaluation is not successful: {item.eval_id}"
                )
            if item.head_sha != self.head_sha:
                raise ValueError(
                    f"review verification evaluation head does not match context: {item.eval_id}"
                )
            if item.executed_sha != self.executed_sha:
                raise ValueError(
                    "review verification evaluation executed revision does not match context: "
                    f"{item.eval_id}"
                )

        object.__setattr__(
            self,
            "policy_refs",
            _text_tuple(self.policy_refs, "policy_refs", required=True),
        )
        object.__setattr__(
            self,
            "rubric",
            _text_tuple(self.rubric, "rubric", required=True),
        )
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
        test_items = _mapping_tuple(payload["test_evidence"], "test_evidence")
        eval_items = _mapping_tuple(payload["eval_evidence"], "eval_evidence")
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
            tuple(ReviewTestEvidence.from_dict(item) for item in test_items),
            tuple(ReviewEvalEvidence.from_dict(item) for item in eval_items),
            payload["policy_refs"],  # type: ignore[arg-type]
            payload["rubric"],  # type: ignore[arg-type]
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


class DevelopmentFindingCategory(str, Enum):
    MISSING_TEST = "missing-test"
    INVARIANT_RISK = "invariant-risk"
    REGRESSION_RISK = "regression-risk"


@dataclass(frozen=True)
class DevelopmentReviewFinding:
    finding_id: str
    category: DevelopmentFindingCategory
    severity: FindingSeverity
    summary: str
    evidence_refs: tuple[str, ...]
    location: str
    blocking: bool = False

    def __post_init__(self) -> None:
        category = _enum(self.category, DevelopmentFindingCategory, "category")
        severity = _enum(self.severity, FindingSeverity, "severity")
        if not isinstance(self.blocking, bool):
            raise ValueError("blocking must be boolean")
        if self.blocking and severity is FindingSeverity.INFO:
            raise ValueError("an informational finding cannot be blocking")
        object.__setattr__(self, "finding_id", _required_text(self.finding_id, "finding_id"))
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(
            self,
            "evidence_refs",
            _text_tuple(self.evidence_refs, "evidence_refs"),
        )
        object.__setattr__(self, "location", _required_text(self.location, "location"))

    def to_core(self) -> ReviewFinding:
        return ReviewFinding(
            self.finding_id,
            self.severity,
            self.summary,
            self.evidence_refs,
            self.blocking,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "category": self.category.value,
            "severity": self.severity.value,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "location": self.location,
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DevelopmentReviewFinding":
        payload = _mapping(payload, "development review finding")
        _exact_keys(
            payload,
            {
                "finding_id", "category", "severity", "summary",
                "evidence_refs", "location", "blocking",
            },
            "development review finding",
        )
        return cls(
            payload["finding_id"],
            DevelopmentFindingCategory(payload["category"]),
            FindingSeverity(payload["severity"]),
            payload["summary"],
            payload["evidence_refs"],  # type: ignore[arg-type]
            payload["location"],
            payload["blocking"],
        )


@dataclass(frozen=True)
class DevelopmentReviewReport:
    schema_version: int
    review: ReviewResult
    findings: tuple[DevelopmentReviewFinding, ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != _REPORT_SCHEMA_VERSION:
            raise ValueError(f"report schema_version must be {_REPORT_SCHEMA_VERSION}")
        if not isinstance(self.review, ReviewResult):
            raise ValueError("report review must be ReviewResult")
        findings = _typed_tuple(
            self.findings,
            DevelopmentReviewFinding,
            "report findings",
        )
        ids = tuple(item.finding_id for item in findings)
        if len(ids) != len(set(ids)):
            raise ValueError("report finding IDs must be unique")
        expected_review = ReviewResult(
            self.review.review_id,
            self.review.reviewer_id,
            self.review.review_context_id,
            self.review.inspected_sha,
            self.review.disposition,
            self.review.summary,
            tuple(item.to_core() for item in findings),
        )
        if self.review != expected_review:
            raise ValueError("structured report finding projection does not match core review")
        object.__setattr__(self, "findings", findings)

    @classmethod
    def create(
        cls,
        *,
        review_id: str,
        reviewer_id: str,
        review_context_id: str,
        inspected_sha: str,
        disposition: ReviewDisposition,
        summary: str,
        findings: tuple[DevelopmentReviewFinding, ...],
    ) -> "DevelopmentReviewReport":
        rich_findings = _typed_tuple(
            findings,
            DevelopmentReviewFinding,
            "report findings",
        )
        core = ReviewResult(
            review_id,
            reviewer_id,
            review_context_id,
            inspected_sha,
            disposition,
            summary,
            tuple(item.to_core() for item in rich_findings),
        )
        return cls(_REPORT_SCHEMA_VERSION, core, rich_findings)

    @property
    def review_id(self) -> str:
        return self.review.review_id

    @property
    def reviewer_id(self) -> str:
        return self.review.reviewer_id

    @property
    def review_context_id(self) -> str:
        return self.review.review_context_id

    @property
    def inspected_sha(self) -> str:
        return self.review.inspected_sha

    @property
    def disposition(self) -> ReviewDisposition:
        return self.review.disposition

    @property
    def summary(self) -> str:
        return self.review.summary

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "review": self.review.to_dict(),
            "findings": [item.to_dict() for item in self.findings],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DevelopmentReviewReport":
        payload = _mapping(payload, "development review report")
        _exact_keys(payload, {"schema_version", "review", "findings"}, "development review report")
        finding_items = _mapping_tuple(payload["findings"], "report findings")
        try:
            review = ReviewResult.from_dict(_mapping(payload["review"], "report review"))
            findings = tuple(DevelopmentReviewFinding.from_dict(item) for item in finding_items)
            return cls(payload["schema_version"], review, findings)
        except ValueError as exc:
            raise ValueError(f"invalid structured review report projection: {exc}") from exc

    @classmethod
    def from_json(cls, payload: str) -> "DevelopmentReviewReport":
        if not isinstance(payload, str):
            raise ValueError("serialized development review report must be a string")
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("serialized development review report must be valid JSON") from exc
        return cls.from_dict(decoded)


@dataclass(frozen=True)
class IndependentReviewer:
    reviewer_id: str
    review: Callable[[DevelopmentReviewContext], DevelopmentReviewReport]
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
    for eval_id in sorted(latest_evals):
        result = latest_evals[eval_id]
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


def _revalidate_context(context: DevelopmentReviewContext) -> DevelopmentReviewContext:
    if not isinstance(context, DevelopmentReviewContext):
        raise ValueError("context must be DevelopmentReviewContext")
    try:
        return DevelopmentReviewContext.from_dict(context.to_dict())
    except Exception as exc:
        raise ValueError(f"development review context failed identity revalidation: {exc}") from exc


def _revalidate_report(report: DevelopmentReviewReport) -> DevelopmentReviewReport:
    if not isinstance(report, DevelopmentReviewReport):
        raise ValueError("independent reviewer must return DevelopmentReviewReport")
    try:
        return DevelopmentReviewReport.from_dict(report.to_dict())
    except Exception as exc:
        raise ValueError(f"development review report failed projection revalidation: {exc}") from exc


def run_independent_development_review(
    context: DevelopmentReviewContext,
    reviewer: IndependentReviewer,
) -> DevelopmentReviewReport:
    """Run one clean reviewer call and bind its structured result to packet/head."""

    clean_context = _revalidate_context(context)
    if not isinstance(reviewer, IndependentReviewer):
        raise ValueError("reviewer must be IndependentReviewer")
    reviewer_id = _required_text(reviewer.reviewer_id, "reviewer_id")
    if reviewer.implementer_id is not None and reviewer.implementer_id == reviewer_id:
        raise ValueError("independent reviewer_id must differ from implementer_id")

    expected_context_id = clean_context.review_context_id
    expected_head = clean_context.head_sha
    expected_run_id = clean_context.development_run_id
    raw_report = reviewer.review(clean_context)
    report = _revalidate_report(raw_report)
    review = report.review

    mismatches: list[str] = []
    if review.reviewer_id != reviewer_id:
        mismatches.append("reviewer")
    if review.review_context_id != expected_context_id:
        mismatches.append("context")
    if review.inspected_sha != expected_head:
        mismatches.append("head")
    if mismatches:
        raise ValueError("review report binding mismatch: " + ", ".join(mismatches))
    if review.review_id in {expected_run_id, expected_context_id}:
        raise ValueError("review_id must identify a distinct independent review run")
    return report


def apply_development_review(
    state: RunState,
    context: DevelopmentReviewContext,
    report: DevelopmentReviewReport,
) -> RunState:
    """Validate exact clean-context binding, then delegate core routing unchanged."""

    if not isinstance(state, RunState):
        raise ValueError("state must be RunState")
    clean_context = _revalidate_context(context)
    clean_report = _revalidate_report(report)

    expected_context = build_development_review_context(
        state,
        base_sha=clean_context.base_sha,
        head_sha=clean_context.head_sha,
        final_diff=clean_context.final_diff,
        policy_refs=clean_context.policy_refs,
        rubric=clean_context.rubric,
    )
    if expected_context != clean_context:
        raise ValueError("development review context does not bind to current run state")
    review = clean_report.review
    if (
        review.review_context_id != clean_context.review_context_id
        or review.inspected_sha != clean_context.head_sha
    ):
        raise ValueError("development review report/context binding mismatch")
    return apply_event(state, ReviewRecorded(review))
