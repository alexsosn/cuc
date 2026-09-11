"""Framework-neutral decision evidence for the HARN-007 Deep Agents evaluation.

This module deliberately contains no Deep Agents/LangGraph runtime types.  It records
what an executable comparison observed and prevents an ADR from claiming primary
semantic equivalence unless the fixed complete-column workload completed successfully.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


_SCHEMA_VERSION = 1
_FIELDS = {
    "schema_version",
    "baseline_revision",
    "deepagents_package",
    "deepagents_version",
    "required_token_ids",
    "observed_token_ids",
    "completed_normally",
    "equivalence_criteria",
    "observations",
    "disposition",
    "selected_components",
    "rationale",
    "evidence_refs",
}


class DeepAgentsDisposition(str, Enum):
    ADOPT_PRIMARY = "adopt-primary"
    ADOPT_SELECTED_COMPONENTS = "adopt-selected-components"
    KEEP_LANGGRAPH = "keep-langgraph"
    REJECT = "reject"


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _text_tuple(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, Mapping)):
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


def _exact_keys(payload: Mapping[str, Any]) -> None:
    actual = set(payload)
    missing = _FIELDS - actual
    extra = actual - _FIELDS
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {sorted(missing)}")
        if extra:
            details.append(f"unknown fields: {sorted(extra)}")
        raise ValueError("invalid DeepAgentsDecisionRecord: " + "; ".join(details))


@dataclass(frozen=True)
class DeepAgentsDecisionRecord:
    """Auditable HARN-007 comparison result bound to one fixed workload."""

    schema_version: int
    baseline_revision: str
    deepagents_package: str
    deepagents_version: str
    required_token_ids: tuple[str, ...]
    observed_token_ids: tuple[str, ...]
    completed_normally: bool
    equivalence_criteria: tuple[str, ...]
    observations: tuple[str, ...]
    disposition: DeepAgentsDisposition
    selected_components: tuple[str, ...]
    rationale: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {_SCHEMA_VERSION}")
        if not isinstance(self.completed_normally, bool):
            raise ValueError("completed_normally must be boolean")

        required = _text_tuple(self.required_token_ids, "required_token_ids", required=True)
        observed = _text_tuple(self.observed_token_ids, "observed_token_ids")
        unknown = set(observed) - set(required)
        if unknown:
            raise ValueError(
                "observed_token_ids must belong to the fixed required workload: "
                + ", ".join(sorted(unknown))
            )
        # Observations must respect the fixed textual order. A framework that
        # visits all tokens in a different order is also not equivalent to the
        # review-automatic-parsing contract.
        positions = {token_id: index for index, token_id in enumerate(required)}
        if tuple(sorted(observed, key=positions.__getitem__)) != observed:
            raise ValueError("observed_token_ids must preserve required textual order")

        try:
            disposition = (
                self.disposition
                if isinstance(self.disposition, DeepAgentsDisposition)
                else DeepAgentsDisposition(self.disposition)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid disposition: {self.disposition!r}") from exc

        selected = _text_tuple(self.selected_components, "selected_components")
        if disposition is DeepAgentsDisposition.ADOPT_SELECTED_COMPONENTS and not selected:
            raise ValueError("selected_components must not be empty for selected-component adoption")

        object.__setattr__(self, "baseline_revision", _required_text(self.baseline_revision, "baseline_revision"))
        object.__setattr__(self, "deepagents_package", _required_text(self.deepagents_package, "deepagents_package"))
        object.__setattr__(self, "deepagents_version", _required_text(self.deepagents_version, "deepagents_version"))
        object.__setattr__(self, "required_token_ids", required)
        object.__setattr__(self, "observed_token_ids", observed)
        object.__setattr__(
            self,
            "equivalence_criteria",
            _text_tuple(self.equivalence_criteria, "equivalence_criteria", required=True),
        )
        object.__setattr__(
            self,
            "observations",
            _text_tuple(self.observations, "observations", required=True),
        )
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "selected_components", selected)
        object.__setattr__(self, "rationale", _required_text(self.rationale, "rationale"))
        object.__setattr__(
            self,
            "evidence_refs",
            _text_tuple(self.evidence_refs, "evidence_refs", required=True),
        )

        if disposition is DeepAgentsDisposition.ADOPT_PRIMARY and (
            not self.completed_normally or self.observed_token_ids != self.required_token_ids
        ):
            raise ValueError(
                "primary adoption requires a successful equivalent probe that completes "
                "normally after every required token in textual order"
            )

    @property
    def early_termination_observed(self) -> bool:
        """True when the framework returned normally before the fixed workload completed."""

        return self.completed_normally and self.observed_token_ids != self.required_token_ids

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "baseline_revision": self.baseline_revision,
            "deepagents_package": self.deepagents_package,
            "deepagents_version": self.deepagents_version,
            "required_token_ids": list(self.required_token_ids),
            "observed_token_ids": list(self.observed_token_ids),
            "completed_normally": self.completed_normally,
            "equivalence_criteria": list(self.equivalence_criteria),
            "observations": list(self.observations),
            "disposition": self.disposition.value,
            "selected_components": list(self.selected_components),
            "rationale": self.rationale,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DeepAgentsDecisionRecord":
        if not isinstance(payload, Mapping):
            raise ValueError("DeepAgentsDecisionRecord payload must be a mapping")
        _exact_keys(payload)
        return cls(
            schema_version=payload["schema_version"],
            baseline_revision=payload["baseline_revision"],
            deepagents_package=payload["deepagents_package"],
            deepagents_version=payload["deepagents_version"],
            required_token_ids=payload["required_token_ids"],
            observed_token_ids=payload["observed_token_ids"],
            completed_normally=payload["completed_normally"],
            equivalence_criteria=payload["equivalence_criteria"],
            observations=payload["observations"],
            disposition=payload["disposition"],
            selected_components=payload["selected_components"],
            rationale=payload["rationale"],
            evidence_refs=payload["evidence_refs"],
        )
