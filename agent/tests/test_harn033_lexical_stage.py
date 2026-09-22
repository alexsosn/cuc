"""HARN-033 RED gate: stage 1 (lexical linking + POS) with Jev, trimmed inputs, own scorer."""

from __future__ import annotations

import importlib
import json

import pytest

from harness.column_state import ColumnSnapshot, ColumnToken, EvidenceRecord
from harness.live_providers import ProviderPermanentError
from tests.test_harn029_typesafe_jev import (
    KEY_ENV,
    Transport,
    _adjudicate_payload,
    _request,
    _row,
    _state,
)


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
        ("vb G prefc. 3 m. sg.", "vb"),
        ("vb Gt suffc. 3 m. sg. + encl. -m", "vb"),
        ("vb Špass", "vb"),
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
    # Legacy analyses (surface-spelled, POS unknown) are context for the model, never a
    # candidate: they cannot be exact and would duplicate the parser's lexeme (review H2).
    assert readings == [("lb", "n."), ("/l-b-b/", "vb"), ("lb (II)", "n.")]
    assert cands[0].forms == ("lb/",) and cands[1].forms == ("!l!b[",)
    lb = cands[0]
    assert [r["morphological_parsing"] for r in lb.rows] == ["lb/", "lb/", "lb/"]      # parser rows first, then parallel
    assert lb.rows[0]["pos"] == "n. m. sg. abs. gen." and lb.rows[2]["pos"] == "n. m. sg. cstr. nom."
    assert lb.attestations == 7
    assert lb.glosses == ("heart", "heart, mind")
    assert lb.sources == ("auto-parsing", "corpus-parallels")
    assert set(lb.evidence_ids) == {"op:auto-parsing:1", "op:auto-parsing:2", "op:corpus-parallels:1"}


def test_grouping_limits_and_unresolved_parser_rows() -> None:
    lex = _lex()
    ev = [EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("", "", "", "")).to_dict()]
    assert lex.group_lexical_candidates(ev, max_candidates=64) == ()
    ev = [EvidenceRecord(f"op:corpus-parallels:{i}", "corpus-parallels", f"c{i}", "p",
                         json.dumps({"morphological_parsing": f"x{i}/", "dulat": f"x{i}", "pos": "n.", "gloss": "g"})).to_dict() for i in range(5)]
    with pytest.raises(ProviderPermanentError, match="candidate"):
        lex.group_lexical_candidates(ev, max_candidates=3)


# --- trimmed request ----------------------------------------------------------------------


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




class NoulTransport(LexTransport):
    def __init__(self, *, fits: float, **kw):
        super().__init__(p_by_lemma={}, **kw)
        self.fits = fits

    def __call__(self, url, headers, body, timeout_seconds):
        self.calls.append((url, dict(headers), json.loads(json.dumps(body))))
        answers = {name: {"type": "noul", "noul": self.fits if name == "lexeme_fits" else self.ambiguous} for name in body["questions"]}
        return {"model": self.model, "answers": answers, "usage": {"input_tokens": 40, "output_tokens": 0}}



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
    transport = LexTransport(p_by_lemma={"lb": 1.0})
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
    assert sorted(k for k in crit if k != "none-of-these") == ["reading-1", "reading-2", "reading-3"]
    # the legacy analysis is context, not an option (review H2)
    assert [e for e in state["evidence"] if e["source"] == "legacy-review"][0]["content"].endswith("lb(III)/")
    assert crit["reading-1"]["lemma"] == "lb" and crit["reading-1"]["pos"] == "n." and crit["reading-1"]["glosses"] == ["heart", "heart, mind"]
    assert crit["reading-1"]["reviewed_attestations_elsewhere"] == 7 and crit["reading-1"]["parser_alternatives"] == 2
    assert crit["reading-1"]["forms"] == ["lb/"]             # how the surface maps onto the lemma
    assert "aleph" in body["questions"]["lexeme"]["instructions"].lower()
    assert len(json.dumps(body, ensure_ascii=False).encode()) < 6000


