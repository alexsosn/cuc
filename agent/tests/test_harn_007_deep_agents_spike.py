from __future__ import annotations

import importlib

import pytest


def _spike():
    try:
        return importlib.import_module("harness.deep_agents_spike")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-007 executable Deep Agents spike is not implemented yet: {exc}")


def test_released_deep_agents_can_finish_before_complete_column_traversal() -> None:
    spike = _spike()
    observation = spike.run_early_termination_probe()

    assert observation["deepagents_version"] == "0.7.13"
    assert observation["required_token_ids"] == ("t1", "t2", "t3")
    assert observation["observed_token_ids"] == ("t1",)
    assert observation["completed_normally"] is True
    assert observation["final_response"] == "Done after one token."
    assert observation["tool_result_count"] == 1
