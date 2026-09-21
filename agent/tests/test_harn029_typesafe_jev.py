"""HARN-029 RED gate: Jev (TypeSafe System One) as a HARN-022 provider client."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from harness.column_state import (
    ColumnRunState,
    ColumnSnapshot,
    ColumnToken,
    CompletionGateResult,
    EvidenceRecord,
)
from harness.langgraph_column_review import build_column_task_from_capability
from harness.live_providers import (
    LiveProviderBinding,
    ProviderBudgetPolicy,
    ProviderCredentialError,
    ProviderExecutionPolicy,
    ProviderJSONRequest,
    ProviderPermanentError,
    run_live_benchmark,
)
from harness.model_benchmark import (
    BenchmarkBackendSpec,
    BenchmarkCase,
    SharedBenchmarkAdapters,
)
from harness.parsing_evaluation import (
    EfficiencyMetrics,
    EvaluationTarget,
    ParsingEvaluationRecord,
    measure_column_behavior,
)
from harness.skill_capabilities import SkillCapabilityRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
SHA0 = "0" * 64
GOLD_REF = "reviewed:HARN029-GOLD-DO-NOT-LEAK"
KEY_ENV = "HARN029_TYPESAFE_KEY"


def _jev():
    try:
        return importlib.import_module("harness.typesafe_jev")
    except ModuleNotFoundError as exc:
        pytest.fail(f"HARN-029 Jev client is not implemented yet: {exc}")


# --- fixtures -----------------------------------------------------------------------------


def _row(analysis: str, dulat: str, pos: str, gloss: str, comments: str = "") -> str:
    return json.dumps(
        {
            "morphological_parsing": analysis,
            "dulat": dulat,
            "pos": pos,
            "gloss": gloss,
            "comments": comments,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _evidence(op: str = "op") -> list[dict[str, str]]:
    """Evidence as the HARN-028 collector emits it, serialised for the provider payload."""

    records = [
        EvidenceRecord(f"{op}:auto-parsing:1", "auto-parsing", "auto:1003:alt-1", "auto-parsing:0.2.8",
                       _row("ġr(III)/", "ġr (III)", "n. m. sg. abs. acc.", "skin")),
        EvidenceRecord(f"{op}:auto-parsing:2", "auto-parsing", "auto:1003:alt-2", "auto-parsing:0.2.8",
                       _row("ġr(I)/", "ġr (I)", "n. m. sg. abs. acc.", "mountain")),
        EvidenceRecord(f"{op}:corpus-parallels:1", "corpus-parallels", "corpus-parallels:reviewed/KTU 1.1.tsv:154415", "repository:rev",
                       json.dumps({"morphological_parsing": "ġr(I)/", "dulat": "ġr (I)", "pos": "n. m. sg. abs. acc.", "gloss": "mountain", "attestations": 12, "examples": ["reviewed/KTU 1.1.tsv:154415"]}, ensure_ascii=False, sort_keys=True)),
        EvidenceRecord(f"{op}:corpus-parallels:2", "corpus-parallels", "corpus-parallels:reviewed/KTU 1.3.tsv:9", "repository:rev",
                       json.dumps({"morphological_parsing": "ġr(I)/", "dulat": "ġr (I)", "pos": "n. m. sg. cstr. gen.", "gloss": "mountain", "attestations": 3, "examples": []}, ensure_ascii=False, sort_keys=True)),
        EvidenceRecord(f"{op}:legacy-review:1", "legacy-review", "legacy-review:reviewed/KTU 9.9.txt:I:2:ġr", "repository:rev",
                       json.dumps({"surface": "ġr", "analyses": ["ġr(II)/"]}, ensure_ascii=False, sort_keys=True)),
        EvidenceRecord(f"{op}:dulat:1", "dulat", "dulat_search:KTU 9.9 I:2:entry-37", "dulat_search:abc",
                       json.dumps({"entry_id": 37, "label": "ġr (III)", "sense_labels": ["1) skin"]}, ensure_ascii=False)),
        EvidenceRecord(f"{op}:eupt:1", "eupt", "eupt:EUPT_translation:KTU 9.9 I:2", "modules_cache:abc", "(Ihre) Haut zerkratzte sie"),
    ]
    return [r.to_dict() for r in records]


def _state() -> ColumnRunState:
    registry = SkillCapabilityRegistry(REPO_ROOT)
    manifest = registry.get("review-automatic-parsing")
    provenance = registry.provenance("review-automatic-parsing")
    task = build_column_task_from_capability(
        manifest, provenance, task_id="harn029-run", corpus="CUC", tablet="KTU 9.9", column="I",
        repository_revision="harn029-fixture-revision",
    )
    return ColumnRunState.initial(
        task,
        ColumnSnapshot(
            "harn029-snapshot", "fixture:harn029", "harn029-provenance",
            (ColumnToken("1001", 1, "I:1", "l"), ColumnToken("1003", 2, "I:2", "ġr")),
        ),
    )


def _adjudicate_payload(op: str = "op", **extra) -> dict:
    state = _state()
    payload = {
        "model_context": {"run_id": "harn029-run", "model": {"provider": "typesafe"}},
        "task": state.task.to_dict(),
        "snapshot": state.snapshot.to_dict(),
        "token": state.snapshot.tokens[1].to_dict(),
        "evidence": _evidence(op),
        "prior_decisions": [],
        "skill_context": {"scope": {"every_token_in_order": True}, "evidence": {"absent_sources": ["tropper"]}},
        "revisit_request": None,
    }
    payload.update(extra)
    return payload


def _request(payload: dict, operation: str = "adjudicate", model: str = "jev-latest") -> ProviderJSONRequest:
    return ProviderJSONRequest(
        operation=operation,
        requested_model=model,
        system_prompt="system",
        payload=payload,
        output_schema={"type": "object", "properties": {}, "additionalProperties": False},
        max_output_tokens=64,
    )


class Transport:
    """Fake HTTP transport; answers are chosen by option analysis so tests read naturally."""

    def __init__(self, *, probabilities_by_analysis: dict[str, float], ambiguous: float = 0.05,
                 model: str = "jev-1.13.0", noul_by_token: dict[str, float] | None = None,
                 confidence: float = 0.8) -> None:
        self.probabilities_by_analysis = probabilities_by_analysis
        self.ambiguous = ambiguous
        self.model = model
        self.noul_by_token = noul_by_token or {}
        self.confidence = confidence
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url, headers, body, timeout_seconds):
        self.calls.append((url, dict(headers), json.loads(json.dumps(body))))
        questions = body["questions"]
        answers: dict[str, dict] = {}
        for name, question in questions.items():
            if question["type"] == "choice":
                probs = {}
                for key, description in question["criteria"].items():
                    analysis = description.get("morphological_parsing") if isinstance(description, dict) else None
                    probs[key] = self.probabilities_by_analysis.get(analysis, 0.0) if analysis else self.probabilities_by_analysis.get("none-of-these", 0.0)
                total = sum(probs.values()) or 1.0
                probs = {k: v / total for k, v in probs.items()}
                chosen = max(probs, key=probs.get)
                answers[name] = {"type": "choice", "choice": chosen, "probabilities": probs, "confidence": self.confidence}
            elif question["type"] == "noul":
                token_id = name.split(":")[-1] if ":" in name else None
                value = self.noul_by_token.get(token_id, self.ambiguous) if token_id else self.ambiguous
                answers[name] = {"type": "noul", "noul": value}
        return {"model": self.model, "answers": answers, "usage": {"input_tokens": 120, "output_tokens": 0}, "id": f"req-{len(self.calls)}"}


def _client(mod, transport, **kwargs):
    return mod.TypeSafeJevClient(api_key_env=KEY_ENV, http_json=transport, **kwargs)


# --- request shape ------------------------------------------------------------------------


def test_missing_credential_fails_before_network(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.delenv(KEY_ENV, raising=False)
    transport = Transport(probabilities_by_analysis={"ġr(III)/": 1.0})
    with pytest.raises(ProviderCredentialError):
        _client(mod, transport).generate_json(_request(_adjudicate_payload()), 5.0)
    assert transport.calls == []


def test_adjudicate_request_targets_systemone_with_bearer_and_closed_candidates(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"ġr(III)/": 0.9, "ġr(I)/": 0.1})
    _client(mod, transport).generate_json(_request(_adjudicate_payload()), 5.0)

    url, headers, body = transport.calls[0]
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert headers["Authorization"] == "Bearer sk-test"
    assert body["model"] == "jev-latest"
    assert set(body["questions"]) == {"reading", "ambiguous"}
    reading = body["questions"]["reading"]
    assert reading["type"] == "choice"
    analyses = [d.get("morphological_parsing") for d in reading["criteria"].values() if isinstance(d, dict)]
    # auto rows, distinct parallel readings and the legacy analysis, deduplicated; plus none-of-these
    assert sorted(a for a in analyses if a) == sorted(["ġr(III)/", "ġr(I)/", "ġr(I)/", "ġr(II)/"])
    assert "none-of-these" in reading["criteria"]
    assert body["questions"]["ambiguous"]["type"] == "noul"
    state = body["state"]
    assert state["token"]["surface"] == "ġr"
    assert [t["surface"] for t in state["column"]["tokens"]] == ["l", "ġr"]
    # Non-candidate evidence is in the state; candidate rows are only in the options.
    assert any("Haut" in json.dumps(e, ensure_ascii=False) for e in state["evidence"])
    assert "output_schema" not in json.dumps(body)
    assert "sk-test" not in json.dumps(body)


def test_state_never_carries_gold_or_paths(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"ġr(III)/": 1.0})
    payload = _adjudicate_payload(skill_context={"evaluation_target": GOLD_REF, "path": "/Users/x/dulat.sqlite"})
    _client(mod, transport).generate_json(_request(payload), 5.0)
    encoded = json.dumps(transport.calls[0][2], ensure_ascii=False)
    assert GOLD_REF not in encoded
    assert "/Users/x" not in encoded


# --- answer mapping -----------------------------------------------------------------------


def test_confident_choice_yields_single_analysis_with_structured_row(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"ġr(III)/": 0.92, "ġr(I)/": 0.06, "ġr(II)/": 0.02})
    response = _client(mod, transport).generate_json(_request(_adjudicate_payload()), 5.0)

    assert response.model == "jev-1.13.0"
    assert response.usage.input_tokens == 120
    assert response.request_id == "req-1"
    payload = response.payload
    assert payload["analyses"] == ["ġr(III)/"]
    assert payload["evidence_ids"] == ["op:auto-parsing:1"]
    assert payload["reviewed_rows"] == [
        {"morphological_parsing": "ġr(III)/", "dulat": "ġr (III)", "pos": "n. m. sg. abs. acc.", "gloss": "skin", "comments": ""}
    ]
    assert payload["jev"]["confidence"] == 0.8
    assert "0.92" in payload["summary"]


def test_spread_probabilities_keep_alternatives_in_probability_order(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"ġr(I)/": 0.5, "ġr(III)/": 0.4, "ġr(II)/": 0.1})
    response = _client(mod, transport).generate_json(_request(_adjudicate_payload()), 5.0)
    payload = response.payload
    # Two parallel readings share ġr(I)/ with different POS: both rows survive, one analysis.
    assert payload["analyses"] == ["ġr(I)/", "ġr(III)/"]
    poses = [r["pos"] for r in payload["reviewed_rows"]]
    assert poses[:2] in (["n. m. sg. abs. acc.", "n. m. sg. cstr. gen."], ["n. m. sg. cstr. gen.", "n. m. sg. abs. acc."])
    assert "n. m. sg. abs. acc." in poses and any(r["gloss"] == "skin" for r in payload["reviewed_rows"])
    assert set(payload["evidence_ids"]) >= {"op:auto-parsing:1", "op:auto-parsing:2"}
    assert "op:legacy-review:1" not in payload["evidence_ids"]


def test_ambiguous_noul_lowers_the_alternative_threshold(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    probs = {"ġr(III)/": 0.78, "ġr(I)/": 0.2, "ġr(II)/": 0.02}
    certain = _client(mod, Transport(probabilities_by_analysis=probs, ambiguous=0.1)).generate_json(_request(_adjudicate_payload()), 5.0)
    ambiguous = _client(mod, Transport(probabilities_by_analysis=probs, ambiguous=0.9)).generate_json(_request(_adjudicate_payload()), 5.0)
    assert certain.payload["analyses"] == ["ġr(III)/"]
    assert ambiguous.payload["analyses"] == ["ġr(III)/", "ġr(I)/"]
    assert ambiguous.payload["jev"]["ambiguous"] == 0.9


def test_none_of_these_produces_unresolved_row_with_comment(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"none-of-these": 0.9, "ġr(III)/": 0.1})
    payload = _client(mod, transport).generate_json(_request(_adjudicate_payload()), 5.0).payload
    assert payload["analyses"] == ["?"]
    assert payload["reviewed_rows"][0]["pos"] == "?" and payload["reviewed_rows"][0]["dulat"] == "?"
    assert "hand review" in payload["reviewed_rows"][0]["comments"]
    assert set(payload["evidence_ids"]) == {"op:auto-parsing:1", "op:auto-parsing:2", "op:corpus-parallels:1", "op:corpus-parallels:2", "op:legacy-review:1"}


def test_legacy_only_reading_keeps_pos_and_gloss_unresolved(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"ġr(II)/": 0.95})
    payload = _client(mod, transport).generate_json(_request(_adjudicate_payload()), 5.0).payload
    assert payload["analyses"] == ["ġr(II)/"]
    row = payload["reviewed_rows"][0]
    assert row["pos"] == "?" and row["gloss"] == "?" and row["dulat"] == "?"
    assert "legacy" in row["comments"]
    assert payload["evidence_ids"] == ["op:legacy-review:1"]


def test_revisit_request_enters_the_state(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"ġr(III)/": 1.0})
    revisit = {"request_id": "rv-1", "token_id": "1003", "reason": "inconsistent with 1001", "finding_id": "f-1"}
    _client(mod, transport).generate_json(_request(_adjudicate_payload(revisit_request=revisit)), 5.0)
    assert transport.calls[0][2]["state"]["revisit_request"]["reason"] == "inconsistent with 1001"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["answers"].pop("reading"),
        lambda r: r["answers"]["reading"].__setitem__("choice", "not-an-option"),
        lambda r: r["answers"]["reading"].__setitem__("probabilities", {"x": 2}),
        lambda r: r.pop("model"),
        lambda r: r.pop("usage"),
    ],
    ids=["missing-answer", "foreign-choice", "bad-probabilities", "missing-model", "missing-usage"],
)
def test_malformed_provider_answers_are_permanent_errors(monkeypatch, mutate) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    inner = Transport(probabilities_by_analysis={"ġr(III)/": 1.0})

    def transport(url, headers, body, timeout):
        result = json.loads(json.dumps(inner(url, headers, body, timeout)))
        mutate(result)
        return result

    with pytest.raises(ProviderPermanentError):
        _client(mod, transport).generate_json(_request(_adjudicate_payload()), 5.0)


def test_too_many_candidates_fail_closed(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    evidence = _evidence()
    for n in range(70):
        evidence.append(EvidenceRecord(f"op:corpus-parallels:{n + 10}", "corpus-parallels", f"corpus-parallels:x:{n}", "repository:rev",
                                       json.dumps({"morphological_parsing": f"x{n}/", "dulat": "x", "pos": "n.", "gloss": "g", "attestations": 1, "examples": []})).to_dict())
    with pytest.raises(ProviderPermanentError, match="candidate"):
        _client(mod, Transport(probabilities_by_analysis={})).generate_json(_request(_adjudicate_payload(evidence=evidence)), 5.0)


# --- reconcile -------------------------------------------------------------------------------


def _reconcile_payload() -> dict:
    state = _state()
    decisions = [
        {"decision_id": "d1", "token_id": "1001", "analyses": ["l(I)"], "evidence_ids": ["a:auto-parsing:1"], "summary": "s", "revisit_of": None, "revisit_request_id": None},
        {"decision_id": "d2", "token_id": "1003", "analyses": ["ġr(III)/"], "evidence_ids": ["b:auto-parsing:1"], "summary": "s", "revisit_of": None, "revisit_request_id": None},
    ]
    evidence = [
        EvidenceRecord("a:auto-parsing:1", "auto-parsing", "auto:1001", "p", _row("l(I)", "l (I)", "prep.", "to")).to_dict(),
        EvidenceRecord("b:auto-parsing:1", "auto-parsing", "auto:1003", "p", _row("ġr(III)/", "ġr (III)", "n.", "skin")).to_dict(),
    ]
    return {
        "model_context": {"run_id": "harn029-run"},
        "task": state.task.to_dict(),
        "snapshot": state.snapshot.to_dict(),
        "evidence": evidence,
        "decisions": decisions,
        "skill_context": {},
    }


def test_reconcile_asks_one_noul_per_token_and_requests_revisits_above_threshold(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={}, noul_by_token={"1001": 0.1, "1003": 0.85})
    response = _client(mod, transport).generate_json(_request(_reconcile_payload(), operation="reconcile"), 5.0)
    body = transport.calls[0][2]
    assert len(transport.calls) == 1
    assert sorted(body["questions"]) == ["inconsistent:1001", "inconsistent:1003"]
    assert all(q["type"] == "noul" for q in body["questions"].values())
    findings = response.payload["findings"]
    assert len(findings) == 1
    assert findings[0]["token_ids"] == ["1003"]
    assert findings[0]["scope"] == "column"
    assert findings[0]["requires_revisit"] is True
    assert findings[0]["evidence_ids"] == ["b:auto-parsing:1"]
    assert "0.85" in findings[0]["summary"]


def test_reconcile_records_but_does_not_revisit_between_thresholds(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={}, noul_by_token={"1001": 0.55, "1003": 0.2})
    response = _client(mod, transport).generate_json(_request(_reconcile_payload(), operation="reconcile"), 5.0)
    findings = response.payload["findings"]
    assert [f["token_ids"] for f in findings] == [["1001"]]
    assert findings[0]["requires_revisit"] is False


# --- token estimate ----------------------------------------------------------------------------


def test_count_input_tokens_is_a_conservative_estimate_without_network(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={})
    client = _client(mod, transport)
    small = client.count_input_tokens(_request(_adjudicate_payload()), 5.0)
    big = client.count_input_tokens(_request(_adjudicate_payload(prior_decisions=[{"decision_id": f"d{i}", "token_id": "1001", "analyses": ["l(I)"], "evidence_ids": ["x"], "summary": "s" * 50, "revisit_of": None, "revisit_request_id": None} for i in range(40)])), 5.0)
    assert small > 0
    assert big > small
    assert transport.calls == []


# --- end to end through the HARN-022 runtime -----------------------------------------------------


def _shared() -> SharedBenchmarkAdapters:
    def initialize_skill_context(state, operation_id):
        return {"skill": "review-automatic-parsing"}

    def collect_evidence(state, token, skill_context, operation_id):
        analysis = "l(I)" if token.token_id == "1001" else "ġr(III)/"
        return (
            EvidenceRecord(f"{operation_id}:auto-parsing:1", "auto-parsing", f"auto:{token.token_id}", "auto-parsing:0.2.8",
                           _row(analysis, "x", "n.", "g")),
        )

    def verify_completion(state, gate_id, skill_context, operation_id):
        return CompletionGateResult(gate_id, True, state.decision_revision, (f"gate:{gate_id}",), "passed")

    def evaluate(state, skill_context, operation_id, identity, target):
        return ParsingEvaluationRecord(1, identity, target, state.decision_revision, measure_column_behavior(state), (), (), EfficiencyMetrics(), (f"evaluation:{identity.run_id}",))

    return SharedBenchmarkAdapters(initialize_skill_context, collect_evidence, verify_completion, evaluate)


def test_jev_binding_completes_a_column_through_the_live_runtime(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"l(I)": 1.0, "ġr(III)/": 1.0})
    client = _client(mod, transport)
    spec = BenchmarkBackendSpec("jev", "typesafe", "jev-latest", "jev-1.13.0", SHA0)
    binding = mod.jev_binding(spec, requested_model="jev-latest", exact_model_version="jev-1.13.0", client=client,
                              exact_version_provenance="docs.typesafe.ai/models 2026-09-21")
    assert isinstance(binding, LiveProviderBinding)
    case = BenchmarkCase(1, "harn029-case", _state(), SHA0, SHA0, SHA0,
                         EvaluationTarget("t", GOLD_REF, "gold-provenance", "scorer", "scorer-provenance", SHA0))
    budget = ProviderBudgetPolicy(
        max_generation_requests_per_trial=16, max_generation_requests_per_benchmark=32,
        max_input_tokens_per_trial=20_000, max_input_tokens_per_benchmark=40_000,
        max_output_tokens_per_trial=1_000, max_output_tokens_per_benchmark=2_000,
        max_output_tokens_per_request=64, max_retries_per_request=1, timeout_seconds=10.0,
    )
    result = run_live_benchmark(case, (binding,), shared_adapters=_shared(),
                                execution_policy=ProviderExecutionPolicy(budget=budget, allow_paid_live_execution=True))
    assert result.complete is True
    trial = result.provider_trials[0]
    assert trial.terminal_status == "completed"
    # Two adjudications + one reconciliation request.
    assert len(transport.calls) == 3
    final = result.benchmark.trials[0].final_state
    assert final.completion is not None
    assert final.latest_decision("1003").analyses == ("ġr(III)/",)
    # Redacted artifacts: no state text, no gold.
    artifact = json.dumps(trial.to_dict(), ensure_ascii=False)
    assert GOLD_REF not in artifact and "Haut" not in artifact and "ġr(III)/" not in artifact


def test_model_drift_is_rejected_by_the_runtime(monkeypatch) -> None:
    mod = _jev()
    monkeypatch.setenv(KEY_ENV, "sk-test")
    transport = Transport(probabilities_by_analysis={"l(I)": 1.0, "ġr(III)/": 1.0}, model="jev-1.14.0")
    client = _client(mod, transport)
    spec = BenchmarkBackendSpec("jev", "typesafe", "jev-latest", "jev-1.13.0", SHA0)
    binding = mod.jev_binding(spec, requested_model="jev-latest", exact_model_version="jev-1.13.0", client=client,
                              exact_version_provenance="docs")
    case = BenchmarkCase(1, "harn029-case", _state(), SHA0, SHA0, SHA0,
                         EvaluationTarget("t", GOLD_REF, "gold-provenance", "scorer", "scorer-provenance", SHA0))
    budget = ProviderBudgetPolicy(16, 32, 20_000, 40_000, 1_000, 2_000, 64, 1, 10.0)
    result = run_live_benchmark(case, (binding,), shared_adapters=_shared(),
                                execution_policy=ProviderExecutionPolicy(budget=budget, allow_paid_live_execution=True))
    assert result.complete is False
    assert result.provider_trials[0].terminal_status != "completed"
    assert any(call.error_type == "ProviderModelIdentityError" for call in result.provider_trials[0].calls)
