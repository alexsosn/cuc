from __future__ import annotations

from dataclasses import replace

import pytest

from harness import column_state as cs


SHA0 = "0" * 64


def _row(
    morphology: str = "mlk/",
    dulat: str = "mlk",
    pos: str = "n. m. sg. abs. nom.",
    gloss: str = "king",
    comments: str = "",
):
    row_type = getattr(cs, "ReviewedRow", None)
    assert row_type is not None, "HARN-027 ReviewedRow contract is not implemented yet"
    return row_type(morphology, dulat, pos, gloss, comments)


def _state_with_decision(decision: cs.TokenDecision) -> cs.ColumnRunState:
    task = cs.ColumnTask(
        task_id="run-1",
        corpus="CUC",
        tablet="KTU 1.1",
        column="I",
        repository_revision="rev",
        capability=cs.CapabilityRef("review-automatic-parsing", "1.0.0", SHA0),
        required_completion_gates=("review-status-clean",),
    )
    snapshot = cs.ColumnSnapshot(
        "snapshot-1",
        "reviewed/KTU 1.1.tsv",
        "fixture",
        (cs.ColumnToken("1", 1, "I:1", "mlk"),),
    )
    state = cs.ColumnRunState.initial(task, snapshot)
    evidence = cs.EvidenceRecord("e1", "fixture", "fixture:e1", "fixture-prov", "evidence")
    state = cs.apply_column_event(state, cs.EvidenceRecorded("event-e1", evidence))
    return cs.apply_column_event(state, cs.TokenReviewed("event-d1", decision))


def test_legacy_morphology_only_decision_remains_serialization_compatible() -> None:
    decision = cs.TokenDecision("d1", "1", ("mlk/",), ("e1",), "reviewed")
    payload = decision.to_dict()

    assert "reviewed_rows" not in payload
    assert cs.TokenDecision.from_dict(payload) == decision


def test_structured_rows_round_trip_through_decision_and_column_state() -> None:
    rows = (
        _row(comments="DULAT s.v. mlk"),
        _row("mlk/", "/m-l-k/", "vb G inf.", "to reign", "alternative reading"),
    )
    decision = cs.TokenDecision(
        "d1",
        "1",
        ("mlk/",),
        ("e1",),
        "two lexical readings",
        reviewed_rows=rows,
    )

    restored_decision = cs.TokenDecision.from_dict(decision.to_dict())
    assert restored_decision == decision
    assert restored_decision.reviewed_rows == rows

    state = _state_with_decision(decision)
    restored_state = cs.ColumnRunState.from_json(state.to_json())
    assert restored_state == state
    assert restored_state.decisions[0].reviewed_rows == rows


def test_distinct_curated_rows_may_share_one_morphology_string() -> None:
    rows = (
        _row("]š]lyṭ[/", "/l-y-ṭ/", "vb Š act. ptcpl.", "to enwrap", "lexical reading A"),
        _row("]š]lyṭ[/", "/l-y-ṭ/", "vb Š pass. ptcpl.", "to be cursed", "lexical reading B"),
    )
    decision = cs.TokenDecision(
        "d1",
        "1",
        ("]š]lyṭ[/",),
        ("e1",),
        "same morphology, distinct lexical/POS readings",
        reviewed_rows=rows,
    )
    assert len(decision.reviewed_rows) == 2
    assert decision.analyses == ("]š]lyṭ[/",)


def test_structured_row_morphology_projection_must_equal_analyses() -> None:
    with pytest.raises(ValueError, match="analys|morpholog"):
        cs.TokenDecision(
            "d1",
            "1",
            ("mlk/", "other/"),
            ("e1",),
            "mismatch",
            reviewed_rows=(_row("mlk/"),),
        )


def test_duplicate_identical_curated_rows_are_rejected() -> None:
    row = _row()
    with pytest.raises(ValueError, match="duplicate|reviewed"):
        cs.TokenDecision(
            "d1",
            "1",
            ("mlk/",),
            ("e1",),
            "duplicate",
            reviewed_rows=(row, row),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("morphology", "mlk/\tbad"),
        ("dulat", "mlk\nbad"),
        ("pos", "n.\tbad"),
        ("gloss", "king\nbad"),
        ("comments", "public\tbad"),
        # Every separator str.splitlines() honours must be rejected, or the
        # rendered file re-parses with a phantom row.
        ("comments", "before\u2028after"),
        ("comments", "before\u2029after"),
        ("gloss", "before\x85after"),
        ("dulat", "before\x0bafter"),
        ("pos", "before\x0cafter"),
        ("morphology", "a/\x1cb"),
        ("comments", "before\x1dafter"),
        ("comments", "before\x1eafter"),
        ("gloss", "king\n"),
        ("comments", "\r"),
    ],
)
def test_curated_row_fields_reject_tsv_control_characters(field: str, value: str) -> None:
    kwargs = {
        "morphology": "mlk/",
        "dulat": "mlk",
        "pos": "n.",
        "gloss": "king",
        "comments": "",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match="tab|newline|TSV|control"):
        _row(**kwargs)


def test_reviewed_rows_none_payload_is_a_value_error() -> None:
    payload = {
        "decision_id": "decision-1",
        "token_id": "t1",
        "analyses": ["mlk/"],
        "evidence_ids": ["evidence-1"],
        "summary": "s",
        "reviewed_rows": None,
    }
    with pytest.raises(ValueError, match="reviewed_rows"):
        cs.TokenDecision.from_dict(payload)
