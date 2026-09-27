from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from harness.column_loader import AutomaticRow, LoadedColumn
from harness.column_state import (
    CapabilityRef,
    ColumnCompleted,
    ColumnRunState,
    ColumnSnapshot,
    ColumnTask,
    ColumnToken,
    CompletionGateRecorded,
    CompletionGateResult,
    EvidenceRecord,
    EvidenceRecorded,
    ReconciliationClosed,
    ReviewedRow,
    TokenDecision,
    TokenReviewed,
    apply_column_event,
)
from harness.github_effects import GitHubAction, GitHubEffectRequest


SHA0 = "0" * 64
HEADER = "id\tsurface form\tsign span\tmorphological parsing\tDULAT\tPOS\tgloss\tcomments\n"
MARKER = "# KTU 9.9 I:1\t\t\t\t\t\t\t\n"


def _module():
    try:
        import harness.production_column_output as output
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-028b output boundary is not implemented yet: {exc}")
    return output


def _task() -> ColumnTask:
    return ColumnTask(
        "run-output",
        "CUC",
        "KTU 9.9",
        "I",
        "fixture-revision",
        CapabilityRef("review-automatic-parsing", "1.0.0", SHA0),
        ("review-status-clean",),
    )


def _snapshot() -> ColumnSnapshot:
    return ColumnSnapshot(
        "snapshot-output",
        "auto_parsing/0.2.8/KTU 9.9.tsv#I",
        "fixture-provenance",
        (ColumnToken("1001", 1, "I:1", "a"),),
    )


def _source() -> str:
    return HEADER + MARKER + "1001\ta\ta\ta/\ta\tn.\tA\told\n"


def _loaded(repo_root: Path) -> LoadedColumn:
    reviewed = repo_root / "reviewed" / "KTU 9.9.tsv"
    auto = repo_root / "auto_parsing" / "0.2.8" / "KTU 9.9.tsv"
    reviewed.parent.mkdir(parents=True, exist_ok=True)
    auto.parent.mkdir(parents=True, exist_ok=True)
    reviewed.write_text(_source(), encoding="utf-8")
    auto.write_text("fixture automatic bytes\n", encoding="utf-8")
    return LoadedColumn(
        task=_task(),
        snapshot=_snapshot(),
        automatic_rows={"1001": (AutomaticRow("a/", "a", "n.", "A", ""),)},
        reviewed_text=_source(),
        auto_sha256=sha256(auto.read_bytes()).hexdigest(),
        reviewed_sha256=sha256(reviewed.read_bytes()).hexdigest(),
        tf_version="0.2.8",
        auto_relative_path="auto_parsing/0.2.8/KTU 9.9.tsv",
        reviewed_relative_path="reviewed/KTU 9.9.tsv",
        repo_root=str(repo_root),
        capability_root=str(repo_root),
    )


def _completed() -> ColumnRunState:
    state = ColumnRunState.initial(_task(), _snapshot())
    evidence = EvidenceRecord("e1", "fixture", "fixture:1001", "prov:e1", "evidence")
    state = apply_column_event(state, EvidenceRecorded("record-e1", evidence))
    decision = TokenDecision(
        "d1",
        "1001",
        ("a/",),
        ("e1",),
        "reviewed",
        reviewed_rows=(ReviewedRow("a/", "a", "n.", "A", "new"),),
    )
    state = apply_column_event(state, TokenReviewed("review-1001", decision))
    state = apply_column_event(state, ReconciliationClosed("close"))
    state = apply_column_event(
        state,
        CompletionGateRecorded(
            "gate-review-status",
            CompletionGateResult(
                "review-status-clean",
                True,
                state.decision_revision,
                ("gate:fixture",),
                "passed",
            ),
        ),
    )
    return apply_column_event(state, ColumnCompleted("complete"))


def _plan(tmp_path: Path, **overrides):
    output = _module()
    repo_root = tmp_path / "repo"
    loaded = _loaded(repo_root)
    values = dict(
        loaded_column=loaded,
        repository="alexsosn/cuc",
        branch="harn-028b-output-fixture",
        run_dir=repo_root / "agent" / "reports" / "run-output",
    )
    values.update(overrides)
    return output.ColumnOutputPlan(**values), loaded


