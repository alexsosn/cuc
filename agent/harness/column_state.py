"""Framework-neutral state contracts for complete-column scholarly review."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class InvalidColumnTransition(ValueError):
    """Raised when a column event violates durable parsing-state invariants."""


class ReconciliationScope(str, Enum):
    COLUMN = "column"
    TABLET = "tablet"
    CORPUS = "corpus"


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _text_tuple(
    value: object,
    field: str,
    *,
    required: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an iterable of strings")
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


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, field: str) -> int:
    value = _nonnegative_int(value, field)
    if value == 0:
        raise ValueError(f"{field} must be positive")
    return value


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def _object_tuple(value: object, cls: type, field: str) -> tuple:
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError(f"{field} must be an iterable of {cls.__name__}")
    try:
        items = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an iterable of {cls.__name__}") from exc
    if any(not isinstance(item, cls) for item in items):
        raise ValueError(f"{field} must contain only {cls.__name__}")
    return items


def _enum(value: object, cls: type[Enum], field: str):
    try:
        return value if isinstance(value, cls) else cls(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def _event_id(value: object) -> str:
    return _required_text(value, "event_id")


@dataclass(frozen=True)
class CapabilityRef:
    canonical_name: str
    contract_version: str
    provenance_sha256: str

    def __post_init__(self) -> None:
        digest = _required_text(self.provenance_sha256, "provenance_sha256").lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("provenance_sha256 must be a SHA-256 digest")
        object.__setattr__(self, "canonical_name", _required_text(self.canonical_name, "canonical_name"))
        object.__setattr__(self, "contract_version", _required_text(self.contract_version, "contract_version"))
        object.__setattr__(self, "provenance_sha256", digest)

    def to_dict(self) -> dict[str, object]:
        return {
            "canonical_name": self.canonical_name,
            "contract_version": self.contract_version,
            "provenance_sha256": self.provenance_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CapabilityRef":
        payload = _mapping(payload, "CapabilityRef payload")
        return cls(payload["canonical_name"], payload["contract_version"], payload["provenance_sha256"])


@dataclass(frozen=True)
class ColumnTask:
    task_id: str
    corpus: str
    tablet: str
    column: str
    repository_revision: str
    capability: CapabilityRef
    required_completion_gates: tuple[str, ...]
    evidence_priority_token_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.capability, CapabilityRef):
            raise ValueError("capability must be CapabilityRef")
        object.__setattr__(self, "task_id", _required_text(self.task_id, "task_id"))
        object.__setattr__(self, "corpus", _required_text(self.corpus, "corpus"))
        object.__setattr__(self, "tablet", _required_text(self.tablet, "tablet"))
        object.__setattr__(self, "column", _required_text(self.column, "column"))
        object.__setattr__(self, "repository_revision", _required_text(self.repository_revision, "repository_revision"))
        object.__setattr__(self, "required_completion_gates", _text_tuple(self.required_completion_gates, "required_completion_gates", required=True))
        object.__setattr__(self, "evidence_priority_token_ids", _text_tuple(self.evidence_priority_token_ids, "evidence_priority_token_ids"))

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "corpus": self.corpus,
            "tablet": self.tablet,
            "column": self.column,
            "repository_revision": self.repository_revision,
            "capability": self.capability.to_dict(),
            "required_completion_gates": list(self.required_completion_gates),
            "evidence_priority_token_ids": list(self.evidence_priority_token_ids),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ColumnTask":
        payload = _mapping(payload, "ColumnTask payload")
        return cls(
            payload["task_id"], payload["corpus"], payload["tablet"], payload["column"],
            payload["repository_revision"], CapabilityRef.from_dict(payload["capability"]),
            tuple(payload["required_completion_gates"]), tuple(payload.get("evidence_priority_token_ids", ())),
        )


@dataclass(frozen=True)
class ColumnToken:
    token_id: str
    ordinal: int
    line_ref: str
    surface: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "token_id", _required_text(self.token_id, "token_id"))
        object.__setattr__(self, "ordinal", _positive_int(self.ordinal, "ordinal"))
        object.__setattr__(self, "line_ref", _required_text(self.line_ref, "line_ref"))
        if not isinstance(self.surface, str):
            raise ValueError("surface must be a string")

    def to_dict(self) -> dict[str, object]:
        return {"token_id": self.token_id, "ordinal": self.ordinal, "line_ref": self.line_ref, "surface": self.surface}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ColumnToken":
        payload = _mapping(payload, "ColumnToken payload")
        return cls(payload["token_id"], payload["ordinal"], payload["line_ref"], payload["surface"])


@dataclass(frozen=True)
class ColumnSnapshot:
    snapshot_id: str
    source_ref: str
    source_provenance: str
    tokens: tuple[ColumnToken, ...]

    def __post_init__(self) -> None:
        tokens = _object_tuple(self.tokens, ColumnToken, "tokens")
        if not tokens:
            raise ValueError("tokens must not be empty")
        ids = tuple(token.token_id for token in tokens)
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot token ids must be unique")
        if tuple(token.ordinal for token in tokens) != tuple(range(1, len(tokens) + 1)):
            raise ValueError("snapshot ordinals must be contiguous, ordered, and start at 1")
        object.__setattr__(self, "snapshot_id", _required_text(self.snapshot_id, "snapshot_id"))
        object.__setattr__(self, "source_ref", _required_text(self.source_ref, "source_ref"))
        object.__setattr__(self, "source_provenance", _required_text(self.source_provenance, "source_provenance"))
        object.__setattr__(self, "tokens", tokens)

    @property
    def token_ids(self) -> tuple[str, ...]:
        return tuple(token.token_id for token in self.tokens)

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "source_ref": self.source_ref,
            "source_provenance": self.source_provenance,
            "tokens": [token.to_dict() for token in self.tokens],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ColumnSnapshot":
        payload = _mapping(payload, "ColumnSnapshot payload")
        return cls(payload["snapshot_id"], payload["source_ref"], payload["source_provenance"], tuple(ColumnToken.from_dict(item) for item in payload["tokens"]))


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    source_id: str
    source_ref: str
    provenance_ref: str
    summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _required_text(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "source_id", _required_text(self.source_id, "source_id"))
        object.__setattr__(self, "source_ref", _required_text(self.source_ref, "source_ref"))
        object.__setattr__(self, "provenance_ref", _required_text(self.provenance_ref, "provenance_ref"))
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))

    def to_dict(self) -> dict[str, object]:
        return {"evidence_id": self.evidence_id, "source_id": self.source_id, "source_ref": self.source_ref, "provenance_ref": self.provenance_ref, "summary": self.summary}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceRecord":
        payload = _mapping(payload, "EvidenceRecord payload")
        return cls(payload["evidence_id"], payload["source_id"], payload["source_ref"], payload["provenance_ref"], payload["summary"])


@dataclass(frozen=True)
class TokenDecision:
    decision_id: str
    token_id: str
    analyses: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    summary: str
    revisit_of: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _required_text(self.decision_id, "decision_id"))
        object.__setattr__(self, "token_id", _required_text(self.token_id, "token_id"))
        object.__setattr__(self, "analyses", _text_tuple(self.analyses, "analyses", required=True))
        object.__setattr__(self, "evidence_ids", _text_tuple(self.evidence_ids, "evidence_ids", required=True))
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))
        object.__setattr__(self, "revisit_of", _optional_text(self.revisit_of, "revisit_of"))

    def to_dict(self) -> dict[str, object]:
        return {"decision_id": self.decision_id, "token_id": self.token_id, "analyses": list(self.analyses), "evidence_ids": list(self.evidence_ids), "summary": self.summary, "revisit_of": self.revisit_of}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TokenDecision":
        payload = _mapping(payload, "TokenDecision payload")
        return cls(payload["decision_id"], payload["token_id"], tuple(payload["analyses"]), tuple(payload["evidence_ids"]), payload["summary"], payload.get("revisit_of"))


@dataclass(frozen=True)
class TokenCursor:
    next_index: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "next_index", _nonnegative_int(self.next_index, "next_index"))

    def to_dict(self) -> dict[str, object]:
        return {"next_index": self.next_index}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TokenCursor":
        return cls(_mapping(payload, "TokenCursor payload")["next_index"])


@dataclass(frozen=True)
class RevisitRequest:
    request_id: str
    token_id: str
    reason: str
    finding_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _required_text(self.request_id, "request_id"))
        object.__setattr__(self, "token_id", _required_text(self.token_id, "token_id"))
        object.__setattr__(self, "reason", _required_text(self.reason, "reason"))
        object.__setattr__(self, "finding_id", _optional_text(self.finding_id, "finding_id"))

    def to_dict(self) -> dict[str, object]:
        return {"request_id": self.request_id, "token_id": self.token_id, "reason": self.reason, "finding_id": self.finding_id}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RevisitRequest":
        payload = _mapping(payload, "RevisitRequest payload")
        return cls(payload["request_id"], payload["token_id"], payload["reason"], payload.get("finding_id"))


@dataclass(frozen=True)
class CorpusReconciliationFinding:
    finding_id: str
    scope: ReconciliationScope
    token_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    summary: str
    requires_revisit: bool

    def __post_init__(self) -> None:
        if not isinstance(self.requires_revisit, bool):
            raise ValueError("requires_revisit must be boolean")
        object.__setattr__(self, "finding_id", _required_text(self.finding_id, "finding_id"))
        object.__setattr__(self, "scope", _enum(self.scope, ReconciliationScope, "scope"))
        object.__setattr__(self, "token_ids", _text_tuple(self.token_ids, "token_ids", required=True))
        object.__setattr__(self, "evidence_ids", _text_tuple(self.evidence_ids, "evidence_ids", required=True))
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))

    def to_dict(self) -> dict[str, object]:
        return {"finding_id": self.finding_id, "scope": self.scope.value, "token_ids": list(self.token_ids), "evidence_ids": list(self.evidence_ids), "summary": self.summary, "requires_revisit": self.requires_revisit}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CorpusReconciliationFinding":
        payload = _mapping(payload, "CorpusReconciliationFinding payload")
        return cls(payload["finding_id"], ReconciliationScope(payload["scope"]), tuple(payload["token_ids"]), tuple(payload["evidence_ids"]), payload["summary"], payload["requires_revisit"])


@dataclass(frozen=True)
class CompletionGateResult:
    gate_id: str
    passed: bool
    decision_revision: int
    evidence_refs: tuple[str, ...]
    summary: str

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be boolean")
        object.__setattr__(self, "gate_id", _required_text(self.gate_id, "gate_id"))
        object.__setattr__(self, "decision_revision", _nonnegative_int(self.decision_revision, "decision_revision"))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs", required=True))
        object.__setattr__(self, "summary", _required_text(self.summary, "summary"))

    def to_dict(self) -> dict[str, object]:
        return {"gate_id": self.gate_id, "passed": self.passed, "decision_revision": self.decision_revision, "evidence_refs": list(self.evidence_refs), "summary": self.summary}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CompletionGateResult":
        payload = _mapping(payload, "CompletionGateResult payload")
        return cls(payload["gate_id"], payload["passed"], payload["decision_revision"], tuple(payload["evidence_refs"]), payload["summary"])


@dataclass(frozen=True)
class ColumnCompletion:
    decision_revision: int
    gate_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_revision", _nonnegative_int(self.decision_revision, "decision_revision"))
        object.__setattr__(self, "gate_ids", _text_tuple(self.gate_ids, "gate_ids", required=True))
        object.__setattr__(self, "evidence_refs", _text_tuple(self.evidence_refs, "evidence_refs", required=True))

    def to_dict(self) -> dict[str, object]:
        return {"decision_revision": self.decision_revision, "gate_ids": list(self.gate_ids), "evidence_refs": list(self.evidence_refs)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ColumnCompletion":
        payload = _mapping(payload, "ColumnCompletion payload")
        return cls(payload["decision_revision"], tuple(payload["gate_ids"]), tuple(payload["evidence_refs"]))


@dataclass(frozen=True)
class EventReceipt:
    event_id: str
    event_type: str
    payload_sha256: str

    def __post_init__(self) -> None:
        digest = _required_text(self.payload_sha256, "payload_sha256").lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("payload_sha256 must be a SHA-256 digest")
        object.__setattr__(self, "event_id", _required_text(self.event_id, "event_id"))
        object.__setattr__(self, "event_type", _required_text(self.event_type, "event_type"))
        object.__setattr__(self, "payload_sha256", digest)

    def to_dict(self) -> dict[str, object]:
        return {"event_id": self.event_id, "event_type": self.event_type, "payload_sha256": self.payload_sha256}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EventReceipt":
        payload = _mapping(payload, "EventReceipt payload")
        return cls(payload["event_id"], payload["event_type"], payload["payload_sha256"])


@dataclass(frozen=True)
class ColumnRunState:
    task: ColumnTask
    snapshot: ColumnSnapshot
    cursor: TokenCursor
    evidence: tuple[EvidenceRecord, ...] = ()
    decisions: tuple[TokenDecision, ...] = ()
    revisit_queue: tuple[RevisitRequest, ...] = ()
    resolved_revisit_request_ids: tuple[str, ...] = ()
    reconciliation_findings: tuple[CorpusReconciliationFinding, ...] = ()
    gate_results: tuple[CompletionGateResult, ...] = ()
    reconciliation_closed: bool = False
    decision_revision: int = 0
    completion: ColumnCompletion | None = None
    event_receipts: tuple[EventReceipt, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.task, ColumnTask):
            raise ValueError("task must be ColumnTask")
        if not isinstance(self.snapshot, ColumnSnapshot):
            raise ValueError("snapshot must be ColumnSnapshot")
        if not isinstance(self.cursor, TokenCursor):
            raise ValueError("cursor must be TokenCursor")
        evidence = _object_tuple(self.evidence, EvidenceRecord, "evidence")
        decisions = _object_tuple(self.decisions, TokenDecision, "decisions")
        revisit_queue = _object_tuple(self.revisit_queue, RevisitRequest, "revisit_queue")
        findings = _object_tuple(self.reconciliation_findings, CorpusReconciliationFinding, "reconciliation_findings")
        gates = _object_tuple(self.gate_results, CompletionGateResult, "gate_results")
        receipts = _object_tuple(self.event_receipts, EventReceipt, "event_receipts")
        resolved = _text_tuple(self.resolved_revisit_request_ids, "resolved_revisit_request_ids")
        revision = _nonnegative_int(self.decision_revision, "decision_revision")
        if not isinstance(self.reconciliation_closed, bool):
            raise ValueError("reconciliation_closed must be boolean")
        if self.completion is not None and not isinstance(self.completion, ColumnCompletion):
            raise ValueError("completion must be ColumnCompletion or None")

        token_ids = self.snapshot.token_ids
        token_set = set(token_ids)
        unknown_hints = set(self.task.evidence_priority_token_ids) - token_set
        if unknown_hints:
            raise ValueError("evidence priority token ids are outside the complete snapshot: " + ", ".join(sorted(unknown_hints)))
        if self.cursor.next_index > len(token_ids):
            raise ValueError("cursor is beyond the complete snapshot")

        evidence_ids = tuple(item.evidence_id for item in evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence ids must be unique")
        evidence_set = set(evidence_ids)
        decision_ids = tuple(item.decision_id for item in decisions)
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("decision ids must be unique")
        for decision in decisions:
            if decision.token_id not in token_set:
                raise ValueError("decision references token outside snapshot")
            if not set(decision.evidence_ids).issubset(evidence_set):
                raise ValueError("decision references unknown evidence")
        if revision != len(decisions):
            raise ValueError("decision_revision must equal append-only decision count")
        initial = tuple(item for item in decisions if item.revisit_of is None)
        if tuple(item.token_id for item in initial) != token_ids[: self.cursor.next_index]:
            raise ValueError("initial decision history must match cursor prefix exactly")

        queue_ids = tuple(item.request_id for item in revisit_queue)
        if len(queue_ids) != len(set(queue_ids)):
            raise ValueError("revisit ids must be unique")
        if not set(resolved).issubset(set(queue_ids)):
            raise ValueError("resolved revisit ids must refer to known queue items")
        for item in revisit_queue:
            if item.token_id not in token_set:
                raise ValueError("revisit item references token outside snapshot")

        finding_ids = tuple(item.finding_id for item in findings)
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("reconciliation finding ids must be unique")
        finding_set = set(finding_ids)
        for finding in findings:
            if not set(finding.token_ids).issubset(token_set):
                raise ValueError("reconciliation finding references unknown token")
            if not set(finding.evidence_ids).issubset(evidence_set):
                raise ValueError("reconciliation finding references unknown evidence")
        for item in revisit_queue:
            if item.finding_id is not None and item.finding_id not in finding_set:
                raise ValueError("revisit item references unknown finding")

        receipt_ids = tuple(item.event_id for item in receipts)
        if len(receipt_ids) != len(set(receipt_ids)):
            raise ValueError("event receipt ids must be unique")
        if any(item.decision_revision > revision for item in gates):
            raise ValueError("gate result cannot observe a future decision revision")

        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "revisit_queue", revisit_queue)
        object.__setattr__(self, "resolved_revisit_request_ids", resolved)
        object.__setattr__(self, "reconciliation_findings", findings)
        object.__setattr__(self, "gate_results", gates)
        object.__setattr__(self, "event_receipts", receipts)
        object.__setattr__(self, "decision_revision", revision)
        if self.reconciliation_closed:
            self._validate_reconciliation_can_close()
        if self.completion is not None:
            self._validate_completion_record(self.completion)

    @classmethod
    def initial(cls, task: ColumnTask, snapshot: ColumnSnapshot) -> "ColumnRunState":
        return cls(task=task, snapshot=snapshot, cursor=TokenCursor(0))

    @property
    def initial_decisions(self) -> tuple[TokenDecision, ...]:
        return tuple(item for item in self.decisions if item.revisit_of is None)

    @property
    def initial_pass_complete(self) -> bool:
        return self.cursor.next_index == len(self.snapshot.tokens)

    @property
    def next_token_id(self) -> str | None:
        return None if self.initial_pass_complete else self.snapshot.tokens[self.cursor.next_index].token_id

    @property
    def unresolved_revisits(self) -> tuple[RevisitRequest, ...]:
        resolved = set(self.resolved_revisit_request_ids)
        return tuple(item for item in self.revisit_queue if item.request_id not in resolved)

    def latest_decision(self, token_id: str) -> TokenDecision | None:
        token_id = _required_text(token_id, "token_id")
        for decision in reversed(self.decisions):
            if decision.token_id == token_id:
                return decision
        return None

    def decision_history(self, token_id: str) -> tuple[TokenDecision, ...]:
        token_id = _required_text(token_id, "token_id")
        return tuple(decision for decision in self.decisions if decision.token_id == token_id)

    def _finding_is_resolved(self, finding: CorpusReconciliationFinding) -> bool:
        if not finding.requires_revisit:
            return True
        resolved = set(self.resolved_revisit_request_ids)
        return any(item.finding_id == finding.finding_id and item.request_id in resolved for item in self.revisit_queue)

    def _validate_reconciliation_can_close(self) -> None:
        if not self.initial_pass_complete:
            raise ValueError("reconciliation cannot close before complete traversal")
        if self.unresolved_revisits:
            raise ValueError("reconciliation cannot close with unresolved revisit work")
        pending = tuple(item.finding_id for item in self.reconciliation_findings if not self._finding_is_resolved(item))
        if pending:
            raise ValueError("reconciliation cannot close before required revisits: " + ", ".join(pending))

    def _latest_current_gate(self, gate_id: str) -> CompletionGateResult | None:
        for result in reversed(self.gate_results):
            if result.gate_id == gate_id and result.decision_revision == self.decision_revision:
                return result
        return None

    def _validate_completion_record(self, completion: ColumnCompletion) -> None:
        if not self.initial_pass_complete or not self.reconciliation_closed:
            raise ValueError("completed state requires traversal and reconciliation")
        if completion.decision_revision != self.decision_revision:
            raise ValueError("completion revision must equal current decision revision")
        if completion.gate_ids != self.task.required_completion_gates:
            raise ValueError("completion gates must match task-required gates")
        for gate_id in self.task.required_completion_gates:
            result = self._latest_current_gate(gate_id)
            if result is None or not result.passed:
                raise ValueError(f"completed state lacks successful current gate: {gate_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "task": self.task.to_dict(), "snapshot": self.snapshot.to_dict(), "cursor": self.cursor.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence], "decisions": [item.to_dict() for item in self.decisions],
            "revisit_queue": [item.to_dict() for item in self.revisit_queue], "resolved_revisit_request_ids": list(self.resolved_revisit_request_ids),
            "reconciliation_findings": [item.to_dict() for item in self.reconciliation_findings], "gate_results": [item.to_dict() for item in self.gate_results],
            "reconciliation_closed": self.reconciliation_closed, "decision_revision": self.decision_revision,
            "completion": None if self.completion is None else self.completion.to_dict(),
            "event_receipts": [item.to_dict() for item in self.event_receipts],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ColumnRunState":
        payload = _mapping(payload, "ColumnRunState payload")
        completion = payload.get("completion")
        return cls(
            task=ColumnTask.from_dict(payload["task"]), snapshot=ColumnSnapshot.from_dict(payload["snapshot"]), cursor=TokenCursor.from_dict(payload["cursor"]),
            evidence=tuple(EvidenceRecord.from_dict(item) for item in payload.get("evidence", ())),
            decisions=tuple(TokenDecision.from_dict(item) for item in payload.get("decisions", ())),
            revisit_queue=tuple(RevisitRequest.from_dict(item) for item in payload.get("revisit_queue", ())),
            resolved_revisit_request_ids=tuple(payload.get("resolved_revisit_request_ids", ())),
            reconciliation_findings=tuple(CorpusReconciliationFinding.from_dict(item) for item in payload.get("reconciliation_findings", ())),
            gate_results=tuple(CompletionGateResult.from_dict(item) for item in payload.get("gate_results", ())),
            reconciliation_closed=payload.get("reconciliation_closed", False), decision_revision=payload.get("decision_revision", 0),
            completion=None if completion is None else ColumnCompletion.from_dict(completion),
            event_receipts=tuple(EventReceipt.from_dict(item) for item in payload.get("event_receipts", ())),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str) -> "ColumnRunState":
        if not isinstance(payload, str):
            raise ValueError("serialized state must be a string")
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("serialized state must be valid JSON") from exc
        return cls.from_dict(decoded)


@dataclass(frozen=True)
class EvidenceRecorded:
    event_id: str
    evidence: EvidenceRecord
    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _event_id(self.event_id))
        if not isinstance(self.evidence, EvidenceRecord):
            raise ValueError("evidence must be EvidenceRecord")
    def to_dict(self) -> dict[str, object]:
        return {"event_id": self.event_id, "event_type": type(self).__name__, "evidence": self.evidence.to_dict()}


@dataclass(frozen=True)
class TokenReviewed:
    event_id: str
    decision: TokenDecision
    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _event_id(self.event_id))
        if not isinstance(self.decision, TokenDecision):
            raise ValueError("decision must be TokenDecision")
    def to_dict(self) -> dict[str, object]:
        return {"event_id": self.event_id, "event_type": type(self).__name__, "decision": self.decision.to_dict()}


@dataclass(frozen=True)
class RevisitRequested:
    event_id: str
    request: RevisitRequest
    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _event_id(self.event_id))
        if not isinstance(self.request, RevisitRequest):
            raise ValueError("request must be RevisitRequest")
    def to_dict(self) -> dict[str, object]:
        return {"event_id": self.event_id, "event_type": type(self).__name__, "request": self.request.to_dict()}


@dataclass(frozen=True)
class TokenRevisited:
    event_id: str
    request_id: str
    decision: TokenDecision
    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _event_id(self.event_id))
        object.__setattr__(self, "request_id", _required_text(self.request_id, "request_id"))
        if not isinstance(self.decision, TokenDecision):
            raise ValueError("decision must be TokenDecision")
    def to_dict(self) -> dict[str, object]:
        return {"event_id": self.event_id, "event_type": type(self).__name__, "request_id": self.request_id, "decision": self.decision.to_dict()}


@dataclass(frozen=True)
class ReconciliationFindingRecorded:
    event_id: str
    finding: CorpusReconciliationFinding
    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _event_id(self.event_id))
        if not isinstance(self.finding, CorpusReconciliationFinding):
            raise ValueError("finding must be CorpusReconciliationFinding")
    def to_dict(self) -> dict[str, object]:
        return {"event_id": self.event_id, "event_type": type(self).__name__, "finding": self.finding.to_dict()}


@dataclass(frozen=True)
class ReconciliationClosed:
    event_id: str
    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _event_id(self.event_id))
    def to_dict(self) -> dict[str, object]:
        return {"event_id": self.event_id, "event_type": type(self).__name__}


@dataclass(frozen=True)
class CompletionGateRecorded:
    event_id: str
    result: CompletionGateResult
    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _event_id(self.event_id))
        if not isinstance(self.result, CompletionGateResult):
            raise ValueError("result must be CompletionGateResult")
    def to_dict(self) -> dict[str, object]:
        return {"event_id": self.event_id, "event_type": type(self).__name__, "result": self.result.to_dict()}


@dataclass(frozen=True)
class ColumnCompleted:
    event_id: str
    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _event_id(self.event_id))
    def to_dict(self) -> dict[str, object]:
        return {"event_id": self.event_id, "event_type": type(self).__name__}


_COLUMN_EVENT_TYPES = (EvidenceRecorded, TokenReviewed, RevisitRequested, TokenRevisited, ReconciliationFindingRecorded, ReconciliationClosed, CompletionGateRecorded, ColumnCompleted)


def _receipt_for(event: object) -> EventReceipt:
    encoded = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")  # type: ignore[attr-defined]
    return EventReceipt(event.event_id, type(event).__name__, sha256(encoded).hexdigest())  # type: ignore[attr-defined]


def _known_evidence_ids(state: ColumnRunState) -> set[str]:
    return {item.evidence_id for item in state.evidence}


def _validate_decision_evidence(state: ColumnRunState, decision: TokenDecision) -> None:
    missing = set(decision.evidence_ids) - _known_evidence_ids(state)
    if missing:
        raise InvalidColumnTransition("decision references unrecorded evidence: " + ", ".join(sorted(missing)))


def _with_receipt(state: ColumnRunState, receipt: EventReceipt) -> ColumnRunState:
    return replace(state, event_receipts=state.event_receipts + (receipt,))


def apply_column_event(state: ColumnRunState, event: object) -> ColumnRunState:
    """Apply one validated parsing-state event without external side effects."""
    if not isinstance(state, ColumnRunState):
        raise InvalidColumnTransition("state must be ColumnRunState")
    if not isinstance(event, _COLUMN_EVENT_TYPES):
        raise InvalidColumnTransition(f"unsupported column event type: {type(event).__name__}")

    receipt = _receipt_for(event)
    prior = next((item for item in state.event_receipts if item.event_id == receipt.event_id), None)
    if prior is not None:
        if prior.event_type == receipt.event_type and prior.payload_sha256 == receipt.payload_sha256:
            return state
        raise InvalidColumnTransition(f"event id {receipt.event_id!r} was reused with a different payload")
    if state.completion is not None:
        raise InvalidColumnTransition("completed column state is immutable")

    if isinstance(event, EvidenceRecorded):
        if any(item.evidence_id == event.evidence.evidence_id for item in state.evidence):
            raise InvalidColumnTransition(f"evidence id already recorded: {event.evidence.evidence_id}")
        return _with_receipt(replace(state, evidence=state.evidence + (event.evidence,)), receipt)

    if isinstance(event, TokenReviewed):
        if state.initial_pass_complete:
            raise InvalidColumnTransition("initial pass is complete; use an explicit revisit")
        if event.decision.revisit_of is not None:
            raise InvalidColumnTransition("initial decision cannot declare revisit_of")
        if event.decision.token_id != state.next_token_id:
            raise InvalidColumnTransition(f"initial review must target current token {state.next_token_id!r}")
        _validate_decision_evidence(state, event.decision)
        if any(item.decision_id == event.decision.decision_id for item in state.decisions):
            raise InvalidColumnTransition(f"decision id already recorded: {event.decision.decision_id}")
        updated = replace(state, cursor=TokenCursor(state.cursor.next_index + 1), decisions=state.decisions + (event.decision,), decision_revision=state.decision_revision + 1, reconciliation_closed=False)
        return _with_receipt(updated, receipt)

    if isinstance(event, RevisitRequested):
        if not state.initial_pass_complete:
            raise InvalidColumnTransition("revisit can be requested only after complete initial traversal")
        if any(item.request_id == event.request.request_id for item in state.revisit_queue):
            raise InvalidColumnTransition(f"revisit id already recorded: {event.request.request_id}")
        if state.latest_decision(event.request.token_id) is None:
            raise InvalidColumnTransition("revisit requires an already reviewed token")
        if event.request.finding_id is not None:
            finding = next((item for item in state.reconciliation_findings if item.finding_id == event.request.finding_id), None)
            if finding is None:
                raise InvalidColumnTransition(f"unknown reconciliation finding: {event.request.finding_id}")
            if not finding.requires_revisit or event.request.token_id not in finding.token_ids:
                raise InvalidColumnTransition("revisit does not satisfy the linked finding")
            if any(item.finding_id == finding.finding_id for item in state.revisit_queue):
                raise InvalidColumnTransition("required finding already has a revisit item")
        return _with_receipt(replace(state, revisit_queue=state.revisit_queue + (event.request,), reconciliation_closed=False), receipt)

    if isinstance(event, TokenRevisited):
        if not state.initial_pass_complete:
            raise InvalidColumnTransition("token revisit requires complete initial traversal")
        request = next((item for item in state.revisit_queue if item.request_id == event.request_id), None)
        if request is None:
            raise InvalidColumnTransition(f"unknown revisit id: {event.request_id}")
        if request.request_id in state.resolved_revisit_request_ids:
            raise InvalidColumnTransition(f"revisit already resolved: {event.request_id}")
        if event.decision.token_id != request.token_id:
            raise InvalidColumnTransition("revisit decision token does not match queued token")
        prior_decision = state.latest_decision(request.token_id)
        if prior_decision is None or event.decision.revisit_of != prior_decision.decision_id:
            raise InvalidColumnTransition("revisit_of must identify the latest prior decision")
        _validate_decision_evidence(state, event.decision)
        if any(item.decision_id == event.decision.decision_id for item in state.decisions):
            raise InvalidColumnTransition(f"decision id already recorded: {event.decision.decision_id}")
        updated = replace(state, decisions=state.decisions + (event.decision,), resolved_revisit_request_ids=state.resolved_revisit_request_ids + (request.request_id,), decision_revision=state.decision_revision + 1, reconciliation_closed=False)
        return _with_receipt(updated, receipt)

    if isinstance(event, ReconciliationFindingRecorded):
        if not state.initial_pass_complete:
            raise InvalidColumnTransition("reconciliation finding requires complete initial traversal")
        if any(item.finding_id == event.finding.finding_id for item in state.reconciliation_findings):
            raise InvalidColumnTransition(f"finding id already recorded: {event.finding.finding_id}")
        if not set(event.finding.token_ids).issubset(set(state.snapshot.token_ids)):
            raise InvalidColumnTransition("finding references token outside snapshot")
        if not set(event.finding.evidence_ids).issubset(_known_evidence_ids(state)):
            raise InvalidColumnTransition("finding references unrecorded evidence")
        return _with_receipt(replace(state, reconciliation_findings=state.reconciliation_findings + (event.finding,), reconciliation_closed=False), receipt)

    if isinstance(event, ReconciliationClosed):
        try:
            state._validate_reconciliation_can_close()
        except ValueError as exc:
            raise InvalidColumnTransition(str(exc)) from exc
        return _with_receipt(replace(state, reconciliation_closed=True), receipt)

    if isinstance(event, CompletionGateRecorded):
        if not state.initial_pass_complete or not state.reconciliation_closed:
            raise InvalidColumnTransition("completion gate requires completed traversal and closed reconciliation")
        if event.result.decision_revision != state.decision_revision:
            raise InvalidColumnTransition("completion gate result is stale for current decision revision")
        return _with_receipt(replace(state, gate_results=state.gate_results + (event.result,)), receipt)

    if isinstance(event, ColumnCompleted):
        if not state.initial_pass_complete:
            raise InvalidColumnTransition("column completion requires every snapshot token to be reviewed")
        if not state.reconciliation_closed or state.unresolved_revisits:
            raise InvalidColumnTransition("column completion requires resolved, closed reconciliation")
        pending = tuple(item.finding_id for item in state.reconciliation_findings if not state._finding_is_resolved(item))
        if pending:
            raise InvalidColumnTransition("column completion has unresolved required findings: " + ", ".join(pending))
        current_gates: list[CompletionGateResult] = []
        for gate_id in state.task.required_completion_gates:
            result = state._latest_current_gate(gate_id)
            if result is None:
                raise InvalidColumnTransition(f"missing current completion gate: {gate_id}")
            if not result.passed:
                raise InvalidColumnTransition(f"required completion gate failed: {gate_id}")
            current_gates.append(result)
        evidence_refs: list[str] = []
        seen: set[str] = set()
        for result in current_gates:
            for ref in result.evidence_refs:
                if ref not in seen:
                    evidence_refs.append(ref)
                    seen.add(ref)
        completion = ColumnCompletion(state.decision_revision, state.task.required_completion_gates, tuple(evidence_refs))
        return _with_receipt(replace(state, completion=completion), receipt)

    raise InvalidColumnTransition(f"unsupported column event type: {type(event).__name__}")
