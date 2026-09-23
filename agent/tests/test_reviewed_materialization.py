from __future__ import annotations

from dataclasses import replace
import importlib
import inspect

import pytest

from harness import column_state as cs


SHA0 = "0" * 64
HEADER = "id\tsurface form\tsign span\tmorphological parsing\tDULAT\tPOS\tgloss\tcomments\n"
PRELUDE = "# KTU 9.9 II:0\t\t\t\t\t\t\t\n"
LINE_I1 = "# KTU 9.9 I:1\t\t\t\t\t\t\t\n"
LINE_I2 = "# KTU 9.9 I:2\t\t\t\t\t\t\t\n"
LINE_II1 = "# KTU 9.9 II:1\t\t\t\t\t\t\t\n"
OUTSIDE_BEFORE = "u0\toutside\t[out]\told/\told\tn.\toutside\tkeep-before\n"
OUTSIDE_AFTER = "u9\tafter\t[after]\told/\told\tn.\tafter\tkeep-after\n"


def _load_materializer():
    try:
        return importlib.import_module("harness.reviewed_materialization")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-027 materializer is not implemented yet: {exc}")


def _row(morphology: str, dulat: str, pos: str, gloss: str, comments: str = "") -> cs.ReviewedRow:
    return cs.ReviewedRow(morphology, dulat, pos, gloss, comments)


def _task() -> cs.ColumnTask:
    return cs.ColumnTask(
        task_id="materialize-run",
        corpus="CUC",
        tablet="KTU 9.9",
        column="I",
        repository_revision="fixture-revision",
        capability=cs.CapabilityRef("review-automatic-parsing", "1.0.0", SHA0),
        required_completion_gates=("review-status-clean",),
    )


def _snapshot() -> cs.ColumnSnapshot:
    return cs.ColumnSnapshot(
        "snapshot-9.9-i",
        "reviewed/KTU 9.9.tsv",
        "fixture-provenance",
        (
            cs.ColumnToken("t1", 1, "I:1", "a"),
            cs.ColumnToken("t2", 2, "I:1", "b"),
            cs.ColumnToken("t3", 3, "I:2", "c"),
        ),
    )


def _decision(token_id: str, rows: tuple[cs.ReviewedRow, ...]) -> cs.TokenDecision:
    analyses = tuple(dict.fromkeys(row.morphological_parsing for row in rows))
    return cs.TokenDecision(
        f"decision-{token_id}",
        token_id,
        analyses,
        (f"evidence-{token_id}",),
        f"reviewed {token_id}",
        reviewed_rows=rows,
    )


def _default_rows() -> dict[str, tuple[cs.ReviewedRow, ...]]:
    return {
        "t1": (_row("a/", "a", "n. m. sg. abs.", "A", "DULAT s.v. a"),),
        "t2": (
            _row("b/", "b (I)", "n. m. sg. abs.", "B-one", "reading one"),
            _row("b/", "b (II)", "vb G impv. m. sg.", "B-two", "reading two"),
        ),
        "t3": (_row("c/", "c", "prep.", "C", ""),),
    }


def _state(
    *,
    rows_by_token: dict[str, tuple[cs.ReviewedRow, ...]] | None = None,
    complete: bool = True,
    close_reconciliation: bool = True,
    pass_gate: bool = True,
    unresolved_revisit: bool = False,
) -> cs.ColumnRunState:
    rows_by_token = _default_rows() if rows_by_token is None else rows_by_token
    state = cs.ColumnRunState.initial(_task(), _snapshot())

    for token_id in ("t1", "t2", "t3"):
        evidence = cs.EvidenceRecord(
            f"evidence-{token_id}",
            "fixture",
            f"fixture:{token_id}",
            "fixture-provenance",
            f"evidence for {token_id}",
        )
        state = cs.apply_column_event(
            state,
            cs.EvidenceRecorded(f"record-{token_id}", evidence),
        )
        state = cs.apply_column_event(
            state,
            cs.TokenReviewed(
                f"review-{token_id}", _decision(token_id, rows_by_token[token_id])
            ),
        )

    if unresolved_revisit:
        state = cs.apply_column_event(
            state,
            cs.RevisitRequested(
                "request-revisit-t1",
                cs.RevisitRequest("revisit-t1", "t1", "re-check t1"),
            ),
        )
        return state

    if close_reconciliation:
        state = cs.apply_column_event(state, cs.ReconciliationClosed("close-reconciliation"))
        state = cs.apply_column_event(
            state,
            cs.CompletionGateRecorded(
                "gate-review-status",
                cs.CompletionGateResult(
                    "review-status-clean",
                    pass_gate,
                    state.decision_revision,
                    ("fixture:review-status",),
                    "checked",
                ),
            ),
        )
    if complete and close_reconciliation and pass_gate:
        state = cs.apply_column_event(state, cs.ColumnCompleted("complete-column"))
    return state


