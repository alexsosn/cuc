"""HARN-033 RED gate: stage 1 (lexical linking + POS) with Jev, trimmed inputs, own scorer."""

from __future__ import annotations

import importlib
import json

import pytest

from harness.column_state import ColumnSnapshot, ColumnToken, EvidenceRecord
from harness.live_providers import ProviderPermanentError
from tests.test_harn029_typesafe_jev import KEY_ENV, Transport, _adjudicate_payload, _request, _row, _state


def _lex():
    try:
        return importlib.import_module("harness.lexical_stage")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-033 lexical stage is not implemented yet: {exc}")


def _jev():
    return importlib.import_module("harness.typesafe_jev")


# --- definitions --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pos,expected",
    [
        ("n. f. sg. cstr. gen.", "n."),
        ("vb G prefc. 3 m. sg.", "vb G"),
        ("vb Gt suffc. 3 m. sg. + encl. -m", "vb Gt"),
        ("vb Špass", "vb Špass"),
        ("vb", "vb"),
        ("DN m. sg. abs. gen.", "DN"),
        ("Subordinating functor", "subordinating"),
        ("prep./conj./adv.", "prep./conj./adv."),
        ("?", "?"),
        ("", "?"),
        ("  adj. m. pl. ", "adj."),
    ],
)
def test_pos_class(pos, expected) -> None:
    assert _lex().pos_class(pos) == expected


@pytest.mark.parametrize(
    "dulat,expected",
    [("ġr (III)", "ġr (III)"), ("ġr  (III)", "ġr (III)"), ("/m-ġ-y/", "/m-ġ-y/"), (" -n (IV) ", "-n (IV)"), ("?", "?"), ("", "?")],
)
def test_lemma_key(dulat, expected) -> None:
    assert _lex().lemma_key(dulat) == expected


# --- grouping -----------------------------------------------------------------------------


def _evidence(op="op"):
    recs = [
        EvidenceRecord(f"{op}:auto-parsing:1", "auto-parsing", "auto:1", "p", _row("lb/", "lb", "n. m. sg. abs. gen.", "heart")),
        EvidenceRecord(f"{op}:auto-parsing:2", "auto-parsing", "auto:2", "p", _row("lb/", "lb", "n. m. sg. abs. nom.", "heart")),
        EvidenceRecord(f"{op}:auto-parsing:3", "auto-parsing", "auto:3", "p", _row("!l!b[", "/l-b-b/", "vb G prefc. 3 m. sg.", "to be wise")),
        EvidenceRecord(f"{op}:corpus-parallels:1", "corpus-parallels", "cp:1", "p",
                       json.dumps({"morphological_parsing": "lb/", "dulat": "lb", "pos": "n. m. sg. cstr. nom.", "gloss": "heart, mind", "attestations": 7, "examples": []})),
        EvidenceRecord(f"{op}:corpus-parallels:2", "corpus-parallels", "cp:2", "p",
                       json.dumps({"morphological_parsing": "lb(II)/", "dulat": "lb (II)", "pos": "n. m. sg. abs.", "gloss": "lion", "attestations": 2, "examples": []})),
        EvidenceRecord(f"{op}:legacy-review:1", "legacy-review", "lr:1", "p", json.dumps({"surface": "lb", "analyses": ["lb(III)/"]})),
        EvidenceRecord(f"{op}:dulat:1", "dulat", "dulat_search:KTU 9.9 I:5:entry-1", "p",
                       json.dumps({"entry_id": 1, "label": "lb", "sense_labels": ["1) heart"], "reference_translations": []})),
        EvidenceRecord(f"{op}:dulat:2", "dulat", "dulat_search:KTU 9.9 I:5:entry-2", "p",
                       json.dumps({"entry_id": 2, "label": "ảbn", "sense_labels": ["1) stone"], "reference_translations": []})),
        EvidenceRecord(f"{op}:eupt:1", "eupt", "eupt:EUPT_translation:KTU 9.9 I:5", "p", "ihr Herz"),
        EvidenceRecord(f"{op}:tropper:1", "tropper", "tropper:KTU 9.9 I:5:pages-12", "p", "Tropper cites KTU 9.9 I:5 on pages 12"),
        EvidenceRecord(f"{op}:burns-cultic-vocabulary:1", "burns-cultic-vocabulary", "burns:x.csv:KTU 9.9 I:5:ab šnm", "p", json.dumps({"headword": "ab šnm", "category": "DN"})),
        EvidenceRecord(f"{op}:burns-cultic-vocabulary:2", "burns-cultic-vocabulary", "burns:x.csv:KTU 9.9 I:5:lb", "p", json.dumps({"headword": "lb", "category": "cultic term"})),
    ]
    return [r.to_dict() for r in recs]


