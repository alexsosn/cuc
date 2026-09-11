"""Runtime safety boundary for development-controller GitHub operations.

The model/controller receives typed operation intents, never a generic HTTP/GraphQL/CLI
escape hatch. A trusted host owns the human-approval registry, durable journal store,
and provider adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import math
from types import MappingProxyType
from typing import Any, Mapping, Protocol


JsonScalar = str | int | float | bool | None
FrozenJson = JsonScalar | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]
JournalPersist = Callable[[dict[str, object]], None]

_FORK = ("alexsosn", "cuc")
_UPSTREAM = ("dt-ucph", "cuc")
_PROTECTED_FORK_REFS = frozenset({"main", "agent-harness-safety"})
_SENSITIVE_REF_ACTIONS = frozenset(
    {"merge-pr", "trigger-workflow"}
)


class SideEffectDenied(PermissionError):
    """Raised when policy denies an operation regardless of approval."""


class ApprovalRequired(PermissionError):
    """Raised when an operation lacks a trusted exact human approval."""


class OperationReplayConflict(RuntimeError):
    """Raised when an operation ID is reused for another immutable intent."""


class GitHubDestination(str, Enum):
    FORK = "fork"
    UPSTREAM = "upstream"
    DENIED = "denied"


class GitHubOperationKind(str, Enum):
    READ = "read"
    CREATE_BRANCH = "create-branch"
    UPDATE_BRANCH = "update-branch"
    CREATE_ISSUE = "create-issue"
    UPDATE_ISSUE = "update-issue"
    CREATE_PR = "create-pr"
    UPDATE_PR = "update-pr"
    COMMENT = "comment"
    MERGE_PR = "merge-pr"
    TRIGGER_WORKFLOW = "trigger-workflow"

    @property
    def is_write(self) -> bool:
        return self is not GitHubOperationKind.READ


class PolicyDisposition(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require-approval"
    DENY = "deny"


class JournalStatus(str, Enum):
    PREPARED = "prepared"
    COMPLETED = "completed"


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field)


def _exact_keys(payload: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        pieces: list[str] = []
        if missing:
            pieces.append(f"missing={missing}")
        if extra:
            pieces.append(f"extra={extra}")
        raise ValueError(f"invalid {field} fields: " + ", ".join(pieces))


def _freeze_json(value: object, field: str = "payload") -> FrozenJson:
    """Copy JSON into immutable values while preserving object/array types."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJson] = {}
        for raw_key, item in value.items():
            key = _required_text(raw_key, f"{field} key")
            if key in frozen:
                raise ValueError(f"{field} contains duplicate key {key!r}")
            frozen[key] = _freeze_json(item, f"{field}.{key}")
        return MappingProxyType(dict(sorted(frozen.items())))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, f"{field}[]") for item in value)
    raise ValueError(f"{field} contains non-JSON value {type(value).__name__}")


def _thaw_json(value: FrozenJson) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class GitHubTarget:
    owner: str
    repo: str
    ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", _required_text(self.owner, "target.owner"))
        object.__setattr__(self, "repo", _required_text(self.repo, "target.repo"))
        object.__setattr__(self, "ref", _optional_text(self.ref, "target.ref"))

    @property
    def canonical_repository(self) -> tuple[str, str]:
        return self.owner.casefold(), self.repo.casefold()

    def to_dict(self) -> dict[str, object]:
        return {"owner": self.owner, "repo": self.repo, "ref": self.ref}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GitHubTarget":
        if not isinstance(payload, Mapping):
            raise ValueError("target must be a mapping")
        _exact_keys(payload, {"owner", "repo", "ref"}, "target")
        return cls(payload["owner"], payload["repo"], payload["ref"])


