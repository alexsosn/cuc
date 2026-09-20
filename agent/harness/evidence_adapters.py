"""HARN-028 evidence adapters: optional, local-only evidence sources behind one collector.

Each adapter wraps one resource that the review skill consults (DULAT citations,
EUPT, Tropper, the legacy expert review, corpus parallels, Burns) or the
repository's own automatic parse. Resources are resolved through the skill's
``sources.py`` locator (explicit path, ``CUC_*`` environment variable, sibling
checkout) and are never committed. An absent resource is recorded in the
``EvidencePolicy`` and contributes nothing; it never aborts a run.

Resource text is placed only in ``EvidenceRecord.summary``; ids, refs and the
skill context carry source ids, digests and locator kinds.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .column_loader import COLUMNLESS, LoadedColumn
from .column_state import ColumnRunState, ColumnToken, EvidenceRecord
from .evidence_policy import (
    AUTO_PARSING,
    BURNS,
    CORPUS_PARALLELS,
    DULAT,
    EUPT,
    LEGACY_REVIEW,
    REPOSITORY_SOURCE_IDS,
    RESOURCE_KIND_BY_SOURCE,
    TROPPER,
    EvidencePolicy,
    build_evidence_policy,
    resource_digest,
)

_SKILL_SCRIPTS = Path(".agents/skills/review-automatic-parsing/scripts")
_SEED_MARK = "SEEDED from auto-parse"
_EUPT_MODULES = ("EUPT_vocalisation", "EUPT_translation", "EUPT_commentary")
_BURNS_COL_RE = re.compile(r"^([IVX]+)\.(.*)$")
_HTML_RE = re.compile(r"<[^>]+>")
_MAX_PARALLELS = 12


# --- locators --------------------------------------------------------------------------


class ResourceLocator(Protocol):
    def locate(self, kind: str) -> tuple[Path, str] | None: ...


class StaticLocator:
    """Fixed kind -> (path, locator_kind) mapping; the test and CLI override."""

    def __init__(self, mapping: Mapping[str, tuple[Path | str, str]]) -> None:
        self._mapping = {kind: (Path(path), locator_kind) for kind, (path, locator_kind) in mapping.items()}

    def locate(self, kind: str) -> tuple[Path, str] | None:
        return self._mapping.get(kind)


def _load_script_module(repo_root: Path, name: str):
    path = repo_root / _SKILL_SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"cuc_skill_scripts.{name}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load skill script {path}")
    module = importlib.util.module_from_spec(spec)
    scripts_dir = str(path.parent)
    agent_dir = str(repo_root / "agent")
    for entry in (scripts_dir, agent_dir):
        if entry not in sys.path:
            sys.path.append(entry)
    previous = os.getcwd()
    try:
        # sources.py resolves the repository root from the working directory at import.
        os.chdir(repo_root)
        spec.loader.exec_module(module)
    finally:
        os.chdir(previous)
    return module


class SkillScriptLocator:
    """Resolve resources exactly as the review skill's ``sources.py`` does."""

    def __init__(self, repo_root: Path | str, explicit: Mapping[str, Path | str] | None = None) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.explicit = {kind: Path(path) for kind, path in (explicit or {}).items()}
        self._sources = None

    def _module(self):
        if self._sources is None:
            self._sources = _load_script_module(self.repo_root, "sources")
        return self._sources

    def locate(self, kind: str) -> tuple[Path, str] | None:
        explicit = self.explicit.get(kind)
        if explicit is not None:
            return (explicit, "explicit") if explicit.exists() else None
        sources = self._module()
        env_name = sources.ENV_VARS.get(kind, "")
        try:
            located = sources.locate(kind, None, required=False, quiet=True)
        except SystemExit:
            # An explicit/env path that does not exist; record absence, do not abort.
            return None
        except Exception:
            return None
        if located is None:
            return None
        path = Path(located)
        env_value = os.environ.get(env_name, "").strip() if env_name else ""
        if env_value and Path(env_value).expanduser() == path:
            return path, "env"
        return path, "sibling"