def _source_text() -> str:
    return (
        HEADER
        + PRELUDE
        + OUTSIDE_BEFORE
        + LINE_I1
        + "t1\ta\t[a ]\tOLD-A-1\told-a-1\tn.\told A1\t## SEEDED from auto-parse; not yet hand-reviewed.\n"
        + "t1\ta\t[a ]\tOLD-A-2\told-a-2\tn.\told A2\told alternative\n"
        + "t2\tb\t[b ]\tOLD-B\told-b\tn.\told B\t## SEEDED from auto-parse; not yet hand-reviewed.\n"
        + LINE_I2
        + "t3\tc\t[c ]\tOLD-C\told-c\tn.\told C\told C comment\n"
        + LINE_II1
        + OUTSIDE_AFTER
    )


def _expected_text() -> str:
    return (
        HEADER
        + PRELUDE
        + OUTSIDE_BEFORE
        + LINE_I1
        + "t1\ta\t[a ]\ta/\ta\tn. m. sg. abs.\tA\tDULAT s.v. a\n"
        + "t2\tb\t[b ]\tb/\tb (I)\tn. m. sg. abs.\tB-one\treading one\n"
        + "t2\tb\t[b ]\tb/\tb (II)\tvb G impv. m. sg.\tB-two\treading two\n"
        + LINE_I2
        + "t3\tc\t[c ]\tc/\tc\tprep.\tC\t\n"
        + LINE_II1
        + OUTSIDE_AFTER
    )


def test_completed_column_replaces_target_blocks_and_preserves_unrelated_lines() -> None:
    materializer = _load_materializer()
    source = _source_text()
    rendered = materializer.materialize_completed_column(source, _state())

    assert rendered == _expected_text()
    assert OUTSIDE_BEFORE in rendered
    assert OUTSIDE_AFTER in rendered
    assert rendered.count("\nt1\t") == 1
    assert rendered.count("\nt2\t") == 2
    assert "OLD-" not in rendered
    assert "SEEDED from auto-parse" not in rendered


def test_duplicate_morphology_lexical_alternatives_survive_materialization() -> None:
    materializer = _load_materializer()
    rendered = materializer.materialize_completed_column(_source_text(), _state())
    t2_lines = [line for line in rendered.splitlines() if line.startswith("t2\t")]
    assert len(t2_lines) == 2
    assert all("\tb/\t" in line for line in t2_lines)
    assert "\tb (I)\t" in t2_lines[0]
    assert "\tb (II)\t" in t2_lines[1]


@pytest.mark.parametrize(
    "source,match",
    [
        (
            _source_text().replace("t2\tb\t[b ]", "t2\tWRONG\t[b ]", 1),
            "surface|snapshot|identity",
        ),
        (
            _source_text().replace(
                "t2\tb\t[b ]\tOLD-B\told-b\tn.\told B\t## SEEDED from auto-parse; not yet hand-reviewed.\n",
                "",
            ),
            "missing|t2",
        ),
        (
            _source_text().replace(
                "t1\ta\t[a ]\tOLD-A-2\told-a-2\tn.\told A2\told alternative\n",
                "",
            ).replace(
                LINE_II1,
                "t1\ta\t[a ]\tLATE\tlate\tn.\tlate\tlate alternative\n" + LINE_II1,
            ),
            "contiguous|block|t1",
        ),
        (
            _source_text().replace(
                "t3\tc\t[c ]\tOLD-C\told-c\tn.\told C\told C comment",
                "t3\tc\t[c ]\tOLD-C\told-c\tn.\told C",
            ),
            "8|column|malformed|t3",
        ),
        (
            _source_text().replace(
                "t1\ta\t[a ]\tOLD-A-2",
                "t1\ta\t[DIFFERENT ]\tOLD-A-2",
            ),
            "sign span|immutable|t1",
        ),
    ],
)
def test_source_identity_and_block_shape_fail_closed(source: str, match: str) -> None:
    materializer = _load_materializer()
    with pytest.raises(ValueError, match=match):
        materializer.materialize_completed_column(source, _state())