@dataclass(frozen=True)
class GitHubOperationIntent:
    operation_id: str
    kind: GitHubOperationKind
    target: GitHubTarget
    payload: object

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_id",
            _required_text(self.operation_id, "operation_id"),
        )
        try:
            kind = (
                self.kind
                if isinstance(self.kind, GitHubOperationKind)
                else GitHubOperationKind(self.kind)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"unsupported GitHub operation kind: {self.kind!r}"
            ) from exc
        if not isinstance(self.target, GitHubTarget):
            raise ValueError("target must be GitHubTarget")
        frozen = _freeze_json(self.payload)
        if not isinstance(frozen, Mapping):
            raise ValueError("payload must be a JSON mapping")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", frozen)

    @property
    def fingerprint(self) -> str:
        canonical = _canonical_json(
            {
                "schema_version": 1,
                "operation_id": self.operation_id,
                "kind": self.kind.value,
                "target": self.target.to_dict(),
                "payload": _thaw_json(self.payload),
            }
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "target": self.target.to_dict(),
            "payload": _thaw_json(self.payload),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GitHubOperationIntent":
        if not isinstance(payload, Mapping):
            raise ValueError("operation intent must be a mapping")
        _exact_keys(
            payload,
            {"operation_id", "kind", "target", "payload"},
            "operation intent",
        )
        return cls(
            payload["operation_id"],
            payload["kind"],
            GitHubTarget.from_dict(payload["target"]),
            payload["payload"],
        )


@dataclass(frozen=True)
class HumanApprovalGrant:
    approval_id: str
    approved_by: str
    operation_id: str
    operation_fingerprint: str
    issued_at: str

    def __post_init__(self) -> None:
        for field in (
            "approval_id",
            "approved_by",
            "operation_id",
            "operation_fingerprint",
            "issued_at",
        ):
            object.__setattr__(
                self,
                field,
                _required_text(getattr(self, field), field),
            )
        digest = self.operation_fingerprint.lower()
        if len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise ValueError(
                "operation_fingerprint must be a SHA-256 hex digest"
            )
        object.__setattr__(self, "operation_fingerprint", digest)

    def matches(self, intent: GitHubOperationIntent) -> bool:
        return (
            self.operation_id == intent.operation_id
            and self.operation_fingerprint == intent.fingerprint
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "approval_id": self.approval_id,
            "approved_by": self.approved_by,
            "operation_id": self.operation_id,
            "operation_fingerprint": self.operation_fingerprint,
            "issued_at": self.issued_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HumanApprovalGrant":
        if not isinstance(payload, Mapping):
            raise ValueError("approval must be a mapping")
        _exact_keys(
            payload,
            {
                "approval_id",
                "approved_by",
                "operation_id",
                "operation_fingerprint",
                "issued_at",
            },
            "approval",
        )
        return cls(
            payload["approval_id"],
            payload["approved_by"],
            payload["operation_id"],
            payload["operation_fingerprint"],
            payload["issued_at"],
        )


class HumanApprovalRegistry:
    """Trusted host-owned store of human-issued exact operation approvals."""

    def __init__(self) -> None:
        self._grants: dict[str, HumanApprovalGrant] = {}

    def register(self, grant: HumanApprovalGrant) -> None:
        if not isinstance(grant, HumanApprovalGrant):
            raise ValueError("grant must be HumanApprovalGrant")
        existing = self._grants.get(grant.approval_id)
        if existing is not None and existing != grant:
            raise ValueError(
                f"approval ID {grant.approval_id!r} cannot be rebound"
            )
        self._grants[grant.approval_id] = grant

    def resolve(
        self,
        approval: HumanApprovalGrant | str | None,
    ) -> HumanApprovalGrant | None:
        if approval is None:
            return None
        if isinstance(approval, HumanApprovalGrant):
            registered = self._grants.get(approval.approval_id)
            return registered if registered == approval else None
        if isinstance(approval, str):
            return self._grants.get(
                _required_text(approval, "approval_id")
            )
        return None

    def get(self, approval_id: str | None) -> HumanApprovalGrant | None:
        if approval_id is None:
            return None
        return self._grants.get(
            _required_text(approval_id, "approval_id")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "grants": [
                self._grants[key].to_dict()
                for key in sorted(self._grants)
            ],
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "HumanApprovalRegistry":
        if not isinstance(payload, Mapping):
            raise ValueError("approval registry must be a mapping")
        _exact_keys(
            payload,
            {"schema_version", "grants"},
            "approval registry",
        )
        if payload["schema_version"] != 1:
            raise ValueError("unsupported approval registry schema_version")
        raw_grants = payload["grants"]
        if isinstance(raw_grants, (str, bytes, Mapping)):
            raise ValueError("approval registry grants must be a list")
        try:
            items = tuple(raw_grants)
        except TypeError as exc:
            raise ValueError(
                "approval registry grants must be iterable"
            ) from exc
        registry = cls()
        for raw in items:
            if not isinstance(raw, Mapping):
                raise ValueError(
                    "approval registry grants must contain mappings"
                )
            registry.register(HumanApprovalGrant.from_dict(raw))
        return registry


@dataclass(frozen=True)
class PolicyDecision:
    disposition: PolicyDisposition
    destination: GitHubDestination
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, PolicyDisposition):
            object.__setattr__(
                self,
                "disposition",
                PolicyDisposition(self.disposition),
            )
        if not isinstance(self.destination, GitHubDestination):
            object.__setattr__(
                self,
                "destination",
                GitHubDestination(self.destination),
            )
        object.__setattr__(
            self,
            "reason",
            _required_text(self.reason, "reason"),
        )


@dataclass(frozen=True)
class SideEffectResult:
    operation_id: str
    status: str
    result_ref: str | None
    intent_fingerprint: str
    policy_disposition: PolicyDisposition

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_id",
            _required_text(self.operation_id, "operation_id"),
        )
        if self.status not in {"read", "dry-run", "executed", "replayed"}:
            raise ValueError(
                f"unsupported side-effect result status: {self.status!r}"
            )
        object.__setattr__(
            self,
            "result_ref",
            _optional_text(self.result_ref, "result_ref"),
        )
        object.__setattr__(
            self,
            "intent_fingerprint",
            _required_text(self.intent_fingerprint, "intent_fingerprint"),
        )
        if not isinstance(self.policy_disposition, PolicyDisposition):
            object.__setattr__(
                self,
                "policy_disposition",
                PolicyDisposition(self.policy_disposition),
            )


@dataclass(frozen=True)
class _JournalEntry:
    intent: GitHubOperationIntent
    fingerprint: str
    status: JournalStatus
    approval_id: str | None = None
    result_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, GitHubOperationIntent):
            raise ValueError("journal intent must be GitHubOperationIntent")
        if self.fingerprint != self.intent.fingerprint:
            raise ValueError(
                "journal fingerprint does not match operation intent"
            )
        if not isinstance(self.status, JournalStatus):
            object.__setattr__(self, "status", JournalStatus(self.status))
        object.__setattr__(
            self,
            "approval_id",
            _optional_text(self.approval_id, "approval_id"),
        )
        object.__setattr__(
            self,
            "result_ref",
            _optional_text(self.result_ref, "result_ref"),
        )
        if (
            self.status is JournalStatus.COMPLETED
            and self.result_ref is None
        ):
            raise ValueError(
                "completed journal entry requires result_ref"
            )
        if (
            self.status is JournalStatus.PREPARED
            and self.result_ref is not None
        ):
            raise ValueError(
                "prepared journal entry cannot contain result_ref"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent.to_dict(),
            "fingerprint": self.fingerprint,
            "status": self.status.value,
            "approval_id": self.approval_id,
            "result_ref": self.result_ref,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "_JournalEntry":
        if not isinstance(payload, Mapping):
            raise ValueError("journal entry must be a mapping")
        _exact_keys(
            payload,
            {
                "intent",
                "fingerprint",
                "status",
                "approval_id",
                "result_ref",
            },
            "journal entry",
        )
        intent = GitHubOperationIntent.from_dict(payload["intent"])
        return cls(
            intent=intent,
            fingerprint=_required_text(
                payload["fingerprint"],
                "fingerprint",
            ),
            status=JournalStatus(payload["status"]),
            approval_id=payload["approval_id"],
            result_ref=payload["result_ref"],
        )


class OperationJournal:
    """Two-phase journal with optional synchronous durable persistence.

    If ``persist`` is configured, every state mutation must be durably stored before
    ``prepare`` or ``complete`` returns. A failed persistence call rolls the in-memory
    mutation back, so a provider write can never follow a failed PREPARED commit.
    """

    def __init__(self, *, persist: JournalPersist | None = None) -> None:
        if persist is not None and not callable(persist):
            raise ValueError("persist must be callable or None")
        self._entries: dict[str, _JournalEntry] = {}
        self._persist = persist

    def _matching_entry(
        self,
        intent: GitHubOperationIntent,
    ) -> _JournalEntry | None:
        entry = self._entries.get(intent.operation_id)
        if entry is not None and entry.fingerprint != intent.fingerprint:
            raise OperationReplayConflict(
                f"operation ID {intent.operation_id!r} was reused "
                "with a different fingerprint"
            )
        return entry

    def _mutate_durably(
        self,
        operation_id: str,
        entry: _JournalEntry,
    ) -> None:
        prior = self._entries.get(operation_id)
        self._entries[operation_id] = entry
        if self._persist is None:
            return
        try:
            self._persist(self.to_dict())
        except BaseException:
            if prior is None:
                self._entries.pop(operation_id, None)
            else:
                self._entries[operation_id] = prior
            raise

    def entry(
        self,
        intent: GitHubOperationIntent,
    ) -> _JournalEntry | None:
        return self._matching_entry(intent)

    def status(self, operation_id: str) -> str | None:
        entry = self._entries.get(operation_id)
        return None if entry is None else entry.status.value

    def prepare(
        self,
        intent: GitHubOperationIntent,
        approval_id: str | None,
    ) -> None:
        entry = self._matching_entry(intent)
        normalized_approval = _optional_text(
            approval_id,
            "approval_id",
        )
        if entry is not None:
            if (
                entry.approval_id is not None
                and normalized_approval is not None
                and entry.approval_id != normalized_approval
            ):
                raise OperationReplayConflict(
                    "prepared operation cannot be rebound to another approval"
                )
            return
        prepared = _JournalEntry(
            intent=intent,
            fingerprint=intent.fingerprint,
            status=JournalStatus.PREPARED,
            approval_id=normalized_approval,
        )
        self._mutate_durably(intent.operation_id, prepared)

    def complete(
        self,
        intent: GitHubOperationIntent,
        result_ref: str,
    ) -> None:
        entry = self._matching_entry(intent)
        if entry is None:
            raise ValueError("operation must be prepared before completion")
        result_ref = _required_text(result_ref, "result_ref")
        if entry.status is JournalStatus.COMPLETED:
            if entry.result_ref != result_ref:
                raise OperationReplayConflict(
                    "completed operation cannot be rebound to a different provider result"
                )
            return
        completed = _JournalEntry(
            intent=intent,
            fingerprint=intent.fingerprint,
            status=JournalStatus.COMPLETED,
            approval_id=entry.approval_id,
            result_ref=result_ref,
        )
        self._mutate_durably(intent.operation_id, completed)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "entries": [
                self._entries[key].to_dict()
                for key in sorted(self._entries)
            ],
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        persist: JournalPersist | None = None,
    ) -> "OperationJournal":
        if not isinstance(payload, Mapping):
            raise ValueError("journal must be a mapping")
        _exact_keys(
            payload,
            {"schema_version", "entries"},
            "journal",
        )
        if payload["schema_version"] != 1:
            raise ValueError("unsupported journal schema_version")
        entries = payload["entries"]
        if isinstance(entries, (str, bytes, Mapping)):
            raise ValueError("journal entries must be a list")
        try:
            raw_entries = tuple(entries)
        except TypeError as exc:
            raise ValueError("journal entries must be iterable") from exc
        journal = cls(persist=persist)
        for raw in raw_entries:
            entry = _JournalEntry.from_dict(raw)
            operation_id = entry.intent.operation_id
            if operation_id in journal._entries:
                raise ValueError(
                    f"duplicate journal operation ID: {operation_id}"
                )
            journal._entries[operation_id] = entry
        return journal