def test_grouping_collapses_case_and_gloss_variants_and_keeps_homonyms_apart() -> None:
    lex = _lex()
    cands = lex.group_lexical_candidates(_evidence(), max_candidates=64)
    readings = [(c.reading.lemma, c.reading.pos_class) for c in cands]
    assert readings == [("lb", "n."), ("/l-b-b/", "vb G"), ("lb (II)", "n."), ("lb (III)", "?")]
    lb = cands[0]
    assert [r["morphological_parsing"] for r in lb.rows] == ["lb/", "lb/", "lb/"]      # parser rows first, then parallel
    assert lb.rows[0]["pos"] == "n. m. sg. abs. gen." and lb.rows[2]["pos"] == "n. m. sg. cstr. nom."
    assert lb.attestations == 7
    assert lb.glosses == ("heart", "heart, mind")
    assert lb.sources == ("auto-parsing", "corpus-parallels")
    assert set(lb.evidence_ids) == {"op:auto-parsing:1", "op:auto-parsing:2", "op:corpus-parallels:1"}
    legacy = cands[3]
    assert legacy.rows[0]["dulat"] == "lb (III)" and legacy.rows[0]["pos"] == "?"


def test_grouping_limits_and_unresolved_parser_rows() -> None:
    lex = _lex()
    ev = [EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("", "", "", "")).to_dict()]
    assert lex.group_lexical_candidates(ev, max_candidates=64) == ()
    ev = [EvidenceRecord(f"op:corpus-parallels:{i}", "corpus-parallels", f"c{i}", "p",
                         json.dumps({"morphological_parsing": f"x{i}/", "dulat": f"x{i}", "pos": "n.", "gloss": "g"})).to_dict() for i in range(5)]
    with pytest.raises(ProviderPermanentError, match="candidate"):
        lex.group_lexical_candidates(ev, max_candidates=3)


# --- trimmed request ----------------------------------------------------------------------


def _lexical_client(mod, transport):
    return mod.TypeSafeJevClient(api_key_env=KEY_ENV, http_json=transport, decision_policy=mod.JevDecisionPolicy(stage="lexical"))


def _column_payload(evidence, token_index=2, prior=()):
    """A 5-token column over three lines so the window is testable."""

    state = _state()
    snapshot = ColumnSnapshot("s", "f", "p", (
        ColumnToken("1", 1, "I:4", "w"), ColumnToken("2", 2, "I:5", "l"), ColumnToken("3", 3, "I:5", "lb"),
        ColumnToken("4", 4, "I:6", "il"), ColumnToken("5", 5, "I:9", "far"),
    ))
    payload = _adjudicate_payload(evidence=evidence)
    payload["snapshot"] = snapshot.to_dict()
    payload["token"] = snapshot.tokens[token_index].to_dict()
    payload["prior_decisions"] = list(prior)
    return payload


