"""HARN-028a RED gate: load a real column into HARN-018 state from repository data."""

from __future__ import annotations

import importlib
import json
from hashlib import sha256
from pathlib import Path

import pytest

from harness.column_state import ColumnRunState, ColumnSnapshot, ColumnTask
from harness.skill_capabilities import SkillCapabilityRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]

AUTO_HEADER = "id\tsurface form\tmorphological parsing\tDULAT\tPOS\tgloss\tcomments\n"
REVIEWED_HEADER = (
    "id\tsurface form\tsign span\tmorphological parsing\tDULAT\tPOS\tgloss\tcomments\n"
)
SEED = "## SEEDED from auto-parse; not yet hand-reviewed."


def _loader():
    try:
        return importlib.import_module("harness.column_loader")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-028 column loader is not implemented yet: {exc}")


def _auto_text() -> str:
    return (
        AUTO_HEADER
        + "# KTU 9.9 I:1\t\t\t\t\t\t\n"
        + "1001\tl\tl(I)\tl (I)\tprep.\tto\t\n"
        + "1002\tbˤl\tbˤl(II)/\tbʕl (II)\tn. m. sg. abs. gen.\tBaal\t\n"
        + "1002\tbˤl\tbˤl(II)/\tbʕl (II)\tDN m. sg. abs. gen.\tBaʿlu\tDULAT direct ref\n"
        + "# KTU 9.9 I:2\t\t\t\t\t\t\n"
        + "1003\tġr\tġr(III)/\tġr (III)\tn. m. sg. abs. acc.\tskin\t\n"
        + "# KTU 9.9 II:1\t\t\t\t\t\t\n"
        + "1004\tb\tb\tb\tprep.\tin\t\n"
    )


def _reviewed_text() -> str:
    return (
        REVIEWED_HEADER
        + "# KTU 9.9 I:1\t\t\t\t\t\t\t\n"
        + f"1001\tl\tl \tl(I)\tl (I)\tprep.\tto\t{SEED}\n"
        + f"1002\tbˤl\tbʿl\tbˤl(II)/\tbʕl (II)\tn. m. sg. abs. gen.\tBaal\t{SEED}\n"
        + "# KTU 9.9 I:2\t\t\t\t\t\t\t\n"
        + f"1003\tġr\tġr  \tġr(III)/\tġr (III)\tn. m. sg. abs. acc.\tskin\t{SEED}\n"
        + "# KTU 9.9 II:1\t\t\t\t\t\t\t\n"
        + "1004\tb\tb\tb\tb\tprep.\tin\treviewed already\n"
    )


def _repo(tmp_path: Path, *, auto: str | None = None, reviewed: str | None = None) -> Path:
    """Build a minimal repository layout around the real capability manifests."""

    root = tmp_path / "repo"
    (root / "auto_parsing" / "0.2.8").mkdir(parents=True)
    (root / "reviewed").mkdir()
    (root / "auto_parsing" / "0.2.8" / "KTU 9.9.tsv").write_text(
        _auto_text() if auto is None else auto, encoding="utf-8"
    )
    (root / "reviewed" / "KTU 9.9.tsv").write_text(
        _reviewed_text() if reviewed is None else reviewed, encoding="utf-8"
    )
    return root


def _load(root: Path, **overrides):
    loader = _loader()
    kwargs = dict(
        tablet="KTU 9.9",
        column="I",
        tf_version="0.2.8",
        repository_revision="rev-test",
        task_id="run-ktu-9.9-i",
        capability_root=REPO_ROOT,
    )
    kwargs.update(overrides)
    return loader.load_column(root, **kwargs)


