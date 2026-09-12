"""Framework-neutral bridge from systematic parsing findings to development proposals.

This module classifies and renders proposals. It performs no GitHub I/O and owns no
mutation authority. READY decisions contain the existing HARN-010 ``TaskSpec`` and a
HARN-023 ``GitHubEffectRequest`` that a trusted controller may later authorize.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Iterable

from .contracts import TaskSpec
from .github_effects import GitHubAction, GitHubEffectRequest


FORK_REPOSITORY = "alexsosn/cuc"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MACHINE_ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_RULE_LIKE_CLASSIFICATIONS = frozenset(
    {
        "parser-config-defect",
        "linter-defect",
        "skill-procedure-defect",
    }
)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _single_line(value: object, field: str) -> str:
    text = _required_text(value, field)
    if "\n" in text or "\r" in text:
        raise ValueError(f"{field} must be a single line")
    return text


def _text_tuple(
    value: object,
    field: str,
    *,
    required: bool = False,
    single_line: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an iterable of strings")
    normalizer = _single_line if single_line else _required_text
    try:
        items = tuple(normalizer(item, field) for item in value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an iterable of strings") from exc
    if required and not items:
        raise ValueError(f"{field} must not be empty")
    if len(items) != len(set(items)):
        raise ValueError(f"{field} must not contain duplicates")
    return items


def _digest(value: object, field: str) -> str:
    text = _required_text(value, field).lower()
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return text


def _machine_id(value: object, field: str) -> str:
    text = _single_line(value, field)
    if not _MACHINE_ID_RE.fullmatch(text):
        raise ValueError(
            f"{field} must be a canonical lower-case machine ID using letters, digits, dots and hyphens"
        )
    return text


def _normalized_source_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _safe_markdown(value: str) -> str:
    """Prevent caller text from manufacturing hidden bridge markers/comments."""
    return value.replace("<!--", "&lt;!--").replace("-->", "--&gt;")


class FindingClassification(str, Enum):
    LOCAL_SCHOLARLY_READING = "local-scholarly-reading"
    PARSER_CONFIG_DEFECT = "parser-config-defect"
    LINTER_DEFECT = "linter-defect"
    SKILL_PROCEDURE_DEFECT = "skill-procedure-defect"
    DETERMINISTIC_TOOL_OPPORTUNITY = "deterministic-tool-opportunity"
    HARNESS_RUNTIME_DEFECT = "harness-runtime-defect"
    EVAL_BENCHMARK_DEFECT = "eval-benchmark-defect"


class SystematicSignalKind(str, Enum):
    EVAL_REGRESSION = "eval-regression"
    EXPERT_FEEDBACK_PATTERN = "expert-feedback-pattern"
    MODEL_COMPARISON = "model-comparison"
    REPEATED_WORKFLOW_COST = "repeated-workflow-cost"


class FindingDisposition(str, Enum):
    LOCAL_NO_DEVELOPMENT_ISSUE = "local-no-development-issue"
    INSUFFICIENT_SYSTEMATIC_EVIDENCE = "insufficient-systematic-evidence"
    DUPLICATE_EXISTING_ISSUE = "duplicate-existing-issue"
    READY_FOR_DEVELOPMENT_ISSUE = "ready-for-development-issue"


@dataclass(frozen=True)
class FindingOccurrence:
    occurrence_id: str
    corpus: str
    tablet: str
    column: str
    locus_ref: str
    token_ids: tuple[str, ...]
    summary: str
    run_id: str
    model_provider: str
    model_name: str
    model_version: str
    skill_name: str
    skill_contract_version: str
    skill_provenance_sha256: str
    evidence_refs: tuple[str, ...]
    metric_refs: tuple[str, ...] = ()
    expert_feedback_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurrence_id", _single_line(self.occurrence_id, "occurrence_id"))
        object.__setattr__(self, "corpus", _single_line(self.corpus, "corpus"))
        object.__setattr__(self, "tablet", _single_line(self.tablet, "tablet"))
        object.__setattr__(self, "column", _single_line(self.column, "column"))
        object.__setattr__(self, "locus_ref", _single_line(self.locus_ref, "locus_ref"))
        object.__setattr__(
            self,
            "token_ids",
            _text_tuple(self.token_ids, "token_ids", required=True, single_line=True),
        )
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(self, "run_id", _single_line(self.run_id, "run_id"))
        object.__setattr__(self, "model_provider", _single_line(self.model_provider, "model_provider"))
        object.__setattr__(self, "model_name", _single_line(self.model_name, "model_name"))
        object.__setattr__(self, "model_version", _single_line(self.model_version, "model_version"))
        object.__setattr__(self, "skill_name", _single_line(self.skill_name, "skill_name"))
        object.__setattr__(
            self,
            "skill_contract_version",
            _single_line(self.skill_contract_version, "skill_contract_version"),
        )
        object.__setattr__(
            self,
            "skill_provenance_sha256",
            _digest(self.skill_provenance_sha256, "skill_provenance_sha256"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _text_tuple(self.evidence_refs, "evidence_refs", required=True, single_line=True),
        )
        object.__setattr__(
            self,
            "metric_refs",
            _text_tuple(self.metric_refs, "metric_refs", single_line=True),
        )
        object.__setattr__(
            self,
            "expert_feedback_refs",
            _text_tuple(self.expert_feedback_refs, "expert_feedback_refs", single_line=True),
        )

    @property
    def locus_key(self) -> tuple[str, str, str, str]:
        """Stable textual locus identity; token IDs are evidence, not recurrence identity."""
        return tuple(
            _normalized_source_text(item)
            for item in (self.corpus, self.tablet, self.column, self.locus_ref)
        )  # type: ignore[return-value]


@dataclass(frozen=True)
class SystematicSignal:
    signal_id: str
    kind: SystematicSignalKind
    subsystem: str
    problem_key: str
    summary: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_id", _single_line(self.signal_id, "signal_id"))
        try:
            kind = self.kind if isinstance(self.kind, SystematicSignalKind) else SystematicSignalKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid systematic signal kind: {self.kind!r}") from exc
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "subsystem", _machine_id(self.subsystem, "subsystem"))
        object.__setattr__(self, "problem_key", _machine_id(self.problem_key, "problem_key"))
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(
            self,
            "evidence_refs",
            _text_tuple(self.evidence_refs, "evidence_refs", required=True, single_line=True),
        )


@dataclass(frozen=True)
class SystematicFindingCandidate:
    classification: FindingClassification
    subsystem: str
    problem_key: str
    title: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    occurrences: tuple[FindingOccurrence, ...]
    systematic_signals: tuple[SystematicSignal, ...] = ()
    positive_cases: tuple[str, ...] = ()
    negative_cases: tuple[str, ...] = ()
    boundary_cases: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            classification = (
                self.classification
                if isinstance(self.classification, FindingClassification)
                else FindingClassification(self.classification)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid finding classification: {self.classification!r}") from exc
        object.__setattr__(self, "classification", classification)
        subsystem = _machine_id(self.subsystem, "subsystem")
        problem_key = _machine_id(self.problem_key, "problem_key")
        object.__setattr__(self, "subsystem", subsystem)
        object.__setattr__(self, "problem_key", problem_key)
        object.__setattr__(self, "title", _single_line(self.title, "title"))
        object.__setattr__(self, "objective", _required_text(self.objective, "objective"))
        object.__setattr__(
            self,
            "acceptance_criteria",
            _text_tuple(
                self.acceptance_criteria,
                "acceptance_criteria",
                required=True,
                single_line=True,
            ),
        )
        if isinstance(self.occurrences, (str, bytes)):
            raise ValueError("occurrences must be an iterable of FindingOccurrence")
        occurrences = tuple(self.occurrences)
        if not occurrences or any(not isinstance(item, FindingOccurrence) for item in occurrences):
            raise ValueError("occurrences must contain FindingOccurrence values")
        occurrence_ids = tuple(item.occurrence_id for item in occurrences)
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("occurrence IDs must be unique")
        object.__setattr__(self, "occurrences", occurrences)

        if isinstance(self.systematic_signals, (str, bytes)):
            raise ValueError("systematic_signals must be an iterable of SystematicSignal")
        signals = tuple(self.systematic_signals)
        if any(not isinstance(item, SystematicSignal) for item in signals):
            raise ValueError("systematic_signals must contain SystematicSignal values")
        signal_ids = tuple(item.signal_id for item in signals)
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("systematic signal IDs must be unique")
        mismatched = tuple(
            item.signal_id
            for item in signals
            if item.subsystem != subsystem or item.problem_key != problem_key
        )
        if mismatched:
            raise ValueError(
                "systematic signal subsystem/problem identity does not match candidate: "
                + ", ".join(mismatched)
            )
        object.__setattr__(self, "systematic_signals", signals)

        positive = _text_tuple(self.positive_cases, "positive_cases", single_line=True)
        negative = _text_tuple(self.negative_cases, "negative_cases", single_line=True)
        boundary = _text_tuple(self.boundary_cases, "boundary_cases", single_line=True)
        normalized_roles = {
            "positive": {_normalized_source_text(item) for item in positive},
            "negative": {_normalized_source_text(item) for item in negative},
            "boundary": {_normalized_source_text(item) for item in boundary},
        }
        overlap = (
            normalized_roles["positive"] & normalized_roles["negative"]
            | normalized_roles["positive"] & normalized_roles["boundary"]
            | normalized_roles["negative"] & normalized_roles["boundary"]
        )
        if overlap:
            raise ValueError(
                "positive, negative and boundary case roles must be disjoint; overlap: "
                + ", ".join(sorted(overlap))
            )
        object.__setattr__(self, "positive_cases", positive)
        object.__setattr__(self, "negative_cases", negative)
        object.__setattr__(self, "boundary_cases", boundary)
        object.__setattr__(
            self,
            "evidence_refs",
            _text_tuple(self.evidence_refs, "evidence_refs", single_line=True),
        )


@dataclass(frozen=True)
class FindingBridgeDecision:
    disposition: FindingDisposition
    reason: str
    fingerprint: str
    task: TaskSpec | None = None
    github_request: GitHubEffectRequest | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, FindingDisposition):
            raise ValueError("disposition must be FindingDisposition")
        object.__setattr__(self, "reason", _required_text(self.reason, "reason"))
        object.__setattr__(self, "fingerprint", _digest(self.fingerprint, "fingerprint"))
        ready = self.disposition is FindingDisposition.READY_FOR_DEVELOPMENT_ISSUE
        if ready:
            if not isinstance(self.task, TaskSpec) or not isinstance(self.github_request, GitHubEffectRequest):
                raise ValueError("READY decision requires TaskSpec and GitHubEffectRequest")
        elif self.task is not None or self.github_request is not None:
            raise ValueError("non-READY decision cannot contain task or GitHub request")


def _fingerprint(candidate: SystematicFindingCandidate) -> str:
    identity = {
        "classification": candidate.classification.value,
        "subsystem": candidate.subsystem,
        "problem_key": candidate.problem_key,
    }
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _existing_fingerprints(values: Iterable[str]) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("existing_issue_fingerprints must be an iterable of SHA-256 digests")
    return frozenset(_digest(value, "existing issue fingerprint") for value in values)


def _render_refs(refs: tuple[str, ...]) -> str:
    return ", ".join(_safe_markdown(item) for item in sorted(refs)) if refs else "none"


def _render_issue_body(
    candidate: SystematicFindingCandidate,
    fingerprint: str,
    task: TaskSpec,
) -> str:
    lines = [
        f"<!-- harn-017:fingerprint:{fingerprint} -->",
        "## Systematic parsing/development finding",
        "",
        f"- Classification: `{candidate.classification.value}`",
        f"- Subsystem: `{_safe_markdown(candidate.subsystem)}`",
        f"- Problem key: `{_safe_markdown(candidate.problem_key)}`",
        f"- Task ID: `{task.task_id}`",
        "",
        "## Objective",
        "",
        _safe_markdown(task.objective),
        "",
        "## Acceptance criteria",
        "",
    ]
    lines.extend(f"- {_safe_markdown(item)}" for item in task.acceptance_criteria)
    lines.extend(["", "## Reproducible occurrences", ""])

    for occurrence in sorted(
        candidate.occurrences,
        key=lambda item: (item.occurrence_id.casefold(), item.occurrence_id),
    ):
        tokens = ", ".join(_safe_markdown(item) for item in sorted(occurrence.token_ids))
        lines.extend(
            [
                f"### {_safe_markdown(occurrence.occurrence_id)}",
                (
                    f"- Locus: `{_safe_markdown(occurrence.corpus)}` / "
                    f"`{_safe_markdown(occurrence.tablet)}` / "
                    f"`{_safe_markdown(occurrence.column)}` / "
                    f"source `{_safe_markdown(occurrence.locus_ref)}` / tokens `{tokens}`"
                ),
                f"- Summary: {_safe_markdown(occurrence.summary)}",
                f"- Run: `{_safe_markdown(occurrence.run_id)}`",
                (
                    f"- Model: `{_safe_markdown(occurrence.model_provider)}` / "
                    f"`{_safe_markdown(occurrence.model_name)}` / "
                    f"`{_safe_markdown(occurrence.model_version)}`"
                ),
                (
                    f"- Skill: `{_safe_markdown(occurrence.skill_name)}` contract "
                    f"`{_safe_markdown(occurrence.skill_contract_version)}` provenance "
                    f"`{occurrence.skill_provenance_sha256}`"
                ),
                f"- Evidence refs: {_render_refs(occurrence.evidence_refs)}",
                f"- Metric refs: {_render_refs(occurrence.metric_refs)}",
                f"- Expert-feedback refs: {_render_refs(occurrence.expert_feedback_refs)}",
                "",
            ]
        )

    if candidate.systematic_signals:
        lines.extend(["## Systematic signals", ""])
        for signal in sorted(
            candidate.systematic_signals,
            key=lambda item: (item.signal_id.casefold(), item.signal_id),
        ):
            lines.append(
                f"- `{_safe_markdown(signal.signal_id)}` ({signal.kind.value}; "
                f"`{signal.subsystem}` / `{signal.problem_key}`): "
                f"{_safe_markdown(signal.summary)} — refs: {_render_refs(signal.evidence_refs)}"
            )
        lines.append("")

    if candidate.positive_cases or candidate.negative_cases or candidate.boundary_cases:
        lines.extend(["## Generalization case matrix", ""])
        lines.append(f"- Positive cases: {_render_refs(candidate.positive_cases)}")
        lines.append(f"- Negative cases: {_render_refs(candidate.negative_cases)}")
        lines.append(f"- Boundary cases: {_render_refs(candidate.boundary_cases)}")
        lines.append("")

    lines.extend(
        [
            "## Additional evidence",
            "",
            _render_refs(candidate.evidence_refs),
            "",
            "## Development workflow",
            "",
            (
                "This proposal is fork-local and is intended to enter the separate "
                "research → plan → TDD/RED → implementation → verification → logically "
                "independent review development loop. The bridge only constructs a proposal; "
                "trusted GitHub authorization and dispatch remain external to this module."
            ),
        ]
    )
    return "\n".join(lines)


def _insufficient_reason(candidate: SystematicFindingCandidate) -> str | None:
    distinct_loci = {item.locus_key for item in candidate.occurrences}
    if len(distinct_loci) < 2 and not candidate.systematic_signals:
        return (
            "insufficient systematic evidence: need at least two distinct source loci "
            "or one typed systematic signal"
        )

    if candidate.classification.value in _RULE_LIKE_CLASSIFICATIONS:
        missing: list[str] = []
        if not candidate.positive_cases:
            missing.append("positive")
        if not candidate.negative_cases:
            missing.append("negative")
        if not candidate.boundary_cases:
            missing.append("boundary")
        if missing:
            return "insufficient generalization evidence: missing " + ", ".join(missing) + " cases"
    return None


def plan_development_issue(
    candidate: SystematicFindingCandidate,
    *,
    existing_issue_fingerprints: Iterable[str] = (),
) -> FindingBridgeDecision:
    """Classify and deterministically construct a fork-local development proposal."""

    if not isinstance(candidate, SystematicFindingCandidate):
        raise ValueError("candidate must be SystematicFindingCandidate")
    fingerprint = _fingerprint(candidate)

    if candidate.classification is FindingClassification.LOCAL_SCHOLARLY_READING:
        return FindingBridgeDecision(
            FindingDisposition.LOCAL_NO_DEVELOPMENT_ISSUE,
            "local scholarly reading remains in the parsing/review workflow",
            fingerprint,
        )

    existing = _existing_fingerprints(existing_issue_fingerprints)
    if fingerprint in existing:
        return FindingBridgeDecision(
            FindingDisposition.DUPLICATE_EXISTING_ISSUE,
            "an existing development issue already carries this systematic finding fingerprint",
            fingerprint,
        )

    reason = _insufficient_reason(candidate)
    if reason is not None:
        return FindingBridgeDecision(
            FindingDisposition.INSUFFICIENT_SYSTEMATIC_EVIDENCE,
            reason,
            fingerprint,
        )

    task = TaskSpec(
        task_id=f"finding-{fingerprint}",
        title=candidate.title,
        objective=candidate.objective,
        acceptance_criteria=candidate.acceptance_criteria,
    )
    body = _render_issue_body(candidate, fingerprint, task)
    request = GitHubEffectRequest(
        operation_id=f"harn-017:create-issue:{fingerprint}",
        repository=FORK_REPOSITORY,
        action=GitHubAction.CREATE_ISSUE,
        payload={"title": task.title, "body": body},
    )
    return FindingBridgeDecision(
        FindingDisposition.READY_FOR_DEVELOPMENT_ISSUE,
        "systematic evidence is sufficient for a fork-local development issue proposal",
        fingerprint,
        task,
        request,
    )
