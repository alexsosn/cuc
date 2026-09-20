"""HARN-028 evidence policy: which optional local evidence sources a run may consult.

An ablation arm is one ``EvidencePolicy``. Its digest keys HARN-016 comparability
(``ParsingWorkloadRef.evidence_policy_sha256``), so two runs that enable different
sources, or the same source at a different resource version, are never compared
as equals. The policy records source ids, locator kinds and resource digests only;
resource paths and resource text never enter it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping, Protocol

AUTO_PARSING = "auto-parsing"
DULAT = "dulat"
TROPPER = "tropper"
EUPT = "eupt"
LEGACY_REVIEW = "legacy-review"
CORPUS_PARALLELS = "corpus-parallels"
BURNS = "burns-cultic-vocabulary"

# Canonical order. ``published-translations`` is deliberately absent from this slice:
# the modules cache holds whole-tablet documents with no line alignment.
ALL_SOURCE_IDS: tuple[str, ...] = (
    AUTO_PARSING,
    DULAT,
    TROPPER,
    EUPT,
    LEGACY_REVIEW,
    CORPUS_PARALLELS,
    BURNS,
)

# Sources that live in the repository itself (the legacy expert reviews are the
# committed ``reviewed/*.txt`` files) and therefore need no locator.
REPOSITORY_SOURCE_IDS: frozenset[str] = frozenset({AUTO_PARSING, CORPUS_PARALLELS, LEGACY_REVIEW})

# Resource kind consulted through the skill-script locator for each external source.
RESOURCE_KIND_BY_SOURCE: Mapping[str, str] = {
    DULAT: "dulat_search",
    TROPPER: "tropper",
    EUPT: "modules",
    BURNS: "burns",
}

LOCATOR_KINDS: tuple[str, ...] = ("repository", "explicit", "env", "sibling", "absent")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ResourceAvailability:
    """How one enabled source resolved at run start; carries no path or text."""

    source_id: str
    enabled: bool
    available: bool
    locator_kind: str
    resource_sha256: str | None

    def __post_init__(self) -> None:
        source_id = _required_text(self.source_id, "source_id")
        if source_id not in ALL_SOURCE_IDS:
            raise ValueError(f"unknown evidence source id: {source_id}")
        if not isinstance(self.enabled, bool) or not isinstance(self.available, bool):
            raise ValueError("enabled and available must be booleans")
        kind = _required_text(self.locator_kind, "locator_kind")
        if kind not in LOCATOR_KINDS:
            raise ValueError(f"locator_kind must be one of {LOCATOR_KINDS}")
        if self.available:
            if kind == "absent":
                raise ValueError("an available resource cannot have locator_kind 'absent'")
            digest = self.resource_sha256
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest.lower()):
                raise ValueError("an available resource requires a SHA-256 digest")
            object.__setattr__(self, "resource_sha256", digest.lower())
        else:
            if kind != "absent":
                raise ValueError("an unavailable resource must have locator_kind 'absent'")
            if self.resource_sha256 is not None:
                raise ValueError("an unavailable resource cannot carry a digest")
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "locator_kind", kind)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "enabled": self.enabled,
            "available": self.available,
            "locator_kind": self.locator_kind,
            "resource_sha256": self.resource_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResourceAvailability":
        if not isinstance(payload, Mapping):
            raise ValueError("ResourceAvailability payload must be a mapping")
        return cls(
            payload["source_id"],
            payload["enabled"],
            payload["available"],
            payload["locator_kind"],
            payload.get("resource_sha256"),
        )


def _source_order(source_id: str) -> int:
    return ALL_SOURCE_IDS.index(source_id)


@dataclass(frozen=True)
class EvidencePolicy:
    enabled_sources: tuple[str, ...]
    availability: tuple[ResourceAvailability, ...]

    def __post_init__(self) -> None:
        if isinstance(self.enabled_sources, str):
            raise ValueError("enabled_sources must be a sequence of source ids")
        enabled = tuple(_required_text(item, "enabled source id") for item in self.enabled_sources)
        unknown = [item for item in enabled if item not in ALL_SOURCE_IDS]
        if unknown:
            raise ValueError("unknown evidence source ids: " + ", ".join(unknown))
        if len(enabled) != len(set(enabled)):
            raise ValueError("enabled_sources must not contain duplicates")
        if AUTO_PARSING not in enabled:
            raise ValueError(f"{AUTO_PARSING} evidence cannot be disabled")
        enabled = tuple(sorted(enabled, key=_source_order))

        availability = tuple(self.availability)
        if any(not isinstance(item, ResourceAvailability) for item in availability):
            raise ValueError("availability must contain ResourceAvailability values")
        covered = tuple(item.source_id for item in availability)
        if len(covered) != len(set(covered)):
            raise ValueError("availability must not repeat a source id")
        if set(covered) != set(enabled):
            raise ValueError("availability must cover exactly the enabled sources")
        if any(not item.enabled for item in availability):
            raise ValueError("availability for an enabled source must be marked enabled")
        availability = tuple(sorted(availability, key=lambda item: _source_order(item.source_id)))

        object.__setattr__(self, "enabled_sources", enabled)
        object.__setattr__(self, "availability", availability)

    @property
    def available_sources(self) -> tuple[str, ...]:
        return tuple(item.source_id for item in self.availability if item.available)

    @property
    def absent_sources(self) -> tuple[str, ...]:
        return tuple(item.source_id for item in self.availability if not item.available)

    @property
    def sha256(self) -> str:
        payload = {
            "enabled_sources": list(self.enabled_sources),
            "resources": [
                [item.source_id, item.available, item.resource_sha256]
                for item in self.availability
            ],
        }
        return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()

    def availability_for(self, source_id: str) -> ResourceAvailability | None:
        return next((item for item in self.availability if item.source_id == source_id), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled_sources": list(self.enabled_sources),
            "availability": [item.to_dict() for item in self.availability],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidencePolicy":
        if not isinstance(payload, Mapping):
            raise ValueError("EvidencePolicy payload must be a mapping")
        return cls(
            tuple(payload["enabled_sources"]),
            tuple(ResourceAvailability.from_dict(item) for item in payload["availability"]),
        )

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_json(cls, payload: str) -> "EvidencePolicy":
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("EvidencePolicy JSON is invalid") from exc
        return cls.from_dict(decoded)


class ResourceLocator(Protocol):
    """Resolve one resource kind to a local path plus how it was found."""

    def locate(self, kind: str) -> tuple[Any, str] | None: ...


def resource_digest(path: Any) -> str:
    """SHA-256 of a resource file, or of the sorted file digests for a directory."""

    from pathlib import Path

    target = Path(path)
    if target.is_dir():
        digest = sha256()
        for child in sorted(p for p in target.rglob("*") if p.is_file()):
            digest.update(child.relative_to(target).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(_file_sha256(child).encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()
    return _file_sha256(target)


def _file_sha256(path: Any) -> str:
    digest = sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_evidence_policy(
    requested: tuple[str, ...] | list[str],
    locator: ResourceLocator,
    *,
    resource_digest_for=resource_digest,
) -> EvidencePolicy:
    """Resolve requested sources to a policy; absent resources are recorded, never fatal."""

    enabled = tuple(requested)
    availability: list[ResourceAvailability] = []
    for source_id in enabled:
        if source_id in REPOSITORY_SOURCE_IDS:
            availability.append(
                ResourceAvailability(source_id, True, True, "repository", _REPOSITORY_DIGEST)
            )
            continue
        kind = RESOURCE_KIND_BY_SOURCE.get(source_id)
        located = locator.locate(kind) if kind is not None else None
        if located is None:
            availability.append(ResourceAvailability(source_id, True, False, "absent", None))
            continue
        path, locator_kind = located
        availability.append(
            ResourceAvailability(source_id, True, True, locator_kind, resource_digest_for(path))
        )
    return EvidencePolicy(enabled, tuple(availability))


# Repository-internal sources are versioned by the repository revision carried in the
# task, so their availability record uses a fixed marker digest rather than a file hash.
_REPOSITORY_DIGEST = sha256(b"cuc-repository-source").hexdigest()