def test_loader_builds_snapshot_from_matching_auto_and_reviewed_column(tmp_path: Path) -> None:
    loaded = _load(_repo(tmp_path))

    assert isinstance(loaded.task, ColumnTask)
    assert isinstance(loaded.snapshot, ColumnSnapshot)
    assert loaded.task.tablet == "KTU 9.9"
    assert loaded.task.column == "I"
    assert loaded.snapshot.token_ids == ("1001", "1002", "1003")
    assert [t.surface for t in loaded.snapshot.tokens] == ["l", "bˤl", "ġr"]
    assert [t.line_ref for t in loaded.snapshot.tokens] == ["I:1", "I:1", "I:2"]
    assert [t.ordinal for t in loaded.snapshot.tokens] == [1, 2, 3]
    # Tokens outside the target column are not part of the work unit.
    assert "1004" not in loaded.snapshot.token_ids

    rows = loaded.automatic_rows
    assert tuple(rows) == ("1001", "1002", "1003")
    assert len(rows["1002"]) == 2
    assert rows["1002"][0].pos == "n. m. sg. abs. gen."
    assert rows["1002"][1].pos == "DN m. sg. abs. gen."
    assert rows["1002"][1].comments == "DULAT direct ref"

    # The initial HARN-018 state is pristine and bound to this snapshot.
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    assert state.next_token_id == "1001"


def test_loader_binds_canonical_capability_and_manifest_gates(tmp_path: Path) -> None:
    loaded = _load(_repo(tmp_path))
    registry = SkillCapabilityRegistry(REPO_ROOT)
    manifest = registry.get("review-automatic-parsing")

    assert loaded.task.capability.canonical_name == "review-automatic-parsing"
    assert loaded.task.required_completion_gates == manifest.completion_verifiers

    runtime = importlib.import_module("harness.langgraph_column_review")
    # Admission into the HARN-004 graph must succeed for a loaded column.
    runtime.initial_graph_input(ColumnRunState.initial(loaded.task, loaded.snapshot))


