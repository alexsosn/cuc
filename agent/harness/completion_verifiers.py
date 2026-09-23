"""Production completion gates for one loaded HARN-028 column.

Gate execution inspects the exact canonical candidate TSV produced by
``reviewed_materialization``. Candidate text is confined to the caller-supplied
local run directory; durable gate evidence contains only counts and digests.
"""

from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
import importlib.util
from pathlib import Path
from types import ModuleType

from linter.lint import lint_file

from .column_loader import LoadedColumn
from .column_state import ColumnRunState, CompletionGateResult
from .reviewed_materialization import materialize_candidate_column


_REVIEW_STATUS_GATE = "review-status-clean"
_LINT_GATE = "lint-error-delta-no-regression"
_COUNT_GATE = "report-token-count"
_SUPPORTED_GATES = frozenset({_REVIEW_STATUS_GATE, _LINT_GATE, _COUNT_GATE})


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@lru_cache(maxsize=8)
def _load_review_status_module(capability_root: str) -> ModuleType:
    path = (
        Path(capability_root)
        / ".agents"
        / "skills"
        / "review-automatic-parsing"
        / "scripts"
        / "review_status.py"
    )
    if not path.is_file():
        raise ValueError("review-status helper is missing from capability root")
    spec = importlib.util.spec_from_file_location(
        f"_harn028_review_status_{sha256(str(path).encode()).hexdigest()[:12]}",
        path,
    )
    if spec is None or spec.loader is None:
        raise ValueError("review-status helper cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "scan_text", None)):
        raise ValueError("review-status helper does not expose scan_text")
    return module


class ProductionCompletionVerifier:
    """Graph-compatible verifier for the canonical review capability gates."""

    __slots__ = ("_loaded", "_run_dir")

    def __init__(self, *, loaded_column: LoadedColumn, run_dir: Path | str) -> None:
        if not isinstance(loaded_column, LoadedColumn):
            raise ValueError("loaded_column must be LoadedColumn")
        path = Path(run_dir)
        if not path.name:
            raise ValueError("run_dir must be an explicit directory")
        self._loaded = loaded_column
        self._run_dir = path

    def _validate_binding(self, state: ColumnRunState) -> None:
        if not isinstance(state, ColumnRunState):
            raise ValueError("completion verification requires ColumnRunState")
        if state.task != self._loaded.task or state.snapshot != self._loaded.snapshot:
            raise ValueError("completion state does not match the loaded column")

    def _result(
        self,
        state: ColumnRunState,
        gate_id: str,
        passed: bool,
        summary: str,
        *evidence_refs: str,
    ) -> CompletionGateResult:
        return CompletionGateResult(
            gate_id,
            passed,
            state.decision_revision,
            tuple(evidence_refs),
            summary,
        )

    def _candidate(self, state: ColumnRunState) -> str:
        return materialize_candidate_column(self._loaded.reviewed_text, state)

    def _review_status(
        self, state: ColumnRunState, gate_id: str, operation_id: str
    ) -> CompletionGateResult:
        candidate = self._candidate(state)
        module = _load_review_status_module(self._loaded.capability_root)
        stats, _outstanding = module.scan_text(candidate)
        stat = stats.get(state.task.column, {})
        seeded = int(stat.get("seeded", 0))
        undocumented = int(stat.get("q_undocumented", 0))
        passed = seeded == 0 and undocumented == 0
        return self._result(
            state,
            gate_id,
            passed,
            f"review status seeded={seeded} undocumented={undocumented}",
            f"operation:{operation_id}",
            f"candidate-sha256:{_digest(candidate)}",
            "verifier:review_status.py:scan_text",
        )

    def _gate_path(self, label: str) -> Path:
        reviewed = self._run_dir / "completion-gates" / label / "reviewed"
        reviewed.mkdir(parents=True, exist_ok=True)
        return reviewed / Path(self._loaded.reviewed_relative_path).name

    @staticmethod
    def _lint_errors(path: Path) -> int:
        issues = lint_file(
            path=path,
            dulat_forms={},
            entry_meta={},
            lemma_map={},
            entry_stems={},
            entry_gender={},
            udb_words=None,
            baseline=None,
            input_format="auto",
            db_checks=False,
        )
        return sum(1 for issue in issues if issue.level == "error")

    def _lint_delta(
        self, state: ColumnRunState, gate_id: str, operation_id: str
    ) -> CompletionGateResult:
        candidate = self._candidate(state)
        baseline_path = self._gate_path("lint-baseline")
        candidate_path = self._gate_path("lint-candidate")
        baseline_path.write_text(self._loaded.reviewed_text, encoding="utf-8")
        candidate_path.write_text(candidate, encoding="utf-8")
        baseline_errors = self._lint_errors(baseline_path)
        candidate_errors = self._lint_errors(candidate_path)
        passed = candidate_errors <= baseline_errors
        return self._result(
            state,
            gate_id,
            passed,
            f"lint errors candidate={candidate_errors} baseline={baseline_errors}",
            f"operation:{operation_id}",
            f"candidate-sha256:{_digest(candidate)}",
            f"baseline-sha256:{_digest(self._loaded.reviewed_text)}",
            "verifier:agent.linter.lint:lint_file:no-db",
        )

    def _token_count(
        self, state: ColumnRunState, gate_id: str, operation_id: str
    ) -> CompletionGateResult:
        expected = len(state.snapshot.tokens)
        observed = sum(
            1
            for token_id in state.snapshot.token_ids
            if state.latest_decision(token_id) is not None
        )
        passed = observed == expected and state.initial_pass_complete
        return self._result(
            state,
            gate_id,
            passed,
            f"reported token count {observed}/{expected}",
            f"operation:{operation_id}",
            f"expected-token-count:{expected}",
            f"observed-token-count:{observed}",
        )

    def verify(
        self,
        state: ColumnRunState,
        gate_id: str,
        skill_context: object,
        operation_id: str,
    ) -> CompletionGateResult:
        del skill_context
        self._validate_binding(state)
        gate = _required_text(gate_id, "gate_id")
        operation = _required_text(operation_id, "operation_id")
        if gate not in _SUPPORTED_GATES or gate not in state.task.required_completion_gates:
            raise ValueError(f"unknown or unsupported completion gate: {gate}")
        if gate == _REVIEW_STATUS_GATE:
            return self._review_status(state, gate, operation)
        if gate == _LINT_GATE:
            return self._lint_delta(state, gate, operation)
        return self._token_count(state, gate, operation)
