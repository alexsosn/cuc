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
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .live_providers import (
    ExecutionKind,
    HttpJSON,
    LiveProviderBinding,
    ProviderIOCapture,
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
_STAGES = ("full", "lexical")
_ROW_FIELDS = ("morphological_parsing", "dulat", "pos", "gloss", "comments")
# Keys of the skill context that may reach the model; evaluation or path-like data never does.
_SKILL_CONTEXT_KEYS: Mapping[str, tuple[str, ...]] = {
    "scope": ("tablet", "column", "token_count", "every_token_in_order", "worklists_are_attention_only"),
    "worklist": ("priority_token_ids",),
    "evidence": ("enabled_sources", "available_sources", "absent_sources"),
}

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
_LEXEME_INSTRUCTIONS = (
    "You are linking one token of a Ugaritic tablet line to its lexeme. The state "
    "holds the line (with the neighbouring lines for context), the token under "
    "review, the dictionary citations and translations that concern it, and the "
    "lexemes already chosen for the other words of the line. Each option is one "
    "lexeme with its part of speech, identified by its DULAT lemma, its glosses and "
    "the parser's forms, which show how the written surface maps onto the lemma "
    "(prefixes, suffixes and endings are marked with /, +, ~, [ and similar signs). "
    "The DULAT lemma writes aleph vowels as ả, ỉ, ủ where the tablet surface writes "
    "plain a, i, u; that difference is not a mismatch. Pick the lexeme the line "
    "and the evidence support best. Choose none-of-these only when no listed "
    "lexeme is defensible."
)
_LEXEME_FITS_INSTRUCTIONS = (
    "The candidate lexeme is the right lexical link for this token in this line: "
    "the surface maps onto the lemma through the given form, and the meaning and "
    "part of speech suit the line and the evidence. The DULAT lemma writes aleph "
    "vowels as ả, ỉ, ủ where the surface writes a, i, u; that is not a mismatch."
)
_LEXICAL_AMBIGUOUS_INSTRUCTIONS = (
    "More than one of the listed lexemes is defensible for this token in this "
    "line, so the curated data should keep several readings rather than one."
)
_LEXICAL_INCONSISTENT_INSTRUCTIONS = (
    "The lexeme chosen for this token contradicts the lexemes chosen for the "
    "other occurrences of the same surface form in this column, and the "
    "contradiction is not explained by the context of the lines involved. "
    "Differences of case, number, state or gloss wording are not contradictions."
)
_INCONSISTENT_INSTRUCTIONS = (
    "The reading chosen for this token contradicts the readings chosen for the "
    "other occurrences of the same surface form in this column, and the "
    "contradiction is not explained by the context of the lines involved."
)


_QUALIFIER_RE = re.compile(r"\s*\([^()]*\)\s*$")


def _match_key(value: object) -> str:
    """Comparison key for a lemma, a headword or a surface.

    Drops trailing qualifiers (``(I)``, ``(DN)``), the DULAT aleph diacritics
    (``ả``→``a``) and the ayin spelling variants, so that the parser's ``bʕl (II)``
    meets Burns' ``bʿl (DN)`` and the DULAT headword ``aps`` meets ``ảps``.
    """

    from .evidence_adapters import _normalize_surface

    text = _text(value)
    while True:
        stripped = _QUALIFIER_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return _normalize_surface(text.rstrip("†*!").strip())


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
    reconcile_batch_size: int = 150
    # HARN-033: "full" asks for complete curated rows; "lexical" asks only for the
    # lexeme + POS class over trimmed inputs and inherits the parser's morphology.
    stage: str = "full"
    # Abstention needs both a majority for none-of-these and this much confidence;
    # below it the answer is "no opinion" and the best reading is kept, flagged.
    abstention_confidence: float = 0.5
    # A single candidate is accepted as the parser gave it (right ~98% of the time on
    # KTU 1.6 I); with verify_single_candidate a yes/no question is asked and recorded
    # as a review-priority signal only.
    verify_single_candidate: bool = False
    # In the lexical stage alternatives are kept only when the ambiguity question
    # says the token is ambiguous; otherwise the top lexeme alone.
    lexical_alternatives_need_ambiguity: bool = True

    def __post_init__(self) -> None:
        if self.stage not in _STAGES:
            raise ValueError(f"stage must be one of {_STAGES}")
        for field in (
            "alternative_threshold",
            "ambiguous_threshold",
            "ambiguous_noul_cutoff",
            "revisit_threshold",
            "finding_threshold",
            "abstention_confidence",
        ):
            object.__setattr__(self, field, _positive_fraction(getattr(self, field), field))
        if not isinstance(self.verify_single_candidate, bool) or not isinstance(self.lexical_alternatives_need_ambiguity, bool):
            raise ValueError("verify_single_candidate and lexical_alternatives_need_ambiguity must be booleans")
        if self.ambiguous_threshold > self.alternative_threshold:
            raise ValueError("ambiguous_threshold must not exceed alternative_threshold")
        if self.finding_threshold > self.revisit_threshold:
            raise ValueError("finding_threshold must not exceed revisit_threshold")
        if isinstance(self.max_candidates, bool) or not isinstance(self.max_candidates, int) or not 1 <= self.max_candidates <= 254:
            raise ValueError("max_candidates must be between 1 and 254 (Jev allows 255 options)")
        if isinstance(self.reconcile_batch_size, bool) or not isinstance(self.reconcile_batch_size, int) or self.reconcile_batch_size < 1:
            raise ValueError("reconcile_batch_size must be a positive integer")

    def to_dict(self) -> dict[str, object]:
        return {
            "alternative_threshold": self.alternative_threshold,
            "ambiguous_threshold": self.ambiguous_threshold,
            "ambiguous_noul_cutoff": self.ambiguous_noul_cutoff,
            "revisit_threshold": self.revisit_threshold,
            "finding_threshold": self.finding_threshold,
            "max_candidates": self.max_candidates,
            "reconcile_batch_size": self.reconcile_batch_size,
            "stage": self.stage,
            "abstention_confidence": self.abstention_confidence,
            "verify_single_candidate": self.verify_single_candidate,
            "lexical_alternatives_need_ambiguity": self.lexical_alternatives_need_ambiguity,
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


_SEED_MARKER = "SEEDED from auto-parse"


def _curated_field_ok(value: str) -> bool:
    """A candidate field must be representable as a HARN-027 curated TSV field."""

    if "\t" in value or (value and value.splitlines() != [value]):
        return False
    return _SEED_MARKER not in value


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
            analyses = data.get("analyses")
            if not isinstance(analyses, list):
                continue
            for analysis in analyses:
                analysis = _text(analysis)
                if analysis and _curated_field_ok(analysis):
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
        raw_row = {
            "morphological_parsing": _text(data.get("morphological_parsing")),
            "dulat": _text(data.get("dulat")),
            "pos": _text(data.get("pos")),
            "gloss": _text(data.get("gloss")),
        }
        if not any(raw_row.values()):
            continue
        # A curated row has no empty field: what the source left blank is unresolved.
        row = {key: value or UNRESOLVED for key, value in raw_row.items()}
        row["comments"] = ""
        if not all(_curated_field_ok(value) for value in row.values()):
            # Control characters or workflow markers can never become curated rows.
            continue
        attestations = data.get("attestations")
        if isinstance(attestations, bool) or not isinstance(attestations, int) or attestations < 0:
            attestations = 0
        add(row, evidence_id, source, attestations)

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


def _context_value_ok(value: object) -> bool:
    """Whitelisted context values are scalars or lists of ids; never path-like text."""

    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return "/" not in value and "\\" not in value
    if isinstance(value, list):
        return all(isinstance(item, str) and "/" not in item and "\\" not in item for item in value)
    return False


def _safe_skill_context(skill_context: object) -> dict[str, object]:
    """Whitelist the review context per key and value shape; nothing path-like can pass."""

    if not isinstance(skill_context, Mapping):
        return {}
    safe: dict[str, object] = {}
    for key, allowed in _SKILL_CONTEXT_KEYS.items():
        value = skill_context.get(key)
        if not isinstance(value, Mapping):
            continue
        picked = {}
        for name in allowed:
            if name not in value:
                continue
            item = value[name]
            if isinstance(item, list):
                item = [x for x in item if isinstance(x, str) and _context_value_ok(x)]
            if _context_value_ok(item):
                picked[name] = item
        if picked:
            safe[key] = picked
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
        bytes_per_token: float = 1.5,
        capture: ProviderIOCapture | None = None,
    ) -> None:
        super().__init__(api_key_env=api_key_env, http_json=http_json, base_url=base_url, capture=capture)
        self.decision_policy = decision_policy or JevDecisionPolicy()
        if not isinstance(self.decision_policy, JevDecisionPolicy):
            raise ValueError("decision_policy must be JevDecisionPolicy")
        if isinstance(bytes_per_token, bool) or not isinstance(bytes_per_token, (int, float)) or bytes_per_token <= 0:
            raise ValueError("bytes_per_token must be positive")
        self._bytes_per_token = float(bytes_per_token)
        # Set by jev_binding: the exact model version to report on zero-call responses.
        self.declared_exact_model: str | None = None

    # --- request construction ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }

    def _adjudicate_body(self, request: ProviderJSONRequest) -> tuple[dict[str, Any], tuple[Any, ...]]:
        if self.decision_policy.stage == "lexical":
            return self._lexical_body(request)
        payload = request.payload
        evidence = payload.get("evidence")
        if not isinstance(evidence, list):
            raise ProviderPermanentError("adjudicate payload lacks evidence")
        candidates = extract_candidates(evidence, max_candidates=self.decision_policy.max_candidates)
        if not candidates:
            raise ProviderPermanentError(
                "no candidate readings for this token; the closed-choice backend needs at least one"
            )
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

    def _lexical_body(self, request: ProviderJSONRequest) -> tuple[dict[str, Any], tuple[Any, ...]]:
        """Stage 1 request: the line window, matching evidence, one option per lexeme."""

        from .lexical_stage import group_lexical_candidates, lemma_key, pos_class

        payload = request.payload
        evidence = payload.get("evidence")
        if not isinstance(evidence, list):
            raise ProviderPermanentError("adjudicate payload lacks evidence")
        candidates = group_lexical_candidates(evidence, max_candidates=self.decision_policy.max_candidates)
        if not candidates:
            # Nothing to choose from (the parser left the token unresolved): stage 1 leaves
            # it unresolved without a call. generate_json handles this before the network.
            return {"model": request.requested_model, "state": {}, "questions": {}}, ()
        lemma_keys = {_match_key(c.reading.lemma) for c in candidates if c.reading.lemma != UNRESOLVED}
        token = payload.get("token") if isinstance(payload.get("token"), Mapping) else {}
        surface = _text(token.get("surface"))
        surface_key = _match_key(surface)
        line_ref = _text(token.get("line_ref"))

        # Line window from the snapshot: tokens of this line, one line before and after.
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), Mapping) else {}
        tokens = [t for t in (snapshot.get("tokens") or ()) if isinstance(t, Mapping)]
        lines: list[str] = []
        for t in tokens:
            ref = _text(t.get("line_ref"))
            if ref and ref not in lines:
                lines.append(ref)
        index = lines.index(line_ref) if line_ref in lines else -1
        def line_text(ref: str | None) -> dict[str, str] | None:
            if ref is None:
                return None
            return {"ref": ref, "text": " ".join(_text(t.get("surface")) for t in tokens if _text(t.get("line_ref")) == ref)}
        line_tokens = [
            {"token_id": t.get("token_id"), "surface": t.get("surface")}
            for t in tokens
            if _text(t.get("line_ref")) == line_ref
        ]
        context_lines = {
            "before": line_text(lines[index - 1]) if index > 0 else None,
            "after": line_text(lines[index + 1]) if 0 <= index < len(lines) - 1 else None,
        }

        # Same-line lexical decisions as context; nothing else from the history.
        line_ids = {_text(t.get("token_id")) for t in line_tokens}
        surfaces = {_text(t.get("token_id")): _text(t.get("surface")) for t in tokens}
        line_decisions = []
        for decision in payload.get("prior_decisions") or ():
            if not isinstance(decision, Mapping):
                continue
            tid = _text(decision.get("token_id"))
            rows = decision.get("reviewed_rows") or ()
            if tid in line_ids and rows and isinstance(rows[0], Mapping):
                line_decisions.append(
                    {
                        "surface": surfaces.get(tid, ""),
                        "lemma": lemma_key(rows[0].get("dulat")),
                        "pos": pos_class(rows[0].get("pos")),
                    }
                )

        # Evidence: DULAT citations whose entry headword is a candidate lemma, EUPT for
        # the line, Burns rows whose headword is the surface, and the legacy expert
        # analyses of the surface (context only; they are never an option). Tropper
        # (grammar pages) and citations for other words are dropped.
        context_evidence: list[dict[str, object]] = []
        other_dulat = 0
        for r in evidence:
            if not isinstance(r, Mapping):
                continue
            source = _text(r.get("source_id"))
            summary = r.get("summary")
            if source == "dulat":
                data = _parse_summary(summary) or {}
                headword = _match_key(data.get("headword"))
                if headword:
                    matched = headword in lemma_keys
                else:
                    # Older summaries carry only the cited phrase; match it as a whole
                    # or by its first word so a single-word citation still gets through.
                    label = _match_key(data.get("label"))
                    matched = bool(label) and (label in lemma_keys or label.split(" ")[0] in lemma_keys)
                if matched:
                    context_evidence.append({"source": source, "ref": _text(r.get("source_ref")), "content": summary})
                else:
                    other_dulat += 1
            elif source == "eupt":
                context_evidence.append({"source": source, "ref": _text(r.get("source_ref")), "content": summary})
            elif source == "burns-cultic-vocabulary":
                data = _parse_summary(summary) or {}
                headword = _match_key(data.get("headword"))
                if headword and surface_key and (headword == surface_key or headword.split(" ")[0] == surface_key):
                    context_evidence.append({"source": source, "ref": _text(r.get("source_ref")), "content": summary})
            elif source == "legacy-review":
                data = _parse_summary(summary) or {}
                analyses = [_text(a) for a in (data.get("analyses") or ()) if _text(a)]
                if analyses:
                    context_evidence.append({
                        "source": source,
                        "ref": _text(r.get("source_ref")),
                        "content": "analyses of this surface from the legacy expert review (no DULAT link, no POS): "
                                   + ", ".join(analyses),
                    })

        task = payload.get("task") if isinstance(payload.get("task"), Mapping) else {}
        state = {
            "tablet": task.get("tablet"),
            "column_name": task.get("column"),
            "line": {"ref": line_ref, "tokens": line_tokens},
            "context_lines": context_lines,
            "token": {"token_id": token.get("token_id"), "surface": surface},
            "line_decisions": line_decisions,
            "evidence": context_evidence,
            "dulat_citations_for_other_words_on_line": other_dulat,
            "revisit_request": payload.get("revisit_request"),
        }
        if len(candidates) == 1 and candidates[0].parser_row_count:
            # A choice between one lexeme and "none" is miscalibrated, and even a yes/no
            # veto proved noise against the parser's 98% on unambiguous tokens. The
            # parser's single candidate is accepted; optionally a fit question is asked
            # and kept as a review-priority signal. A single candidate that the parser
            # did not offer (parallels only) has no such base rate and is asked below.
            state["candidate"] = candidates[0].option()
            if not self.decision_policy.verify_single_candidate:
                return {"model": request.requested_model, "state": state, "questions": {}}, candidates
            questions = {"lexeme_fits": {"type": "noul", "instructions": _LEXEME_FITS_INSTRUCTIONS}}
        else:
            criteria: dict[str, object] = {c.key: c.option() for c in candidates}
            criteria[NONE_OF_THESE] = {
                "what": "No listed lexeme is defensible for this token in this line.",
                "consequence": "The token stays unresolved and is escalated to hand review.",
            }
            questions = {
                "lexeme": {"type": "choice", "instructions": _LEXEME_INSTRUCTIONS, "criteria": criteria},
                "ambiguous": {"type": "noul", "instructions": _LEXICAL_AMBIGUOUS_INSTRUCTIONS},
            }
        body = {"model": request.requested_model, "state": state, "questions": questions}
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
        lexical = self.decision_policy.stage == "lexical"

        def view(token_id: str, decision: Mapping[str, Any]) -> dict[str, Any]:
            item: dict[str, Any] = {
                "token_id": token_id,
                "surface": tokens[token_id].get("surface"),
                "line": tokens[token_id].get("line_ref"),
            }
            if lexical:
                # Stage 1 decided the lexeme; the morphology strings would invite
                # findings about case or number, which this stage does not decide.
                from .lexical_stage import lemma_key, pos_class

                rows = decision.get("reviewed_rows") or ()
                first = rows[0] if rows and isinstance(rows[0], Mapping) else {}
                item["reading"] = {"lemma": lemma_key(first.get("dulat")), "pos": pos_class(first.get("pos"))}
            else:
                item["analyses"] = list(decision.get("analyses") or ())
            return item

        state = {
            "tablet": task.get("tablet"),
            "column_name": task.get("column"),
            "column": _column_view(snapshot),
            "decisions": [view(token_id, decision) for token_id, decision in latest.items()],
        }
        questions = {
            f"inconsistent:{token_id}": {
                "type": "noul",
                "instructions": {
                    "question": _LEXICAL_INCONSISTENT_INSTRUCTIONS if lexical else _INCONSISTENT_INSTRUCTIONS,
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
        """Conservative estimate: Jev has no count endpoint; the response carries real usage.

        Measured 2026-09-21 on KTU 1.6 I: 13,087 reported input tokens for a 20 KB
        adjudicate body, i.e. ~1.55 bytes per token (transliteration is diacritic-heavy),
        hence the 1.5 default.
        """

        encoded = json.dumps(self._body(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return max(1, math.ceil(len(encoded) / self._bytes_per_token))

    def _no_call_response(self, request: ProviderJSONRequest, payload: dict[str, Any]) -> ProviderResponse:
        return ProviderResponse(
            model=self.declared_exact_model or request.requested_model,
            payload=payload,
            usage=ProviderUsage(0, 0),
            request_id=None,
        )

    def resolve_locally(self, request: ProviderJSONRequest) -> ProviderResponse | None:
        """Answers that need no provider: the runtime books no request for them.

        - adjudicate with no candidate lexeme at all → unresolved;
        - lexical stage, exactly one candidate and the parser offered it → accepted;
        - reconcile over no decisions → no findings.
        """

        if request.operation == "adjudicate":
            body, candidates = self._adjudicate_body(request)
            if candidates and not body.get("questions"):
                return self._no_call_response(request, self._accept_single(candidates[0]))
            if not candidates:
                evidence_ids = [
                    _text(r.get("evidence_id"))
                    for r in request.payload.get("evidence") or ()
                    if isinstance(r, Mapping) and _text(r.get("source_id")) == "auto-parsing"
                ] or [
                    _text(r.get("evidence_id")) for r in request.payload.get("evidence") or () if isinstance(r, Mapping)
                ]
                if not evidence_ids:
                    raise ProviderPermanentError("no evidence at all for an unresolved token")
                return self._no_call_response(request, {
                    "analyses": [UNRESOLVED],
                    "evidence_ids": evidence_ids,
                    "summary": "no candidate lexeme offered by any source; token left unresolved (no provider call)",
                    "reviewed_rows": [{"morphological_parsing": UNRESOLVED, "dulat": UNRESOLVED, "pos": UNRESOLVED, "gloss": UNRESOLVED,
                                       "comments": "no candidate lexeme from the parser or reviewed parallels; needs hand review"}],
                    "jev": {"stage": self.decision_policy.stage, "choice": None, "reading": None, "readings": {}, "probabilities": {},
                            "abstention_probability": None, "confidence": None, "ambiguous": None, "no_candidates": True,
                            "alternative_threshold": self.decision_policy.alternative_threshold},
                })
            return None
        if request.operation == "reconcile":
            _, latest = self._reconcile_body(request)
            if not latest:
                return self._no_call_response(request, {"findings": []})
            return None
        return None

    def generate_json(self, request: ProviderJSONRequest, timeout_seconds: float) -> ProviderResponse:
        self._current_operation = request.operation
        local = self.resolve_locally(request)
        if local is not None:
            return local
        if request.operation == "adjudicate":
            body, candidates = self._adjudicate_body(request)
            result = self._http_json(f"{self._base_url}/systemone", self._headers(), body, timeout_seconds)
            return ProviderResponse(
                model=_required(result.get("model"), "model"),
                payload=self._adjudicate_payload(result, candidates),
                usage=_usage(result.get("usage")),
                request_id=_optional_text(result.get("id")),
            )
        return self._reconcile(request, timeout_seconds)

    def _reconcile(self, request: ProviderJSONRequest, timeout_seconds: float) -> ProviderResponse:
        """One noul per token, sent in batches; findings and usage are merged."""

        body, latest = self._reconcile_body(request)
        questions = body["questions"]
        keys = list(questions)
        size = self.decision_policy.reconcile_batch_size
        findings: list[dict[str, object]] = []
        input_tokens = output_tokens = 0
        model: str | None = None
        request_ids: list[str] = []
        for start in range(0, len(keys), size):
            batch_keys = keys[start : start + size]
            batch_body = dict(body)
            batch_body["questions"] = {key: questions[key] for key in batch_keys}
            result = self._http_json(f"{self._base_url}/systemone", self._headers(), batch_body, timeout_seconds)
            batch_model = _required(result.get("model"), "model")
            if model is not None and batch_model != model:
                raise ProviderPermanentError("Jev reconcile batches were answered by different models")
            model = batch_model
            usage = _usage(result.get("usage"))
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens
            request_id = _optional_text(result.get("id"))
            if request_id:
                request_ids.append(request_id)
            batch_latest = {token_id: latest[token_id] for token_id in (k.split(":", 1)[1] for k in batch_keys)}
            findings.extend(self._reconcile_payload(result, batch_latest)["findings"])
        return ProviderResponse(
            model=model or request.requested_model,
            payload={"findings": findings},
            usage=ProviderUsage(input_tokens, output_tokens),
            request_id=",".join(request_ids) or None,
        )

    # --- answer mapping ------------------------------------------------------------------

    def _adjudicate_payload(self, result: Mapping[str, Any], candidates: tuple[Any, ...]) -> dict[str, Any]:
        lexical = self.decision_policy.stage == "lexical"
        question = "lexeme" if lexical else "reading"
        answers = result.get("answers")
        if not isinstance(answers, Mapping):
            raise ProviderPermanentError("Jev response has no answers")
        if lexical and len(candidates) == 1 and "lexeme_fits" in answers:
            return self._lexical_fit_payload(answers, candidates[0])
        reading = answers.get(question)
        if not isinstance(reading, Mapping):
            raise ProviderPermanentError(f"Jev response lacks the {question} answer")
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
        if choice not in probabilities:
            raise ProviderPermanentError("Jev chose an option that has no probability")
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

        # Abstention must not win by plurality: when the readings together outweigh
        # none-of-these, the best reading is chosen and the abstention share recorded.
        abstention = probabilities.get(NONE_OF_THESE, 0.0)
        # A missing confidence is not "confidence ≥ threshold": it blocks abstention too.
        low_confidence = confidence_value is None or confidence_value < policy.abstention_confidence
        if choice == NONE_OF_THESE and (abstention <= 0.5 or low_confidence):
            readings_only = {k: v for k, v in probabilities.items() if k != NONE_OF_THESE}
            if readings_only:
                choice = max(readings_only, key=lambda k: (readings_only[k], -int(k.rsplit("-", 1)[-1] or 0)))
        self._low_confidence = low_confidence

        if lexical:
            return self._lexical_payload(choice, probabilities, confidence_value, ambiguous, threshold, candidates)

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

    def _jev_block(self, choice, probabilities, confidence, ambiguous, threshold) -> dict[str, object]:
        return {
            "stage": self.decision_policy.stage,
            "question": "lexeme" if self.decision_policy.stage == "lexical" else "reading",
            "choice": choice,
            "probabilities": dict(sorted(probabilities.items())),
            "abstention_probability": probabilities.get(NONE_OF_THESE, 0.0),
            "confidence": confidence,
            "low_confidence": bool(getattr(self, "_low_confidence", False)),
            "ambiguous": ambiguous,
            "alternative_threshold": threshold,
        }

    def _accept_single(self, candidate: Any, fits: float | None = None) -> dict[str, Any]:
        rows = [dict(r) for r in (candidate.rows[: candidate.parser_row_count] if candidate.parser_row_count else candidate.rows)]
        analyses = list(dict.fromkeys(r["morphological_parsing"] for r in rows))
        block: dict[str, Any] = {
            "stage": self.decision_policy.stage,
            "question": "single-candidate-accepted" if fits is None else "lexeme_fits",
            "reading": candidate.reading.to_dict(),
            "readings": {candidate.key: candidate.reading.to_dict()},
        }
        summary = (
            f"lexical stage: only candidate {candidate.reading.lemma} [{candidate.reading.pos_class}] "
            "accepted as the parser gave it"
        )
        if fits is not None:
            block["fits"] = fits
            block["review_priority"] = 1.0 - fits
            summary += f"; Jev fit p={fits:.2f} (review priority {1.0 - fits:.2f})"
        return {"analyses": analyses, "evidence_ids": list(candidate.evidence_ids), "summary": summary, "reviewed_rows": rows, "jev": block}

    def _lexical_fit_payload(self, answers: Mapping[str, Any], candidate: Any) -> dict[str, Any]:
        answer = answers.get("lexeme_fits")
        if not isinstance(answer, Mapping):
            raise ProviderPermanentError("Jev response lacks the lexeme_fits answer")
        try:
            fits = _positive_fraction(answer.get("noul"), "lexeme_fits")
        except ValueError as exc:
            raise ProviderPermanentError(str(exc)) from None
        return self._accept_single(candidate, fits)

    def _lexical_payload(self, choice, probabilities, confidence, ambiguous, threshold, candidates) -> dict[str, Any]:
        by_key = {c.key: c for c in candidates}
        block = self._jev_block(choice, probabilities, confidence, ambiguous, threshold)
        block["readings"] = {c.key: c.reading.to_dict() for c in candidates}
        if choice == NONE_OF_THESE:
            all_evidence = tuple(dict.fromkeys(eid for c in candidates for eid in c.evidence_ids))
            if not all_evidence:
                raise ProviderPermanentError("no candidate evidence available for an unresolved decision")
            block["reading"] = None
            return {
                "analyses": [UNRESOLVED],
                "evidence_ids": list(all_evidence),
                "summary": (
                    f"Jev (lexical stage) chose none-of-these (p={probabilities.get(NONE_OF_THESE, 0.0):.2f}, "
                    f"confidence={confidence}); token left unresolved for hand review"
                ),
                "reviewed_rows": [
                    {"morphological_parsing": UNRESOLVED, "dulat": UNRESOLVED, "pos": UNRESOLVED, "gloss": UNRESOLVED,
                     "comments": "no candidate lexeme accepted by Jev; needs hand review"}
                ],
                "jev": block,
            }
        policy = self.decision_policy
        ambiguous_enough = ambiguous is not None and ambiguous >= policy.ambiguous_noul_cutoff
        if policy.lexical_alternatives_need_ambiguity and not ambiguous_enough:
            kept = [choice]
        else:
            kept = [choice] + sorted(
                (k for k, p in probabilities.items() if k != choice and k != NONE_OF_THESE and p >= threshold),
                key=lambda k: (-probabilities[k], k),
            )
        rows: list[dict[str, str]] = []
        analyses: list[str] = []
        evidence_ids: list[str] = []
        for key in kept:
            candidate = by_key[key]
            # The parser's rows for this lexeme carry stages 2–4 unchanged; parallel rows
            # are used only when the parser offered no row for the chosen lexeme.
            chosen_rows = candidate.rows[: candidate.parser_row_count] if candidate.parser_row_count else candidate.rows
            for row in chosen_rows:
                rows.append(dict(row))
                if row["morphological_parsing"] not in analyses:
                    analyses.append(row["morphological_parsing"])
            for eid in candidate.evidence_ids:
                if eid not in evidence_ids:
                    evidence_ids.append(eid)
        block["reading"] = by_key[choice].reading.to_dict()
        chosen = by_key[choice].reading
        summary = (
            f"Jev (lexical stage) chose {chosen.lemma} [{chosen.pos_class}] "
            f"(p={probabilities.get(choice, 0.0):.2f}, confidence={confidence}); "
            f"kept {len(kept)} reading(s) at threshold {threshold:.2f}; ambiguous={ambiguous}"
        )
        return {"analyses": analyses, "evidence_ids": evidence_ids, "summary": summary, "reviewed_rows": rows, "jev": block}

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
    execution_kind: ExecutionKind = ExecutionKind.LIVE_PROVIDER,
) -> LiveProviderBinding:
    """Bind a Jev client as a HARN-016/022 backend arm."""

    if not isinstance(client, TypeSafeJevClient):
        raise ValueError("client must be TypeSafeJevClient")
    client.declared_exact_model = exact_model_version
    return LiveProviderBinding(
        spec=spec,
        requested_model=requested_model,
        exact_model_version=exact_model_version,
        exact_version_provenance=exact_version_provenance,
        client=client,
        execution_kind=execution_kind,
    )