def test_lexical_request_at_column_edges_has_partial_window(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = NoulTransport(fits=0.9)
    ev = [EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("w", "w", "conj.", "and")).to_dict()]
    client = mod.TypeSafeJevClient(api_key_env=KEY_ENV, http_json=transport,
                                   decision_policy=mod.JevDecisionPolicy(stage="lexical", verify_single_candidate=True))
    client.generate_json(_request(_column_payload(ev, token_index=0)), 5.0)
    state = transport.calls[0][2]["state"]
    assert state["context_lines"]["before"] is None and state["context_lines"]["after"]["ref"] == "I:5"


# --- answer mapping -----------------------------------------------------------------------


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


def test_alternative_lexemes_kept_only_when_jev_reports_ambiguity(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    # Same distribution; without an ambiguity signal only the top lexeme is kept.
    transport = LexTransport(p_by_lemma={"lb": 0.55, "lb (II)": 0.45}, ambiguous=0.1)
    payload = _lexical_client(mod, transport).generate_json(_request(_column_payload(_evidence())), 5.0).payload
    assert payload["analyses"] == ["lb/"]
    transport = LexTransport(p_by_lemma={"lb": 0.55, "lb (II)": 0.45}, ambiguous=0.8)
    payload = _lexical_client(mod, transport).generate_json(_request(_column_payload(_evidence())), 5.0).payload
    assert payload["analyses"] == ["lb/", "lb(II)/"]
    assert payload["reviewed_rows"][-1]["gloss"] == "lion"      # parallel-only lexeme uses the parallel row


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
    offered = {"1": [("lb", "n."), ("/l-b-b/", "vb")], "2": [("šnm", "DN"), ("šnt (I)", "n.")], "3": [("/n-d-d/", "vb")], "4": [("qn", "n.")]}
    parser_first = {"1": ("lb", "n."), "2": ("šnm", "DN"), "3": ("/n-d-d/", "vb"), "4": ("qn", "n.")}
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


def test_token_without_any_lexeme_is_left_unresolved_without_a_call(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = LexTransport(p_by_lemma={})
    client = _lexical_client(mod, transport)
    client.declared_exact_model = "jev-1.13.0"
    ev = [EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("?", "?", "?", "?")).to_dict(),
          EvidenceRecord("op:dulat:1", "dulat", "d", "p", "{}").to_dict()]
    response = client.generate_json(_request(_column_payload(ev)), 5.0)
    assert transport.calls == []
    assert response.model == "jev-1.13.0" and response.usage.input_tokens == 0
    assert response.payload["analyses"] == ["?"]
    assert response.payload["evidence_ids"] == ["op:auto-parsing:1"]
    assert response.payload["jev"]["reading"] is None and response.payload["jev"]["no_candidates"] is True


def test_binding_declares_the_exact_model_to_the_client(monkeypatch) -> None:
    mod = _jev()
    from harness.model_benchmark import BenchmarkBackendSpec
    client = _lexical_client(mod, LexTransport(p_by_lemma={}))
    mod.jev_binding(BenchmarkBackendSpec("j", "typesafe", "jev-latest", "jev-1.13.0", "0" * 64), requested_model="jev-latest",
                    exact_model_version="jev-1.13.0", client=client, exact_version_provenance="docs")
    assert client.declared_exact_model == "jev-1.13.0"



# --- round 2: single-candidate tokens and confidence-gated abstention ---------------------------


def _single_evidence():
    return [EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("aps/+h", "ảps", "n. m. sg. cstr. nom.", "extremity")).to_dict(),
            EvidenceRecord("op:dulat:1", "dulat", "d", "p", json.dumps({"entry_id": 1, "label": "ảps", "sense_labels": ["1) extremity"]})).to_dict()]


def test_single_candidate_token_is_accepted_without_a_call_by_default(monkeypatch) -> None:
    """The parser's only lexeme is right ~98% of the time; a veto question is noise."""

    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = NoulTransport(fits=0.2)
    client = _lexical_client(mod, transport)
    client.declared_exact_model = "jev-1.13.0"
    response = client.generate_json(_request(_column_payload(_single_evidence())), 5.0)
    assert transport.calls == []
    assert response.model == "jev-1.13.0" and response.usage.input_tokens == 0
    payload = response.payload
    assert payload["analyses"] == ["aps/+h"] and payload["reviewed_rows"][0]["dulat"] == "ảps"
    assert payload["jev"]["question"] == "single-candidate-accepted"
    assert payload["jev"]["reading"] == {"lemma": "ảps", "pos": "n."}


