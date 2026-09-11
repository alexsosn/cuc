"""Trusted durable host for the bounded development controller.

Durability stays outside ``development_controller`` and GitHub authorization stays
inside HARN-023 ``GitHubEffectGateway``.  This module composes those existing
authorities; it does not create another side-effect journal or executor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import errno
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
    GitHubEffectJournal,
    GitHubTaskPolicy,
    HumanApproval,
    HumanApprovalAuthority,
)
from .state_machine import ResumeRequested, apply_event


_HOST_SCHEMA_VERSION = 1
_HOST_FIELDS = frozenset({"schema_version", "controller_state", "trusted_approvals"})
_DIRECTORY_FSYNC_UNSUPPORTED = frozenset(
    {
        errno.EINVAL,
        errno.ENOTSUP,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }
)


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
    """Fsync a replaced file's directory, ignoring only unsupported operations.

    Platforms without ``O_DIRECTORY`` do not expose the POSIX directory-descriptor
    primitive needed here. On platforms that do expose it, genuine filesystem
    failures such as EIO propagate; only explicit not-supported errors are tolerated.
    """

    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    flags = os.O_RDONLY | directory_flag
    try:
        directory_fd = os.open(directory, flags)
    except OSError as exc:
        if exc.errno in _DIRECTORY_FSYNC_UNSUPPORTED:
            return
        raise
    try:
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            if exc.errno in _DIRECTORY_FSYNC_UNSUPPORTED:
                return
            raise
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


class TrustedDevelopmentHost:
    """Production composition boundary for durable HARN-010 execution.

    Privileged dependencies are intentionally private. Public methods expose only
    controller lifecycle operations and the narrowly authorized recovery actions
    needed after restart.
    """

    def __init__(
        self,
        durable_store: AtomicJsonDevelopmentHostStore,
        *,
        controller_policy: DevelopmentControllerPolicy,
        controller_ports: DevelopmentControllerPorts,
        reviewer: IndependentReviewer,
        github_policy: GitHubTaskPolicy,
        github_adapter: object,
        github_reconciler: object,
        policy_refs: tuple[str, ...],
        review_rubric: tuple[str, ...],
        implementer_id: str,
    ) -> None:
        if not isinstance(durable_store, AtomicJsonDevelopmentHostStore):
            raise ValueError("an explicit durable AtomicJsonDevelopmentHostStore is required")

        loaded = durable_store.load()
        envelope = loaded or DevelopmentHostEnvelope(_HOST_SCHEMA_VERSION, None, ())
        authority = HumanApprovalAuthority()
        for approval in envelope.trusted_approvals:
            authority.register(approval)

        gateway = GitHubEffectGateway(
            github_policy,
            github_adapter,
            approval_authority=authority,
            reconciler=github_reconciler,
        )

        self._store = durable_store
        self._envelope = envelope
        self._authority = authority
        self._gateway = gateway
        self._controller = BoundedDevelopmentController(
            policy=controller_policy,
            ports=controller_ports,
            reviewer=reviewer,
            github_gateway=gateway,
            persist=self._persist_controller_payload,
            policy_refs=policy_refs,
            review_rubric=review_rubric,
            implementer_id=implementer_id,
        )

    @property
    def envelope(self) -> DevelopmentHostEnvelope:
        return self._envelope

    @property
    def state(self) -> DevelopmentControllerState | None:
        return self._envelope.controller_state

    def _save_envelope(self, candidate: DevelopmentHostEnvelope) -> None:
        self._store.save(candidate)
        self._envelope = candidate

    def _persist_controller_payload(self, payload: dict[str, object]) -> None:
        # Re-enter the canonical state validator before caller-provided serialized
        # data becomes trusted durable state.
        validated = DevelopmentControllerState.from_dict(payload)
        self._save_envelope(replace(self._envelope, controller_state=validated))

    def _persist_controller_state(self, state: DevelopmentControllerState) -> None:
        if not isinstance(state, DevelopmentControllerState):
            raise ValueError("state must be DevelopmentControllerState")
        self._persist_controller_payload(state.to_dict())

    def start(
        self,
        *,
        run_id: str,
        task: TaskSpec,
        base_sha: str,
        provenance_refs: tuple[str, ...] = (),
    ) -> DevelopmentControllerState:
        if self.state is not None:
            raise ValueError("durable development host already contains controller state")
        return self._controller.start(
            run_id=run_id,
            task=task,
            base_sha=base_sha,
            provenance_refs=provenance_refs,
        )

    def step(self) -> DevelopmentControllerState:
        state = self.state
        if state is None:
            raise ValueError("durable development host has no controller state")
        return self._controller.step(state)

    def run(self, *, max_steps: int | None = None) -> DevelopmentControllerState:
        state = self.state
        if state is None:
            raise ValueError("durable development host has no controller state")
        return self._controller.run_until_stop(state, max_steps=max_steps)

    def _pending_request(self):
        state = self.state
        if state is None:
            raise ValueError("durable development host has no controller state")
        pending = state.pending_implementation
        if pending is None or state.pending_operation_index >= len(pending.github_operations):
            raise ValueError("controller state has no pending GitHub request")
        return state, pending.github_operations[state.pending_operation_index]

    def resume_with_approval(self, approval: HumanApproval) -> DevelopmentControllerState:
        if not isinstance(approval, HumanApproval):
            raise ValueError("approval must be HumanApproval")
        state, request = self._pending_request()
        if state.core.phase is not RunPhase.AWAITING_HUMAN:
            raise ValueError("human approval can resume only AWAITING_HUMAN state")
        if approval.operation_id != request.operation_id:
            raise ValueError("approval operation does not match pending GitHub request")
        if approval.request_sha256 != request.request_sha256:
            raise ValueError("approval digest does not match pending GitHub request")

        existing = next(
            (
                item
                for item in self._envelope.trusted_approvals
                if item.approval_id == approval.approval_id
            ),
            None,
        )
        if existing is not None and existing != approval:
            raise ValueError("trusted approval ID already binds different approval content")

        if existing is None:
            # Durable authority first: a crash after this save is safe because a new
            # process restores the authority from the envelope. The live authority
            # cannot authorize anything unless this persistence succeeds.
            candidate = replace(
                self._envelope,
                trusted_approvals=self._envelope.trusted_approvals + (approval,),
            )
            self._save_envelope(candidate)
            self._authority.register(approval)

        return self._controller.resume(self.state, approval=approval)  # type: ignore[arg-type]

    def reconcile_uncertain(self) -> DevelopmentControllerState:
        state, request = self._pending_request()
        if state.core.phase is not RunPhase.BLOCKED:
            raise ValueError("trusted reconciliation requires BLOCKED controller state")
        if state.stop_code is not ControllerStopCode.BLOCKED_EXECUTION:
            raise ValueError("only blocked-execution state is eligible for trusted reconciliation")
        if state.github_journal.uncertain_for(request.operation_id) is None:
            raise ValueError("pending request is not quarantined as an uncertain GitHub effect")

        # The gateway calls this only after the trusted reconciler has proven either
        # EXECUTED or NOT_EXECUTED. Persist journal resolution and controller resume
        # as one host-envelope transition so no restart can observe a resolved journal
        # paired with an unresumable BLOCKED controller.
        def checkpoint(journal: GitHubEffectJournal) -> None:
            current = self.state
            if current is None:
                raise ValueError("durable development host lost controller state")
            resumed_core = apply_event(current.core, ResumeRequested())
            resolved = replace(
                current,
                core=resumed_core,
                github_journal=journal,
                stop_code=None,
                stop_reason=None,
                audit_events=current.audit_events
                + (f"github:{request.operation_id}:reconciled",),
            )
            self._persist_controller_state(resolved)

        self._gateway.reconcile_uncertain(
            request,
            state.github_journal,
            checkpoint=checkpoint,
        )

        current = self.state
        if current is None:
            raise ValueError("durable development host lost controller state")
        if current.github_journal.uncertain_for(request.operation_id) is not None:
            raise RuntimeError("trusted reconciliation returned without durable resolution")
        if current.core.phase is RunPhase.BLOCKED or current.stop_code is not None:
            raise RuntimeError("trusted reconciliation returned without durable controller resume")
        return current
