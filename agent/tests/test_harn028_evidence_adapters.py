"""HARN-028a RED gate: optional local evidence adapters and the composite collector."""

from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest

from harness.column_state import ColumnRunState, EvidenceRecord
from tests.test_harn028_column_loader import REPO_ROOT, _repo  # noqa: F401

SENTINEL = "LEAK-SENTINEL-8b1f"


def _adapters():
    try:
        return importlib.import_module("harness.evidence_adapters")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-028 evidence adapters are not implemented yet: {exc}")


def _policy_module():
    return importlib.import_module("harness.evidence_policy")


def _loader():
    return importlib.import_module("harness.column_loader")


# --- fixture resources with the real schemas -------------------------------------------


def _dulat_search_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE dulat_reverse_refs (norm_ref TEXT, entry_id INTEGER, payload TEXT, "
        "PRIMARY KEY(norm_ref, entry_id))"
    )
    con.executemany(
        "INSERT INTO dulat_reverse_refs VALUES (?, ?, ?)",
        [
            (
                "KTU 9.9 I:2",
                37,
                json.dumps(
                    {
                        "entry_id": 37,
                        "label": "ġr (III)",
                        "reference_translations": ["skin"],
                        "sense_labels": [f"1) skin {SENTINEL}"],
                        "stem_names": [],
                    }
                ),
            ),
            (
                "KTU 9.9 I:2",
                4756,
                json.dumps(
                    {
                        "entry_id": 4756,
                        "label": "<i>ġr b ảbn</i>",
                        "reference_translations": ["to rip"],
                        "sense_labels": ["1) to rip, scratch"],
                        "stem_names": ["G"],
                    }
                ),
            ),
            ("KTU 9.9 II:1", 5, json.dumps({"entry_id": 5, "label": "b", "sense_labels": []})),
        ],
    )
    con.commit()
    con.close()
    return path


def _modules_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE module_records (id INTEGER PRIMARY KEY AUTOINCREMENT, module_id TEXT, "
        "record_id TEXT, ref_display TEXT, ref_norm TEXT, content_text TEXT, "
        "content_html TEXT, file_path TEXT, data_json TEXT)"
    )
    con.executemany(
        "INSERT INTO module_records (module_id, record_id, ref_display, ref_norm, "
        "content_text, content_html, file_path, data_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("EUPT_vocalisation", "r1", "KTU 9.9 I:2", "KTU 9.9 I:2", "ġôra bi ˀabni", "", "", "{}"),
            ("EUPT_translation", "r2", "KTU 9.9 I:2", "KTU 9.9 I:2", "(Ihre) Haut zerkratzte sie", "", "", "{}"),
            ("CUC", "r3", "KTU 9.9 I:2", "KTU 9.9 I:2", "ġr . b abn", "", "", "{}"),
            ("UNP", "doc", "KTU 9.9", "KTU 9.9", "whole tablet translation", "", "", "{}"),
        ],
    )
    con.commit()
    con.close()
    return path


def _tropper_db(path: Path) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE ktu (tablet text, column text, line text, span text, pages text, "
        "verified int, in_corpus int, pages_suspect int, repaired int, raw text)"
    )
    con.executemany(
        "INSERT INTO ktu VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("9.9", "I", "2", "", "497, 612", 1, 1, 0, 0, "1.9.9 I:2 497 612"),
            ("9.9", "I", "2", "", "88", 0, 1, 1, 1, "OCR noise"),
            ("9.9", "II", "1", "", "12", 1, 1, 0, 0, "x"),
        ],
    )
    con.commit()
    con.close()
    return path


def _burns_dir(path: Path) -> Path:
    workbook = path / "Workbooks"
    workbook.mkdir(parents=True)
    (workbook / "deities.csv").write_text(
        "headword,root,category,ktu,references\n"
        f"ġr,ġ-r-r,cultic object {SENTINEL},9.9,I.2; II.1\n"
        "bʿl,b-ʿ-l,DN,9.9,I.1\n"
        "x,x,DN,not attested,\n",
        encoding="utf-8",
    )
    return path


