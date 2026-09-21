"""HARN-029: Jev (TypeSafe System One) as a HARN-022 provider client.

Jev is a typed decision model, not a text generator: it evaluates ``choice``,
``score`` and ``noul`` questions against a ``state`` and returns calibrated
probabilities. It therefore cannot invent a reading. This client gives it a
closed candidate set per token, built from the evidence the HARN-028 collector
supplies (automatic-parsing alternatives, corpus-parallel readings, the legacy
expert review) plus an explicit ``none-of-these`` option, and maps the returned
probability distribution onto preserved alternatives.

The client speaks the HARN-022 ``ProviderJSONClient`` protocol, so exact model
identity, budgets, retries and redacted call artifacts apply unchanged.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from .live_providers import (
    ExecutionKind,
    HttpJSON,
    LiveProviderBinding,
    ProviderJSONRequest,
    ProviderPermanentError,
    ProviderResponse,
    ProviderUsage,
    _default_http_json,
    _EnvironmentCredentialClient,
)
from .model_benchmark import BenchmarkBackendSpec

PROVIDER = "typesafe"
DEFAULT_BASE_URL = "https://api.typesafe.ai/v1"
NONE_OF_THESE = "none-of-these"
UNRESOLVED = "?"

_CANDIDATE_SOURCES = ("auto-parsing", "corpus-parallels", "legacy-review")
_ROW_FIELDS = ("morphological_parsing", "dulat", "pos", "gloss", "comments")
# Keys of the skill context that may reach the model; evaluation or path-like data never does.
_SKILL_CONTEXT_KEYS = ("scope", "worklist", "evidence")
_SKILL_EVIDENCE_KEYS = ("enabled_sources", "available_sources", "absent_sources")

_READING_INSTRUCTIONS = (
    "You are reviewing one token of a Ugaritic tablet column, in order, as a "
    "morphological parser. The state holds the whole column, the token under "
    "review, the evidence gathered for it and the decisions already taken for "
    "earlier tokens. Each option is one complete candidate reading: morphological "
    "parsing, DULAT lexeme, part of speech and gloss. Pick the reading that the "
    "line, the clause and the evidence support best. Prefer readings attested "
    "for this surface elsewhere in the corpus only when the context agrees. "
    "Choose none-of-these only when no listed reading is defensible."
)
_AMBIGUOUS_INSTRUCTIONS = (
    "More than one of the candidate readings offered for this token is "
    "defensible in this context, so the curated data should keep several "
    "alternatives rather than one."
)
_INCONSISTENT_INSTRUCTIONS = (
    "The reading chosen for this token contradicts the readings chosen for the "
    "other occurrences of the same surface form in this column, and the "
    "contradiction is not explained by the context of the lines involved."
)


def _positive_fraction(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field} must be a number between 0 and 1")
    return float(value)


@dataclass(frozen=True)
class JevDecisionPolicy:
    """How a probability distribution becomes preserved alternatives."""

    alternative_threshold: float = 0.25
    ambiguous_threshold: float = 0.15
    ambiguous_noul_cutoff: float = 0.5
    revisit_threshold: float = 0.7
    finding_threshold: float = 0.5
    max_candidates: int = 64

    def __post_init__(self) -> None:
        for field in (
            "alternative_threshold",
            "ambiguous_threshold",
            "ambiguous_noul_cutoff",
            "revisit_threshold",
            "finding_threshold",
        ):
            object.__setattr__(self, field, _positive_fraction(getattr(self, field), field))
        if self.ambiguous_threshold > self.alternative_threshold:
            raise ValueError("ambiguous_threshold must not exceed alternative_threshold")
        if self.finding_threshold > self.revisit_threshold:
            raise ValueError("finding_threshold must not exceed revisit_threshold")
        if isinstance(self.max_candidates, bool) or not isinstance(self.max_candidates, int) or not 1 <= self.max_candidates <= 254:
            raise ValueError("max_candidates must be between 1 and 254 (Jev allows 255 options)")

    def to_dict(self) -> dict[str, object]:
        return {
            "alternative_threshold": self.alternative_threshold,
            "ambiguous_threshold": self.ambiguous_threshold,
            "ambiguous_noul_cutoff": self.ambiguous_noul_cutoff,
            "revisit_threshold": self.revisit_threshold,
            "finding_threshold": self.finding_threshold,
            "max_candidates": self.max_candidates,
        }


@dataclass(frozen=True)
class Candidate:
    key: str
    row: dict[str, str]
    evidence_ids: tuple[str, ...]
    sources: tuple[str, ...]
    attestations: int = 0

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return (self.row["morphological_parsing"], self.row["dulat"], self.row["pos"], self.row["gloss"])


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_summary(summary: object) -> Mapping[str, Any] | None:
    if not isinstance(summary, str):
        return None
    try:
        decoded = json.loads(summary)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, Mapping) else None


def extract_candidates(
    evidence: list[Mapping[str, Any]],
    *,
    max_candidates: int,
) -> tuple[Candidate, ...]:
    """Closed candidate set from candidate-bearing evidence, deduplicated per curated row."""

    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str, str]] = []

    def add(row: dict[str, str], evidence_id: str, source: str, attestations: int) -> None:
        key = (row["morphological_parsing"], row["dulat"], row["pos"], row["gloss"])
        if key not in merged:
            merged[key] = {"row": row, "evidence_ids": [], "sources": [], "attestations": 0}
            order.append(key)
        entry = merged[key]
        if evidence_id not in entry["evidence_ids"]:
            entry["evidence_ids"].append(evidence_id)
        if source not in entry["sources"]:
            entry["sources"].append(source)
        entry["attestations"] += attestations

    for record in evidence:
        source = _text(record.get("source_id"))
        evidence_id = _text(record.get("evidence_id"))
        if source not in _CANDIDATE_SOURCES or not evidence_id:
            continue
        data = _parse_summary(record.get("summary"))
        if data is None:
            continue
        if source == "legacy-review":
            for analysis in data.get("analyses") or ():
                analysis = _text(analysis)
                if analysis:
                    add(
                        {
                            "morphological_parsing": analysis,
                            "dulat": UNRESOLVED,
                            "pos": UNRESOLVED,
                            "gloss": UNRESOLVED,
                            "comments": "analysis from the legacy expert review; DULAT/POS/gloss not resolved",
                        },
                        evidence_id,
                        source,
                        0,
                    )
            continue
        analysis = _text(data.get("morphological_parsing"))
        if not analysis:
            continue
        row = {
            "morphological_parsing": analysis,
            "dulat": _text(data.get("dulat")),
            "pos": _text(data.get("pos")),
            "gloss": _text(data.get("gloss")),
            "comments": "",
        }
        attestations = data.get("attestations")
        add(row, evidence_id, source, attestations if isinstance(attestations, int) and not isinstance(attestations, bool) else 0)

    if len(order) > max_candidates:
        raise ProviderPermanentError(
            f"too many candidate readings for one token ({len(order)} > {max_candidates})"
        )
    candidates = tuple(
        Candidate(
            f"reading-{index}",
            dict(merged[key]["row"]),
            tuple(merged[key]["evidence_ids"]),
            tuple(merged[key]["sources"]),
            merged[key]["attestations"],
        )
        for index, key in enumerate(order, start=1)
    )
    return candidates


def _option_description(candidate: Candidate) -> dict[str, object]:
    description: dict[str, object] = {
        "morphological_parsing": candidate.row["morphological_parsing"],
        "dulat": candidate.row["dulat"],
        "pos": candidate.row["pos"],
        "gloss": candidate.row["gloss"],
        "from": list(candidate.sources),
    }
    if candidate.attestations:
        description["reviewed_attestations_elsewhere"] = candidate.attestations
    if candidate.row["comments"]:
        description["note"] = candidate.row["comments"]
    return description


def _safe_skill_context(skill_context: object) -> dict[str, object]:
    if not isinstance(skill_context, Mapping):
        return {}
    safe: dict[str, object] = {}
    for key in _SKILL_CONTEXT_KEYS:
        value = skill_context.get(key)
        if not isinstance(value, Mapping):
            continue
        if key == "evidence":
            safe[key] = {k: value[k] for k in _SKILL_EVIDENCE_KEYS if k in value}
        else:
            safe[key] = dict(value)
    return safe


def _column_view(snapshot: Mapping[str, Any]) -> dict[str, object]:
    tokens = snapshot.get("tokens") if isinstance(snapshot, Mapping) else None
    view = []
    for token in tokens or ():
        if isinstance(token, Mapping):
            view.append(
                {
                    "token_id": token.get("token_id"),
                    "line": token.get("line_ref"),
                    "surface": token.get("surface"),
                }
            )
    return {"tokens": view}


def _prior_decisions_view(decisions: object) -> list[dict[str, object]]:
    view = []
    for decision in decisions if isinstance(decisions, list) else ():
        if isinstance(decision, Mapping):
            view.append(
                {
                    "token_id": decision.get("token_id"),
                    "analyses": list(decision.get("analyses") or ()),
                }
            )
    return view


class TypeSafeJevClient(_EnvironmentCredentialClient):
    provider = PROVIDER
    execution_kind = ExecutionKind.LIVE_PROVIDER
    # Jev has no token-count endpoint; the preflight is a byte-based estimate and the
    # HARN-022 ledger reconciles it against the usage the response reports.
    input_token_count_kind = "estimate"

    def __init__(
        self,
        *,
        api_key_env: str = "TYPESAFE_API_KEY",
        http_json: HttpJSON = _default_http_json,
        base_url: str = DEFAULT_BASE_URL,
        decision_policy: JevDecisionPolicy | None = None,
        bytes_per_token: float = 3.0,
    ) -> None:
        super().__init__(api_key_env=api_key_env, http_json=http_json, base_url=base_url)
        self.decision_policy = decision_policy or JevDecisionPolicy()
        if not isinstance(self.decision_policy, JevDecisionPolicy):
            raise ValueError("decision_policy must be JevDecisionPolicy")
        if isinstance(bytes_per_token, bool) or not isinstance(bytes_per_token, (int, float)) or bytes_per_token <= 0:
            raise ValueError("bytes_per_token must be positive")
        self._bytes_per_token = float(bytes_per_token)

    # --- request construction ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }

    def _adjudicate_body(self, request: ProviderJSONRequest) -> tuple[dict[str, Any], tuple[Candidate, ...]]:
        payload = request.payload
        evidence = payload.get("evidence")
        if not isinstance(evidence, list):
            raise ProviderPermanentError("adjudicate payload lacks evidence")
        candidates = extract_candidates(evidence, max_candidates=self.decision_policy.max_candidates)
        criteria: dict[str, object] = {c.key: _option_description(c) for c in candidates}
        criteria[NONE_OF_THESE] = {
            "what": "No listed reading is defensible for this token in this context.",
            "consequence": "The token stays unresolved and is escalated to hand review.",
        }
        context_evidence = [
            {"source": _text(r.get("source_id")), "ref": _text(r.get("source_ref")), "content": r.get("summary")}
            for r in evidence
            if isinstance(r, Mapping) and _text(r.get("source_id")) not in _CANDIDATE_SOURCES
        ]
        task = payload.get("task") if isinstance(payload.get("task"), Mapping) else {}
        state = {
            "tablet": task.get("tablet"),
            "column_name": task.get("column"),
            "column": _column_view(payload.get("snapshot") or {}),
            "token": payload.get("token"),
            "evidence": context_evidence,
            "prior_decisions": _prior_decisions_view(payload.get("prior_decisions")),
            "review_context": _safe_skill_context(payload.get("skill_context")),
            "revisit_request": payload.get("revisit_request"),
        }
        body = {
            "model": request.requested_model,
            "state": state,
            "questions": {
                "reading": {"type": "choice", "instructions": _READING_INSTRUCTIONS, "criteria": criteria},
                "ambiguous": {"type": "noul", "instructions": _AMBIGUOUS_INSTRUCTIONS},
            },
        }
        return body, candidates

    def _reconcile_body(self, request: ProviderJSONRequest) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        payload = request.payload
        decisions = payload.get("decisions")
        if not isinstance(decisions, list):
            raise ProviderPermanentError("reconcile payload lacks decisions")
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
        tokens = {
            _text(t.get("token_id")): t
            for t in (snapshot.get("tokens") or ())
            if isinstance(t, Mapping)
        }
        latest: dict[str, dict[str, Any]] = {}
        for decision in decisions:
            if isinstance(decision, Mapping):
                token_id = _text(decision.get("token_id"))
                if token_id in tokens:
                    latest[token_id] = dict(decision)
        task = payload.get("task") if isinstance(payload.get("task"), Mapping) else {}
        state = {
            "tablet": task.get("tablet"),
            "column_name": task.get("column"),
            "column": _column_view(snapshot),
            "decisions": [
                {
                    "token_id": token_id,
                    "surface": tokens[token_id].get("surface"),
                    "line": tokens[token_id].get("line_ref"),
                    "analyses": list(decision.get("analyses") or ()),
                }
                for token_id, decision in latest.items()
            ],
        }
        questions = {
            f"inconsistent:{token_id}": {
                "type": "noul",
                "instructions": {
                    "question": _INCONSISTENT_INSTRUCTIONS,
                    "token_id": token_id,
                    "surface": tokens[token_id].get("surface"),
                },
            }
            for token_id in latest
        }
        body = {"model": request.requested_model, "state": state, "questions": questions}
        return body, latest

    def _body(self, request: ProviderJSONRequest) -> dict[str, Any]:
        if request.operation == "adjudicate":
            return self._adjudicate_body(request)[0]
        return self._reconcile_body(request)[0]

    # --- protocol ------------------------------------------------------------------------

    def count_input_tokens(self, request: ProviderJSONRequest, timeout_seconds: float) -> int:
        """Conservative estimate: Jev has no count endpoint; the response carries real usage."""

        encoded = json.dumps(self._body(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return max(1, math.ceil(len(encoded) / self._bytes_per_token))

    def generate_json(self, request: ProviderJSONRequest, timeout_seconds: float) -> ProviderResponse:
        if request.operation == "adjudicate":
            body, candidates = self._adjudicate_body(request)
            result = self._http_json(f"{self._base_url}/systemone", self._headers(), body, timeout_seconds)
            payload = self._adjudicate_payload(result, candidates)
        else:
            body, latest = self._reconcile_body(request)
            result = self._http_json(f"{self._base_url}/systemone", self._headers(), body, timeout_seconds)
            payload = self._reconcile_payload(result, latest)
        return ProviderResponse(
            model=_required(result.get("model"), "model"),
            payload=payload,
            usage=_usage(result.get("usage")),
            request_id=_optional_text(result.get("id")),
        )

    # --- answer mapping ------------------------------------------------------------------

    def _adjudicate_payload(self, result: Mapping[str, Any], candidates: tuple[Candidate, ...]) -> dict[str, Any]:
        answers = result.get("answers")
        if not isinstance(answers, Mapping):
            raise ProviderPermanentError("Jev response has no answers")
        reading = answers.get("reading")
        if not isinstance(reading, Mapping):
            raise ProviderPermanentError("Jev response lacks the reading answer")
        by_key = {c.key: c for c in candidates}
        valid_keys = set(by_key) | {NONE_OF_THESE}
        choice = _text(reading.get("choice"))
        if choice not in valid_keys:
            raise ProviderPermanentError("Jev chose an option outside the supplied candidates")
        raw_probabilities = reading.get("probabilities")
        if not isinstance(raw_probabilities, Mapping):
            raise ProviderPermanentError("Jev reading answer lacks probabilities")
        probabilities: dict[str, float] = {}
        for key, value in raw_probabilities.items():
            if key not in valid_keys:
                raise ProviderPermanentError("Jev probabilities reference an unknown option")
            try:
                probabilities[key] = _positive_fraction(value, "probability")
            except ValueError as exc:
                raise ProviderPermanentError(str(exc)) from None
        if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.05):
            raise ProviderPermanentError("Jev probabilities do not sum to one")
        confidence = reading.get("confidence")
        try:
            confidence_value = _positive_fraction(confidence, "confidence") if confidence is not None else None
        except ValueError as exc:
            raise ProviderPermanentError(str(exc)) from None
        ambiguous_answer = answers.get("ambiguous")
        ambiguous = None
        if isinstance(ambiguous_answer, Mapping) and ambiguous_answer.get("noul") is not None:
            try:
                ambiguous = _positive_fraction(ambiguous_answer.get("noul"), "ambiguous")
            except ValueError as exc:
                raise ProviderPermanentError(str(exc)) from None

        policy = self.decision_policy
        threshold = policy.alternative_threshold
        if ambiguous is not None and ambiguous >= policy.ambiguous_noul_cutoff:
            threshold = policy.ambiguous_threshold

        if choice == NONE_OF_THESE:
            all_evidence = tuple(dict.fromkeys(eid for c in candidates for eid in c.evidence_ids))
            if not all_evidence:
                raise ProviderPermanentError("no candidate evidence available for an unresolved decision")
            rows = [
                {
                    "morphological_parsing": UNRESOLVED,
                    "dulat": UNRESOLVED,
                    "pos": UNRESOLVED,
                    "gloss": UNRESOLVED,
                    "comments": "no candidate reading accepted by Jev; needs hand review",
                }
            ]
            summary = (
                f"Jev chose none-of-these (p={probabilities.get(NONE_OF_THESE, 0.0):.2f}, "
                f"confidence={confidence_value}); token left unresolved for hand review"
            )
            return {
                "analyses": [UNRESOLVED],
                "evidence_ids": list(all_evidence),
                "summary": summary,
                "reviewed_rows": rows,
                "jev": self._jev_block(choice, probabilities, confidence_value, ambiguous, threshold),
            }

        kept = [choice] + sorted(
            (k for k, p in probabilities.items() if k != choice and k != NONE_OF_THESE and p >= threshold),
            key=lambda k: (-probabilities[k], k),
        )
        rows: list[dict[str, str]] = []
        analyses: list[str] = []
        evidence_ids: list[str] = []
        for key in kept:
            candidate = by_key[key]
            rows.append(dict(candidate.row))
            if candidate.row["morphological_parsing"] not in analyses:
                analyses.append(candidate.row["morphological_parsing"])
            for eid in candidate.evidence_ids:
                if eid not in evidence_ids:
                    evidence_ids.append(eid)
        summary = (
            f"Jev chose {by_key[choice].row['morphological_parsing']} "
            f"(p={probabilities.get(choice, 0.0):.2f}, confidence={confidence_value}); "
            f"kept {len(kept)} alternative(s) at threshold {threshold:.2f}; "
            f"ambiguous={ambiguous}"
        )
        return {
            "analyses": analyses,
            "evidence_ids": evidence_ids,
            "summary": summary,
            "reviewed_rows": rows,
            "jev": self._jev_block(choice, probabilities, confidence_value, ambiguous, threshold),
        }

    @staticmethod
    def _jev_block(choice, probabilities, confidence, ambiguous, threshold) -> dict[str, object]:
        return {
            "choice": choice,
            "probabilities": dict(sorted(probabilities.items())),
            "confidence": confidence,
            "ambiguous": ambiguous,
            "alternative_threshold": threshold,
        }

    def _reconcile_payload(self, result: Mapping[str, Any], latest: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        answers = result.get("answers")
        if not isinstance(answers, Mapping):
            raise ProviderPermanentError("Jev response has no answers")
        policy = self.decision_policy
        findings: list[dict[str, object]] = []
        for token_id, decision in latest.items():
            answer = answers.get(f"inconsistent:{token_id}")
            if not isinstance(answer, Mapping):
                raise ProviderPermanentError("Jev reconcile response lacks an answer for a token")
            try:
                probability = _positive_fraction(answer.get("noul"), "noul")
            except ValueError as exc:
                raise ProviderPermanentError(str(exc)) from None
            if probability < policy.finding_threshold:
                continue
            evidence_ids = [e for e in decision.get("evidence_ids") or () if isinstance(e, str) and e]
            if not evidence_ids:
                continue
            findings.append(
                {
                    "scope": "column",
                    "token_ids": [token_id],
                    "evidence_ids": evidence_ids,
                    "summary": (
                        f"Jev judges the chosen reading inconsistent with other occurrences of the "
                        f"surface in this column (p={probability:.2f})"
                    ),
                    "requires_revisit": probability >= policy.revisit_threshold,
                }
            )
        return {"findings": findings}


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProviderPermanentError(f"Jev response lacks {field}")
    return value.strip()


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _usage(value: object) -> ProviderUsage:
    if not isinstance(value, Mapping):
        raise ProviderPermanentError("Jev response lacks usage")
    try:
        return ProviderUsage(int(value.get("input_tokens", 0)), int(value.get("output_tokens", 0)))
    except (TypeError, ValueError) as exc:
        raise ProviderPermanentError("Jev usage is malformed") from exc


def jev_binding(
    spec: BenchmarkBackendSpec,
    *,
    requested_model: str,
    exact_model_version: str,
    client: TypeSafeJevClient,
    exact_version_provenance: str,
) -> LiveProviderBinding:
    """Bind a Jev client as a HARN-016/022 backend arm."""

    if not isinstance(client, TypeSafeJevClient):
        raise ValueError("client must be TypeSafeJevClient")
    return LiveProviderBinding(
        spec=spec,
        requested_model=requested_model,
        exact_model_version=exact_model_version,
        exact_version_provenance=exact_version_provenance,
        client=client,
        execution_kind=ExecutionKind.LIVE_PROVIDER,
    )