class GitHubSideEffectAdapter(Protocol):
    """Trusted adapter surface. No generic request/URL method is exposed."""

    def read(self, intent: GitHubOperationIntent) -> str: ...
    def reconcile(self, intent: GitHubOperationIntent) -> str | None: ...
    def create_branch(self, intent: GitHubOperationIntent) -> str: ...
    def update_branch(self, intent: GitHubOperationIntent) -> str: ...
    def create_issue(self, intent: GitHubOperationIntent) -> str: ...
    def update_issue(self, intent: GitHubOperationIntent) -> str: ...
    def create_pr(self, intent: GitHubOperationIntent) -> str: ...
    def update_pr(self, intent: GitHubOperationIntent) -> str: ...
    def comment(self, intent: GitHubOperationIntent) -> str: ...
    def merge_pr(self, intent: GitHubOperationIntent) -> str: ...
    def trigger_workflow(self, intent: GitHubOperationIntent) -> str: ...


_WRITE_METHODS: dict[GitHubOperationKind, str] = {
    GitHubOperationKind.CREATE_BRANCH: "create_branch",
    GitHubOperationKind.UPDATE_BRANCH: "update_branch",
    GitHubOperationKind.CREATE_ISSUE: "create_issue",
    GitHubOperationKind.UPDATE_ISSUE: "update_issue",
    GitHubOperationKind.CREATE_PR: "create_pr",
    GitHubOperationKind.UPDATE_PR: "update_pr",
    GitHubOperationKind.COMMENT: "comment",
    GitHubOperationKind.MERGE_PR: "merge_pr",
    GitHubOperationKind.TRIGGER_WORKFLOW: "trigger_workflow",
}