def test_verify_singles_asks_a_noul_and_records_it_as_review_priority_only(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = NoulTransport(fits=0.2)
    client = mod.TypeSafeJevClient(api_key_env=KEY_ENV, http_json=transport,
                                   decision_policy=mod.JevDecisionPolicy(stage="lexical", verify_single_candidate=True))
    payload = client.generate_json(_request(_column_payload(_single_evidence())), 5.0).payload
    body = transport.calls[0][2]
    assert list(body["questions"]) == ["lexeme_fits"] and body["state"]["candidate"]["forms"] == ["aps/+h"]
    # Still accepted: the fit is a worklist signal, never a decision.
    assert payload["analyses"] == ["aps/+h"]
    assert payload["jev"]["question"] == "lexeme_fits" and payload["jev"]["fits"] == 0.2
    assert payload["jev"]["review_priority"] == pytest.approx(0.8)
    assert "review priority" in payload["summary"]


def test_multi_candidate_abstention_needs_confidence(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    # p(none)=0.6 but confidence 0.1: "no opinion", so the best reading is taken and flagged.
    transport = LexTransport(p_by_lemma={"none-of-these": 0.6, "lb": 0.3, "/l-b-b/": 0.1}, confidence=0.1)
    payload = _lexical_client(mod, transport).generate_json(_request(_column_payload(_evidence())), 5.0).payload
    assert payload["analyses"] == ["lb/"]
    assert payload["jev"]["abstention_probability"] == pytest.approx(0.6) and payload["jev"]["low_confidence"] is True
    transport = LexTransport(p_by_lemma={"none-of-these": 0.6, "lb": 0.3, "/l-b-b/": 0.1}, confidence=0.7)
    payload = _lexical_client(mod, transport).generate_json(_request(_column_payload(_evidence())), 5.0).payload
    assert payload["analyses"] == ["?"]


# --- review findings (2026-09-22) --------------------------------------------------------------


def _parser_single(analysis="aps/+h", dulat="ảps", pos="n. m. sg. cstr. nom.", gloss="extremity"):
    return EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row(analysis, dulat, pos, gloss)).to_dict()


def _legacy(*analyses):
    return EvidenceRecord("op:legacy-review:1", "legacy-review", "lr:1", "p", json.dumps({"surface": "krt", "analyses": list(analyses)})).to_dict()


def test_legacy_only_token_is_not_a_candidate_and_never_accepted_silently(monkeypatch) -> None:
    """H1/H2: a damaged legacy string (`xxxx`, `]š]lyṭ[/`) must not become a curated row."""

    mod, lex = _jev(), _lex()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    ev = [EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("?", "?", "?", "?")).to_dict(), _legacy("xxxx", "]š]lyṭ[/")]
    assert lex.group_lexical_candidates(ev, max_candidates=64) == ()
    transport = NoulTransport(fits=0.9)
    client = _lexical_client(mod, transport)
    client.declared_exact_model = "jev-1.13.0"
    payload = client.generate_json(_request(_column_payload(ev)), 5.0).payload
    assert transport.calls == []
    assert payload["analyses"] == ["?"] and payload["jev"]["no_candidates"] is True
    assert "xxxx" not in json.dumps(payload)