def test_output_plan_rejects_upstream_protected_branch_and_wrong_reviewed_path(tmp_path: Path) -> None:
    output = _module()
    repo_root = tmp_path / "repo"
    loaded = _loaded(repo_root)
    run_dir = repo_root / "agent" / "reports" / "run-output"

    with pytest.raises((ValueError, PermissionError), match="repository|fork|upstream"):
        output.ColumnOutputPlan(
            loaded_column=loaded,
            repository="DT-UCPH/cuc",
            branch="harn-028b-output-fixture",
            run_dir=run_dir,
        )
    for branch in ("main", "agent-harness-safety"):
        with pytest.raises((ValueError, PermissionError), match="branch|protected|integration"):
            output.ColumnOutputPlan(
                loaded_column=loaded,
                repository="alexsosn/cuc",
                branch=branch,
                run_dir=run_dir,
            )

    escaped = replace(loaded, reviewed_relative_path="docs/not-reviewed.tsv")
    with pytest.raises((ValueError, PermissionError), match="reviewed|path"):
        output.ColumnOutputPlan(
            loaded_column=escaped,
            repository="alexsosn/cuc",
            branch="harn-028b-output-fixture",
            run_dir=run_dir,
        )


def test_output_plan_requires_ignored_local_run_directory(tmp_path: Path) -> None:
    output = _module()
    repo_root = tmp_path / "repo"
    loaded = _loaded(repo_root)
    with pytest.raises((ValueError, PermissionError), match="run_dir|reports|local|ignored"):
        output.ColumnOutputPlan(
            loaded_column=loaded,
            repository="alexsosn/cuc",
            branch="harn-028b-output-fixture",
            run_dir=repo_root / "reviewed" / "run-output",
        )


def test_stale_reviewed_source_fails_before_any_output_is_prepared(tmp_path: Path) -> None:
    plan, loaded = _plan(tmp_path)
    source = Path(loaded.repo_root) / loaded.reviewed_relative_path
    source.write_text(_source().replace("\told\n", "\texternally changed\n"), encoding="utf-8")

    with pytest.raises(ValueError, match="stale|digest|changed"):
        plan.validate_source()
    assert not plan.run_dir.exists()


def test_dry_run_writes_only_local_draft_and_never_touches_corpus_files(tmp_path: Path) -> None:
    plan, loaded = _plan(tmp_path)
    reviewed_path = Path(loaded.repo_root) / loaded.reviewed_relative_path
    auto_path = Path(loaded.repo_root) / loaded.auto_relative_path
    before_reviewed = reviewed_path.read_bytes()
    before_auto = auto_path.read_bytes()

    artifact = plan.write_dry_run(_completed())

    assert artifact.is_file()
    assert artifact.resolve().is_relative_to(plan.run_dir.resolve())
    assert artifact.read_text(encoding="utf-8").endswith("1001\ta\ta\ta/\ta\tn.\tA\tnew\n")
    assert reviewed_path.read_bytes() == before_reviewed
    assert auto_path.read_bytes() == before_auto


def test_real_output_builds_one_exact_harn023_update_contents_request(tmp_path: Path) -> None:
    plan, loaded = _plan(tmp_path)
    request = plan.build_update_request(_completed())

    assert isinstance(request, GitHubEffectRequest)
    assert request.repository == "alexsosn/cuc"
    assert request.action is GitHubAction.UPDATE_CONTENTS
    assert request.target_ref == "harn-028b-output-fixture"
    assert request.operation_id == "column-output:run-output"
    assert request.payload["path"] == loaded.reviewed_relative_path
    assert request.payload["expected_source_sha256"] == loaded.reviewed_sha256
    assert request.payload["content"].endswith("1001\ta\ta\ta/\ta\tn.\tA\tnew\n")
    assert set(request.payload) == {
        "path",
        "content",
        "expected_source_sha256",
        "message",
    }


def test_source_is_revalidated_immediately_before_real_request(tmp_path: Path) -> None:
    plan, loaded = _plan(tmp_path)
    plan.validate_source()
    source = Path(loaded.repo_root) / loaded.reviewed_relative_path
    source.write_text(_source().replace("\told\n", "\trace\n"), encoding="utf-8")

    with pytest.raises(ValueError, match="stale|digest|changed"):
        plan.build_update_request(_completed())
