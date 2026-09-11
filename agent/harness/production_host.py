"""Trusted durable production composition for the development controller.

The pure HARN-010 controller deliberately accepts an abstract persistence callback.
This module supplies the concrete durable host boundary. HARN-023 remains the sole
GitHub mutation authority; this module must never grow a second raw write path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import errno
from hashlib import sha256
from hmac import compare_digest
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .contracts import RunPhase, TaskSpec
from .development_controller import (
    BoundedDevelopmentController,
    ControllerStopCode,
    DevelopmentControllerPolicy,
    DevelopmentControllerPorts,
    DevelopmentControllerState,
)
from .development_reviewer import IndependentReviewer
from .github_effects import (
    GitHubEffectGateway,
    GitHubTaskPolicy,
    HumanApproval,
    HumanApprovalAuthority,
)
from .state_machine import ResumeRequested, apply_event


_HOST_SCHEMA = 1
_HOST_PAYLOAD_FIELDS = (
    "schema_version",
    "controller_state",
    "trusted_approvals",
)
_HOST_FIELDS = frozenset((*_HOST_PAYLOAD_FIELDS, "payload_sha256"))
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


def _host_payload_sha256(payload: Mapping[str, object]) -> str:
    """Checksum the complete serialized host payload, excluding the checksum itself.

    This detects accidental corruption/truncation of durable state. It is not an
    authentication mechanism; the filesystem remains a trusted host boundary.
    """

    encoded = json.dumps(
        {field: payload[field] for field in _HOST_PAYLOAD_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


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

    def _payload_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "controller_state": (
                None if self.controller_state is None else self.controller_state.to_dict()
            ),
            "trusted_approvals": [item.to_dict() for item in self.trusted_approvals],
        }

    def to_dict(self) -> dict[str, object]:
        payload = self._payload_dict()
        return {
            **payload,
            "payload_sha256": _host_payload_sha256(payload),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProductionHostEnvelope":
        if not isinstance(payload, Mapping):
            raise ValueError("production host envelope must be an object")
        supplied = set(payload)
        missing = _HOST_FIELDS - supplied
        if missing:
            raise ValueError(
                "production host envelope missing required fields: "
                + ", ".join(sorted(missing))
            )
        extra = supplied - _HOST_FIELDS
        if extra:
            raise ValueError(
                "production host envelope contains unknown fields: "
                + ", ".join(sorted(extra))
            )
        if isinstance(payload["schema_version"], bool) or payload["schema_version"] != _HOST_SCHEMA:
            raise ValueError(f"host schema_version must be {_HOST_SCHEMA}")

        raw_state = payload["controller_state"]
        raw_approvals = payload["trusted_approvals"]
        if isinstance(raw_approvals, (str, bytes, Mapping)):
            raise ValueError("trusted_approvals must be an array")
        try:
            approval_items = tuple(raw_approvals)
        except TypeError as exc:
            raise ValueError("trusted_approvals must be an array") from exc

        # Parse semantic contracts before the checksum so malformed values retain
        # precise validation errors. The checksum then catches otherwise-valid
        # truncation where permissive nested readers would have supplied defaults.
        state = (
            None
            if raw_state is None
            else DevelopmentControllerState.from_dict(raw_state)
        )
        approvals = tuple(HumanApproval.from_dict(item) for item in approval_items)
        _approval_sequence(approvals)

        supplied_digest = payload["payload_sha256"]
        if (
            not isinstance(supplied_digest, str)
            or len(supplied_digest) != 64
            or any(character not in "0123456789abcdef" for character in supplied_digest)
        ):
            raise ValueError("production host envelope payload_sha256 must be a SHA-256 digest")
        expected_digest = _host_payload_sha256(payload)
        if not compare_digest(supplied_digest, expected_digest):
            raise ValueError(
                "production host envelope integrity digest mismatch; state may be corrupt or truncated"
            )

        return cls(
            schema_version=payload["schema_version"],
            controller_state=state,
            trusted_approvals=approvals,
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


class ProductionDevelopmentHost:
    """Trusted durable runtime for HARN-010 using the canonical HARN-023 gateway.

    Privileged dependencies are deliberately private. Model-facing controller ports
    receive no store, approval-registration, reconciliation, or raw adapter handle.
    """

    __slots__ = (
        "__store",
        "__envelope",
        "__approval_authority",
        "__gateway",
        "__controller",
    )

    def __init__(
        self,
        *,
        store: AtomicHostStateStore,
        policy: DevelopmentControllerPolicy,
        ports: DevelopmentControllerPorts,
        reviewer: IndependentReviewer,
        github_policy: GitHubTaskPolicy,
        github_adapter: object,
        github_reconciler: object,
        policy_refs: tuple[str, ...],
        review_rubric: tuple[str, ...],
        implementer_id: str,
    ) -> None:
        if not isinstance(store, AtomicHostStateStore):
            raise ValueError("production host requires an explicit durable AtomicHostStateStore")
        if not isinstance(github_policy, GitHubTaskPolicy):
            raise ValueError("github_policy must be GitHubTaskPolicy")
        if not callable(getattr(github_reconciler, "reconcile", None)):
            raise ValueError("production host requires a trusted GitHub reconciler")

        self.__store = store
        loaded = store.load()
        self.__envelope = loaded or ProductionHostEnvelope(_HOST_SCHEMA)

        authority = HumanApprovalAuthority()
        for approval in self.__envelope.trusted_approvals:
            authority.register(approval)
        self.__approval_authority = authority

        self.__gateway = GitHubEffectGateway(
            github_policy,
            github_adapter,  # type: ignore[arg-type]
            approval_authority=authority,
            reconciler=github_reconciler,  # type: ignore[arg-type]
        )
        self.__controller = BoundedDevelopmentController(
            policy=policy,
            ports=ports,
            reviewer=reviewer,
            github_gateway=self.__gateway,
            persist=self.__persist_controller_payload,
            policy_refs=policy_refs,
            review_rubric=review_rubric,
            implementer_id=implementer_id,
        )

    @property
    def state(self) -> DevelopmentControllerState | None:
        return self.__envelope.controller_state

    def __persist_envelope(self, envelope: ProductionHostEnvelope) -> None:
        self.__store.save(envelope)
        self.__envelope = envelope

    def __persist_controller_payload(self, payload: dict[str, object]) -> None:
        state = DevelopmentControllerState.from_dict(payload)
        self.__persist_envelope(
            ProductionHostEnvelope(
                _HOST_SCHEMA,
                state,
                self.__envelope.trusted_approvals,
            )
        )

    def __require_state(self) -> DevelopmentControllerState:
        state = self.__envelope.controller_state
        if state is None:
            raise ValueError("production host has no controller state; start a run first")
        return state

    @staticmethod
    def __pending_request(state: DevelopmentControllerState):
        pending = state.pending_implementation
        if pending is None or state.pending_operation_index >= len(pending.github_operations):
            raise ValueError("controller state has no pending GitHub request")
        return pending.github_operations[state.pending_operation_index]

    def start(
        self,
        *,
        run_id: str,
        task: TaskSpec,
        base_sha: str,
        provenance_refs: tuple[str, ...] = (),
    ) -> DevelopmentControllerState:
        if self.state is not None:
            raise ValueError("production host already contains controller state")
        return self.__controller.start(
            run_id=run_id,
            task=task,
            base_sha=base_sha,
            provenance_refs=provenance_refs,
        )

    def step(self) -> DevelopmentControllerState:
        return self.__controller.step(self.__require_state())

    def run_until_stop(self, *, max_steps: int | None = None) -> DevelopmentControllerState:
        return self.__controller.run_until_stop(
            self.__require_state(),
            max_steps=max_steps,
        )

    def __persist_trusted_approval(self, approval: HumanApproval) -> None:
        if not isinstance(approval, HumanApproval):
            raise ValueError("approval must be HumanApproval")
        state = self.__require_state()
        request = self.__pending_request(state)
        if approval.operation_id != request.operation_id:
            raise ValueError("approval operation does not match pending GitHub request")
        if approval.request_sha256 != request.request_sha256:
            raise ValueError("approval digest does not match pending GitHub request")
        journal_approval = state.github_journal.approval_for(request.operation_id)
        if journal_approval is not None and journal_approval != approval:
            raise ValueError(
                "pending operation journal contains a different approval; refusing to create trusted authority"
            )

        approvals = self.__envelope.trusted_approvals
        existing_by_id = next(
            (item for item in approvals if item.approval_id == approval.approval_id),
            None,
        )
        existing_by_operation = next(
            (item for item in approvals if item.operation_id == approval.operation_id),
            None,
        )
        if existing_by_id is not None and existing_by_id != approval:
            raise ValueError("trusted approval ID cannot be rebound")
        if existing_by_operation is not None and existing_by_operation != approval:
            raise ValueError("pending operation already has a different trusted approval")

        if existing_by_id is None:
            durable = ProductionHostEnvelope(
                _HOST_SCHEMA,
                state,
                approvals + (approval,),
            )
            # The approval becomes live authority only after durable persistence succeeds.
            self.__persist_envelope(durable)
        self.__approval_authority.register(approval)

    def resume(self, *, approval: HumanApproval | None = None) -> DevelopmentControllerState:
        state = self.__require_state()
        if approval is not None:
            if (
                state.core.phase is not RunPhase.AWAITING_HUMAN
                or state.stop_code is not ControllerStopCode.NEEDS_HUMAN
            ):
                raise ValueError(
                    "human approval may only be persisted while controller is awaiting human resume"
                )
            self.__persist_trusted_approval(approval)
            state = self.__require_state()
        return self.__controller.resume(state, approval=approval)

    def __resume_after_reconciliation(
        self,
        state: DevelopmentControllerState,
    ) -> DevelopmentControllerState:
        if state.core.phase is not RunPhase.BLOCKED:
            raise ValueError("trusted reconciliation recovery requires BLOCKED controller phase")
        if state.stop_code is not ControllerStopCode.BLOCKED_EXECUTION:
            raise ValueError(
                "trusted reconciliation may only recover a blocked-execution controller state"
            )
        request = self.__pending_request(state)
        if state.github_journal.uncertain_for(request.operation_id) is not None:
            raise ValueError("trusted reconciliation did not resolve pending uncertainty")

        core = apply_event(state.core, ResumeRequested())
        resumed = replace(
            state,
            core=core,
            stop_code=None,
            stop_reason=None,
            audit_events=state.audit_events + (
                f"trusted-reconciliation:{request.operation_id}",
            ),
        )
        self.__persist_controller_payload(resumed.to_dict())
        return resumed

    def reconcile_uncertain(self) -> DevelopmentControllerState:
        state = self.__require_state()
        if state.core.phase is not RunPhase.BLOCKED:
            raise ValueError("reconciliation requires BLOCKED controller state")
        if state.stop_code is not ControllerStopCode.BLOCKED_EXECUTION:
            raise ValueError("reconciliation requires a blocked-execution stop reason")
        request = self.__pending_request(state)
        if state.github_journal.uncertain_for(request.operation_id) is None:
            raise ValueError("pending GitHub request is not quarantined as uncertain")

        def checkpoint(journal) -> None:
            checkpoint_state = replace(state, github_journal=journal)
            self.__persist_controller_payload(checkpoint_state.to_dict())

        journal, _receipt = self.__gateway.reconcile_uncertain(
            request,
            state.github_journal,
            checkpoint=checkpoint,
        )
        durable = self.__require_state()
        if durable.github_journal != journal:
            raise RuntimeError("durable reconciliation checkpoint does not match gateway result")
        return self.__resume_after_reconciliation(durable)
