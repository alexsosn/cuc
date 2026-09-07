"""Framework-neutral durable contracts for the CUC agent harness."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class RunPhase(str, Enum):
    RESEARCH = "research"
    PLAN = "plan"
    TEST_DESIGN = "test-design"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    REVIEW = "review"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    AWAITING_HUMAN = "awaiting-human"


class GateOutcome(str, Enum):
    SUCCESS = "success"
    TEST_FAILURE = "test-failure"
    REGRESSION = "regression"
    BLOCKED_EXECUTION = "blocked-execution"
    HUMAN_ESCALATION = "human-escalation"


class TestKind(str, Enum):
    TARGETED = "targeted"
    REGRESSION = "regression"


class FindingSeverity(str, Enum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class ReviewDisposition(str, Enum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request-changes"
    ESCALATE = "escalate"


JsonScalar = str | int | float | bool | None


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _text_tuple(values: object, field_name: str, *, required: bool = False) -> tuple[str, ...]:
    try:
        raw = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field_name} must be an iterable of strings") from exc
    normalized = tuple(_required_text(value, field_name) for value in raw)
    if required and not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _enum(value: object, enum_type: type[Enum], field_name: str):
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


def _nonnegative(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _contract(value: object, contract_type: type, field_name: str):
    if not isinstance(value, contract_type):
        raise ValueError(f"{field_name} must be {contract_type.__name__}")
    return value


def _optional_contract(value: object, contract_type: type, field_name: str):
    if value is None:
        return None
    return _contract(value, contract_type, field_name)


def _contract_tuple(values: object, contract_type: type, field_name: str) -> tuple:
    try:
        normalized = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field_name} must be an iterable of {contract_type.__name__}") from exc
    if any(not isinstance(value, contract_type) for value in normalized):
        raise ValueError(f"{field_name} must contain only {contract_type.__name__}")
    return normalized


def _latest_by_key(items, key):
    latest = {}
    for item in items:
        latest[key(item)] = item
    return latest


@dataclass(frozen=True)
class TaskSpec:
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
    def from_dict(cls, payload: Mapping[str, Any]) -> TaskSpec:
        return cls(
            payload["task_id"],
            payload["title"],
            payload["objective"],
            tuple(payload["acceptance_criteria"]),
        )


@dataclass(frozen=True)
class ResearchArtifact:
    artifact_id: str
    summary: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", _required_text(self.artifact_id, "artifact_id"))
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ResearchArtifact:
        return cls(
            payload["artifact_id"],
            payload["summary"],
            tuple(payload.get("evidence_refs", ())),
        )


@dataclass(frozen=True)
class PlanArtifact:
    plan_id: str
    summary: str
    steps: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _required_text(self.plan_id, "plan_id"))
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(self, "steps", _text_tuple(self.steps, "steps", required=True))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "summary": self.summary,
            "steps": list(self.steps),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PlanArtifact:
        return cls(
            payload["plan_id"],
            payload["summary"],
            tuple(payload["steps"]),
            tuple(payload.get("evidence_refs", ())),
        )


@dataclass(frozen=True)
class TestIntent:
    intent_id: str
    kind: TestKind
    command: tuple[str, ...]
    working_directory: str
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_id", _required_text(self.intent_id, "intent_id"))
        object.__setattr__(self, "kind", _enum(self.kind, TestKind, "kind"))
        object.__setattr__(self, "command", _text_tuple(self.command, "command", required=True))
        object.__setattr__(self, "working_directory", _required_text(self.working_directory, "working_directory"))
        object.__setattr__(self, "description", _required_text(self.description, "description"))

    def to_dict(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "kind": self.kind.value,
            "command": list(self.command),
            "working_directory": self.working_directory,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TestIntent:
        return cls(
            payload["intent_id"],
            TestKind(payload["kind"]),
            tuple(payload["command"]),
            payload["working_directory"],
            payload["description"],
        )


@dataclass(frozen=True)
class TestResult:
    intent_id: str
    change_id: str
    outcome: GateOutcome
    head_sha: str | None
    executed_sha: str | None
    exit_code: int | None
    passed_tests: int
    failed_tests: int
    summary: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        outcome = _enum(self.outcome, GateOutcome, "outcome")
        head_sha = _optional_text(self.head_sha, "head_sha")
        executed_sha = _optional_text(self.executed_sha, "executed_sha")
        passed_tests = _nonnegative(self.passed_tests, "passed_tests")
        failed_tests = _nonnegative(self.failed_tests, "failed_tests")
        summary = _required_text(self.summary, "summary")
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)):
            raise ValueError("exit_code must be an integer or None")

        executed_outcomes = {GateOutcome.SUCCESS, GateOutcome.TEST_FAILURE, GateOutcome.REGRESSION}
        if outcome in executed_outcomes:
            if head_sha is None or executed_sha is None:
                raise ValueError(f"{outcome.value} requires head_sha and executed_sha")
            if self.exit_code is None:
                raise ValueError(f"{outcome.value} requires exit_code")

        if outcome is GateOutcome.SUCCESS:
            if self.exit_code != 0 or failed_tests != 0 or passed_tests == 0:
                raise ValueError("success requires exit_code=0, failed_tests=0, and at least one passed test")
        elif outcome in {GateOutcome.TEST_FAILURE, GateOutcome.REGRESSION}:
            if self.exit_code == 0 or failed_tests == 0:
                raise ValueError(f"{outcome.value} requires a non-zero exit and at least one failed test")
        elif outcome in {GateOutcome.BLOCKED_EXECUTION, GateOutcome.HUMAN_ESCALATION}:
            if self.exit_code is not None or passed_tests or failed_tests:
                raise ValueError(f"{outcome.value} cannot claim completed test execution")

        object.__setattr__(self, "intent_id", _required_text(self.intent_id, "intent_id"))
        object.__setattr__(self, "change_id", _required_text(self.change_id, "change_id"))
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "head_sha", head_sha)
        object.__setattr__(self, "executed_sha", executed_sha)
        object.__setattr__(self, "passed_tests", passed_tests)
        object.__setattr__(self, "failed_tests", failed_tests)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "change_id": self.change_id,
            "outcome": self.outcome.value,
            "head_sha": self.head_sha,
            "executed_sha": self.executed_sha,
            "exit_code": self.exit_code,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> TestResult:
        return cls(
            payload["intent_id"],
            payload["change_id"],
            GateOutcome(payload["outcome"]),
            payload.get("head_sha"),
            payload.get("executed_sha"),
            payload.get("exit_code"),
            payload.get("passed_tests", 0),
            payload.get("failed_tests", 0),
            payload["summary"],
            tuple(payload.get("evidence_refs", ())),
        )


@dataclass(frozen=True)
class EvalResult:
    eval_id: str
    change_id: str
    outcome: GateOutcome
    head_sha: str | None
    executed_sha: str | None
    summary: str
    metrics: tuple[tuple[str, JsonScalar], ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        outcome = _enum(self.outcome, GateOutcome, "outcome")
        head_sha = _optional_text(self.head_sha, "head_sha")
        executed_sha = _optional_text(self.executed_sha, "executed_sha")
        if outcome in {GateOutcome.SUCCESS, GateOutcome.TEST_FAILURE, GateOutcome.REGRESSION} and (head_sha is None or executed_sha is None):
            raise ValueError(f"{outcome.value} requires head_sha and executed_sha")

        try:
            raw_metrics = tuple(self.metrics)
        except TypeError as exc:
            raise ValueError("metrics must be key/value pairs") from exc
        normalized_metrics: list[tuple[str, JsonScalar]] = []
        names: list[str] = []
        for metric in raw_metrics:
            if not isinstance(metric, (tuple, list)) or len(metric) != 2:
                raise ValueError("metrics must contain key/value pairs")
            name, value = metric
            normalized_name = _required_text(name, "metric name")
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise ValueError(f"metric {normalized_name!r} is not JSON-scalar")
            names.append(normalized_name)
            normalized_metrics.append((normalized_name, value))
        if len(names) != len(set(names)):
            raise ValueError("metric names must be unique")
        normalized_metrics.sort(key=lambda item: item[0])

        object.__setattr__(self, "eval_id", _required_text(self.eval_id, "eval_id"))
        object.__setattr__(self, "change_id", _required_text(self.change_id, "change_id"))
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "head_sha", head_sha)
        object.__setattr__(self, "executed_sha", executed_sha)
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(self, "metrics", tuple(normalized_metrics))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, object]:
        return {
            "eval_id": self.eval_id,
            "change_id": self.change_id,
            "outcome": self.outcome.value,
            "head_sha": self.head_sha,
            "executed_sha": self.executed_sha,
            "summary": self.summary,
            "metrics": dict(self.metrics),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EvalResult:
        metrics = payload.get("metrics", {})
        metric_items = tuple(metrics.items()) if isinstance(metrics, Mapping) else tuple(metrics)
        return cls(
            payload["eval_id"],
            payload["change_id"],
            GateOutcome(payload["outcome"]),
            payload.get("head_sha"),
            payload.get("executed_sha"),
            payload["summary"],
            metric_items,
            tuple(payload.get("evidence_refs", ())),
        )


@dataclass(frozen=True)
class ChangeSet:
    change_id: str
    summary: str
    changed_paths: tuple[str, ...]
    operation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "change_id", _required_text(self.change_id, "change_id"))
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(self, "changed_paths", _text_tuple(self.changed_paths, "changed_paths", required=True))
        operation_ids = _text_tuple(self.operation_ids, "operation_ids")
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operation_ids must be unique within a change")
        object.__setattr__(self, "operation_ids", operation_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            "change_id": self.change_id,
            "summary": self.summary,
            "changed_paths": list(self.changed_paths),
            "operation_ids": list(self.operation_ids),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ChangeSet:
        return cls(
            payload["change_id"],
            payload["summary"],
            tuple(payload["changed_paths"]),
            tuple(payload.get("operation_ids", ())),
        )


@dataclass(frozen=True)
class ReviewFinding:
    finding_id: str
    severity: FindingSeverity
    summary: str
    evidence_refs: tuple[str, ...] = ()
    blocking: bool = False

    def __post_init__(self) -> None:
        severity = _enum(self.severity, FindingSeverity, "severity")
        if not isinstance(self.blocking, bool):
            raise ValueError("blocking must be boolean")
        if self.blocking and severity is FindingSeverity.INFO:
            raise ValueError("an informational finding cannot be blocking")
        object.__setattr__(self, "finding_id", _required_text(self.finding_id, "finding_id"))
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs"))

    def to_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity.value,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "blocking": self.blocking,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ReviewFinding:
        return cls(
            payload["finding_id"],
            FindingSeverity(payload["severity"]),
            payload["summary"],
            tuple(payload.get("evidence_refs", ())),
            payload.get("blocking", False),
        )


@dataclass(frozen=True)
class ReviewResult:
    review_id: str
    reviewer_id: str
    review_context_id: str
    inspected_sha: str
    disposition: ReviewDisposition
    summary: str
    findings: tuple[ReviewFinding, ...] = ()

    def __post_init__(self) -> None:
        disposition = _enum(self.disposition, ReviewDisposition, "disposition")
        findings = _contract_tuple(self.findings, ReviewFinding, "findings")
        ids = [finding.finding_id for finding in findings]
        if len(ids) != len(set(ids)):
            raise ValueError("review finding IDs must be unique")
        blockers = tuple(finding for finding in findings if finding.blocking)
        if disposition is ReviewDisposition.APPROVE and blockers:
            raise ValueError("approved review cannot contain blocking findings")
        if disposition is ReviewDisposition.REQUEST_CHANGES and not blockers:
            raise ValueError("request-changes review requires a blocking finding")
        object.__setattr__(self, "review_id", _required_text(self.review_id, "review_id"))
        object.__setattr__(self, "reviewer_id", _required_text(self.reviewer_id, "reviewer_id"))
        object.__setattr__(self, "review_context_id", _required_text(self.review_context_id, "review_context_id"))
        object.__setattr__(self, "inspected_sha", _required_text(self.inspected_sha, "inspected_sha"))
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(self, "findings", findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "review_id": self.review_id,
            "reviewer_id": self.reviewer_id,
            "review_context_id": self.review_context_id,
            "inspected_sha": self.inspected_sha,
            "disposition": self.disposition.value,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ReviewResult:
        return cls(
            payload["review_id"],
            payload["reviewer_id"],
            payload["review_context_id"],
            payload["inspected_sha"],
            ReviewDisposition(payload["disposition"]),
            payload["summary"],
            tuple(ReviewFinding.from_dict(item) for item in payload.get("findings", ())),
        )


@dataclass(frozen=True)
class RunState:
    run_id: str
    task: TaskSpec
    phase: RunPhase = RunPhase.RESEARCH
    research: ResearchArtifact | None = None
    plan: PlanArtifact | None = None
    test_intents: tuple[TestIntent, ...] = ()
    changes: tuple[ChangeSet, ...] = ()
    test_results: tuple[TestResult, ...] = ()
    eval_results: tuple[EvalResult, ...] = ()
    review: ReviewResult | None = None
    resume_phase: RunPhase | None = None
    pause_reason: str | None = None
    verified_head_sha: str | None = None

    def __post_init__(self) -> None:
        task = _contract(self.task, TaskSpec, "task")
        research = _optional_contract(self.research, ResearchArtifact, "research")
        plan = _optional_contract(self.plan, PlanArtifact, "plan")
        test_intents = _contract_tuple(self.test_intents, TestIntent, "test_intents")
        changes = _contract_tuple(self.changes, ChangeSet, "changes")
        test_results = _contract_tuple(self.test_results, TestResult, "test_results")
        eval_results = _contract_tuple(self.eval_results, EvalResult, "eval_results")
        review = _optional_contract(self.review, ReviewResult, "review")
        phase = _enum(self.phase, RunPhase, "phase")
        resume_phase = None if self.resume_phase is None else _enum(self.resume_phase, RunPhase, "resume_phase")
        pause_reason = _optional_text(self.pause_reason, "pause_reason")
        verified_head_sha = _optional_text(self.verified_head_sha, "verified_head_sha")

        intent_ids = [intent.intent_id for intent in test_intents]
        if len(intent_ids) != len(set(intent_ids)):
            raise ValueError("test intent IDs must be unique")
        change_ids = [change.change_id for change in changes]
        if len(change_ids) != len(set(change_ids)):
            raise ValueError("change IDs must be unique")
        operation_ids = [operation for change in changes for operation in change.operation_ids]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operation IDs must be unique across a run")

        known_intents = set(intent_ids)
        known_changes = set(change_ids)
        for result in test_results:
            if result.intent_id not in known_intents:
                raise ValueError(f"test result uses undeclared intent: {result.intent_id}")
            if result.change_id not in known_changes:
                raise ValueError(f"test result uses unknown change: {result.change_id}")
        for result in eval_results:
            if result.change_id not in known_changes:
                raise ValueError(f"evaluation result uses unknown change: {result.change_id}")

        exceptional = {RunPhase.BLOCKED, RunPhase.AWAITING_HUMAN}
        if phase in exceptional:
            if resume_phase is None or resume_phase in exceptional or resume_phase is RunPhase.COMPLETE:
                raise ValueError("exceptional phase requires a resumable non-exceptional resume_phase")
            if pause_reason is None:
                raise ValueError("exceptional phase requires pause_reason")
        elif resume_phase is not None or pause_reason is not None:
            raise ValueError("resume_phase/pause_reason are only valid for exceptional phases")

        effective_phase = resume_phase if phase in exceptional else phase
        assert effective_phase is not None
        self._validate_phase_snapshot(
            effective_phase,
            research,
            plan,
            test_intents,
            changes,
            test_results,
            eval_results,
            review,
            verified_head_sha,
        )

        object.__setattr__(self, "run_id", _required_text(self.run_id, "run_id"))
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "research", research)
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "test_intents", test_intents)
        object.__setattr__(self, "changes", changes)
        object.__setattr__(self, "test_results", test_results)
        object.__setattr__(self, "eval_results", eval_results)
        object.__setattr__(self, "review", review)
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "resume_phase", resume_phase)
        object.__setattr__(self, "pause_reason", pause_reason)
        object.__setattr__(self, "verified_head_sha", verified_head_sha)

    @staticmethod
    def _validate_phase_snapshot(
        phase: RunPhase,
        research: ResearchArtifact | None,
        plan: PlanArtifact | None,
        test_intents: tuple[TestIntent, ...],
        changes: tuple[ChangeSet, ...],
        test_results: tuple[TestResult, ...],
        eval_results: tuple[EvalResult, ...],
        review: ReviewResult | None,
        verified_head_sha: str | None,
    ) -> None:
        if phase is RunPhase.RESEARCH:
            if any((research is not None, plan is not None, test_intents, changes, test_results, eval_results, review is not None, verified_head_sha is not None)):
                raise ValueError("research phase cannot contain later-phase state")
            return

        if research is None:
            raise ValueError(f"{phase.value} phase requires research evidence")
        if phase is RunPhase.PLAN:
            if any((plan is not None, test_intents, changes, test_results, eval_results, review is not None, verified_head_sha is not None)):
                raise ValueError("plan phase cannot contain later-phase state")
            return

        if plan is None:
            raise ValueError(f"{phase.value} phase requires a plan")
        if phase is RunPhase.TEST_DESIGN:
            if any((test_intents, changes, test_results, eval_results, review is not None, verified_head_sha is not None)):
                raise ValueError("test-design phase cannot contain later-phase state")
            return

        if not test_intents:
            raise ValueError(f"{phase.value} phase requires declared tests")
        if phase is RunPhase.IMPLEMENT:
            if verified_head_sha is not None:
                raise ValueError("implementation phase cannot retain verified_head_sha")
            if review is not None and review.disposition is not ReviewDisposition.REQUEST_CHANGES:
                raise ValueError("implementation phase may retain only a request-changes review")
            return

        if not changes:
            raise ValueError(f"{phase.value} phase requires a recorded change")
        if phase is RunPhase.VERIFY:
            if verified_head_sha is not None or review is not None:
                raise ValueError("verify phase cannot contain verified/review-complete state")
            return

        if phase not in {RunPhase.REVIEW, RunPhase.COMPLETE}:
            raise ValueError(f"unsupported durable phase: {phase.value}")
        if verified_head_sha is None:
            raise ValueError(f"{phase.value} phase requires verified_head_sha")

        RunState._validate_verified_evidence(
            test_intents,
            changes,
            test_results,
            eval_results,
            verified_head_sha,
        )
        if phase is RunPhase.REVIEW:
            if review is not None:
                if review.disposition is not ReviewDisposition.ESCALATE:
                    raise ValueError("review phase may retain only an escalated review awaiting retry")
                if review.inspected_sha != verified_head_sha:
                    raise ValueError("review inspected a different verified head")
            return

        if review is None or review.disposition is not ReviewDisposition.APPROVE:
            raise ValueError("complete phase requires an approving review")
        if review.inspected_sha != verified_head_sha:
            raise ValueError("complete review inspected a different verified head")

    @staticmethod
    def _validate_verified_evidence(
        test_intents: tuple[TestIntent, ...],
        changes: tuple[ChangeSet, ...],
        test_results: tuple[TestResult, ...],
        eval_results: tuple[EvalResult, ...],
        verified_head_sha: str,
    ) -> None:
        change_id = changes[-1].change_id
        current_tests = tuple(result for result in test_results if result.change_id == change_id)
        latest_tests = _latest_by_key(current_tests, lambda result: result.intent_id)
        missing = [intent.intent_id for intent in test_intents if intent.intent_id not in latest_tests]
        if missing:
            raise ValueError("verified snapshot is missing current tests: " + ", ".join(sorted(missing)))
        if any(result.outcome is not GateOutcome.SUCCESS for result in latest_tests.values()):
            raise ValueError("verified snapshot contains a non-successful current test")

        current_evals = tuple(result for result in eval_results if result.change_id == change_id)
        latest_evals = _latest_by_key(current_evals, lambda result: result.eval_id)
        if any(result.outcome is not GateOutcome.SUCCESS for result in latest_evals.values()):
            raise ValueError("verified snapshot contains a non-successful evaluation")

        evidence = tuple(latest_tests.values()) + tuple(latest_evals.values())
        if {result.head_sha for result in evidence} != {verified_head_sha}:
            raise ValueError("verified evidence does not match verified_head_sha")
        executed_shas = {result.executed_sha for result in evidence}
        if len(executed_shas) != 1 or None in executed_shas:
            raise ValueError("verified evidence must share one executed revision")

    @property
    def latest_change(self) -> ChangeSet | None:
        return self.changes[-1] if self.changes else None

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "task": self.task.to_dict(),
            "phase": self.phase.value,
            "research": self.research.to_dict() if self.research else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "test_intents": [intent.to_dict() for intent in self.test_intents],
            "changes": [change.to_dict() for change in self.changes],
            "test_results": [result.to_dict() for result in self.test_results],
            "eval_results": [result.to_dict() for result in self.eval_results],
            "review": self.review.to_dict() if self.review else None,
            "resume_phase": self.resume_phase.value if self.resume_phase else None,
            "pause_reason": self.pause_reason,
            "verified_head_sha": self.verified_head_sha,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RunState:
        research = payload.get("research")
        plan = payload.get("plan")
        review = payload.get("review")
        resume_phase = payload.get("resume_phase")
        return cls(
            run_id=payload["run_id"],
            task=TaskSpec.from_dict(payload["task"]),
            phase=RunPhase(payload.get("phase", RunPhase.RESEARCH.value)),
            research=ResearchArtifact.from_dict(research) if research else None,
            plan=PlanArtifact.from_dict(plan) if plan else None,
            test_intents=tuple(TestIntent.from_dict(item) for item in payload.get("test_intents", ())),
            changes=tuple(ChangeSet.from_dict(item) for item in payload.get("changes", ())),
            test_results=tuple(TestResult.from_dict(item) for item in payload.get("test_results", ())),
            eval_results=tuple(EvalResult.from_dict(item) for item in payload.get("eval_results", ())),
            review=ReviewResult.from_dict(review) if review else None,
            resume_phase=RunPhase(resume_phase) if resume_phase else None,
            pause_reason=payload.get("pause_reason"),
            verified_head_sha=payload.get("verified_head_sha"),
        )