def test_loader_records_source_digests_and_deterministic_snapshot_id(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    first = _load(root)
    second = _load(root)

    auto_bytes = (root / "auto_parsing" / "0.2.8" / "KTU 9.9.tsv").read_bytes()
    reviewed_bytes = (root / "reviewed" / "KTU 9.9.tsv").read_bytes()
    assert first.auto_sha256 == sha256(auto_bytes).hexdigest()
    assert first.reviewed_sha256 == sha256(reviewed_bytes).hexdigest()
    assert first.reviewed_text == reviewed_bytes.decode("utf-8")

    provenance = json.loads(first.snapshot.source_provenance)
    assert provenance["auto_sha256"] == first.auto_sha256
    assert provenance["reviewed_sha256"] == first.reviewed_sha256
    assert provenance["tf_version"] == "0.2.8"
    assert first.snapshot.source_ref == "auto_parsing/0.2.8/KTU 9.9.tsv#I"
    assert first.snapshot.snapshot_id == second.snapshot.snapshot_id
    assert first.snapshot == second.snapshot


def test_loader_forwards_evidence_priority_without_narrowing_scope(tmp_path: Path) -> None:
    loaded = _load(_repo(tmp_path), evidence_priority_token_ids=("1003",))
    assert loaded.task.evidence_priority_token_ids == ("1003",)
    assert loaded.snapshot.token_ids == ("1001", "1002", "1003")


@pytest.mark.parametrize(
    "auto,reviewed,match",
    [
        # reviewed column lacks a token the automatic column has
        (
            None,
            _reviewed_text().replace(
                f"1003\tġr\tġr  \tġr(III)/\tġr (III)\tn. m. sg. abs. acc.\tskin\t{SEED}\n", ""
            ),
            "1003",
        ),
        # automatic column lacks a token the reviewed column has
        (
            _auto_text().replace(
                "1003\tġr\tġr(III)/\tġr (III)\tn. m. sg. abs. acc.\tskin\t\n", ""
            ),
            None,
            "1003",
        ),
        # surface mismatch on one token
        (None, _reviewed_text().replace("1003\tġr\t", "1003\tġrx\t"), "1003"),
        # reviewed order differs from automatic order
        (
            None,
            _reviewed_text()
            .replace(
                f"1001\tl\tl \tl(I)\tl (I)\tprep.\tto\t{SEED}\n"
                f"1002\tbˤl\tbʿl\tbˤl(II)/\tbʕl (II)\tn. m. sg. abs. gen.\tBaal\t{SEED}\n",
                f"1002\tbˤl\tbʿl\tbˤl(II)/\tbʕl (II)\tn. m. sg. abs. gen.\tBaal\t{SEED}\n"
                f"1001\tl\tl \tl(I)\tl (I)\tprep.\tto\t{SEED}\n",
            ),
            "order|sequence",
        ),
        # malformed reviewed row inside the target column
        (None, _reviewed_text().replace(f"\tskin\t{SEED}\n", f"\tskin\t{SEED}\textra\n"), "1003|8 columns"),
        # missing sign span inside the target column
        (None, _reviewed_text().replace("1003\tġr\tġr  \t", "1003\tġr\t\t"), "1003|sign span"),
        # wrong reviewed header (not seeded in the 8-column layout)
        (None, _reviewed_text().replace(REVIEWED_HEADER, AUTO_HEADER), "header|8-column"),
    ],
    ids=[
        "reviewed-missing-token",
        "auto-missing-token",
        "surface-mismatch",
        "order-mismatch",
        "malformed-reviewed-row",
        "missing-sign-span",
        "wrong-reviewed-header",
    ],
)
def test_loader_fails_closed_on_source_disagreement(
    tmp_path: Path, auto: str | None, reviewed: str | None, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _load(_repo(tmp_path, auto=auto, reviewed=reviewed))


def test_loader_rejects_unknown_column_and_missing_version(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(ValueError, match="column|IV"):
        _load(root, column="IV")
    with pytest.raises(ValueError, match="0.2.7|auto_parsing"):
        _load(root, tf_version="0.2.7")


def test_loader_does_not_read_rows_outside_the_target_column(tmp_path: Path) -> None:
    loaded = _load(_repo(tmp_path), column="II")
    assert loaded.snapshot.token_ids == ("1004",)
    assert loaded.snapshot.tokens[0].line_ref == "II:1"


def test_loader_supports_columnless_tablets(tmp_path: Path) -> None:
    auto = (
        AUTO_HEADER
        + "# KTU 2.99 1\t\t\t\t\t\t\n"
        + "2001\tl\tl(I)\tl (I)\tprep.\tto\t\n"
        + "# KTU 2.99 2\t\t\t\t\t\t\n"
        + "2002\tb\tb\tb\tprep.\tin\t\n"
    )
    reviewed = (
        REVIEWED_HEADER
        + "# KTU 2.99 1\t\t\t\t\t\t\t\n"
        + f"2001\tl\tl \tl(I)\tl (I)\tprep.\tto\t{SEED}\n"
        + "# KTU 2.99 2\t\t\t\t\t\t\t\n"
        + f"2002\tb\tb\tb\tb\tprep.\tin\t{SEED}\n"
    )
    root = tmp_path / "repo"
    (root / "auto_parsing" / "0.2.8").mkdir(parents=True)
    (root / "reviewed").mkdir()
    (root / "auto_parsing" / "0.2.8" / "KTU 2.99.tsv").write_text(auto, encoding="utf-8")
    (root / "reviewed" / "KTU 2.99.tsv").write_text(reviewed, encoding="utf-8")

    loaded = _load(root, tablet="KTU 2.99", column="-")
    assert loaded.task.column == "-"
    assert loaded.snapshot.token_ids == ("2001", "2002")
    assert [t.line_ref for t in loaded.snapshot.tokens] == ["1", "2"]


def test_loader_reads_real_ktu_1_6_column_i_from_the_repository() -> None:
    loaded = _load(
        REPO_ROOT,
        tablet="KTU 1.6",
        column="I",
        task_id="run-ktu-1.6-i",
    )
    assert len(loaded.snapshot.tokens) == 299
    assert loaded.snapshot.tokens[0].token_id == "159263"
    assert loaded.snapshot.tokens[0].surface == "l"
    assert loaded.snapshot.tokens[0].line_ref == "I:1"
    assert all(len(loaded.automatic_rows[t]) >= 1 for t in loaded.snapshot.token_ids)