def test_source_line_marker_must_match_snapshot_line_ref_and_task_tablet() -> None:
    materializer = _load_materializer()
    wrong_line = _source_text().replace("# KTU 9.9 I:2", "# KTU 9.9 III:2")
    with pytest.raises(ValueError, match="line|marker|snapshot|identity"):
        materializer.materialize_completed_column(wrong_line, _state())

    wrong_tablet = _source_text().replace("# KTU 9.9 I:1", "# KTU 9.8 I:1")
    with pytest.raises(ValueError, match="tablet|line|marker|snapshot|identity"):
        materializer.materialize_completed_column(wrong_tablet, _state())


def test_source_column_token_set_must_exactly_match_snapshot() -> None:
    materializer = _load_materializer()
    extra = "tx\textra\t[extra]\told/\told\tn.\textra\tstale source token\n"
    source = _source_text().replace(
        "t2\tb\t[b ]\tOLD-B",
        extra + "t2\tb\t[b ]\tOLD-B",
        1,
    )
    with pytest.raises(ValueError, match="complete|column|snapshot|token"):
        materializer.materialize_completed_column(source, _state())


def test_task_column_must_match_source_column() -> None:
    materializer = _load_materializer()
    state = _state()
    wrong_task = replace(state.task, column="III")
    forged = replace(state, task=wrong_task)
    with pytest.raises(ValueError, match="column|snapshot|source|identity"):
        materializer.materialize_completed_column(_source_text(), forged)


def test_source_target_order_must_match_snapshot_order() -> None:
    materializer = _load_materializer()
    source = _source_text()
    t1_block = (
        "t1\ta\t[a ]\tOLD-A-1\told-a-1\tn.\told A1\t## SEEDED from auto-parse; not yet hand-reviewed.\n"
        "t1\ta\t[a ]\tOLD-A-2\told-a-2\tn.\told A2\told alternative\n"
    )
    t2_block = "t2\tb\t[b ]\tOLD-B\told-b\tn.\told B\t## SEEDED from auto-parse; not yet hand-reviewed.\n"
    source = source.replace(t1_block + t2_block, t2_block + t1_block)
    with pytest.raises(ValueError, match="order|snapshot"):
        materializer.materialize_completed_column(source, _state())


@pytest.mark.parametrize(
    "state,match",
    [
        (_state(complete=False), "completion|completed"),
        (_state(complete=False, close_reconciliation=False), "reconciliation|completion|completed"),
        (_state(complete=False, pass_gate=False), "gate|completion|completed"),
        (_state(complete=False, unresolved_revisit=True), "revisit|reconciliation|completion"),
    ],
)
def test_incomplete_states_cannot_materialize(state: cs.ColumnRunState, match: str) -> None:
    materializer = _load_materializer()
    with pytest.raises(ValueError, match=match):
        materializer.materialize_completed_column(_source_text(), state)


def test_latest_decision_without_structured_rows_cannot_materialize() -> None:
    materializer = _load_materializer()
    rows = _default_rows()
    rows["t2"] = ()

    state = cs.ColumnRunState.initial(_task(), _snapshot())
    for token_id in ("t1", "t2", "t3"):
        evidence = cs.EvidenceRecord(
            f"evidence-{token_id}", "fixture", f"fixture:{token_id}", "prov", "evidence"
        )
        state = cs.apply_column_event(state, cs.EvidenceRecorded(f"record-{token_id}", evidence))
        if token_id == "t2":
            decision = cs.TokenDecision(
                "decision-t2",
                "t2",
                ("b/",),
                ("evidence-t2",),
                "legacy morphology-only decision",
            )
        else:
            decision = _decision(token_id, rows[token_id])
        state = cs.apply_column_event(state, cs.TokenReviewed(f"review-{token_id}", decision))
    state = cs.apply_column_event(state, cs.ReconciliationClosed("close-reconciliation"))
    state = cs.apply_column_event(
        state,
        cs.CompletionGateRecorded(
            "gate-review-status",
            cs.CompletionGateResult(
                "review-status-clean",
                True,
                state.decision_revision,
                ("fixture:review-status",),
                "checked",
            ),
        ),
    )
    state = cs.apply_column_event(state, cs.ColumnCompleted("complete-column"))

    with pytest.raises(ValueError, match="structured|reviewed_rows|t2"):
        materializer.materialize_completed_column(_source_text(), state)


