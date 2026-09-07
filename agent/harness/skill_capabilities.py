"""Framework-neutral machine contracts for existing CUC skill packages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping


_SCHEMA_VERSION = 1
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MANIFEST_FIELDS = {
    "schema_version",
    "contract_version",
    "canonical_name",
    "skill_path",
    "aliases",
    "work_unit",
    "ordered_stages",
    "scope_invariants",
    "required_evidence",
    "optional_evidence",
    "authoritative_resources",
    "helper_resources",
    "effect",
    "write_scopes",
    "read_only_scopes",
    "completion_verifiers",
    "escalation_targets",
    "evaluator_requirements",
    "permissions",
}


class SkillEffect(str, Enum):
    READ_ONLY = "read-only"
    CURATED_DATA_WRITE = "curated-data-write"
    GENERATED_DATA_WRITE = "generated-data-write"


class UnmanagedSkillError(LookupError):
    """Raised when a legacy skill exists but has no canonical capability manifest."""


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _text_tuple(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an array of strings, not a scalar string")
    try:
        raw = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{field} must be an array of strings") from exc
    items = tuple(_required_text(item, field) for item in raw)
    if len(set(items)) != len(items):
        raise ValueError(f"{field} must not contain duplicates")
    if required and not items:
        raise ValueError(f"{field} must not be empty")
    return items


def _repo_path(value: object, field: str, *, glob: bool = False) -> str:
    text = _required_text(value, field)
    if "\\" in text or text.startswith("/"):
        raise ValueError(f"{field} must be a repository-relative POSIX path")
    path = PurePosixPath(text)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{field} must not contain traversal segments")
    if not glob and any(any(char in part for char in "*?[") for part in path.parts):
        raise ValueError(f"{field} must be a concrete repository path")
    return text


def _path_tuple(value: object, field: str, *, glob: bool = False) -> tuple[str, ...]:
    items = _text_tuple(value, field)
    normalized = tuple(_repo_path(item, field, glob=glob) for item in items)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field} must not contain duplicates")
    return normalized


@dataclass(frozen=True)
class SkillCapabilityManifest:
    schema_version: int
    contract_version: str
    canonical_name: str
    skill_path: str
    aliases: tuple[str, ...]
    work_unit: str
    ordered_stages: tuple[str, ...]
    scope_invariants: tuple[str, ...]
    required_evidence: tuple[str, ...]
    optional_evidence: tuple[str, ...]
    authoritative_resources: tuple[str, ...]
    helper_resources: tuple[str, ...]
    effect: SkillEffect
    write_scopes: tuple[str, ...]
    read_only_scopes: tuple[str, ...]
    completion_verifiers: tuple[str, ...]
    escalation_targets: tuple[str, ...]
    evaluator_requirements: tuple[str, ...]
    permissions: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != _SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {_SCHEMA_VERSION}")
        contract_version = _required_text(self.contract_version, "contract_version")
        if not _SEMVER_RE.fullmatch(contract_version):
            raise ValueError("contract_version must be MAJOR.MINOR.PATCH")
        canonical_name = _required_text(self.canonical_name, "canonical_name")
        if not _NAME_RE.fullmatch(canonical_name):
            raise ValueError("canonical_name must be lower-case kebab-case")

        skill_path = _repo_path(self.skill_path, "skill_path")
        expected_skill_path = f".agents/skills/{canonical_name}"
        if skill_path != expected_skill_path:
            raise ValueError(
                f"skill_path must be canonical package path {expected_skill_path!r}"
            )

        aliases = _path_tuple(self.aliases, "aliases")
        expected_alias = f".claude/skills/{canonical_name}"
        if any(alias != expected_alias for alias in aliases):
            raise ValueError(f"aliases may only contain {expected_alias!r}")

        effect = self.effect if isinstance(self.effect, SkillEffect) else SkillEffect(self.effect)
        write_scopes = _path_tuple(self.write_scopes, "write_scopes", glob=True)
        read_only_scopes = _path_tuple(self.read_only_scopes, "read_only_scopes", glob=True)
        if effect is SkillEffect.READ_ONLY and write_scopes:
            raise ValueError("read-only capabilities cannot declare write_scopes")
        if effect is not SkillEffect.READ_ONLY and not write_scopes:
            raise ValueError("write capabilities must declare write_scopes")
        if set(write_scopes) & set(read_only_scopes):
            raise ValueError("the same path scope cannot be both writable and read-only")

        authoritative = _path_tuple(
            self.authoritative_resources, "authoritative_resources"
        )
        helpers = _path_tuple(self.helper_resources, "helper_resources")
        if set(authoritative) & set(helpers):
            raise ValueError("authoritative_resources and helper_resources must not overlap")

        object.__setattr__(self, "contract_version", contract_version)
        object.__setattr__(self, "canonical_name", canonical_name)
        object.__setattr__(self, "skill_path", skill_path)
        object.__setattr__(self, "aliases", aliases)
        object.__setattr__(self, "work_unit", _required_text(self.work_unit, "work_unit"))
        object.__setattr__(
            self,
            "ordered_stages",
            _text_tuple(self.ordered_stages, "ordered_stages", required=True),
        )
        object.__setattr__(
            self,
            "scope_invariants",
            _text_tuple(self.scope_invariants, "scope_invariants", required=True),
        )
        object.__setattr__(
            self, "required_evidence", _text_tuple(self.required_evidence, "required_evidence")
        )
        object.__setattr__(
            self, "optional_evidence", _text_tuple(self.optional_evidence, "optional_evidence")
        )
        object.__setattr__(self, "authoritative_resources", authoritative)
        object.__setattr__(self, "helper_resources", helpers)
        object.__setattr__(self, "effect", effect)
        object.__setattr__(self, "write_scopes", write_scopes)
        object.__setattr__(self, "read_only_scopes", read_only_scopes)
        object.__setattr__(
            self,
            "completion_verifiers",
            _text_tuple(self.completion_verifiers, "completion_verifiers", required=True),
        )
        object.__setattr__(
            self,
            "escalation_targets",
            _text_tuple(self.escalation_targets, "escalation_targets"),
        )
        object.__setattr__(
            self,
            "evaluator_requirements",
            _text_tuple(self.evaluator_requirements, "evaluator_requirements"),
        )
        object.__setattr__(
            self,
            "permissions",
            _text_tuple(self.permissions, "permissions", required=True),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SkillCapabilityManifest":
        if not isinstance(payload, Mapping):
            raise ValueError("skill capability manifest must be a JSON object")
        keys = set(payload)
        missing = _MANIFEST_FIELDS - keys
        extra = keys - _MANIFEST_FIELDS
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append(f"missing fields: {sorted(missing)}")
            if extra:
                details.append(f"unknown fields: {sorted(extra)}")
            raise ValueError("; ".join(details))
        schema_version = payload["schema_version"]
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise ValueError("schema_version must be an integer")
        try:
            effect = SkillEffect(payload["effect"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid effect: {payload['effect']!r}") from exc
        return cls(
            schema_version=schema_version,
            contract_version=payload["contract_version"],
            canonical_name=payload["canonical_name"],
            skill_path=payload["skill_path"],
            aliases=payload["aliases"],
            work_unit=payload["work_unit"],
            ordered_stages=payload["ordered_stages"],
            scope_invariants=payload["scope_invariants"],
            required_evidence=payload["required_evidence"],
            optional_evidence=payload["optional_evidence"],
            authoritative_resources=payload["authoritative_resources"],
            helper_resources=payload["helper_resources"],
            effect=effect,
            write_scopes=payload["write_scopes"],
            read_only_scopes=payload["read_only_scopes"],
            completion_verifiers=payload["completion_verifiers"],
            escalation_targets=payload["escalation_targets"],
            evaluator_requirements=payload["evaluator_requirements"],
            permissions=payload["permissions"],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "contract_version": self.contract_version,
            "canonical_name": self.canonical_name,
            "skill_path": self.skill_path,
            "aliases": list(self.aliases),
            "work_unit": self.work_unit,
            "ordered_stages": list(self.ordered_stages),
            "scope_invariants": list(self.scope_invariants),
            "required_evidence": list(self.required_evidence),
            "optional_evidence": list(self.optional_evidence),
            "authoritative_resources": list(self.authoritative_resources),
            "helper_resources": list(self.helper_resources),
            "effect": self.effect.value,
            "write_scopes": list(self.write_scopes),
            "read_only_scopes": list(self.read_only_scopes),
            "completion_verifiers": list(self.completion_verifiers),
            "escalation_targets": list(self.escalation_targets),
            "evaluator_requirements": list(self.evaluator_requirements),
            "permissions": list(self.permissions),
        }


@dataclass(frozen=True)
class ResourceDigest:
    path: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class SkillProvenance:
    canonical_name: str
    contract_version: str
    manifest_sha256: str
    skill_package_sha256: str
    resources: tuple[ResourceDigest, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "canonical_name": self.canonical_name,
            "contract_version": self.contract_version,
            "manifest_sha256": self.manifest_sha256,
            "skill_package_sha256": self.skill_package_sha256,
            "resources": [item.to_dict() for item in self.resources],
        }


class SkillCapabilityRegistry:
    """Load canonical capability manifests and resolve compatibility aliases."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.manifest_dir = self.repo_root / "agent" / "harness" / "capability_manifests"
        self._manifests: dict[str, SkillCapabilityManifest] = {}
        self._manifest_paths: dict[str, Path] = {}
        self._aliases: dict[str, SkillCapabilityManifest] = {}
        self._load_manifests()
        self.unmanaged_legacy_paths = self._discover_unmanaged_legacy_paths()

    def _load_manifests(self) -> None:
        if not self.manifest_dir.exists():
            return
        for path in sorted(self.manifest_dir.glob("*.json"), key=lambda item: item.name):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid capability JSON: {path}") from exc
            manifest = SkillCapabilityManifest.from_dict(payload)
            if path.stem != manifest.canonical_name:
                raise ValueError(
                    f"manifest filename {path.stem!r} does not match canonical_name "
                    f"{manifest.canonical_name!r}"
                )
            if manifest.canonical_name in self._manifests:
                raise ValueError(f"duplicate capability: {manifest.canonical_name}")
            self._validate_package(manifest)
            self._manifests[manifest.canonical_name] = manifest
            self._manifest_paths[manifest.canonical_name] = path
            for alias in manifest.aliases:
                if alias in self._aliases:
                    raise ValueError(f"duplicate skill alias: {alias}")
                self._validate_alias(alias, manifest)
                self._aliases[alias] = manifest

    def _resolve_repo_path(self, relative: str, *, require_file: bool | None = None) -> Path:
        normalized = _repo_path(relative, "resource path")
        candidate = self.repo_root / PurePosixPath(normalized)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f"declared capability resource does not exist: {relative}") from exc
        try:
            resolved.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValueError(f"declared capability resource escapes repository: {relative}") from exc
        if require_file is True and not resolved.is_file():
            raise ValueError(f"declared capability resource is not a file: {relative}")
        if require_file is False and not resolved.is_dir():
            raise ValueError(f"declared capability package is not a directory: {relative}")
        return resolved

    def _validate_package(self, manifest: SkillCapabilityManifest) -> None:
        package = self._resolve_repo_path(manifest.skill_path, require_file=False)
        skill_file = package / "SKILL.md"
        if not skill_file.is_file():
            raise ValueError(f"canonical skill is missing SKILL.md: {manifest.skill_path}")
        frontmatter_name = _skill_frontmatter_name(skill_file.read_text(encoding="utf-8"))
        if frontmatter_name != manifest.canonical_name:
            raise ValueError(
                f"SKILL.md name {frontmatter_name!r} does not match capability "
                f"{manifest.canonical_name!r}"
            )
        for resource in (*manifest.authoritative_resources, *manifest.helper_resources):
            self._resolve_repo_path(resource, require_file=True)

    def _validate_alias(self, alias: str, manifest: SkillCapabilityManifest) -> None:
        alias_path = self.repo_root / PurePosixPath(alias)
        if not (alias_path.exists() or alias_path.is_symlink()):
            raise ValueError(f"declared skill alias does not exist: {alias}")
        target = _alias_target(alias_path)
        try:
            target = target.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError(f"declared skill alias is broken: {alias}") from exc
        try:
            target.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValueError(f"declared skill alias escapes repository: {alias}") from exc
        canonical = self._resolve_repo_path(manifest.skill_path, require_file=False)
        if target != canonical:
            raise ValueError(
                f"declared alias {alias!r} targets {target}, expected {canonical}"
            )

    def _discover_unmanaged_legacy_paths(self) -> tuple[str, ...]:
        claude_root = self.repo_root / ".claude" / "skills"
        agents_root = self.repo_root / ".agents" / "skills"
        if not claude_root.is_dir():
            return ()
        paths: list[str] = []
        for entry in sorted(claude_root.iterdir(), key=lambda item: item.name):
            if (agents_root / entry.name).exists():
                continue
            paths.append(f".claude/skills/{entry.name}")
        return tuple(paths)

    def get(self, canonical_name: str) -> SkillCapabilityManifest:
        name = _required_text(canonical_name, "canonical_name")
        try:
            return self._manifests[name]
        except KeyError:
            legacy_path = f".claude/skills/{name}"
            if legacy_path in self.unmanaged_legacy_paths:
                raise UnmanagedSkillError(
                    f"skill {name!r} is legacy-only and has no canonical capability manifest"
                ) from None
            raise

    def resolve_alias(self, alias: str) -> SkillCapabilityManifest:
        normalized = _repo_path(alias, "alias")
        try:
            return self._aliases[normalized]
        except KeyError:
            if normalized in self.unmanaged_legacy_paths:
                raise UnmanagedSkillError(
                    f"alias {normalized!r} points to an unmanaged legacy-only skill"
                ) from None
            raise

    def provenance(self, canonical_name: str) -> SkillProvenance:
        manifest = self.get(canonical_name)
        manifest_path = self._manifest_paths[manifest.canonical_name]
        manifest_digest = sha256(manifest_path.read_bytes()).hexdigest()
        paths = (
            f"{manifest.skill_path}/SKILL.md",
            *manifest.authoritative_resources,
            *manifest.helper_resources,
        )
        resources = tuple(
            ResourceDigest(
                path=path,
                sha256=sha256(self._resolve_repo_path(path, require_file=True).read_bytes()).hexdigest(),
            )
            for path in paths
        )
        return SkillProvenance(
            canonical_name=manifest.canonical_name,
            contract_version=manifest.contract_version,
            manifest_sha256=manifest_digest,
            skill_package_sha256=self._skill_package_digest(manifest),
            resources=resources,
        )

    def _skill_package_digest(self, manifest: SkillCapabilityManifest) -> str:
        """Digest every file/symlink entry in the canonical skill package deterministically."""
        package = self._resolve_repo_path(manifest.skill_path, require_file=False)
        digest = sha256()
        entries: list[tuple[str, bytes]] = []
        for candidate in package.rglob("*"):
            relative = candidate.relative_to(package).as_posix()
            if candidate.is_symlink():
                try:
                    resolved = candidate.resolve(strict=True)
                except FileNotFoundError as exc:
                    raise ValueError(
                        f"canonical skill package contains a broken symlink: {relative}"
                    ) from exc
                try:
                    resolved.relative_to(self.repo_root)
                except ValueError as exc:
                    raise ValueError(
                        f"canonical skill package symlink escapes repository: {relative}"
                    ) from exc
                payload = b"L\0" + candidate.readlink().as_posix().encode("utf-8")
                entries.append((relative, payload))
                continue
            if candidate.is_dir():
                continue
            if not candidate.is_file():
                raise ValueError(
                    f"canonical skill package contains unsupported entry: {relative}"
                )
            try:
                candidate.resolve(strict=True).relative_to(self.repo_root)
            except ValueError as exc:
                raise ValueError(
                    f"canonical skill package file escapes repository: {relative}"
                ) from exc
            entries.append((relative, b"F\0" + candidate.read_bytes()))

        for relative, payload in sorted(entries, key=lambda item: item[0]):
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(payload)
            digest.update(b"\0")
        return digest.hexdigest()


def _skill_frontmatter_name(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


def _alias_target(path: Path) -> Path:
    if path.is_symlink():
        return path.parent / path.readlink()
    if path.is_file():
        # Some export/checkout environments materialize a symlink as its target text.
        target_text = path.read_text(encoding="utf-8").strip()
        if target_text and "\n" not in target_text:
            return path.parent / target_text
    return path