def test_legacy_analyses_reach_the_model_as_context_not_as_options(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    ev = [r for r in _evidence() if r["source_id"] != "legacy-review"] + [_legacy("krt/", "!k!rt[")]
    transport = LexTransport(p_by_lemma={"lb": 0.9, "/l-b-b/": 0.05, "lb (II)": 0.05})
    _lexical_client(mod, transport).generate_json(_request(_column_payload(ev)), 5.0)
    body = transport.calls[0][2]
    criteria = body["questions"]["lexeme"]["criteria"]
    assert [c["lemma"] for k, c in criteria.items() if k != "none-of-these"] == ["lb", "/l-b-b/", "lb (II)"]
    legacy = [e for e in body["state"]["evidence"] if e["source"] == "legacy-review"]
    assert len(legacy) == 1 and "krt/" in legacy[0]["content"] and "!k!rt[" in legacy[0]["content"]


def test_single_candidate_without_a_parser_row_is_asked_not_accepted(monkeypatch) -> None:
    """The zero-call rule rests on the parser's 98% on singles; a parallel-only single is not that."""

    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    ev = [EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("?", "?", "?", "?")).to_dict(),
          EvidenceRecord("op:corpus-parallels:1", "corpus-parallels", "cp:1", "p",
                         json.dumps({"morphological_parsing": "krt/", "dulat": "krt", "pos": "PN", "gloss": "Kirta", "attestations": 4, "examples": []})).to_dict()]
    transport = LexTransport(p_by_lemma={"krt": 0.8, "none-of-these": 0.2}, confidence=0.9)
    client = _lexical_client(mod, transport)
    client.declared_exact_model = "jev-1.13.0"
    payload = client.generate_json(_request(_column_payload(ev)), 5.0).payload
    assert len(transport.calls) == 1
    body = transport.calls[0][2]
    assert set(body["questions"]) == {"lexeme", "ambiguous"} and "candidate" not in body["state"]
    assert set(body["questions"]["lexeme"]["criteria"]) == {"reading-1", "none-of-these"}
    assert payload["jev"]["question"] == "lexeme" and payload["reviewed_rows"][0]["dulat"] == "krt"
    # And a confident "none" on such a token is honoured.
    transport = LexTransport(p_by_lemma={"krt": 0.2, "none-of-these": 0.8}, confidence=0.9)
    client = _lexical_client(mod, transport)
    client.declared_exact_model = "jev-1.13.0"
    assert client.generate_json(_request(_column_payload(ev)), 5.0).payload["analyses"] == ["?"]


def test_zero_call_decisions_are_not_booked_as_requests_by_the_runtime(monkeypatch) -> None:
    """M4: a locally resolved token reserves no budget and leaves no phantom call artifact."""

    from harness.column_state import CompletionGateResult, EvidenceRecord as ER
    from harness.model_benchmark import BenchmarkBackendSpec, BenchmarkCase, EvaluationTarget, SharedBenchmarkAdapters
    from harness.parsing_evaluation import EfficiencyMetrics, ParsingEvaluationRecord, measure_column_behavior
    from harness.live_providers import ProviderBudgetPolicy, ProviderExecutionPolicy, run_live_benchmark
    from tests.test_harn029_typesafe_jev import GOLD_REF, SHA0, _state

    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = NoulTransport(fits=0.9)          # answers reconcile nouls; adjudicate must not call it
    client = _lexical_client(mod, transport)
    spec = BenchmarkBackendSpec("jev", "typesafe", "jev-latest", "jev-1.13.0", SHA0)
    binding = mod.jev_binding(spec, requested_model="jev-latest", exact_model_version="jev-1.13.0", client=client, exact_version_provenance="docs")

    def initialize(state, operation_id):
        return {"skill": "review-automatic-parsing"}

    def evidence(state, token, skill_context, operation_id):
        return (ER(f"{operation_id}:auto-parsing:1", "auto-parsing", f"auto:{token.token_id}", "auto-parsing:0.2.8", _row("l(I)", "l (I)", "prep.", "to")),)

    def gate(state, gate_id, skill_context, operation_id):
        return CompletionGateResult(gate_id, True, state.decision_revision, (f"gate:{gate_id}",), "passed")

    def evaluate(state, skill_context, operation_id, identity, target):
        return ParsingEvaluationRecord(1, identity, target, state.decision_revision, measure_column_behavior(state), (), (), EfficiencyMetrics(), (f"evaluation:{identity.run_id}",))

    shared = SharedBenchmarkAdapters(initialize, evidence, gate, evaluate)
    case = BenchmarkCase(1, "harn033-m4", _state(), SHA0, SHA0, SHA0, EvaluationTarget("t", GOLD_REF, "gold-provenance", "scorer", "scorer-provenance", SHA0))
    # Budget for exactly one generation request: the reconcile. Two zero-call adjudications must fit.
    budget = ProviderBudgetPolicy(1, 1, 20_000, 40_000, 1_000, 2_000, 64, 1, 10.0)
    result = run_live_benchmark(case, (binding,), shared_adapters=shared, execution_policy=ProviderExecutionPolicy(budget=budget, allow_paid_live_execution=True))
    trial = result.provider_trials[0]
    assert trial.terminal_status == "completed", trial
    assert len(transport.calls) == 1 and trial.request_count == 1
    assert [c.operation for c in trial.calls] == ["reconcile"]
    assert trial.local_resolutions == 2 and trial.to_dict()["local_resolutions"] == 2


