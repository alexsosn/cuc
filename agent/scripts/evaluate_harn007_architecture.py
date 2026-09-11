#!/usr/bin/env python3
"""Emit deterministic repository evidence for the HARN-007 architecture decision.

This script deliberately does not import or execute Deep Agents. External framework
properties are dated research inputs pinned to an immutable upstream revision; CUC
properties are recovered from the current repository source so the decision cannot
silently drift away from HARN-004/HARN-006 semantics.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import tomllib
from typing import Any, Iterable


AGENT_ROOT = Path(__file__).resolve().parents[1]
COLUMN_REVIEW = AGENT_ROOT / "harness" / "langgraph_column_review.py"
COLUMN_STATE = AGENT_ROOT / "harness" / "column_state.py"
DEVELOPMENT_REVIEWER = AGENT_ROOT / "harness" / "development_reviewer.py"
PYPROJECT = AGENT_ROOT / "pyproject.toml"

CHECKED_ON = "2026-09-11"
DEEPAGENTS_REVISION = "54696577caf3dfcefb662db08b4a8034ec6a35cd"
DEEPAGENTS_SOURCES = (
    f"https://github.com/langchain-ai/deepagents/tree/{DEEPAGENTS_REVISION}",
    f"https://github.com/langchain-ai/deepagents/blob/{DEEPAGENTS_REVISION}/README.md",
    f"https://github.com/langchain-ai/deepagents/blob/{DEEPAGENTS_REVISION}/libs/ARCHITECTURE.md",
    "https://docs.langchain.com/oss/python/deepagents/overview",
)


def _parse_python(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _contains_attribute(node: ast.AST, attribute: str) -> bool:
    return any(
        isinstance(item, ast.Attribute) and item.attr == attribute
        for item in ast.walk(node)
    )


def _graph_nodes(tree: ast.AST) -> tuple[str, ...]:
    result: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_node":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        value = node.args[0].value
        if isinstance(value, str):
            result.append(value)
    return tuple(sorted(set(result)))


def _has_required_gate_loop(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.comprehension)):
            iterator = node.iter
            if _contains_attribute(iterator, "required_completion_gates"):
                return True
    return False


def _binds_manifest_completion_verifiers(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "required_completion_gates":
                continue
            if isinstance(keyword.value, ast.Attribute) and keyword.value.attr == "completion_verifiers":
                return True
    return False


def _has_class(tree: ast.AST, class_name: str) -> bool:
    return any(
        isinstance(node, ast.ClassDef) and node.name == class_name
        for node in ast.walk(tree)
    )


def _declared_dependencies(pyproject: dict[str, Any]) -> tuple[str, ...]:
    project = pyproject.get("project", {})
    dependencies = project.get("dependencies", []) if isinstance(project, dict) else []
    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    values: list[str] = []
    if isinstance(dependencies, list):
        values.extend(item for item in dependencies if isinstance(item, str))
    if isinstance(optional, dict):
        for group in optional.values():
            if isinstance(group, list):
                values.extend(item for item in group if isinstance(item, str))
    return tuple(sorted(values))


def _dependency_name(requirement: str) -> str:
    stop = len(requirement)
    for separator in ("[", "<", ">", "=", "!", "~", ";", " "):
        index = requirement.find(separator)
        if index >= 0:
            stop = min(stop, index)
    return requirement[:stop].strip().lower().replace("_", "-")


def _repository_evidence() -> dict[str, Any]:
    column_review_tree = _parse_python(COLUMN_REVIEW)
    column_state_tree = _parse_python(COLUMN_STATE)
    development_tree = _parse_python(DEVELOPMENT_REVIEWER)
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependencies = _declared_dependencies(pyproject)

    return {
        "column_review_source": str(COLUMN_REVIEW.relative_to(AGENT_ROOT)),
        "column_state_source": str(COLUMN_STATE.relative_to(AGENT_ROOT)),
        "development_reviewer_source": str(DEVELOPMENT_REVIEWER.relative_to(AGENT_ROOT)),
        "pyproject_source": str(PYPROJECT.relative_to(AGENT_ROOT)),
        "column_review_imports_langgraph": "langgraph" in _import_roots(column_review_tree),
        "column_state_imports_langgraph": "langgraph" in _import_roots(column_state_tree),
        "development_reviewer_imports_deepagents": "deepagents" in _import_roots(development_tree),
        "core_declares_deepagents_dependency": any(
            _dependency_name(item) == "deepagents" for item in dependencies
        ),
        "graph_nodes": list(_graph_nodes(column_review_tree)),
        "uses_next_token_id_cursor": _contains_attribute(column_review_tree, "next_token_id"),
        "loops_required_completion_gates": _has_required_gate_loop(column_review_tree),
        "binds_capability_completion_verifiers": _binds_manifest_completion_verifiers(
            column_review_tree
        ),
        "has_clean_context_development_review": _has_class(
            development_tree, "DevelopmentReviewContext"
        ),
        "declared_dependencies": list(dependencies),
    }


def _external_observations() -> dict[str, Any]:
    # Primary GitHub evidence is pinned above. The docs URL is retained as a dated
    # convenience reference, not as the sole evidence for any decision invariant.
    return {
        "checked_on": CHECKED_ON,
        "deepagents_revision": DEEPAGENTS_REVISION,
        "deepagents_runtime": "langgraph",
        "opinionated_agent_loop": True,
        "bundled_subagents": True,
        "bundled_filesystem_context_management": True,
        "bundled_human_in_the_loop": True,
        "compiled_langgraph_can_be_subagent": True,
        "tool_boundary_required_for_security": True,
        "custom_graph_recommended_when_agent_loop_is_wrong_shape": True,
        "sources": list(DEEPAGENTS_SOURCES),
    }


def _decision(repository: dict[str, Any], external: dict[str, Any]) -> dict[str, str]:
    strict_parser_contract = all(
        (
            repository["column_review_imports_langgraph"],
            not repository["column_state_imports_langgraph"],
            repository["uses_next_token_id_cursor"],
            repository["loops_required_completion_gates"],
            repository["binds_capability_completion_verifiers"],
        )
    )
    deepagents_is_generic_loop = bool(external["opinionated_agent_loop"])
    graph_is_composable = bool(external["compiled_langgraph_can_be_subagent"])
    security_is_external = bool(external["tool_boundary_required_for_security"])
    clean_review_exists = bool(repository["has_clean_context_development_review"])

    parser_fit = (
        "reject"
        if strict_parser_contract
        and deepagents_is_generic_loop
        and graph_is_composable
        else "needs-more-research"
    )
    parser = (
        "retain-explicit-langgraph"
        if parser_fit == "reject"
        else "undecided"
    )

    development_fit = (
        "conditional"
        if clean_review_exists and security_is_external
        else "needs-more-research"
    )
    development = (
        "defer-deepagents-until-harn009"
        if development_fit == "conditional"
        else "undecided"
    )

    core_dependency = (
        "do-not-add-deepagents-now"
        if not repository["core_declares_deepagents_dependency"]
        and parser_fit == "reject"
        else "reassess-dependency"
    )

    return {
        "parser_primary_deepagents_fit": parser_fit,
        "parser_orchestrator": parser,
        "development_helper_deepagents_fit": development_fit,
        "development_controller": development,
        "core_dependency": core_dependency,
    }


def build_report() -> dict[str, Any]:
    repository = _repository_evidence()
    external = _external_observations()
    return {
        "schema_version": 1,
        "repository_evidence": repository,
        "external_observations": external,
        "decision": _decision(repository, external),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the deterministic architecture report as JSON",
    )
    args = parser.parse_args(tuple(argv) if argv is not None else None)
    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        decision = report["decision"]
        print(f"parser: {decision['parser_orchestrator']}")
        print(f"development: {decision['development_controller']}")
        print(f"dependency: {decision['core_dependency']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
