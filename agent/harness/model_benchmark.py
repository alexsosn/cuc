"""Provider-neutral complete-column benchmark contracts and aggregation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .parsing_evaluation import (
    EfficiencyMetrics,
    EvaluationMeasurement,
    EvaluationTarget,
    MeasurementKind,
    MeasurementScope,
    ParsingEvaluationRecord,
    ParsingRunIdentity,
    ParsingWorkloadRef,
    compare_run_identities,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_CONTEXT_KEY_PARTS = frozenset(
    {"gold", "heldout", "reviewed", "scorer", "evaluationtarget"}
)
_EFFICIENCY_FIELDS = (
    "model_calls",
    "tool_calls",
    "retries",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "cost",
)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _sha256(value: object, field: str) -> str:
    digest = _required_text(value, field).lower()
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return digest


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _freeze_json(value: object, path: str = "workload_context") -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            frozen[key] = _freeze_json(item, f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{path}[]") for item in value)
    raise ValueError(f"{path} must contain only JSON-compatible values")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _normalized_key(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


def _target_markers(target: EvaluationTarget) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            target.target_id,
            target.reviewed_ref,
            target.reviewed_provenance,
            target.scorer_id,
            target.scorer_provenance,
        )
        if value
    )


def _validate_no_target_leakage(value: object, target: EvaluationTarget, path: str = "workload_context") -> None:
    markers = _target_markers(target)
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _normalized_key(key)
            if any(part in normalized for part in _FORBIDDEN_CONTEXT_KEY_PARTS):
                raise ValueError(f"held-out evaluation target material is forbidden in {path}: {key}")
            _validate_no_target_leakage(item, target, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_no_target_leakage(item, target, path)
        return
    if isinstance(value, str):
        lowered = value.casefold()
        for marker in markers:
            if marker.casefold() in lowered:
                raise ValueError(f"held-out evaluation target material leaked into {path}")


def _model_identity_key(backend: "BenchmarkBackend") -> tuple[str, str, str, str]:
    return (
        backend.provider,
        backend.model_id,
        backend.model_version,
        backend.model_config_sha256,
    )


def _run_id(workload: ParsingWorkloadRef, backend: "BenchmarkBackend", repetition: int) -> str:
    payload = {
        "workload": workload.to_dict(),
        "model": {
            "provider": backend.provider,
            "id": backend.model_id,
            "version": backend.model_version,
            "config_sha256": backend.model_config_sha256,
        },
        "repetition": repetition,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"benchmark-{sha256(encoded).hexdigest()[:24]}-r{repetition}"


@dataclass(frozen=True)
class BenchmarkBackend:
    provider: str
    model_id: str
    model_version: str
    model_config_sha256: str
    execute: Callable[["BenchmarkRequest"], ParsingEvaluationRecord]

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _required_text(self.provider, "provider"))
        object.__setattr__(self, "model_id", _required_text(self.model_id, "model_id"))
        object.__setattr__(self, "model_version", _required_text(self.model_version, "model_version"))
        object.__setattr__(self, "model_config_sha256", _sha256(self.model_config_sha256, "model_config_sha256"))
        if not callable(self.execute):
            raise ValueError("execute must be callable")

    @property
    def identity_key(self) -> tuple[str, str, str, str]:
        return _model_identity_key(self)

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_config_sha256": self.model_config_sha256,
        }


@dataclass(frozen=True)
class BenchmarkRequest:
    identity: ParsingRunIdentity
    workload_context: Mapping[str, object]
    repetition: int

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ParsingRunIdentity):
            raise ValueError("identity must be ParsingRunIdentity")
        if not isinstance(self.workload_context, Mapping):
            raise ValueError("workload_context must be a mapping")
        object.__setattr__(self, "workload_context", _freeze_json(self.workload_context))
        object.__setattr__(self, "repetition", _positive_int(self.repetition, "repetition"))

    def to_dict(self) -> dict[str, object]:
        return {
            "identity": self.identity.to_dict(),
            "workload_context": _thaw_json(self.workload_context),
            "repetition": self.repetition,
        }


@dataclass(frozen=True)
class BenchmarkProtocol:
    workload: ParsingWorkloadRef
    target: EvaluationTarget
    repetitions: int
    workload_context: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.workload, ParsingWorkloadRef):
            raise ValueError("workload must be ParsingWorkloadRef")
        if not isinstance(self.target, EvaluationTarget):
            raise ValueError("target must be EvaluationTarget")
        repetitions = _positive_int(self.repetitions, "repetitions")
        if not isinstance(self.workload_context, Mapping):
            raise ValueError("workload_context must be a mapping")
        frozen = _freeze_json(self.workload_context)
        _validate_no_target_leakage(frozen, self.target)
        object.__setattr__(self, "repetitions", repetitions)
        object.__setattr__(self, "workload_context", frozen)

    def to_dict(self) -> dict[str, object]:
        return {
            "workload": self.workload.to_dict(),
            "target": self.target.to_dict(),
            "repetitions": self.repetitions,
            "workload_context": _thaw_json(self.workload_context),
        }


@dataclass(frozen=True)
class BenchmarkRun:
    repetition: int
    record: ParsingEvaluationRecord

    def __post_init__(self) -> None:
        object.__setattr__(self, "repetition", _positive_int(self.repetition, "repetition"))
        if not isinstance(self.record, ParsingEvaluationRecord):
            raise ValueError("record must be ParsingEvaluationRecord")

    def to_dict(self) -> dict[str, object]:
        return {"repetition": self.repetition, "record": self.record.to_dict()}


@dataclass(frozen=True)
class BenchmarkArmSummary:
    provider: str
    model_id: str
    model_version: str
    model_config_sha256: str
    repetitions: int
    quality_means: Mapping[str, float]
    efficiency_means: Mapping[str, float]
    feedback_counts: Mapping[str, int]
    cost_currency: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _required_text(self.provider, "provider"))
        object.__setattr__(self, "model_id", _required_text(self.model_id, "model_id"))
        object.__setattr__(self, "model_version", _required_text(self.model_version, "model_version"))
        object.__setattr__(self, "model_config_sha256", _sha256(self.model_config_sha256, "model_config_sha256"))
        object.__setattr__(self, "repetitions", _positive_int(self.repetitions, "repetitions"))
        object.__setattr__(self, "quality_means", MappingProxyType(dict(sorted(self.quality_means.items()))))
        object.__setattr__(self, "efficiency_means", MappingProxyType(dict(sorted(self.efficiency_means.items()))))
        object.__setattr__(self, "feedback_counts", MappingProxyType(dict(sorted(self.feedback_counts.items()))))
        if self.cost_currency is not None:
            object.__setattr__(self, "cost_currency", _required_text(self.cost_currency, "cost_currency"))

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_config_sha256": self.model_config_sha256,
            "repetitions": self.repetitions,
            "quality_means": dict(self.quality_means),
            "efficiency_means": dict(self.efficiency_means),
            "feedback_counts": dict(self.feedback_counts),
            "cost_currency": self.cost_currency,
        }


@dataclass(frozen=True)
class BenchmarkReport:
    workload: ParsingWorkloadRef
    target: EvaluationTarget
    runs: tuple[BenchmarkRun, ...]
    arm_summaries: tuple[BenchmarkArmSummary, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "workload": self.workload.to_dict(),
            "target": self.target.to_dict(),
            "runs": [run.to_dict() for run in self.runs],
            "arm_summaries": [summary.to_dict() for summary in self.arm_summaries],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _measurement_schema(record: ParsingEvaluationRecord) -> tuple[tuple[object, ...], ...]:
    return tuple(
        sorted(
            (
                item.name,
                item.kind.value,
                item.scope.value,
                item.source,
                item.token_id,
                item.deterministic,
            )
            for item in record.deterministic_measurements
        )
    )


def _authoritative_behavior(record: ParsingEvaluationRecord, name: str) -> EvaluationMeasurement:
    matches = tuple(
        item
        for item in record.deterministic_measurements
        if item.name == name
        and item.scope is MeasurementScope.COLUMN
        and item.source == "column-state"
    )
    if len(matches) != 1:
        raise ValueError(f"complete-column benchmark requires exactly one authoritative measurement: {name}")
    return matches[0]


def _validate_complete_column(record: ParsingEvaluationRecord) -> None:
    expected = _authoritative_behavior(record, "behavior.expected_token_count")
    visited = _authoritative_behavior(record, "behavior.visited_initial_tokens")
    completed = _authoritative_behavior(record, "behavior.column_completed")
    if expected.kind is not MeasurementKind.NUMERIC or visited.kind is not MeasurementKind.NUMERIC:
        raise ValueError("complete-column token counts must be numeric measurements")
    if completed.kind is not MeasurementKind.BOOLEAN:
        raise ValueError("complete-column completion flag must be boolean")
    if isinstance(expected.value, bool) or not isinstance(expected.value, int) or expected.value <= 0:
        raise ValueError("expected token count must be a positive integer")
    if isinstance(visited.value, bool) or not isinstance(visited.value, int):
        raise ValueError("visited token count must be an integer")
    if visited.value != expected.value or completed.value is not True:
        raise ValueError("benchmark run is incomplete: every expected token must be visited and column completed")


def _validate_fixed_inputs(reference: ParsingEvaluationRecord, record: ParsingEvaluationRecord) -> None:
    identity_report = compare_run_identities(reference.identity, record.identity, ignore_model_identity=True)
    if not identity_report.comparable:
        raise ValueError("benchmark workload is not comparable: " + ", ".join(identity_report.mismatched_dimensions))
    if reference.schema_version != record.schema_version:
        raise ValueError("benchmark evaluation schema drifted between model arms")
    if reference.target != record.target:
        raise ValueError("benchmark evaluation target drifted between model arms")


def _quality_means(records: tuple[ParsingEvaluationRecord, ...]) -> dict[str, float]:
    schemas = tuple(_measurement_schema(record) for record in records)
    if any(schema != schemas[0] for schema in schemas[1:]):
        raise ValueError("deterministic measurement schema drifted between benchmark repetitions")
    by_name: dict[str, list[float]] = {}
    for record in records:
        for item in record.deterministic_measurements:
            if item.kind is not MeasurementKind.NUMERIC:
                continue
            if isinstance(item.value, bool) or not isinstance(item.value, (int, float)):
                raise ValueError(f"numeric benchmark measurement has invalid value: {item.name}")
            by_name.setdefault(item.name, []).append(float(item.value))
    return {name: sum(values) / len(values) for name, values in sorted(by_name.items())}


def _efficiency_means(records: tuple[ParsingEvaluationRecord, ...]) -> tuple[dict[str, float], str | None]:
    result: dict[str, float] = {}
    for field in _EFFICIENCY_FIELDS:
        values = [getattr(record.efficiency, field) for record in records]
        if all(value is not None for value in values):
            result[field] = sum(float(value) for value in values) / len(values)  # type: ignore[arg-type]
    currencies = {
        record.efficiency.currency
        for record in records
        if record.efficiency.cost is not None
    }
    if len(currencies) > 1:
        raise ValueError("cost currency must be identical across repetitions of a benchmark arm")
    currency = next(iter(currencies), None)
    return result, currency


def _feedback_counts(records: tuple[ParsingEvaluationRecord, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for feedback in record.expert_feedback:
            key = feedback.disposition.value
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _summarize_arm(backend: BenchmarkBackend, records: tuple[ParsingEvaluationRecord, ...]) -> BenchmarkArmSummary:
    quality = _quality_means(records)
    efficiency, currency = _efficiency_means(records)
    return BenchmarkArmSummary(
        provider=backend.provider,
        model_id=backend.model_id,
        model_version=backend.model_version,
        model_config_sha256=backend.model_config_sha256,
        repetitions=len(records),
        quality_means=quality,
        efficiency_means=efficiency,
        feedback_counts=_feedback_counts(records),
        cost_currency=currency,
    )


def run_benchmark(protocol: BenchmarkProtocol, backends: object) -> BenchmarkReport:
    if not isinstance(protocol, BenchmarkProtocol):
        raise ValueError("protocol must be BenchmarkProtocol")
    if isinstance(backends, (str, bytes, Mapping)):
        raise ValueError("backends must be an iterable of BenchmarkBackend values")
    try:
        backend_values = tuple(backends)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("backends must be an iterable of BenchmarkBackend values") from exc
    if len(backend_values) < 2 or any(not isinstance(item, BenchmarkBackend) for item in backend_values):
        raise ValueError("benchmark requires at least two BenchmarkBackend values")
    identities = tuple(item.identity_key for item in backend_values)
    if len(identities) != len(set(identities)):
        raise ValueError("benchmark model identities must be distinct; duplicate identity detected")

    ordered = tuple(sorted(backend_values, key=lambda item: item.identity_key))
    all_runs: list[BenchmarkRun] = []
    summaries: list[BenchmarkArmSummary] = []
    reference: ParsingEvaluationRecord | None = None

    for backend in ordered:
        arm_records: list[ParsingEvaluationRecord] = []
        for repetition in range(1, protocol.repetitions + 1):
            identity = ParsingRunIdentity(
                run_id=_run_id(protocol.workload, backend, repetition),
                workload=protocol.workload,
                model_provider=backend.provider,
                model_id=backend.model_id,
                model_version=backend.model_version,
                model_config_sha256=backend.model_config_sha256,
            )
            request = BenchmarkRequest(identity, protocol.workload_context, repetition)
            record = backend.execute(request)
            if not isinstance(record, ParsingEvaluationRecord):
                raise ValueError("benchmark backend must return ParsingEvaluationRecord")
            if record.identity != identity:
                raise ValueError("benchmark backend returned a record whose identity/model/version does not match the issued request")
            if record.target != protocol.target:
                raise ValueError("benchmark backend returned a different evaluation target")
            _validate_complete_column(record)
            if reference is not None:
                _validate_fixed_inputs(reference, record)
            else:
                reference = record
            arm_records.append(record)
            all_runs.append(BenchmarkRun(repetition, record))
        summaries.append(_summarize_arm(backend, tuple(arm_records)))

    return BenchmarkReport(protocol.workload, protocol.target, tuple(all_runs), tuple(summaries))