# --- adapter protocol ------------------------------------------------------------------


@dataclass(frozen=True)
class TokenEvidenceContext:
    loaded_column: LoadedColumn
    state: ColumnRunState
    token: ColumnToken
    operation_id: str
    policy: EvidencePolicy | None = None

    def __post_init__(self) -> None:
        if self.policy is not None and not isinstance(self.policy, EvidencePolicy):
            raise ValueError("policy must be EvidencePolicy or None")
        if not isinstance(self.loaded_column, LoadedColumn):
            raise ValueError("loaded_column must be LoadedColumn")
        if not isinstance(self.state, ColumnRunState):
            raise ValueError("state must be ColumnRunState")
        if not isinstance(self.token, ColumnToken):
            raise ValueError("token must be ColumnToken")
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")

    @property
    def tablet(self) -> str:
        return self.loaded_column.task.tablet

    @property
    def tablet_number(self) -> str:
        return self.tablet.split(" ", 1)[1]

    @property
    def column(self) -> str:
        return self.loaded_column.task.column

    @property
    def line(self) -> str:
        return self.token.line_ref.split(":")[-1]

    @property
    def modules_ref(self) -> str:
        """Modules-cache key; columnless texts are normalised as column I there."""

        column = "I" if self.column == COLUMNLESS else self.column
        return f"{self.tablet} {column}:{self.line}"

    @property
    def dulat_ref(self) -> str:
        """DULAT reverse-index key, which omits the synthetic column."""

        if self.column == COLUMNLESS:
            return f"{self.tablet}:{self.line}"
        return f"{self.tablet} {self.column}:{self.line}"


class EvidenceAdapter(Protocol):
    source_id: str

    def collect(self, ctx: TokenEvidenceContext, resource: Path | None) -> tuple[EvidenceRecord, ...]: ...


def _record(ctx: TokenEvidenceContext, source_id: str, index: int, source_ref: str, provenance_ref: str, summary: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"{ctx.operation_id}:{source_id}:{index}",
        source_id=source_id,
        source_ref=source_ref,
        provenance_ref=provenance_ref,
        summary=summary,
    )


def _connect_ro(path: Path) -> sqlite3.Connection:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.execute("select name from sqlite_master limit 1").fetchall()
        return con
    except sqlite3.Error:
        return sqlite3.connect(str(path))


def _repository_provenance(ctx: TokenEvidenceContext) -> str:
    return f"repository:{ctx.loaded_column.task.repository_revision}"


# --- adapters --------------------------------------------------------------------------


class AutoParsingAdapter:
    """The current automatic parse; always enabled and always available."""

    source_id = AUTO_PARSING

    def collect(self, ctx: TokenEvidenceContext, resource: Path | None) -> tuple[EvidenceRecord, ...]:
        rows = ctx.loaded_column.automatic_rows[ctx.token.token_id]
        base_ref = f"{ctx.loaded_column.auto_relative_path}:{ctx.token.token_id}"
        provenance = f"auto-parsing:{ctx.loaded_column.tf_version}:{ctx.loaded_column.auto_sha256[:16]}"
        return tuple(
            _record(
                ctx,
                self.source_id,
                index,
                f"{base_ref}:alt-{index}",
                provenance,
                json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True),
            )
            for index, row in enumerate(rows, start=1)
        )


