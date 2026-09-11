"""Trusted durable host primitives for the bounded development controller.

This module deliberately keeps durability outside ``development_controller`` and
GitHub authorization outside this module.  HARN-010 remains the controller state
machine; HARN-023 ``GitHubEffectGateway`` remains the sole mutation authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .development_controller import DevelopmentControllerState
from .github_effects import HumanApproval


_HOST_SCHEMA_VERSION = 1
_HOST_FIELDS = frozenset({"schema_version", "controller_state", "trusted_approvals"})


def _approval_tuple(value: object) -> tuple[HumanApproval, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError("trusted_approvals must be an iterable of HumanApproval")
    try:
        approvals = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("trusted_approvals must be an iterable of HumanApproval") from exc
    if any(not isinstance(item, HumanApproval) for item in approvals):
        raise ValueError("trusted_approvals must contain only HumanApproval")
    approval_ids = tuple(item.approval_id for item in approvals)
    if len(approval_ids) != len(set(approval_ids)):
        raise ValueError("trusted approval IDs must be unique")
    return approvals


@dataclass(frozen=True)
class DevelopmentHostEnvelope:
    """One atomically persisted trusted-host snapshot."""

    schema_version: int
    controller_state: DevelopmentControllerState | None = None
    trusted_approvals: tuple[HumanApproval, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != _HOST_SCHEMA_VERSION:
            raise ValueError(f"host envelope schema_version must be {_HOST_SCHEMA_VERSION}")
        if self.controller_state is not None and not isinstance(
            self.controller_state, DevelopmentControllerState
        ):
            raise ValueError(
                "controller_state must be DevelopmentControllerState or None"
            )
        object.__setattr__(
            self,
            "trusted_approvals",
            _approval_tuple(self.trusted_approvals),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "controller_state": (
                None if self.controller_state is None else self.controller_state.to_dict()
            ),
            "trusted_approvals": [item.to_dict() for item in self.trusted_approvals],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DevelopmentHostEnvelope":
        if not isinstance(payload, Mapping):
            raise ValueError("development host envelope must be a JSON object")
        fields = set(payload)
        missing = _HOST_FIELDS - fields
        unknown = fields - _HOST_FIELDS
        if missing or unknown:
            details: list[str] = []
            if missing:
                details.append(f"missing fields: {sorted(missing)}")
            if unknown:
                details.append(f"unknown fields: {sorted(unknown)}")
            raise ValueError("development host envelope fields invalid: " + "; ".join(details))

        raw_state = payload["controller_state"]
        if raw_state is None:
            controller_state = None
        else:
            if not isinstance(raw_state, Mapping):
                raise ValueError("controller_state must be an object or null")
            controller_state = DevelopmentControllerState.from_dict(raw_state)

        raw_approvals = payload["trusted_approvals"]
        if isinstance(raw_approvals, (str, bytes, Mapping)):
            raise ValueError("trusted_approvals must be an array")
        try:
            approval_values = tuple(raw_approvals)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError("trusted_approvals must be an array") from exc
        approvals = tuple(
            item if isinstance(item, HumanApproval) else HumanApproval.from_dict(item)
            for item in approval_values
        )
        return cls(payload["schema_version"], controller_state, approvals)

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, payload: str) -> "DevelopmentHostEnvelope":
        if not isinstance(payload, str):
            raise ValueError("development host envelope JSON must be a string")
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("development host state is not valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise ValueError("development host envelope must be a JSON object")
        return cls.from_dict(decoded)


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory fsync after replace where the platform supports it.

    File fsync and atomic replacement are mandatory. Some supported platforms do
    not permit opening/fsyncing directories; that capability gap must not cause a
    fallback to an unsafe direct overwrite.
    """

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(directory, flags)
    except OSError:
        return
    try:
        try:
            os.fsync(directory_fd)
        except OSError:
            return
    finally:
        os.close(directory_fd)


class AtomicJsonDevelopmentHostStore:
    """Single-file atomic store for :class:`DevelopmentHostEnvelope`.

    A missing path means no host has been initialized yet. An existing malformed
    file is never treated as an empty host.
    """

    def __init__(self, state_path: str | os.PathLike[str]) -> None:
        if state_path is None:  # type: ignore[comparison-overlap]
            raise ValueError("development host state path is required")
        try:
            path = Path(state_path)
        except TypeError as exc:
            raise ValueError("development host state path is required") from exc
        if not str(path).strip():
            raise ValueError("development host state path is required")
        self._state_path = path

    @property
    def state_path(self) -> Path:
        return self._state_path

    def load(self) -> DevelopmentHostEnvelope | None:
        try:
            payload = self._state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError as exc:
            raise ValueError("development host state is not valid UTF-8") from exc
        try:
            return DevelopmentHostEnvelope.from_json(payload)
        except ValueError as exc:
            raise ValueError(f"invalid development host state envelope: {exc}") from exc

    def save(self, envelope: DevelopmentHostEnvelope) -> None:
        if not isinstance(envelope, DevelopmentHostEnvelope):
            raise ValueError("envelope must be DevelopmentHostEnvelope")

        # Serialize/validate before touching the previous durable snapshot.
        payload = envelope.to_json().encode("utf-8")
        destination = self._state_path
        parent = destination.parent
        parent.mkdir(parents=True, exist_ok=True)

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=parent,
        )
        temporary = Path(temporary_name)
        replaced = False
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())

            os.replace(temporary, destination)
            replaced = True
            _fsync_directory(parent)
        finally:
            if not replaced:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
