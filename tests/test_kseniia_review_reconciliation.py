"""Guard the conservative Kseniia review migration against ID/token drift.

The candidate TSVs deliberately contain PENDING_TOKENIZATION_REVIEW entries,
and must not be treated as fully reviewed data.
"""
from __future__ import annotations

import csv
from collections import OrderedDict
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "reviewed" / "reconciliation_drafts" / "0.2.7"
AUTO = ROOT / "auto_parsing" / "0.2.7"

CASES = (
    ("KTU 1.6.tsv", "KTU 1.6.tsv", 1027, 974, 53, 39, 7),
    (
        "KTU 1.1 auto parsed reviewed.tsv",
        "KTU 1.1.tsv",
        491, 473, 18, 24, 8,
    ),
)


def read_tokens(path: Path):
    with path.open(encoding="utf-8", newline="") as stream:
        rows = csv.reader(stream, delimiter="\t")
        header = next(rows)
        assert header and header[0].strip() == "id"
        tokens = OrderedDict()
        entries = OrderedDict()
        verse = None
        for line_no, raw in enumerate(rows, 2):
            if not raw or all(not c.strip() for c in raw):
                continue
            assert len(raw) == len(header), (path, line_no, len(raw))
            columns = tuple(c.strip() for c in raw)
            if columns[0].startswith("#"):
                verse = columns[0].removeprefix("#").strip()
                assert verse not in tokens
                tokens[verse] = []
                continue
            assert verse is not None
            assert columns[0].isdigit(), (path, line_no)
            key = (verse, columns[0])
            if key not in entries:
                entries[key] = []
                tokens[verse].append((columns[0], columns[1]))
            else:
                assert entries[key][0][1] == columns[1], (path, line_no)
            entries[key].append(columns)
        return header, tokens, entries


@pytest.mark.parametrize(
    "draft_file,auto_file,token_count,safe,pending,affected,columns", CASES
)
def test_review_drafts_follow_current_upstream_tokens(
    draft_file, auto_file, token_count, safe, pending, affected, columns
):
    header, tokens, entries = read_tokens(DRAFT / draft_file)
    auto_header, auto_tokens, _ = read_tokens(AUTO / auto_file)
    assert len(header) == columns
    assert len(auto_header) == 7
    assert tokens == auto_tokens, "review draft IDs/order/surfaces differ from 0.2.7"
    assert len(entries) == token_count

    reviewed_tokens = pending_tokens = 0
    for token_rows in entries.values():
        marks = [
            (
                "SOURCE_REVIEW_ID=" in row[-1],
                "PENDING_TOKENIZATION_REVIEW" in row[-1],
            )
            for row in token_rows
        ]
        assert len(set(marks)) == 1, "mixed reviewed/pending alternatives"
        assert marks[0] in {(True, False), (False, True)}
        if marks[0][0]:
            reviewed_tokens += 1
            assert token_rows[0][1], "blank surface cannot be safely aligned"
        else:
            pending_tokens += 1
    assert (reviewed_tokens, pending_tokens) == (safe, pending)

    with (DRAFT / "kseniia-tokenization-differences.tsv").open(
        encoding="utf-8", newline=""
    ) as stream:
        conflicts = list(csv.DictReader(stream, delimiter="\t"))
    dataset = auto_file.removesuffix(".tsv")
    current = [r for r in conflicts if r["dataset"] == dataset]
    assert len(current) == affected
    assert all(r["old_unmapped_ids_and_surfaces"] or r["current_unmapped_ids_and_surfaces"] for r in current)


def test_unmatched_expert_analyses_are_preserved_for_adjudication():
    with (DRAFT / "kseniia-unmatched-review-rows.tsv").open(
        encoding="utf-8", newline=""
    ) as stream:
        exception_rows = list(csv.DictReader(stream, delimiter="\t"))
    assert len(exception_rows) == 80
    assert sum(r["dataset"] == "KTU 1.6" for r in exception_rows) == 51
    assert sum(r["dataset"] == "KTU 1.1" for r in exception_rows) == 29
    assert all(r["old_id"].isdigit() and r["verse"].startswith("KTU ") for r in exception_rows)
