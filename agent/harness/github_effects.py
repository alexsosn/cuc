"""Framework-neutral authorization boundary for development-controller GitHub effects.

This module deliberately performs no network I/O.  A caller supplies a narrow adapter;
all repository classification, task-policy checks, human approval and replay handling
happen before that adapter is invoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Protocol


_FORK = "alexsosn/cuc"
_UPSTREAM = "DT-UCPH/cuc"
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RepositoryClass(str, Enum):
    FORK = "fork"
    UPSTREAM = "upstream"
    UNKNOWN = "unknown"


class GitHubAction(str, Enum):
    """Closed action vocabulary exposed to development-controller helpers."""

    READ = "read"
    CREATE_BRANCH = "create-branch"
    UPDATE_REF = "update-ref"
    UPDATE_CONTENTS = "update-contents"
    DELETE_CONTENTS = "delete-contents"
    CREATE_ISSUE = "create-issue"
    UPDATE_ISSUE = "update-issue"
    COMMENT = "comment"
    CREATE_PULL_REQUEST = "create-pull-request"
    UPDATE_PULL_REQUEST = "update-pull-request"
    MERGE_PULL_REQUEST = "merge-pull-request"
    REQUEST_REVIEW = "request-review"
    DISPATCH_WORKFLOW = "dispatch-workflow"


_WRITE_ACTIONS = frozenset(action for action in GitHubAction if action is not GitHubAction.READ)


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _digest(value: object, field: str) -> str:
    text = _required_text(value, field).lower()
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{field} must be a SHA-256 digest")
    return text


def _action(value: object, field: str = "action") -> GitHubAction:
    if isinstance(value, GitHubAction):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a GitHubAction")
    try:
        return GitHubAction(value)
    except ValueError as exc:
        raise ValueError(f"invalid GitHub action: {value!r}") from exc


def _canonical_repository(value: object) -> str:
    text = _required_text(value, "repository")
    if "://" in text or not _REPOSITORY_RE.fullmatch(text):
        raise ValueError("repository must be an owner/repo identity, not a URL or endpoint")
    folded = text.casefold()
    if folded == _FORK.casefold():
        return _FORK
    if folded == _UPSTREAM.casefold():
        return _UPSTREAM
    # GitHub repository identities are case-insensitive.  Canonicalize unknown values so
    # case tricks cannot create multiple policy identities for the same destination.
    owner, repo = text.split("/", 1)
    return f"{owner.casefold()}/{repo.casefold()}"


def classify_repository(value: object) -> RepositoryClass:
    repository = _canonical_repository(value)
    if repository == _FORK:
        return RepositoryClass.FORK
    if repository == _UPSTREAM:
        return RepositoryClass.UPSTREAM
    return RepositoryClass.UNKNOWN


def _json_value(value: object, path: str = "payload") -> object:
    """Validate and copy one JSON value, rejecting ambiguous/non-finite shapes."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path} object keys must be non-empty strings")
            normalized[key] = _json_value(item, f"{path}.{key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [_json_value(item, f"{path}[]") for item in value]
    raise ValueError(f"{path} must contain only JSON-safe values")


def _payload_json(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a JSON object")
    normalized = _json_value(payload)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object_sequence(value: object, field: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError(f"{field} must be an array")
    try:
        return tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an array") from exc


@dataclass(frozen=True)
class GitHubTaskPolicy:
    allowed_fork_write_actions: tuple[GitHubAction, ...] = ()

    def __post_init__(self) -> None:
        raw = _object_sequence(self.allowed_fork_write_actions, "allowed_fork_write_actions")
        actions = tuple(_action(item, "allowed_fork_write_actions") for item in raw)
        if GitHubAction.READ in actions:
            raise ValueError("READ is not a fork write action")
        if len(actions) != len(set(actions)):
            raise ValueError("allowed_fork_write_actions must not contain duplicates")
        object.__setattr__(self, "allowed_fork_write_actions", actions)

    def to_dict(self) -> dict[str, object]:
        return {"allowed_fork_write_actions": [item.value for item in self.allowed_fork_write_actions]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GitHubTaskPolicy":
        if not isinstance(payload, Mapping):
            raise ValueError("GitHubTaskPolicy payload must be an object")
        return cls(payload.get("allowed_fork_write_actions", ()))


@dataclass(frozen=True, init=False)
class GitHubEffectRequest:
    operation_id: str
    repository: str
    action: GitHubAction
    _payload_json: str
    request_sha256: str

    def __init__(
        self,
        operation_id: str,
        repository: str,
        action: GitHubAction | str,
        payload: Mapping[str, Any],
    ) -> None:
        operation = _required_text(operation_id, "operation_id")
        canonical_repository = _canonical_repository(repository)
        normalized_action = _action(action)
        encoded_payload = _payload_json(payload)
        encoded_request = json.dumps(
            {
                "repository": canonical_repository,
                "action": normalized_action.value,
                "payload": json.loads(encoded_payload),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        object.__setattr__(self, "operation_id", operation)
        object.__setattr__(self, "repository", canonical_repository)
        object.__setattr__(self, "action", normalized_action)
        object.__setattr__(self, "_payload_json", encoded_payload)
        object.__setattr__(self, "request_sha256", sha256(encoded_request).hexdigest())

    @property
    def payload(self) -> dict[str, Any]:
        # Return a fresh copy so adapter/caller mutation cannot alter the authenticated
        # request that approvals and receipts are bound to.
        return json.loads(self._payload_json)

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "repository": self.repository,
            "action": self.action.value,
            "payload": self.payload,
            "request_sha256": self.request_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GitHubEffectRequest":
        if not isinstance(payload, Mapping):
            raise ValueError("GitHubEffectRequest payload must be an object")
        request = cls(
            payload["operation_id"],
            payload["repository"],
            payload["action"],
            payload.get("payload", {}),
        )
        supplied = payload.get("request_sha256")
        if supplied is not None and _digest(supplied, "request_sha256") != request.request_sha256:
            raise ValueError("request_sha256 does not match repository/action/payload")
        return request


@dataclass(frozen=True)
class HumanApprovalChallenge:
    operation_id: str
    request_sha256: str
    repository: str
    action: GitHubAction
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _required_text(self.operation_id, "operation_id"))
        object.__setattr__(self, "request_sha256", _digest(self.request_sha256, "request_sha256"))
        object.__setattr__(self, "repository", _canonical_repository(self.repository))
        object.__setattr__(self, "action", _action(self.action))
        object.__setattr__(self, "reason", _required_text(self.reason, "reason"))


@dataclass(frozen=True)
class HumanApproval:
    approval_id: str
    approver_id: str
    operation_id: str
    request_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "approval_id", _required_text(self.approval_id, "approval_id"))
        object.__setattr__(self, "approver_id", _required_text(self.approver_id, "approver_id"))
        object.__setattr__(self, "operation_id", _required_text(self.operation_id, "operation_id"))
        object.__setattr__(self, "request_sha256", _digest(self.request_sha256, "request_sha256"))

    def to_dict(self) -> dict[str, str]:
        return {
            "approval_id": self.approval_id,
            "approver_id": self.approver_id,
            "operation_id": self.operation_id,
            "request_sha256": self.request_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HumanApproval":
        if not isinstance(payload, Mapping):
            raise ValueError("HumanApproval payload must be an object")
        return cls(
            payload["approval_id"],
            payload["approver_id"],
            payload["operation_id"],
            payload["request_sha256"],
        )


@dataclass(frozen=True)
class GitHubEffectReceipt:
    operation_id: str
    request_sha256: str
    repository: str
    action: GitHubAction
    result_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", _required_text(self.operation_id, "operation_id"))
        object.__setattr__(self, "request_sha256", _digest(self.request_sha256, "request_sha256"))
        object.__setattr__(self, "repository", _canonical_repository(self.repository))
        object.__setattr__(self, "action", _action(self.action))
        if self.action is GitHubAction.READ:
            raise ValueError("receipt action must be a write action")
        object.__setattr__(self, "result_ref", _required_text(self.result_ref, "result_ref"))

    def to_dict(self) -> dict[str, str]:
        return {
            "operation_id": self.operation_id,
            "request_sha256": self.request_sha256,
            "repository": self.repository,
            "action": self.action.value,
            "result_ref": self.result_ref,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GitHubEffectReceipt":
        if not isinstance(payload, Mapping):
            raise ValueError("GitHubEffectReceipt payload must be an object")
        return cls(
            payload["operation_id"],
            payload["request_sha256"],
            payload["repository"],
            payload["action"],
            payload["result_ref"],
        )


@dataclass(frozen=True)
class GitHubEffectJournal:
    approvals: tuple[HumanApproval, ...] = ()
    receipts: tuple[GitHubEffectReceipt, ...] = ()

    def __post_init__(self) -> None:
        raw_approvals = _object_sequence(self.approvals, "approvals")
        raw_receipts = _object_sequence(self.receipts, "receipts")
        if any(not isinstance(item, HumanApproval) for item in raw_approvals):
            raise ValueError("approvals must contain HumanApproval values")
        if any(not isinstance(item, GitHubEffectReceipt) for item in raw_receipts):
            raise ValueError("receipts must contain GitHubEffectReceipt values")
        approvals = tuple(raw_approvals)  # type: ignore[assignment]
        receipts = tuple(raw_receipts)  # type: ignore[assignment]

        approval_ids = tuple(item.approval_id for item in approvals)
        approval_operations = tuple(item.operation_id for item in approvals)
        receipt_operations = tuple(item.operation_id for item in receipts)
        if len(approval_ids) != len(set(approval_ids)):
            raise ValueError("approval IDs must be unique")
        if len(approval_operations) != len(set(approval_operations)):
            raise ValueError("an operation may have at most one approval")
        if len(receipt_operations) != len(set(receipt_operations)):
            raise ValueError("receipt operation IDs must be unique")

        object.__setattr__(self, "approvals", approvals)
        object.__setattr__(self, "receipts", receipts)

    def approval_for(self, operation_id: str) -> HumanApproval | None:
        operation_id = _required_text(operation_id, "operation_id")
        return next((item for item in self.approvals if item.operation_id == operation_id), None)

    def receipt_for(self, operation_id: str) -> GitHubEffectReceipt | None:
        operation_id = _required_text(operation_id, "operation_id")
        return next((item for item in self.receipts if item.operation_id == operation_id), None)

    def with_approval(self, approval: HumanApproval) -> "GitHubEffectJournal":
        if not isinstance(approval, HumanApproval):
            raise ValueError("approval must be HumanApproval")
        existing = self.approval_for(approval.operation_id)
        if existing is not None:
            if existing == approval:
                return self
            raise ValueError("operation already has a different approval")
        if any(item.approval_id == approval.approval_id for item in self.approvals):
            raise ValueError("approval ID already exists")
        return GitHubEffectJournal(self.approvals + (approval,), self.receipts)

    def with_receipt(self, receipt: GitHubEffectReceipt) -> "GitHubEffectJournal":
        if not isinstance(receipt, GitHubEffectReceipt):
            raise ValueError("receipt must be GitHubEffectReceipt")
        existing = self.receipt_for(receipt.operation_id)
        if existing is not None:
            if existing == receipt:
                return self
            raise ValueError("operation already has a different receipt")
        return GitHubEffectJournal(self.approvals, self.receipts + (receipt,))

    def to_dict(self) -> dict[str, object]:
        return {
            "approvals": [item.to_dict() for item in self.approvals],
            "receipts": [item.to_dict() for item in self.receipts],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GitHubEffectJournal":
        if not isinstance(payload, Mapping):
            raise ValueError("GitHubEffectJournal payload must be an object")
        approvals = _object_sequence(payload.get("approvals", ()), "approvals")
        receipts = _object_sequence(payload.get("receipts", ()), "receipts")
        return cls(
            tuple(HumanApproval.from_dict(item) for item in approvals),  # type: ignore[arg-type]
            tuple(GitHubEffectReceipt.from_dict(item) for item in receipts),  # type: ignore[arg-type]
        )


class HumanApprovalRequired(RuntimeError):
    """Structured interrupt raised before an upstream write adapter is invoked."""

    def __init__(self, challenge: HumanApprovalChallenge) -> None:
        if not isinstance(challenge, HumanApprovalChallenge):
            raise ValueError("challenge must be HumanApprovalChallenge")
        self.challenge = challenge
        super().__init__(
            f"human approval required for {challenge.action.value} on {challenge.repository}"
        )


class _GitHubWriteAdapter(Protocol):
    def execute(self, request: GitHubEffectRequest) -> str: ...


class GitHubEffectGateway:
    """Fail-closed gateway around a write-capable GitHub adapter."""

    __slots__ = ("_policy", "__adapter")

    def __init__(self, policy: GitHubTaskPolicy, adapter: _GitHubWriteAdapter) -> None:
        if not isinstance(policy, GitHubTaskPolicy):
            raise ValueError("policy must be GitHubTaskPolicy")
        if not callable(getattr(adapter, "execute", None)):
            raise ValueError("adapter must provide execute(request)")
        self._policy = policy
        self.__adapter = adapter

    def authorize_read(self, request: GitHubEffectRequest) -> RepositoryClass:
        if not isinstance(request, GitHubEffectRequest):
            raise ValueError("request must be GitHubEffectRequest")
        if request.action is not GitHubAction.READ:
            raise PermissionError("write action cannot enter the read authorization path")
        repository_class = classify_repository(request.repository)
        if repository_class is RepositoryClass.UNKNOWN:
            raise PermissionError("reads from unknown repositories are outside this task boundary")
        return repository_class

    def _validate_prior_receipt(
        self,
        request: GitHubEffectRequest,
        journal: GitHubEffectJournal,
    ) -> GitHubEffectReceipt | None:
        receipt = journal.receipt_for(request.operation_id)
        if receipt is None:
            return None
        if (
            receipt.request_sha256 != request.request_sha256
            or receipt.repository != request.repository
            or receipt.action is not request.action
        ):
            raise ValueError("operation ID reuse conflicts with the persisted request digest")
        return receipt

    def execute_write(
        self,
        request: GitHubEffectRequest,
        journal: GitHubEffectJournal,
    ) -> tuple[GitHubEffectJournal, GitHubEffectReceipt]:
        if not isinstance(request, GitHubEffectRequest):
            raise ValueError("request must be GitHubEffectRequest")
        if not isinstance(journal, GitHubEffectJournal):
            raise ValueError("journal must be GitHubEffectJournal")
        if request.action not in _WRITE_ACTIONS:
            raise PermissionError("READ is not a write action")

        prior = self._validate_prior_receipt(request, journal)
        if prior is not None:
            return journal, prior

        repository_class = classify_repository(request.repository)
        if repository_class is RepositoryClass.FORK:
            if request.action not in self._policy.allowed_fork_write_actions:
                raise PermissionError(
                    f"fork write action {request.action.value!r} is not allowed by task policy"
                )
        elif repository_class is RepositoryClass.UPSTREAM:
            approval = journal.approval_for(request.operation_id)
            if approval is None:
                raise HumanApprovalRequired(
                    HumanApprovalChallenge(
                        request.operation_id,
                        request.request_sha256,
                        request.repository,
                        request.action,
                        "upstream writes require explicit human authorization at execution time",
                    )
                )
            if approval.request_sha256 != request.request_sha256:
                raise PermissionError(
                    "human approval does not match the exact upstream write request digest"
                )
        else:
            raise PermissionError("writes to an unknown repository are denied")

        # The adapter sees an immutable authenticated request object.  No receipt is
        # persisted until it returns successfully, so a transient failure is retryable.
        result_ref = self.__adapter.execute(request)
        if not isinstance(result_ref, str) or not result_ref.strip():
            raise ValueError("GitHub adapter success must return a non-empty result reference")
        receipt = GitHubEffectReceipt(
            request.operation_id,
            request.request_sha256,
            request.repository,
            request.action,
            result_ref.strip(),
        )
        return journal.with_receipt(receipt), receipt
