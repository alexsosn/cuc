from __future__ import annotations

import importlib
from importlib.metadata import version
import inspect

import pytest


langfuse = pytest.importorskip("langfuse")


def test_locked_langfuse_v4_api_surface_matches_sidecar_usage():
    assert version("langfuse") == "4.15.1"

    client_type = langfuse.Langfuse
    for method in ("create_trace_id", "start_observation", "create_score", "flush"):
        assert callable(getattr(client_type, method, None)), method

    parameters = inspect.signature(client_type).parameters
    assert "should_export_span" in parameters

    span_filter = importlib.import_module("langfuse.span_filter")
    assert callable(span_filter.is_langfuse_span)
