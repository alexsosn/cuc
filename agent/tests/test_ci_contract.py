"""Contract test for the dedicated agent/harness CI execution root."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[1]


def test_agent_ci_runtime_contract() -> None:
    """The agent suite must run on its declared runtime from the agent root."""

    assert sys.version_info >= (3, 13), (
        "agent CI must use Python >=3.13; "
        f"got {sys.version_info.major}.{sys.version_info.minor}"
    )
    assert Path.cwd().resolve() == AGENT_ROOT.resolve(), (
        "agent CI must invoke pytest with agent/ as the working directory; "
        f"got {Path.cwd().resolve()}"
    )

    for module_name in (
        "pipeline",
        "scripts.bootstrap_tablet_labeling",
        "linter.morphology",
        "morph_features",
    ):
        importlib.import_module(module_name)