def test_abstention_without_a_reported_confidence_is_not_honoured(monkeypatch) -> None:
    """L1: the ticket's rule is majority AND confidence ≥ 0.5; an absent confidence is not ≥ 0.5."""

    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = LexTransport(p_by_lemma={"none-of-these": 0.6, "lb": 0.3, "/l-b-b/": 0.1}, confidence=None)
    payload = _lexical_client(mod, transport).generate_json(_request(_column_payload(_evidence())), 5.0).payload
    assert payload["analyses"] == ["lb/"]
    assert payload["jev"]["confidence"] is None and payload["jev"]["low_confidence"] is True


def test_dulat_citations_are_selected_by_headword_with_aleph_and_homonym_normalisation(monkeypatch) -> None:
    """M2: the citation label is a phrase; the entry headword is what a candidate lemma matches."""

    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    ev = [EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("aps/+h", "ảps", "n. m. sg. cstr. nom.", "extremity")).to_dict(),
          EvidenceRecord("op:auto-parsing:2", "auto-parsing", "b", "p", _row("!ʕ!ly[", "/ʕ-l-y/ (I)", "vb G prefc. 3 m. sg.", "to go up")).to_dict(),
          EvidenceRecord("op:dulat:1", "dulat", "d1", "p", json.dumps({"entry_id": 1, "headword": "aps", "headword_pos": "n. m.", "label": "l aps tbl ym", "sense_labels": ["1) extremity"]})).to_dict(),
          EvidenceRecord("op:dulat:2", "dulat", "d2", "p", json.dumps({"entry_id": 2, "headword": "/ʕ-l-y/", "headword_pos": "vb", "label": "tʕl b ḥrn", "sense_labels": ["1) to go up"]})).to_dict(),
          EvidenceRecord("op:dulat:3", "dulat", "d3", "p", json.dumps({"entry_id": 3, "headword": "ym", "headword_pos": "n. m.", "label": "ym ym", "sense_labels": ["1) day"]})).to_dict(),
          EvidenceRecord("op:dulat:4", "dulat", "d4", "p", json.dumps({"entry_id": 4, "label": "ảps", "sense_labels": ["1) extremity (label-only summary)"]})).to_dict()]
    transport = LexTransport(p_by_lemma={"ảps": 0.9, "/ʕ-l-y/ (I)": 0.1})
    _lexical_client(mod, transport).generate_json(_request(_column_payload(ev)), 5.0)
    state = transport.calls[0][2]["state"]
    dulat = [e["ref"] for e in state["evidence"] if e["source"] == "dulat"]
    assert dulat == ["d1", "d2", "d4"]
    assert state["dulat_citations_for_other_words_on_line"] == 1


def test_burns_headword_match_normalises_the_surface_and_ignores_qualifiers(monkeypatch) -> None:
    """M3: `bˤl` (surface) must meet `bʿl (DN)` (Burns) — the DN evidence the misses needed."""

    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    snapshot_surface = "bˤl"
    ev = [EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("bʕl/", "bʕl", "n. m. sg. abs. nom.", "lord")).to_dict(),
          EvidenceRecord("op:auto-parsing:2", "auto-parsing", "b", "p", _row("bʕl(II)/", "bʕl (II)", "DN nom.", "Baal")).to_dict(),
          EvidenceRecord("op:burns-cultic-vocabulary:1", "burns-cultic-vocabulary", "b1", "p", json.dumps({"headword": "bʿl (DN)", "category": "DN"})).to_dict(),
          EvidenceRecord("op:burns-cultic-vocabulary:2", "burns-cultic-vocabulary", "b2", "p", json.dumps({"headword": "ṣpn (GN)", "category": "GN"})).to_dict()]
    payload = _column_payload(ev)
    payload["token"]["surface"] = snapshot_surface
    transport = LexTransport(p_by_lemma={"bʕl": 0.4, "bʕl (II)": 0.6})
    _lexical_client(mod, transport).generate_json(_request(payload), 5.0)
    burns = [e["ref"] for e in transport.calls[0][2]["state"]["evidence"] if e["source"] == "burns-cultic-vocabulary"]
    assert burns == ["b1"]