@pytest.mark.parametrize(
    "row",
    [
        ("a/", "a", "n.", "A", "DULAT ## SEEDED from auto-parse; not yet hand-reviewed."),
        ("a/", "a SEEDED from auto-parse", "n.", "A", ""),
        ("a/", "a", "n. SEEDED from auto-parse", "A", ""),
        ("a/", "a", "n.", "A SEEDED from auto-parse", ""),
        ("SEEDED from auto-parse/", "a", "n.", "A", ""),
    ],
    ids=["comments", "dulat", "pos", "gloss", "morphology"],
)
def test_seed_marker_cannot_be_reintroduced_through_any_structured_field(row) -> None:
    materializer = _load_materializer()
    rows = _default_rows()
    rows["t1"] = (_row(*row),)
    state = _state(rows_by_token=rows)
    with pytest.raises(ValueError, match="SEEDED|seed|workflow"):
        materializer.materialize_completed_column(_source_text(), state)


def test_materializer_is_deterministic_and_has_no_write_or_network_api() -> None:
    materializer = _load_materializer()
    state = _state()
    first = materializer.materialize_completed_column(_source_text(), state)
    second = materializer.materialize_completed_column(_source_text(), state)
    assert first == second

    source = inspect.getsource(materializer).lower()
    forbidden_executable_patterns = (
        "open(",
        "from pathlib",
        "import pathlib",
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import urllib",
        "from urllib",
        "import github",
        "from github",
    )
    for forbidden in forbidden_executable_patterns:
        assert forbidden not in source


def _precompletion_candidate_state() -> cs.ColumnRunState:
    state = _state(complete=False, close_reconciliation=False)
    return cs.apply_column_event(state, cs.ReconciliationClosed("candidate-close-reconciliation"))


def test_candidate_materializer_renders_after_reconciliation_before_completion() -> None:
    materializer = _load_materializer()
    state = _precompletion_candidate_state()

    assert state.completion is None
    assert state.gate_results == ()
    assert materializer.materialize_candidate_column(_source_text(), state) == _expected_text()


def test_candidate_and_completed_materializers_share_identical_rendering() -> None:
    materializer = _load_materializer()
    candidate = materializer.materialize_candidate_column(
        _source_text(), _precompletion_candidate_state()
    )
    completed = materializer.materialize_completed_column(_source_text(), _state())
    assert candidate == completed


def test_candidate_materializer_still_requires_full_traversal_and_closed_reconciliation() -> None:
    materializer = _load_materializer()
    state = _precompletion_candidate_state()
    partial = replace(state, cursor=cs.TokenCursor(2))
    with pytest.raises(ValueError, match="traversal|every|snapshot|complete"):
        materializer.materialize_candidate_column(_source_text(), partial)

    open_reconciliation = _state(complete=False, close_reconciliation=False)
    with pytest.raises(ValueError, match="reconciliation|closed"):
        materializer.materialize_candidate_column(_source_text(), open_reconciliation)


def test_candidate_materializer_supports_columnless_line_markers() -> None:
    materializer = _load_materializer()
    task = replace(_task(), tablet="KTU 2.10", column="-")
    snapshot = cs.ColumnSnapshot(
        "snapshot-2.10",
        "reviewed/KTU 2.10.tsv",
        "fixture-provenance",
        (cs.ColumnToken("c1", 1, "4", "ab"),),
    )
    state = cs.ColumnRunState.initial(task, snapshot)
    evidence = cs.EvidenceRecord("e-c1", "fixture", "fixture:c1", "prov", "evidence")
    state = cs.apply_column_event(state, cs.EvidenceRecorded("record-c1", evidence))
    state = cs.apply_column_event(
        state,
        cs.TokenReviewed(
            "review-c1",
            _decision("c1", (_row("ab/", "ab", "n.", "AB"),)),
        ),
    )
    state = cs.apply_column_event(state, cs.ReconciliationClosed("close-columnless"))
    source = (
        HEADER
        + "# KTU 2.10 4\t\t\t\t\t\t\t\n"
        + "c1\tab\t[a b]\tOLD\told\tn.\told\t## SEEDED from auto-parse; not yet hand-reviewed.\n"
    )
    expected = (
        HEADER
        + "# KTU 2.10 4\t\t\t\t\t\t\t\n"
        + "c1\tab\t[a b]\tab/\tab\tn.\tAB\t\n"
    )

    assert materializer.materialize_candidate_column(source, state) == expected

    wrong = source.replace("# KTU 2.10 4", "# KTU 2.10 5")
    with pytest.raises(ValueError, match="line|marker|snapshot|identity"):
        materializer.materialize_candidate_column(wrong, state)