def test_lexical_request_is_trimmed_to_the_line_window_and_matching_evidence(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    prior = [
        {"decision_id": "d1", "token_id": "1", "analyses": ["w"], "evidence_ids": ["x"], "summary": "s", "revisit_of": None, "revisit_request_id": None,
         "reviewed_rows": [{"morphological_parsing": "w", "dulat": "w", "pos": "conj.", "gloss": "and", "comments": ""}]},
        {"decision_id": "d2", "token_id": "2", "analyses": ["l(I)"], "evidence_ids": ["x"], "summary": "s", "revisit_of": None, "revisit_request_id": None,
         "reviewed_rows": [{"morphological_parsing": "l(I)", "dulat": "l (I)", "pos": "prep.", "gloss": "to", "comments": ""}]},
    ]
    transport = Transport(probabilities_by_analysis={"lb/": 1.0})
    _lexical_client(mod, transport).generate_json(_request(_column_payload(_evidence(), prior=prior)), 5.0)
    body = transport.calls[0][2]
    state = body["state"]
    assert set(body["questions"]) == {"lexeme", "ambiguous"}
    # window: the token's line with neighbours, not the whole column
    assert state["line"]["ref"] == "I:5" and [t["surface"] for t in state["line"]["tokens"]] == ["l", "lb"]
    assert state["context_lines"] == {"before": {"ref": "I:4", "text": "w"}, "after": {"ref": "I:6", "text": "il"}}
    assert "column" not in state and "column_text" not in state
    # history: same-line lexical decisions only
    assert state["line_decisions"] == [{"surface": "l", "lemma": "l (I)", "pos": "prep."}]
    # evidence: matching DULAT only (others counted), EUPT, matching Burns; no Tropper
    kinds = [e["source"] for e in state["evidence"]]
    assert "tropper" not in kinds
    dulat = [e for e in state["evidence"] if e["source"] == "dulat"]
    assert len(dulat) == 1 and "lb" in json.dumps(dulat[0], ensure_ascii=False) and "ảbn" not in json.dumps(dulat, ensure_ascii=False)
    assert state["dulat_citations_for_other_words_on_line"] == 1
    burns = [e for e in state["evidence"] if e["source"] == "burns-cultic-vocabulary"]
    assert len(burns) == 1 and "ab šnm" not in json.dumps(burns, ensure_ascii=False)
    assert any(e["source"] == "eupt" for e in state["evidence"])
    # options: one per lexical reading, with glosses as identification aid
    crit = body["questions"]["lexeme"]["criteria"]
    assert sorted(k for k in crit if k != "none-of-these") == ["reading-1", "reading-2", "reading-3", "reading-4"]
    assert crit["reading-1"]["lemma"] == "lb" and crit["reading-1"]["pos"] == "n." and crit["reading-1"]["glosses"] == ["heart", "heart, mind"]
    assert crit["reading-1"]["reviewed_attestations_elsewhere"] == 7 and crit["reading-1"]["parser_alternatives"] == 2
    assert "morphological_parsing" not in json.dumps(crit)   # stage 1 does not show encodings
    assert len(json.dumps(body, ensure_ascii=False).encode()) < 6000


def test_lexical_request_at_column_edges_has_partial_window(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"lb/": 1.0})
    ev = [EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("w", "w", "conj.", "and")).to_dict()]
    _lexical_client(mod, transport).generate_json(_request(_column_payload(ev, token_index=0)), 5.0)
    state = transport.calls[0][2]["state"]
    assert state["context_lines"]["before"] is None and state["context_lines"]["after"]["ref"] == "I:5"


# --- answer mapping -----------------------------------------------------------------------


class LexTransport(Transport):
    """Answers by lemma key instead of analysis."""

    def __init__(self, *, p_by_lemma, ambiguous=0.05, confidence=0.8, model="jev-1.13.0"):
        super().__init__(probabilities_by_analysis={}, ambiguous=ambiguous, confidence=confidence, model=model)
        self.p_by_lemma = p_by_lemma

    def __call__(self, url, headers, body, timeout_seconds):
        self.calls.append((url, dict(headers), json.loads(json.dumps(body))))
        answers = {}
        for name, q in body["questions"].items():
            if q["type"] == "choice":
                probs = {k: (self.p_by_lemma.get(v.get("lemma"), 0.0) if isinstance(v, dict) and "lemma" in v else self.p_by_lemma.get("none-of-these", 0.0)) for k, v in q["criteria"].items()}
                total = sum(probs.values()) or 1.0
                probs = {k: p / total for k, p in probs.items()}
                answers[name] = {"type": "choice", "choice": max(probs, key=probs.get), "probabilities": probs, "confidence": self.confidence}
            else:
                answers[name] = {"type": "noul", "noul": self.ambiguous}
        return {"model": self.model, "answers": answers, "usage": {"input_tokens": 50, "output_tokens": 0}}


