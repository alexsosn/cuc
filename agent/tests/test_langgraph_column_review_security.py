from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from harness.column_state import ColumnRunState, ColumnSnapshot, ColumnToken
from harness.skill_capabilities import SkillCapabilityRegistry
import harness.langgraph_column_review as runtime


REPO_ROOT = Path(__file__).resolve().parents[2]


def _valid_state() -> ColumnRunState:
    registry = SkillCapabilityRegistry(REPO_ROOT)
    manifest = registry.get("review-automatic-parsing")
    provenance = registry.provenance("review-automatic-parsing")
    task = runtime.build_column_task_from_capability(
        manifest,
        provenance,
        task_id="security-fixture",
        corpus="CUC",
        tablet="KTU 1.1",
        column="I",
        repository_revision="fixture-revision",
    )
    snapshot = ColumnSnapshot(
        "security-snapshot",
        "fixture:security",
        "fixture-provenance",
        (ColumnToken("t1", 1, "1", "a"),),
    )
    return ColumnRunState.initial(task, snapshot)


def test_initial_graph_input_rejects_weakened_completion_contract() -> None:
    state = _valid_state()
    forged_task = replace(
        state.task,
        required_completion_gates=(state.task.required_completion_gates[0],),
    )
    with pytest.raises(ValueError, match="capability|completion|gate"):
        runtime.initial_graph_input(replace(state, task=forged_task))


def test_initial_graph_input_rejects_forged_capability_provenance() -> None:
    state = _valid_state()
    forged_capability = replace(state.task.capability, provenance_sha256="f" * 64)
    with pytest.raises(ValueError, match="capability|provenance"):
        runtime.initial_graph_input(
            replace(state, task=replace(state.task, capability=forged_capability))
        )
