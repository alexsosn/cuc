"""Controlled reviewed-column output boundary for HARN-028 production runs.

This module never talks to GitHub directly. It either writes a local ignored draft
under agent/reports/ or returns one HARN-023 UPDATE_CONTENTS request for a trusted
host/gateway to execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from .column_loader import LoadedColumn
from .column_state import ColumnRunState
from .github_effects import (
    GitHubAction,
    GitHubEffectRequest,
    RepositoryClass,
    classify_repository,
)
from .reviewed_materialization import materialize_completed_column


_PROTECTED_BRANCHES = frozenset({"main", "agent-harness-safety"})
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _sha256_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _validate_branch(value: object) -> str:
    branch = _required_text(value, "branch")
    if (
        not _BRANCH_RE.fullmatch(branch)
        or branch.startswith("/")
        or branch.endswith("/")
        or branch.startswith("refs/")
        or "\\" in branch
        or any(part in {"", ".", ".."} for part in branch.split("/"))
    ):
        raise ValueError("branch must be a plain repository branch name")
    if branch.casefold() in _PROTECTED_BRANCHES:
        raise PermissionError(
            "production column output cannot target a protected integration branch"
        )
    return branch


def _validate_reviewed_path(loaded: LoadedColumn) -> str:
    expected = f"reviewed/{loaded.task.tablet}.tsv"
    relative = loaded.reviewed_relative_path
    if not isinstance(relative, str) or relative != expected:
        raise PermissionError(
            f"reviewed output path must be the exact loaded tablet path {expected!r}"
        )
    path = PurePosixPath(relative)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PermissionError(
            "reviewed output path must be repository-relative and traversal-free"
        )
    return relative


def _same_loaded_work_unit(loaded: LoadedColumn, state: ColumnRunState) -> bool:
    return (
        state.task.corpus == loaded.task.corpus
        and state.task.tablet == loaded.task.tablet
        and state.task.column == loaded.task.column
        and state.task.repository_revision == loaded.task.repository_revision
        and state.task.capability == loaded.task.capability
        and state.task.required_completion_gates
        == loaded.task.required_completion_gates
        and state.snapshot.to_dict() == loaded.snapshot.to_dict()
    )


@dataclass(frozen=True)
class ColumnOutputPlan:
    """Preflighted output identity for exactly one loaded reviewed-column source."""

    loaded_column: LoadedColumn
    repository: str
    branch: str
    run_dir: Path | str

    def __post_init__(self) -> None:
        if not isinstance(self.loaded_column, LoadedColumn):
            raise ValueError("loaded_column must be LoadedColumn")

        repository = _required_text(self.repository, "repository")
        try:
            repository_class = classify_repository(repository)
        except ValueError as exc:
            raise PermissionError(
                "production output repository must be the configured fork"
            ) from exc
        if repository_class is not RepositoryClass.FORK:
            raise PermissionError(
                "production output repository must be the configured fork"
            )
        object.__setattr__(self, "repository", "alexsosn/cuc")
        object.__setattr__(self, "branch", _validate_branch(self.branch))

        _validate_reviewed_path(self.loaded_column)

        repo_root = Path(self.loaded_column.repo_root).resolve()
        local_root = (repo_root / "agent" / "reports").resolve()
        run_dir = Path(self.run_dir).resolve()
        try:
            relative = run_dir.relative_to(local_root)
        except ValueError as exc:
            raise PermissionError(
                "run_dir must stay inside the ignored local agent/reports directory"
            ) from exc
        if not relative.parts:
            raise ValueError("run_dir must name one run beneath agent/reports")
        object.__setattr__(self, "run_dir", run_dir)

        encoded = self.loaded_column.reviewed_text.encode("utf-8")
        if _sha256_bytes(encoded) != self.loaded_column.reviewed_sha256:
            raise ValueError(
                "loaded reviewed text digest does not match loaded source digest"
            )

    @property
    def source_path(self) -> Path:
        return (
            Path(self.loaded_column.repo_root).resolve()
            / self.loaded_column.reviewed_relative_path
        )

    def validate_source(self) -> None:
        try:
            current = self.source_path.read_bytes()
        except FileNotFoundError as exc:
            raise ValueError(
                "reviewed source changed or disappeared after column load"
            ) from exc
        if _sha256_bytes(current) != self.loaded_column.reviewed_sha256:
            raise ValueError(
                "reviewed source is stale: source digest changed after column load"
            )
        if current != self.loaded_column.reviewed_text.encode("utf-8"):
            raise ValueError(
                "reviewed source is stale: loaded text differs from current source"
            )

    def _render(self, completed_state: ColumnRunState) -> str:
        if not isinstance(completed_state, ColumnRunState):
            raise ValueError("completed_state must be ColumnRunState")
        if not _same_loaded_work_unit(self.loaded_column, completed_state):
            raise ValueError(
                "completed state does not match the exact loaded column work unit"
            )
        return materialize_completed_column(
            self.loaded_column.reviewed_text,
            completed_state,
        )

    def write_dry_run(self, completed_state: ColumnRunState) -> Path:
        """Atomically write a local candidate draft without touching corpus files."""

        self.validate_source()
        rendered = self._render(completed_state)
        draft_dir = self.run_dir / "drafts"
        draft_dir.mkdir(parents=True, exist_ok=True)
        destination = (
            draft_dir / Path(self.loaded_column.reviewed_relative_path).name
        )

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=draft_dir,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp_path = Path(handle.name)
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, destination)
            tmp_path = None
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
        return destination

    def build_update_request(
        self,
        completed_state: ColumnRunState,
    ) -> GitHubEffectRequest:
        """Return one exact HARN-023 request; no GitHub transport is reachable here."""

        self.validate_source()
        rendered = self._render(completed_state)
        path = _validate_reviewed_path(self.loaded_column)
        return GitHubEffectRequest(
            f"column-output:{completed_state.task.task_id}",
            self.repository,
            GitHubAction.UPDATE_CONTENTS,
            {
                "path": path,
                "content": rendered,
                "expected_source_sha256": self.loaded_column.reviewed_sha256,
                "message": (
                    f"review {completed_state.task.tablet} "
                    f"{completed_state.task.column} "
                    f"from {completed_state.task.task_id}"
                ),
            },
            target_ref=self.branch,
        )
