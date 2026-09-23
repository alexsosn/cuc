"""HARN-028b RED: real completion gates over the canonical candidate TSV."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

from harness.column_loader import AutomaticRow, LoadedColumn
from harness.column_state import (
    CapabilityRef,
    ColumnRunState,
    ColumnSnapshot,
    ColumnTask,
    ColumnToken,
    EvidenceRecord,
    EvidenceRecorded,
    ReconciliationClosed,
    ReviewedRow,
    TokenDecision,
    TokenReviewed,
    apply_column_event,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SHA0 = "0" * 64
HEADER = "id\tsurface form\tsign span\tmorphological parsing\tDULAT\tPOS\tgloss\tcomments\n"
MARKER = "# KTU 9.9 I:1\t\t\t\t\t\t\t\n"


def _module():
    try:
        return importlib.import_module("harness.completion_verifiers")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-028b completion verifiers are not implemented yet: {exc}")


def _review_status_module():
    path = REPO_ROOT / ".agents/skills/review-automatic-parsing/scripts/review_status.py"
    spec = importlib.util.spec_from_file_location("harn028b_review_status", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _task() -> ColumnTask:
    return ColumnTask(
        "run-028b",
        "CUC",
        "KTU 9.9",
        "I",
        "fixture-revision",
        CapabilityRef("review-automatic-parsing", "1.0.0", SHA0),
        ("review-status-clean", "lint-error-delta-no-regression", "report-token-count"),
    )


def _snapshot() -> ColumnSnapshot:
    return ColumnSnapshot(
        "snapshot-028b",
        "auto_parsing/0.2.8/KTU 9.9.tsv#I",
        "fixture-provenance",
        (ColumnToken("1001", 1, "I:1", "a"),),
    )


def _loaded(tmp_path: Path, *, baseline_morph: str = "a/") -> LoadedColumn:
    reviewed = (
        HEADER
        + MARKER
        + f"1001\ta\ta\t{baseline_morph}\ta\tn.\tA\tclean baseline\n"
    )
    return LoadedColumn(
        task=_task(),
        snapshot=_snapshot(),
        automatic_rows={"1001": (AutomaticRow("a/", "a", "n.", "A", ""),)},
        reviewed_text=reviewed,
        auto_sha256="1" * 64,
        reviewed_sha256="2" * 64,
        tf_version="0.2.8",
        auto_relative_path="auto_parsing/0.2.8/KTU 9.9.tsv",
        reviewed_relative_path="reviewed/KTU 9.9.tsv",
        repo_root=str(tmp_path),
        capability_root=str(REPO_ROOT),
    )


def _state(*, morphology: str = "a/", pos: str = "n.", comments: str = "checked") -> ColumnRunState:
    state = ColumnRunState.initial(_task(), _snapshot())
    evidence = EvidenceRecord("e-1001", "fixture", "fixture:1001", "prov", "evidence")
    state = apply_column_event(state, EvidenceRecorded("record-1001", evidence))
    row = ReviewedRow(morphology, "a", pos, "A", comments)
    decision = TokenDecision(
        "d-1001",
        "1001",
        (morphology,),
        ("e-1001",),
        "decision",
        reviewed_rows=(row,),
    )
    state = apply_column_event(state, TokenReviewed("review-1001", decision))
    return apply_column_event(state, ReconciliationClosed("close-reconciliation"))


def _verifier(tmp_path: Path, loaded: LoadedColumn | None = None):
    cls = _module().ProductionCompletionVerifier
    return cls(loaded_column=loaded or _loaded(tmp_path), run_dir=tmp_path / "run")


def test_review_status_scanner_exposes_shared_text_semantics() -> None:
    module = _review_status_module()
    seeded = HEADER + MARKER + "1001\ta\t[a ]\ta/\ta\tn.\tA\t## SEEDED from auto-parse; not yet hand-reviewed.\n"
    stats, outstanding = module.scan_text(seeded)
    assert stats["I"]["seeded"] == 1
    assert outstanding["I"] == [("1001", "a", "seeded")]


def test_review_status_gate_passes_clean_candidate_and_fails_undocumented_question(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    clean = verifier.verify(_state(), "review-status-clean", {}, "op-clean")
    assert clean.passed is True
    assert clean.decision_revision == _state().decision_revision

    dirty_state = _state(morphology="?", pos="?", comments="")
    dirty = verifier.verify(dirty_state, "review-status-clean", {}, "op-dirty")
    assert dirty.passed is False
    assert "undocumented" in dirty.summary.lower()


def test_lint_delta_gate_detects_new_structural_error(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    passing = verifier.verify(_state(), "lint-error-delta-no-regression", {}, "op-lint-pass")
    assert passing.passed is True

    bad = _state(morphology="a/, +a")
    failing = verifier.verify(bad, "lint-error-delta-no-regression", {}, "op-lint-fail")
    assert failing.passed is False
    assert "error" in failing.summary.lower()


def test_report_token_count_fails_closed_before_complete_traversal(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    initial = ColumnRunState.initial(_task(), _snapshot())
    result = verifier.verify(initial, "report-token-count", {}, "op-count")
    assert result.passed is False
    assert "0/1" in result.summary

    complete = verifier.verify(_state(), "report-token-count", {}, "op-count-ok")
    assert complete.passed is True
    assert "1/1" in complete.summary


def test_completion_gate_evidence_contains_hashes_counts_not_candidate_text(tmp_path: Path) -> None:
    sentinel = "PRIVATE-CANDIDATE-SENTINEL"
    state = _state(comments=sentinel)
    verifier = _verifier(tmp_path)
    for gate in _task().required_completion_gates:
        result = verifier.verify(state, gate, {}, f"op-{gate}")
        rendered_metadata = " ".join((*result.evidence_refs, result.summary))
        assert sentinel not in rendered_metadata
        assert "1001\ta" not in rendered_metadata


def test_unknown_completion_gate_is_rejected(tmp_path: Path) -> None:
    verifier = _verifier(tmp_path)
    with pytest.raises(ValueError, match="gate|unknown|unsupported"):
        verifier.verify(_state(), "not-a-real-gate", {}, "op-unknown")