def _legacy_review(root: Path) -> None:
    (root / "reviewed" / "KTU 9.9.txt").write_text(
        "id\tsurface form\tmorphological parsing\tDULAT\tPOS\tgloss\tcomments\n"
        "# KTU 9.9 I:1\t\t\t\t\t\t\n"
        "1\tl\tl(I)\tl (I)\tprep.\tto\t\n"
        "2\tbˤl\tbˤl(II)/\tbʕl (II)\tDN\tBaal\t\n"
        "# KTU 9.9 I:2\t\t\t\t\t\t\n"
        f"3\tġr\tġr(II)/\tġr (II)\tn.\tmountain {SENTINEL}\t\n",
        encoding="utf-8",
    )


def _parallel_tablet(root: Path) -> None:
    (root / "reviewed" / "KTU 9.8.tsv").write_text(
        "id\tsurface form\tsign span\tmorphological parsing\tDULAT\tPOS\tgloss\tcomments\n"
        "# KTU 9.8 I:1\t\t\t\t\t\t\t\n"
        "501\tġr\tġr\tġr(III)/\tġr (III)\tn. m. sg. abs. nom.\tskin\tparallel\n"
        "502\tġr\tġr\tġr(I)/\tġr (I)\tn. m. sg. abs. nom.\tmountain\t\n",
        encoding="utf-8",
    )


def _loaded(root: Path):
    return _loader().load_column(
        root,
        tablet="KTU 9.9",
        column="I",
        tf_version="0.2.8",
        repository_revision="rev-test",
        task_id="run-ktu-9.9-i",
        capability_root=REPO_ROOT,
    )


def _token(loaded, token_id: str):
    return next(t for t in loaded.snapshot.tokens if t.token_id == token_id)


def _collector(mod, loaded, enabled, locator):
    policy = _policy_module().build_evidence_policy(enabled, locator)
    return mod.EvidenceCollector(policy, loaded, locator=locator), policy


# --- locator --------------------------------------------------------------------------


def test_static_locator_reports_kind_and_absent_resources(tmp_path: Path) -> None:
    mod = _adapters()
    db = _dulat_search_db(tmp_path / "dulat_search.sqlite")
    locator = mod.StaticLocator({"dulat_search": (db, "explicit")})
    assert locator.locate("dulat_search") == (db, "explicit")
    assert locator.locate("tropper") is None


def test_skill_script_locator_never_raises_for_missing_env_path(tmp_path: Path, monkeypatch) -> None:
    mod = _adapters()
    monkeypatch.setenv("CUC_TROPPER_OCR", str(tmp_path / "does-not-exist"))
    locator = mod.SkillScriptLocator(REPO_ROOT)
    # sources.py raises SystemExit here; the harness must record absence instead.
    assert locator.locate("tropper") is None


# --- individual adapters ------------------------------------------------------------