class DulatAdapter:
    source_id = DULAT

    def collect(self, ctx: TokenEvidenceContext, resource: Path | None) -> tuple[EvidenceRecord, ...]:
        if resource is None:
            return ()
        con = _connect_ro(resource)
        try:
            rows = con.execute(
                "select norm_ref, entry_id, payload from dulat_reverse_refs where norm_ref=? "
                "order by entry_id",
                (ctx.dulat_ref,),
            ).fetchall()
        finally:
            con.close()
        provenance = f"dulat_search:{_digest_marker(ctx, self.source_id)}"
        records: list[EvidenceRecord] = []
        for index, (norm_ref, entry_id, payload) in enumerate(rows, start=1):
            try:
                data = json.loads(payload)
            except (TypeError, ValueError):
                data = {}
            label = _HTML_RE.sub("", str(data.get("label") or "")).strip()
            summary = json.dumps(
                {
                    "entry_id": entry_id,
                    "label": label,
                    "sense_labels": list(data.get("sense_labels") or []),
                    "reference_translations": list(data.get("reference_translations") or []),
                    "stem_names": list(data.get("stem_names") or []),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            records.append(
                _record(ctx, self.source_id, index, f"dulat_search:{norm_ref}:entry-{entry_id}", provenance, summary)
            )
        return tuple(records)


class EuptAdapter:
    source_id = EUPT

    def collect(self, ctx: TokenEvidenceContext, resource: Path | None) -> tuple[EvidenceRecord, ...]:
        if resource is None:
            return ()
        con = _connect_ro(resource)
        try:
            placeholders = ",".join("?" * len(_EUPT_MODULES))
            rows = con.execute(
                "select module_id, content_text from module_records "
                f"where ref_norm=? and module_id in ({placeholders}) order by id",
                (ctx.modules_ref, *_EUPT_MODULES),
            ).fetchall()
        finally:
            con.close()
        provenance = f"modules_cache:{_digest_marker(ctx, self.source_id)}"
        return tuple(
            _record(ctx, self.source_id, index, f"eupt:{module_id}:{ctx.modules_ref}", provenance, text or "")
            for index, (module_id, text) in enumerate(rows, start=1)
            if (text or "").strip()
        )


class TropperAdapter:
    source_id = TROPPER

    @staticmethod
    def index_path(resource: Path) -> Path | None:
        if resource.is_file():
            if resource.suffix == ".sqlite":
                return resource
            candidate = resource.with_suffix(".index.sqlite")
            return candidate if candidate.is_file() else None
        if resource.is_dir():
            matches = sorted(resource.glob("*.index.sqlite"))
            return matches[0] if matches else None
        return None

    def collect(self, ctx: TokenEvidenceContext, resource: Path | None) -> tuple[EvidenceRecord, ...]:
        if resource is None:
            return ()
        index = self.index_path(resource)
        if index is None:
            return ()
        column = "" if ctx.column == COLUMNLESS else ctx.column
        con = _connect_ro(index)
        try:
            rows = con.execute(
                "select pages from ktu where tablet=? and column=? and line=? and verified=1 order by pages",
                (ctx.tablet_number, column, ctx.line),
            ).fetchall()
        finally:
            con.close()
        pages = sorted(
            {page for (value,) in rows for page in re.findall(r"\d+", value or "")},
            key=int,
        )
        if not pages:
            return ()
        ref = ctx.dulat_ref if ctx.column == COLUMNLESS else f"{ctx.tablet} {ctx.column}:{ctx.line}"
        summary = f"Tropper, Ugaritische Grammatik (2012) cites {ref} on pages {', '.join(pages)}"
        return (
            _record(
                ctx,
                self.source_id,
                1,
                f"tropper:{ref}:pages-{','.join(pages)}",
                f"tropper_index:{_digest_marker(ctx, self.source_id)}",
                summary,
            ),
        )


class LegacyReviewAdapter:
    """The legacy expert review aligned by line marker and surface, never by legacy id."""

    source_id = LEGACY_REVIEW

    def __init__(self) -> None:
        self._cache: dict[Path, dict[tuple[str, str], list[tuple[str, str, set[str], bool]]]] = {}

    @staticmethod
    def legacy_path(ctx: TokenEvidenceContext) -> Path | None:
        candidate = Path(ctx.loaded_column.repo_root) / "reviewed" / f"{ctx.tablet}.txt"
        return candidate if candidate.is_file() else None

    def _blocks(self, scripts_root: Path, path: Path):
        if path not in self._cache:
            legacy_align = _load_script_module(scripts_root, "legacy_align")
            try:
                self._cache[path] = legacy_align.load(path, legacy_align.LEGACY_IDX)
            except SystemExit:
                self._cache[path] = {}
        return self._cache[path]

    def collect(self, ctx: TokenEvidenceContext, resource: Path | None) -> tuple[EvidenceRecord, ...]:
        path = Path(resource) if resource is not None else self.legacy_path(ctx)
        if path is None or not path.is_file():
            return ()
        repo_root = Path(ctx.loaded_column.repo_root)
        blocks = self._blocks(Path(ctx.loaded_column.capability_root), path)
        key = (ctx.column, ctx.line)
        entries = blocks.get(key, [])
        surface = _normalize_surface(ctx.token.surface)
        matches = [entry for entry in entries if _normalize_surface(entry[1]) == surface and not entry[3]]
        try:
            relative = path.relative_to(repo_root).as_posix()
        except ValueError:
            relative = path.name
        provenance = _repository_provenance(ctx)
        records: list[EvidenceRecord] = []
        for index, (_legacy_id, legacy_surface, analyses, _seeded) in enumerate(matches, start=1):
            summary = json.dumps(
                {"surface": legacy_surface, "analyses": sorted(analyses)},
                ensure_ascii=False,
                sort_keys=True,
            )
            records.append(
                _record(
                    ctx,
                    self.source_id,
                    index,
                    f"legacy-review:{relative}:{ctx.token.line_ref}:{legacy_surface}",
                    provenance,
                    summary,
                )
            )
        return tuple(records)


class CorpusParallelsAdapter:
    """Other reviewed tokens in the repository with the same normalised surface.

    Parallels are aggregated per distinct curated reading (analysis, DULAT, POS,
    gloss) with an attestation count and a few example token refs, so a common
    word does not flood the evidence with identical rows.
    """

    source_id = CORPUS_PARALLELS

    def __init__(self) -> None:
        self._index: dict[Path, dict[str, list[tuple[str, str, str, str, str, str]]]] = {}

    def _build(self, repo_root: Path) -> dict[str, list[tuple[str, str, str, str, str, str]]]:
        index: dict[str, list[tuple[str, str, str, str, str, str]]] = {}
        for path in sorted((repo_root / "reviewed").glob("KTU *.tsv")):
            relative = path.relative_to(repo_root).as_posix()
            for raw in path.read_text(encoding="utf-8").splitlines()[1:]:
                if raw.startswith("#"):
                    continue
                fields = raw.split("\t")
                if len(fields) != 8 or not fields[0].strip().isdigit():
                    continue
                if _SEED_MARK in fields[7]:
                    continue
                index.setdefault(_normalize_surface(fields[1]), []).append(
                    (relative, fields[0].strip(), fields[3], fields[4], fields[5], fields[6])
                )
        return index

    def collect(self, ctx: TokenEvidenceContext, resource: Path | None) -> tuple[EvidenceRecord, ...]:
        repo_root = Path(ctx.loaded_column.repo_root)
        if repo_root not in self._index:
            self._index[repo_root] = self._build(repo_root)
        hits = [
            hit
            for hit in self._index[repo_root].get(_normalize_surface(ctx.token.surface), [])
            if hit[1] != ctx.token.token_id
        ]
        groups: dict[tuple[str, str, str, str], list[tuple[str, str]]] = {}
        for relative, token_id, analysis, dulat, pos, gloss in hits:
            groups.setdefault((analysis, dulat, pos, gloss), []).append((relative, token_id))
        provenance = _repository_provenance(ctx)
        records: list[EvidenceRecord] = []
        ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
        for index, ((analysis, dulat, pos, gloss), refs) in enumerate(ordered[:_MAX_PARALLELS], start=1):
            summary = json.dumps(
                {
                    "morphological_parsing": analysis,
                    "dulat": dulat,
                    "pos": pos,
                    "gloss": gloss,
                    "attestations": len(refs),
                    "examples": [f"{relative}:{token_id}" for relative, token_id in refs[:3]],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            first_relative, first_token = refs[0]
            records.append(
                _record(
                    ctx,
                    self.source_id,
                    index,
                    f"corpus-parallels:{first_relative}:{first_token}",
                    provenance,
                    summary,
                )
            )
        return tuple(records)


class BurnsAdapter:
    """Burns (2003) cultic-vocabulary workbook CSV rows citing the token's line."""

    source_id = BURNS

    def __init__(self) -> None:
        self._cache: dict[Path, dict[tuple[str, str, str], list[tuple[str, dict[str, str]]]]] = {}

    @staticmethod
    def effective_dir(resource: Path) -> Path:
        return resource / "output" if (resource / "output").is_dir() else resource

    def _index(self, root: Path):
        if root in self._cache:
            return self._cache[root]
        index: dict[tuple[str, str, str], list[tuple[str, dict[str, str]]]] = {}
        for csv_path in sorted(root.glob("*/*.csv")):
            relative = csv_path.relative_to(root).as_posix()
            with csv_path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    tablet = (row.get("ktu") or "").strip()
                    refs = (row.get("references") or "").strip()
                    if not tablet or not refs or tablet.lower().startswith("not attested"):
                        continue
                    for chunk in refs.split(";"):
                        chunk = chunk.strip()
                        if not chunk:
                            continue
                        match = _BURNS_COL_RE.match(chunk)
                        column, rest = (match.group(1), match.group(2)) if match else ("", chunk)
                        for line in re.findall(r"\d+", rest):
                            index.setdefault((tablet, column, line), []).append((relative, row))
        self._cache[root] = index
        return index

    def collect(self, ctx: TokenEvidenceContext, resource: Path | None) -> tuple[EvidenceRecord, ...]:
        if resource is None or not Path(resource).is_dir():
            return ()
        root = self.effective_dir(Path(resource))
        column = "" if ctx.column == COLUMNLESS else ctx.column
        hits = self._index(root).get((ctx.tablet_number, column, ctx.line), [])
        provenance = f"burns_workbooks:{_digest_marker(ctx, self.source_id)}"
        ref = f"{ctx.tablet} {ctx.column}:{ctx.line}" if column else ctx.dulat_ref
        records: list[EvidenceRecord] = []
        seen: set[tuple[str, str, str]] = set()
        for relative, row in hits:
            headword = (row.get("headword") or "").strip()
            key = (relative, headword, (row.get("root") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            payload = {
                k: v for k, v in row.items() if k not in ("ktu", "references") and (v or "").strip()
            }
            records.append(
                _record(
                    ctx,
                    self.source_id,
                    len(records) + 1,
                    f"burns:{relative}:{ref}:{headword}",
                    provenance,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                )
            )
        return tuple(records)


def _digest_marker(ctx: TokenEvidenceContext, source_id: str) -> str:
    """Short resource digest for provenance refs; never a path."""

    availability = ctx.policy.availability_for(source_id) if ctx.policy is not None else None
    digest = availability.resource_sha256 if availability is not None else None
    return (digest or "unversioned")[:16]


_NORMALIZE_SURFACE: Callable[[str], str] | None = None


def _normalize_surface(value: str) -> str:
    global _NORMALIZE_SURFACE
    if _NORMALIZE_SURFACE is None:
        from linter.lint import normalize_surface

        _NORMALIZE_SURFACE = normalize_surface
    return _NORMALIZE_SURFACE((value or "").strip())


DEFAULT_ADAPTER_FACTORIES: Mapping[str, Callable[[], Any]] = {
    AUTO_PARSING: AutoParsingAdapter,
    DULAT: DulatAdapter,
    TROPPER: TropperAdapter,
    EUPT: EuptAdapter,
    LEGACY_REVIEW: LegacyReviewAdapter,
    CORPUS_PARALLELS: CorpusParallelsAdapter,
    BURNS: BurnsAdapter,
}


def _policy_digest(path: Path) -> str:
    """Digest the artifact an adapter actually reads, not e.g. a whole OCR directory."""

    target = Path(path)
    if target.is_dir():
        index = TropperAdapter.index_path(target)
        if index is not None:
            return resource_digest(index)
        csvs = sorted(BurnsAdapter.effective_dir(target).glob("*/*.csv"))
        if csvs:
            from hashlib import sha256

            digest = sha256()
            for item in csvs:
                digest.update(item.name.encode("utf-8"))
                digest.update(resource_digest(item).encode("ascii"))
            return digest.hexdigest()
    return resource_digest(target)


def build_policy(requested: tuple[str, ...] | list[str], locator: ResourceLocator) -> EvidencePolicy:
    return build_evidence_policy(requested, locator, resource_digest_for=_policy_digest)


# --- collector -------------------------------------------------------------------------


class EvidenceCollector:
    """Compose enabled adapters into the HARN-004 ``collect_evidence`` boundary."""

    def __init__(
        self,
        policy: EvidencePolicy,
        loaded_column: LoadedColumn,
        *,
        locator: ResourceLocator,
        adapter_factories: Mapping[str, Callable[[], Any]] | None = None,
    ) -> None:
        if not isinstance(policy, EvidencePolicy):
            raise ValueError("policy must be EvidencePolicy")
        if not isinstance(loaded_column, LoadedColumn):
            raise ValueError("loaded_column must be LoadedColumn")
        factories = dict(DEFAULT_ADAPTER_FACTORIES)
        factories.update(adapter_factories or {})
        self.policy = policy
        self.loaded_column = loaded_column
        self._adapters: dict[str, Any] = {}
        self._resources: dict[str, Path | None] = {}
        for source_id in policy.enabled_sources:
            availability = policy.availability_for(source_id)
            if availability is None or not availability.available:
                continue
            adapter = factories[source_id]()
            if adapter.source_id != source_id:
                raise ValueError(f"adapter for {source_id} reports source_id {adapter.source_id}")
            self._adapters[source_id] = adapter
            if source_id in REPOSITORY_SOURCE_IDS:
                self._resources[source_id] = None
            else:
                located = locator.locate(RESOURCE_KIND_BY_SOURCE[source_id])
                self._resources[source_id] = Path(located[0]) if located is not None else None

    def initialize_skill_context(self, state: ColumnRunState, operation_id: str) -> dict[str, Any]:
        if not isinstance(state, ColumnRunState):
            raise ValueError("state must be ColumnRunState")
        task = state.task
        return {
            "operation_id": operation_id,
            "capability": task.capability.to_dict(),
            "scope": {
                "tablet": task.tablet,
                "column": task.column,
                "token_count": len(state.snapshot.tokens),
                "every_token_in_order": True,
                "worklists_are_attention_only": True,
            },
            "worklist": {"priority_token_ids": list(task.evidence_priority_token_ids)},
            "evidence": {
                "policy_sha256": self.policy.sha256,
                "enabled_sources": list(self.policy.enabled_sources),
                "available_sources": list(self.policy.available_sources),
                "absent_sources": list(self.policy.absent_sources),
                "availability": [item.to_dict() for item in self.policy.availability],
            },
            "sources": {
                "tf_version": self.loaded_column.tf_version,
                "auto_sha256": self.loaded_column.auto_sha256,
                "reviewed_sha256": self.loaded_column.reviewed_sha256,
            },
        }

    def collect_evidence(
        self,
        state: ColumnRunState,
        token: ColumnToken,
        skill_context: Any,
        operation_id: str,
    ) -> tuple[EvidenceRecord, ...]:
        if not isinstance(state, ColumnRunState):
            raise ValueError("state must be ColumnRunState")
        if token.token_id not in state.snapshot.token_ids:
            raise ValueError(f"token {token.token_id} is outside the column snapshot")
        ctx = TokenEvidenceContext(self.loaded_column, state, token, operation_id, self.policy)
        records: list[EvidenceRecord] = []
        for source_id in self.policy.enabled_sources:
            adapter = self._adapters.get(source_id)
            if adapter is None:
                continue
            collected = adapter.collect(ctx, self._resources.get(source_id))
            for item in collected:
                if not isinstance(item, EvidenceRecord) or item.source_id != source_id:
                    raise ValueError(f"adapter {source_id} returned a foreign evidence record")
            records.extend(collected)
        if not any(item.source_id == AUTO_PARSING for item in records):
            raise ValueError("auto-parsing evidence must be present for every token visit")
        return tuple(records)