def test_chosen_reading_yields_the_parsers_rows_for_that_lexeme(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = LexTransport(p_by_lemma={"lb": 0.9, "/l-b-b/": 0.1})
    payload = _lexical_client(mod, transport).generate_json(_request(_column_payload(_evidence())), 5.0).payload
    assert payload["analyses"] == ["lb/"]
    poses = [r["pos"] for r in payload["reviewed_rows"]]
    assert poses == ["n. m. sg. abs. gen.", "n. m. sg. abs. nom."]        # parser rows only, parallel row not used
    assert set(payload["evidence_ids"]) == {"op:auto-parsing:1", "op:auto-parsing:2", "op:corpus-parallels:1"}
    assert payload["jev"]["stage"] == "lexical"
    assert payload["jev"]["reading"] == {"lemma": "lb", "pos": "n."}
    assert payload["jev"]["readings"]["reading-1"] == {"lemma": "lb", "pos": "n."}


def test_abstention_cannot_win_by_plurality(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    # none-of-these has the single largest share (0.4) but readings together hold 0.6.
    transport = LexTransport(p_by_lemma={"none-of-these": 0.4, "lb": 0.3, "/l-b-b/": 0.2, "lb (II)": 0.1})
    payload = _lexical_client(mod, transport).generate_json(_request(_column_payload(_evidence())), 5.0).payload
    assert payload["analyses"] == ["lb/"]
    assert payload["jev"]["choice"] == "reading-1"
    assert payload["jev"]["abstention_probability"] == pytest.approx(0.4)
    transport = LexTransport(p_by_lemma={"none-of-these": 0.6, "lb": 0.4})
    payload = _lexical_client(mod, transport).generate_json(_request(_column_payload(_evidence())), 5.0).payload
    assert payload["analyses"] == ["?"]


def test_full_stage_also_refuses_plurality_abstention(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"none-of-these": 0.3, "ġr(III)/": 0.25, "ġr(I)/": 0.2, "ġr(II)/": 0.05})
    payload = mod.TypeSafeJevClient(api_key_env=KEY_ENV, http_json=transport).generate_json(_request(_adjudicate_payload()), 5.0).payload
    assert payload["analyses"][0] != "?"


def test_alternative_lexemes_kept_above_threshold_and_parallel_only_lexeme_uses_parallel_rows(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = LexTransport(p_by_lemma={"lb": 0.55, "lb (II)": 0.45})
    payload = _lexical_client(mod, transport).generate_json(_request(_column_payload(_evidence())), 5.0).payload
    assert payload["analyses"] == ["lb/", "lb(II)/"]
    assert payload["reviewed_rows"][-1]["gloss"] == "lion"


# --- scorer -------------------------------------------------------------------------------


def test_lexical_scorer_reports_exactness_abstentions_and_baselines() -> None:
    lex = _lex()
    gold = {
        "1": [("lb", "n. m. sg. abs. gen.")],
        "2": [("šnm", "DN gen."), ("šnt (I)", "n. f. pl. abs. gen.")],
        "3": [("/y-d-y/ (II)", "vb G prefc. 3 f. sg.")],
        "4": [("qn", "n. m. sg. cstr. acc.")],
    }
    predicted = {
        "1": [("lb", "n.")],                      # exact
        "2": [("šnm", "DN")],                     # dropped one gold reading
        "3": [("?", "?")],                        # abstained; gold not offered → correct abstention
        "4": [("?", "?")],                        # abstained; gold offered → wrong abstention
    }
    offered = {"1": [("lb", "n."), ("/l-b-b/", "vb G")], "2": [("šnm", "DN"), ("šnt (I)", "n.")], "3": [("/n-d-d/", "vb G")], "4": [("qn", "n.")]}
    parser_first = {"1": ("lb", "n."), "2": ("šnm", "DN"), "3": ("/n-d-d/", "vb G"), "4": ("qn", "n.")}
    parser_all = {k: v for k, v in offered.items()}
    score = lex.score_lexical_column(predicted=predicted, gold=gold, offered=offered, parser_first=parser_first, parser_all=parser_all)
    assert score.tokens == 4
    assert score.exact == 1 and score.lemma_exact == 1
    assert score.subset == 2           # exact + dropped-one
    assert score.abstained_correct == 1 and score.abstained_wrong == 1
    assert score.parser_first_exact == 2 and score.parser_all_exact == 2 and score.ceiling == 3
    d = score.to_dict()
    assert d["exact_rate"] == 0.25 and d["ceiling_rate"] == 0.75
    assert lex.gold_lexical_readings([("lb/", "lb", "n. m. sg. abs. gen.", "heart")]) == (lex.LexicalReading("lb", "n."),)