def test_auto_parsing_adapter_emits_one_record_per_automatic_alternative(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    records = mod.AutoParsingAdapter().collect(
        mod.TokenEvidenceContext(loaded, state, _token(loaded, "1002"), "op-1"), None
    )
    assert len(records) == 2
    assert all(isinstance(r, EvidenceRecord) for r in records)
    assert {r.source_id for r in records} == {"auto-parsing"}
    assert "DN m. sg. abs. gen." in records[1].summary
    assert records[0].source_ref.startswith("auto_parsing/0.2.8/KTU 9.9.tsv")


def test_dulat_adapter_returns_cited_entries_for_the_token_line(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    db = _dulat_search_db(tmp_path / "dulat_search.sqlite")
    ctx = mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-2")
    records = mod.DulatAdapter().collect(ctx, db)
    assert [r.source_ref for r in records] == ["dulat_search:KTU 9.9 I:2:entry-37", "dulat_search:KTU 9.9 I:2:entry-4756"]
    assert "ġr (III)" in records[0].summary and "skin" in records[0].summary
    # HTML in labels is stripped.
    assert "<i>" not in records[1].summary
    # A line with no citations yields nothing rather than a placeholder.
    none = mod.DulatAdapter().collect(
        mod.TokenEvidenceContext(loaded, state, _token(loaded, "1001"), "op-3"), db
    )
    assert none == ()


def test_eupt_adapter_returns_only_line_level_eupt_modules(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    db = _modules_db(tmp_path / "modules_cache.sqlite")
    records = mod.EuptAdapter().collect(
        mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-4"), db
    )
    modules = [r.source_ref.split(":")[1] for r in records]
    assert modules == ["EUPT_vocalisation", "EUPT_translation"]
    assert "ġôra" in records[0].summary
    # CUC text and whole-tablet translations are not EUPT evidence.
    assert not any("UNP" in r.source_ref or ":CUC:" in r.source_ref for r in records)


def test_tropper_adapter_returns_verified_page_pointers_only(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    db = _tropper_db(tmp_path / "tropper.index.sqlite")
    records = mod.TropperAdapter().collect(
        mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-5"), db
    )
    assert len(records) == 1
    assert "497" in records[0].summary and "612" in records[0].summary
    assert "OCR noise" not in records[0].summary
    assert records[0].source_ref == "tropper:KTU 9.9 I:2:pages-497,612"


def test_tropper_adapter_accepts_an_ocr_directory_and_finds_the_index(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    ocr = tmp_path / "tropper-full"
    ocr.mkdir()
    _tropper_db(ocr / "Tropper - Ugaritische Grammatik. 2012.ocr.index.sqlite")
    records = mod.TropperAdapter().collect(
        mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-5b"), ocr
    )
    assert len(records) == 1


def test_legacy_review_adapter_aligns_by_line_and_surface(tmp_path: Path) -> None:
    mod = _adapters()
    root = _repo(tmp_path)
    _legacy_review(root)
    loaded = _loaded(root)
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    records = mod.LegacyReviewAdapter().collect(
        mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-6"),
        root / "reviewed" / "KTU 9.9.txt",
    )
    assert len(records) == 1
    assert "ġr(II)/" in records[0].summary
    assert records[0].source_ref == "legacy-review:reviewed/KTU 9.9.txt:I:2:ġr"
    # Legacy ids are from an older id space and must not be used for alignment.
    assert ":3:" not in records[0].source_ref


def test_corpus_parallels_adapter_lists_other_reviewed_tokens_with_same_surface(tmp_path: Path) -> None:
    mod = _adapters()
    root = _repo(tmp_path)
    _parallel_tablet(root)
    loaded = _loaded(root)
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    records = mod.CorpusParallelsAdapter().collect(
        mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-7"), None
    )
    refs = sorted(r.source_ref for r in records)
    assert refs == ["corpus-parallels:reviewed/KTU 9.8.tsv:501", "corpus-parallels:reviewed/KTU 9.8.tsv:502"]
    # The token itself is never its own parallel, and seeded rows are not evidence.
    assert not any(":1003" in r.source_ref for r in records)
    assert any("mountain" in r.summary for r in records)


def test_burns_adapter_reads_workbook_csv_rows_citing_the_line(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    burns = _burns_dir(tmp_path / "context_labeling")
    records = mod.BurnsAdapter().collect(
        mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-8"), burns
    )
    assert len(records) == 1
    assert "cultic object" in records[0].summary
    assert records[0].source_ref == "burns:Workbooks/deities.csv:KTU 9.9 I:2:ġr"


# --- collector ------------------------------------------------------------------------


def test_collector_returns_only_auto_parsing_when_no_external_source_is_enabled(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    collector, policy = _collector(mod, loaded, ("auto-parsing",), mod.StaticLocator({}))
    context = collector.initialize_skill_context(state, "run:init")
    records = collector.collect_evidence(state, _token(loaded, "1003"), context, "run:initial:1003:evidence")
    assert len(records) == 1
    assert records[0].source_id == "auto-parsing"
    assert policy.enabled_sources == ("auto-parsing",)


def test_collector_records_absence_and_never_raises_for_missing_resources(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    collector, policy = _collector(
        mod, loaded, ("auto-parsing", "dulat", "tropper"), mod.StaticLocator({})
    )
    assert policy.absent_sources == ("dulat", "tropper")
    context = collector.initialize_skill_context(state, "run:init")
    records = collector.collect_evidence(state, _token(loaded, "1003"), context, "run:initial:1003:evidence")
    assert {r.source_id for r in records} == {"auto-parsing"}
    assert context["evidence"]["absent_sources"] == ["dulat", "tropper"]


def test_collector_uses_every_enabled_available_source_and_keeps_ids_unique(tmp_path: Path) -> None:
    mod = _adapters()
    root = _repo(tmp_path)
    _legacy_review(root)
    _parallel_tablet(root)
    loaded = _loaded(root)
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    locator = mod.StaticLocator(
        {
            "dulat_search": (_dulat_search_db(tmp_path / "dulat_search.sqlite"), "env"),
            "modules": (_modules_db(tmp_path / "modules_cache.sqlite"), "env"),
            "tropper": (_tropper_db(tmp_path / "tropper.index.sqlite"), "sibling"),
            "burns": (_burns_dir(tmp_path / "context_labeling"), "sibling"),
        }
    )
    enabled = tuple(_policy_module().ALL_SOURCE_IDS)
    collector, policy = _collector(mod, loaded, enabled, locator)
    assert policy.absent_sources == ()
    context = collector.initialize_skill_context(state, "run:init")
    first = collector.collect_evidence(state, _token(loaded, "1003"), context, "run:initial:1003:evidence")
    second = collector.collect_evidence(state, _token(loaded, "1003"), context, "run:revisit:r1:evidence")

    assert {r.source_id for r in first} == set(enabled)
    ids = [r.evidence_id for r in first + second]
    assert len(ids) == len(set(ids))
    assert all(r.evidence_id.startswith("run:initial:1003:evidence:") for r in first)
    assert all(r.provenance_ref for r in first)


def test_collector_disabled_source_is_not_consulted_even_when_present(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    locator = mod.StaticLocator(
        {"dulat_search": (_dulat_search_db(tmp_path / "dulat_search.sqlite"), "env")}
    )
    collector, policy = _collector(mod, loaded, ("auto-parsing",), locator)
    context = collector.initialize_skill_context(state, "run:init")
    records = collector.collect_evidence(state, _token(loaded, "1003"), context, "run:initial:1003:evidence")
    assert {r.source_id for r in records} == {"auto-parsing"}
    assert policy.to_dict()["enabled_sources"] == ["auto-parsing"]


def test_skill_context_is_json_safe_and_describes_the_policy(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    collector, policy = _collector(mod, loaded, ("auto-parsing", "dulat"), mod.StaticLocator({}))
    context = collector.initialize_skill_context(state, "run:init")
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded["evidence"]["policy_sha256"] == policy.sha256
    assert decoded["evidence"]["enabled_sources"] == ["auto-parsing", "dulat"]
    assert decoded["evidence"]["available_sources"] == ["auto-parsing"]
    assert decoded["capability"]["canonical_name"] == "review-automatic-parsing"
    assert decoded["worklist"]["priority_token_ids"] == []
    assert decoded["scope"]["token_count"] == 3


def test_resource_text_appears_only_in_evidence_summaries(tmp_path: Path) -> None:
    mod = _adapters()
    root = _repo(tmp_path)
    _legacy_review(root)
    loaded = _loaded(root)
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    locator = mod.StaticLocator(
        {
            "dulat_search": (_dulat_search_db(tmp_path / "dulat_search.sqlite"), "env"),
            "burns": (_burns_dir(tmp_path / "context_labeling"), "sibling"),
        }
    )
    enabled = ("auto-parsing", "dulat", "legacy-review", "burns-cultic-vocabulary")
    collector, policy = _collector(mod, loaded, enabled, locator)
    context = collector.initialize_skill_context(state, "run:init")
    records = collector.collect_evidence(state, _token(loaded, "1003"), context, "run:initial:1003:evidence")

    assert any(SENTINEL in r.summary for r in records)
    assert SENTINEL not in policy.to_json()
    assert SENTINEL not in json.dumps(context, ensure_ascii=False)
    assert all(SENTINEL not in r.source_ref and SENTINEL not in r.provenance_ref for r in records)
    assert str(tmp_path) not in policy.to_json()
    assert str(tmp_path) not in json.dumps(context, ensure_ascii=False)


# --- review findings (2026-09-21) ---------------------------------------------------------


def test_absent_sqlite_resource_is_never_created_and_yields_nothing(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    missing = tmp_path / "gone.sqlite"
    records = mod.DulatAdapter().collect(
        mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-x"), missing
    )
    assert records == ()
    assert not missing.exists()


@pytest.mark.parametrize("breakage", ["junk-file", "missing-table", "missing-column"])
def test_present_but_unreadable_resource_degrades_to_marker_record(
    tmp_path: Path, breakage: str
) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    db = tmp_path / "modules_cache.sqlite"
    if breakage == "junk-file":
        db.write_bytes(b"not a database")
    else:
        con = sqlite3.connect(db)
        if breakage == "missing-table":
            con.execute("CREATE TABLE other (x)")
        else:
            con.execute("CREATE TABLE module_records (module_id TEXT, ref_norm TEXT)")
        con.commit()
        con.close()
    locator = mod.StaticLocator({"modules": (db, "explicit")})
    collector, policy = _collector(mod, loaded, ("auto-parsing", "eupt"), locator)
    assert policy.available_sources == ("auto-parsing", "eupt")
    context = collector.initialize_skill_context(state, "run:init")
    records = collector.collect_evidence(
        state, _token(loaded, "1003"), context, "run:initial:1003:evidence"
    )
    eupt = [r for r in records if r.source_id == "eupt"]
    assert len(eupt) == 1
    assert eupt[0].source_ref.startswith("eupt:unreadable:")
    assert "unreadable" in eupt[0].summary
    # The failure is reported by exception type only; never a path.
    assert str(tmp_path) not in eupt[0].summary and str(tmp_path) not in eupt[0].source_ref
    assert collector.adapter_failures == {"eupt": eupt[0].source_ref.split(":")[-1]}


def test_static_locator_treats_missing_explicit_path_as_absent(tmp_path: Path) -> None:
    mod = _adapters()
    locator = mod.StaticLocator({"modules": (tmp_path / "missing.sqlite", "explicit")})
    assert locator.locate("modules") is None
    policy = mod.build_policy(("auto-parsing", "eupt"), locator)
    assert policy.absent_sources == ("eupt",)


def test_build_policy_survives_a_raising_locator_without_leaking_paths(tmp_path: Path) -> None:
    mod = _adapters()

    class Raising:
        def locate(self, kind):
            raise RuntimeError(f"boom {tmp_path}")

    policy = mod.build_policy(("auto-parsing", "dulat"), Raising())
    assert policy.absent_sources == ("dulat",)
    assert str(tmp_path) not in policy.to_json()


def test_burns_adapter_tolerates_extra_fields_and_expands_line_ranges(tmp_path: Path) -> None:
    mod = _adapters()
    loaded = _loaded(_repo(tmp_path))
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    burns = tmp_path / "context_labeling" / "Workbooks"
    burns.mkdir(parents=True)
    (burns / "terms.csv").write_text(
        "headword,root,category,ktu,references\n"
        "ġr,ġ-r-r,cultic,9.9,I.1-3,extra,fields\n"
        "zz,z,cultic,9.9,II.1\n",
        encoding="utf-8",
    )
    records = mod.BurnsAdapter().collect(
        mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-b"), burns.parent
    )
    assert [r.source_ref for r in records] == ["burns:Workbooks/terms.csv:KTU 9.9 I:2:ġr"]


def test_legacy_review_is_absent_when_the_tablet_has_no_legacy_file(tmp_path: Path) -> None:
    mod = _adapters()
    root = _repo(tmp_path)
    loaded = _loaded(root)
    policy = mod.build_policy(("auto-parsing", "legacy-review"), mod.StaticLocator({}), loaded)
    assert policy.absent_sources == ("legacy-review",)
    _legacy_review(root)
    policy = mod.build_policy(("auto-parsing", "legacy-review"), mod.StaticLocator({}), loaded)
    assert policy.absent_sources == ()
    assert policy.availability_for("legacy-review").locator_kind == "repository"


def test_legacy_review_adapter_survives_non_utf8_and_markerless_files(tmp_path: Path) -> None:
    mod = _adapters()
    root = _repo(tmp_path)
    loaded = _loaded(root)
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    ctx = mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-l")
    bad = root / "reviewed" / "KTU 9.9.txt"
    bad.write_bytes(b"\xff\xfe\x00 not utf-8")
    assert mod.LegacyReviewAdapter().collect(ctx, bad) == ()
    bad.write_text("id\tsurface\tanalysis\n1\tġr\tġr(II)/\n", encoding="utf-8")
    assert mod.LegacyReviewAdapter().collect(ctx, bad) == ()


def test_corpus_parallels_exclude_the_column_under_review(tmp_path: Path) -> None:
    mod = _adapters()
    root = _repo(tmp_path)
    # Column II of the same tablet is reviewed and shares a surface with column I's 1001.
    (root / "reviewed" / "KTU 9.9.tsv").write_text(
        (root / "reviewed" / "KTU 9.9.tsv").read_text(encoding="utf-8")
        + "# KTU 9.9 II:2\t\t\t\t\t\t\t\n"
        + "1005\tl\tl \tl(I)\tl (I)\tprep.\tto\treviewed elsewhere\n"
        + "# KTU 9.9 I:3\t\t\t\t\t\t\t\n",
        encoding="utf-8",
    )
    # A reviewed (non-seeded) row for another token of column I itself.
    text = (root / "reviewed" / "KTU 9.9.tsv").read_text(encoding="utf-8")
    text = text.replace(
        "1002\tbˤl\tbʿl\tbˤl(II)/\tbʕl (II)\tn. m. sg. abs. gen.\tBaal\t## SEEDED from auto-parse; not yet hand-reviewed.\n",
        "1002\tbˤl\tbʿl\tbˤl(II)/\tbʕl (II)\tn. m. sg. abs. gen.\tBaal\treviewed gold\n",
    )
    (root / "reviewed" / "KTU 9.9.tsv").write_text(text, encoding="utf-8")
    (root / "auto_parsing" / "0.2.8" / "KTU 9.9.tsv").write_text(
        (root / "auto_parsing" / "0.2.8" / "KTU 9.9.tsv").read_text(encoding="utf-8")
        + "# KTU 9.9 II:2\t\t\t\t\t\t\n"
        + "1005\tl\tl(I)\tl (I)\tprep.\tto\t\n",
        encoding="utf-8",
    )
    loaded = _loaded(root)
    state = ColumnRunState.initial(loaded.task, loaded.snapshot)
    adapter = mod.CorpusParallelsAdapter()
    l_records = adapter.collect(mod.TokenEvidenceContext(loaded, state, _token(loaded, "1001"), "op-p"), None)
    assert [r.source_ref for r in l_records] == ["corpus-parallels:reviewed/KTU 9.9.tsv:1005"]
    # Another column-I token's reviewed row must not be evidence for column I.
    b_records = adapter.collect(mod.TokenEvidenceContext(loaded, state, _token(loaded, "1003"), "op-q"), None)
    assert not any(":1002" in r.source_ref for r in b_records)