class GuardedGitHubSideEffects:
    """Deny-by-default policy, trusted approval gate, and replay-safe dispatch."""

    def __init__(
        self,
        *,
        adapter: GitHubSideEffectAdapter,
        journal: OperationJournal | None = None,
        approvals: HumanApprovalRegistry | None = None,
    ) -> None:
        self._adapter = adapter
        self.journal = (
            journal if journal is not None else OperationJournal()
        )
        self._approvals = (
            approvals
            if approvals is not None
            else HumanApprovalRegistry()
        )

    @staticmethod
    def classify(target: GitHubTarget) -> GitHubDestination:
        if not isinstance(target, GitHubTarget):
            raise ValueError("target must be GitHubTarget")
        repository = target.canonical_repository
        if repository == _FORK:
            return GitHubDestination.FORK
        if repository == _UPSTREAM:
            return GitHubDestination.UPSTREAM
        return GitHubDestination.DENIED

    def evaluate(self, intent: GitHubOperationIntent) -> PolicyDecision:
        if not isinstance(intent, GitHubOperationIntent):
            raise ValueError("intent must be GitHubOperationIntent")
        destination = self.classify(intent.target)
        kind = intent.kind

        if destination is GitHubDestination.DENIED:
            return PolicyDecision(
                PolicyDisposition.DENY,
                destination,
                "repository is outside the explicit fork/upstream allowlist",
            )

        if kind is GitHubOperationKind.READ:
            return PolicyDecision(
                PolicyDisposition.ALLOW,
                destination,
                "read is allowed for fork and upstream",
            )

        if kind in {
            GitHubOperationKind.CREATE_BRANCH,
            GitHubOperationKind.UPDATE_BRANCH,
        }:
            if intent.target.ref is None:
                return PolicyDecision(
                    PolicyDisposition.DENY,
                    destination,
                    "branch writes require an explicit target ref",
                )
            if (
                destination is GitHubDestination.FORK
                and intent.target.ref.casefold() in _PROTECTED_FORK_REFS
            ):
                return PolicyDecision(
                    PolicyDisposition.DENY,
                    destination,
                    "direct writes to fork integration refs are prohibited",
                )

        if (
            kind.value in _SENSITIVE_REF_ACTIONS
            and intent.target.ref is None
        ):
            return PolicyDecision(
                PolicyDisposition.DENY,
                destination,
                "integration/publication actions require an explicit target ref",
            )

        if destination is GitHubDestination.UPSTREAM:
            return PolicyDecision(
                PolicyDisposition.REQUIRE_APPROVAL,
                destination,
                "every upstream write requires exact human approval",
            )

        if kind in {
            GitHubOperationKind.MERGE_PR,
            GitHubOperationKind.TRIGGER_WORKFLOW,
        }:
            return PolicyDecision(
                PolicyDisposition.REQUIRE_APPROVAL,
                destination,
                "fork integration/publication action requires exact human approval",
            )

        if kind in _WRITE_METHODS:
            return PolicyDecision(
                PolicyDisposition.ALLOW,
                destination,
                "controlled fork-local development write",
            )

        return PolicyDecision(
            PolicyDisposition.DENY,
            destination,
            "operation kind is not exposed by the guarded capability",
        )

    def _trusted_approval(
        self,
        intent: GitHubOperationIntent,
        supplied: HumanApprovalGrant | str | None,
        entry: _JournalEntry | None,
    ) -> HumanApprovalGrant | None:
        grant = self._approvals.resolve(supplied)
        if grant is not None and grant.matches(intent):
            return grant
        if entry is not None:
            prior = self._approvals.get(entry.approval_id)
            if prior is not None and prior.matches(intent):
                return prior
        return None

    def _result(
        self,
        intent: GitHubOperationIntent,
        status: str,
        policy: PolicyDecision,
        result_ref: str | None = None,
    ) -> SideEffectResult:
        return SideEffectResult(
            operation_id=intent.operation_id,
            status=status,
            result_ref=result_ref,
            intent_fingerprint=intent.fingerprint,
            policy_disposition=policy.disposition,
        )

    def execute(
        self,
        intent: GitHubOperationIntent,
        *,
        approval: HumanApprovalGrant | str | None = None,
        dry_run: bool = False,
    ) -> SideEffectResult:
        if not isinstance(intent, GitHubOperationIntent):
            raise ValueError("intent must be GitHubOperationIntent")
        policy = self.evaluate(intent)

        if dry_run:
            return self._result(intent, "dry-run", policy)

        if policy.disposition is PolicyDisposition.DENY:
            raise SideEffectDenied(policy.reason)

        if intent.kind is GitHubOperationKind.READ:
            result_ref = _required_text(
                self._adapter.read(intent),
                "adapter read result",
            )
            return self._result(intent, "read", policy, result_ref)

        entry = self.journal.entry(intent)

        if policy.disposition is PolicyDisposition.REQUIRE_APPROVAL:
            trusted = self._trusted_approval(intent, approval, entry)
            if trusted is None:
                raise ApprovalRequired(policy.reason)
            approval_id = trusted.approval_id
        else:
            approval_id = None

        if (
            entry is not None
            and entry.status is JournalStatus.COMPLETED
        ):
            return self._result(
                intent,
                "replayed",
                policy,
                entry.result_ref,
            )

        if (
            entry is not None
            and entry.status is JournalStatus.PREPARED
        ):
            reconciled = self._adapter.reconcile(intent)
            if reconciled is not None:
                reconciled = _required_text(
                    reconciled,
                    "reconciled result",
                )
                self.journal.complete(intent, reconciled)
                return self._result(
                    intent,
                    "replayed",
                    policy,
                    reconciled,
                )
        else:
            # prepare() returns only after durable persistence succeeds (when a
            # durable store is configured). Provider dispatch therefore cannot
            # race ahead of the restart record.
            self.journal.prepare(intent, approval_id=approval_id)

        method_name = _WRITE_METHODS.get(intent.kind)
        if method_name is None:
            raise SideEffectDenied(
                "operation kind has no trusted adapter dispatch"
            )
        method = getattr(self._adapter, method_name, None)
        if not callable(method):
            raise TypeError(
                f"trusted adapter does not implement {method_name}()"
            )
        result_ref = _required_text(
            method(intent),
            f"adapter {method_name} result",
        )
        self.journal.complete(intent, result_ref)
        return self._result(intent, "executed", policy, result_ref)