def test_scorer_excludes_tokens_whose_gold_is_unresolved(monkeypatch) -> None:
    """M1: a gold `?` is not a reading; counting it for the parser but not for Jev put the ceiling below a baseline."""

    lex = _lex()
    gold = {"1": [("lb", "n.")], "2": [("?", "?")], "3": [("", "")]}
    predicted = {"1": [("lb", "n.")], "2": [("?", "?")], "3": [("x", "n.")]}
    offered = {"1": [("lb", "n.")], "2": [], "3": [("x", "n.")]}
    parser_first = {"1": ("lb", "n."), "2": ("?", "?"), "3": ("?", "?")}
    score = lex.score_lexical_column(predicted=predicted, gold=gold, offered=offered, parser_first=parser_first, parser_all=offered)
    assert score.tokens == 1 and score.gold_unresolved == 2
    assert score.exact == 1 and score.parser_first_exact == 1 and score.ceiling == 1
    assert score.abstained_correct == 0 and score.abstained_wrong == 0
    d = score.to_dict()
    assert d["tokens"] == 1 and d["gold_unresolved"] == 2 and d["exact_rate"] == 1.0 and d["ceiling_rate"] == 1.0


def test_lexical_reconcile_is_phrased_for_the_lexeme(monkeypatch) -> None:
    """L3: stage 1 does not decide case; the consistency question compares lexemes, not morphology strings."""

    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    from tests.test_harn029_typesafe_jev import _reconcile_payload
    payload = _reconcile_payload()
    for d in payload["decisions"]:
        d["reviewed_rows"] = [{"morphological_parsing": d["analyses"][0], "dulat": "l (I)", "pos": "prep. + sfx.", "gloss": "to", "comments": ""}]
    transport = NoulTransport(fits=0.1)
    _lexical_client(mod, transport).generate_json(_request(payload, operation="reconcile"), 5.0)
    body = transport.calls[0][2]
    for decision in body["state"]["decisions"]:
        assert decision["reading"] == {"lemma": "l (I)", "pos": "prep."} and "analyses" not in decision
    question = next(iter(body["questions"].values()))
    assert "lexeme" in question["instructions"]["question"] and "reading" not in question["instructions"]["question"]


@pytest.mark.parametrize("rows", ["notalist", [{"morphological_parsing": "x/"}], [{"morphological_parsing": "x/", "dulat": 1, "pos": "n.", "gloss": "g", "comments": ""}]])
def test_runtime_rejects_any_malformed_structured_rows_as_a_provider_error(monkeypatch, rows) -> None:
    """L2: a wrong type or a missing field is a provider contract violation, not a crash or a silent drop."""

    from harness.live_providers import (ProviderBudgetPolicy, ProviderExecutionPolicy, ProviderPermanentError as PPE,
                                        _BudgetLedger, _ProviderDecisionRuntime)
    from harness.model_benchmark import BenchmarkBackendSpec
    from tests.test_harn029_typesafe_jev import SHA0, _state

    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    client = _lexical_client(mod, LexTransport(p_by_lemma={}))
    spec = BenchmarkBackendSpec("jev", "typesafe", "jev-latest", "jev-1.13.0", SHA0)
    binding = mod.jev_binding(spec, requested_model="jev-latest", exact_model_version="jev-1.13.0", client=client, exact_version_provenance="docs")
    budget = ProviderBudgetPolicy(16, 32, 20_000, 40_000, 1_000, 2_000, 64, 1, 10.0)
    runtime = _ProviderDecisionRuntime(binding, {"run_id": "r"}, ProviderExecutionPolicy(budget=budget, allow_paid_live_execution=True), _BudgetLedger(budget))
    state = _state()
    token = state.snapshot.tokens[0]
    evidence = (EvidenceRecord("op:auto-parsing:1", "auto-parsing", "a", "p", _row("x/", "x", "n.", "g")),)
    response = {"analyses": ["x/"], "evidence_ids": ["op:auto-parsing:1"], "summary": "s", "reviewed_rows": rows}
    monkeypatch.setattr(runtime, "_invoke", lambda request: response)
    with pytest.raises(PPE, match="structured rows"):
        runtime.adjudicate(state, token, evidence, {"skill": "s"}, "r:initial:1001:adjudicate")
