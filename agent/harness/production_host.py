"""Trusted durable production composition for the development controller.

The pure HARN-010 controller deliberately accepts an abstract persistence callback.
This module supplies the concrete durable host boundary.  HARN-023 remains the sole
GitHub mutation authority; this module must never grow a second raw write path.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .development_controller import DevelopmentControllerState
from .github_effects import HumanApproval


_HOST_SCHEMA = 1
_UNSUPPORTED_DIRECTORY_FSYNC = frozenset(
    value
    for value in (
        getattr(errno, "EINVAL", None),
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "EBADF", None),
        getattr(errno, "ENOSYS", None),
    )
    if value is not None
)


def _approval_sequence(value: object) -> tuple[HumanApproval, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError("trusted_approvals must be an array of HumanApproval values")
    try:
        approvals = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("trusted_approvals must be an array of HumanApproval values") from exc
    if any(not isinstance(item, HumanApproval) for item in approvals):
        raise ValueError("trusted_approvals must contain only HumanApproval values")

    approval_ids = tuple(item.approval_id for item in approvals)
    operation_ids = tuple(item.operation_id for item in approvals)
    if len(approval_ids) != len(set(approval_ids)):
        raise ValueError("trusted approval IDs must be unique")
    if len(operation_ids) != len(set(operation_ids)):
        raise ValueError("trusted approval operation IDs must be unique")
    return approvals


@dataclass(frozen=True)
class ProductionHostEnvelope:
    """One atomic persistence unit for controller state and trusted host authority."""

    schema_version: int
    controller_state: DevelopmentControllerState | None = None
    trusted_approvals: tuple[HumanApproval, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != _HOST_SCHEMA:
            raise ValueError(f"host schema_version must be {_HOST_SCHEMA}")
        if self.controller_state is not None and not isinstance(
            self.controller_state, DevelopmentControllerState
        ):
            raise ValueError("controller_state must be DevelopmentControllerState or None")
        object.__setattr__(
            self,
            "trusted_approvals",
            _approval_sequence(self.trusted_approvals),
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
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProductionHostEnvelope":
        if not isinstance(payload, Mapping):
            raise ValueError("production host envelope must be an object")
        if "schema_version" not in payload:
            raise ValueError("production host envelope schema_version is required")
        raw_state = payload.get("controller_state")
        raw_approvals = payload.get("trusted_approvals", ())
        if isinstance(raw_approvals, (str, bytes, Mapping)):
            raise ValueError("trusted_approvals must be an array")
        try:
            approval_items = tuple(raw_approvals)
        except TypeError as exc:
            raise ValueError("trusted_approvals must be an array") from exc
        return cls(
            schema_version=payload["schema_version"],
            controller_state=(
                None
                if raw_state is None
                else DevelopmentControllerState.from_dict(raw_state)
            ),
            trusted_approvals=tuple(
                HumanApproval.from_dict(item) for item in approval_items
            ),
        )


class AtomicHostStateStore:
    """Small atomic JSON store for the production host envelope.

    A successful ``save`` means the new file has been flushed, fsynced and atomically
    replaced, followed by a parent-directory fsync on platforms that support it.
    """

    __slots__ = ("_path",)

    def __init__(self, path: str | os.PathLike[str]) -> None:
        if isinstance(path, str) and not path.strip():
            raise ValueError("state path must be an explicit non-empty file path")
        try:
            normalized = Path(path)
        except TypeError as exc:
            raise ValueError("state path must be an explicit filesystem path") from exc
        if not normalized.name:
            raise ValueError("state path must name a file")
        self._path = normalized

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ProductionHostEnvelope | None:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        payload = json.loads(text)
        if not isinstance(payload, Mapping):
            raise ValueError("production host state file must contain a JSON object")
        return ProductionHostEnvelope.from_dict(payload)

    @staticmethod
    def _fsync_parent_directory(path: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            if exc.errno in _UNSUPPORTED_DIRECTORY_FSYNC:
                return
            raise
        try:
            try:
                os.fsync(fd)
            except OSError as exc:
                if exc.errno not in _UNSUPPORTED_DIRECTORY_FSYNC:
                    raise
        finally:
            os.close(fd)

    def save(self, envelope: ProductionHostEnvelope) -> None:
        if not isinstance(envelope, ProductionHostEnvelope):
            raise ValueError("envelope must be ProductionHostEnvelope")

        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(
                envelope.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp_path = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(tmp_path, self._path)
            tmp_path = None
            self._fsync_parent_directory(parent)
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
