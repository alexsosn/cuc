"""HARN-033 stage 1: lexical linking + POS category.

Definitions (see the ticket): a *lexical reading* is ``(lemma key, POS class)``.
The lemma key is the DULAT field (``ġr (III)``, ``/m-ġ-y/``); the POS class is
the first token of the POS field (``n.``, ``vb``, ``DN``). The verb stem, number,
gender, case, state, conjugation, gloss wording and encoding are later stages and
are inherited from the parser rows of the chosen lexeme.

Candidates come from the parser and from reviewed parallels only. The legacy
expert review supplies surface-spelled analyses without a DULAT link or a POS;
they reach the model as context but never form an option (they could not be
exact and would only duplicate the parser's lexeme under another spelling).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .live_providers import ProviderPermanentError

UNRESOLVED = "?"
_CANDIDATE_SOURCES = ("auto-parsing", "corpus-parallels")
_SEED_MARKER = "SEEDED from auto-parse"


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def pos_class(pos_field: object) -> str:
    """``n. f. sg. cstr. gen.`` → ``n.``; ``vb G prefc. 3 m. sg.`` → ``vb``; empty → ``?``.

    The verb stem is form-level (stage 2) and is not part of the lexical POS class.
    """

    tokens = _text(pos_field).split(" ")
    head = tokens[0] if tokens and tokens[0] else UNRESOLVED
    if head == "Subordinating":
        head = "subordinating"
    return head


def lemma_key(dulat_field: object) -> str:
    """Whitespace-normalised DULAT field; empty means unresolved."""

    value = _text(dulat_field)
    return value or UNRESOLVED


@dataclass(frozen=True)
class LexicalReading:
    lemma: str
    pos_class: str

    def to_dict(self) -> dict[str, str]:
        return {"lemma": self.lemma, "pos": self.pos_class}


@dataclass(frozen=True)
class LexicalCandidate:
    key: str
    reading: LexicalReading
    rows: tuple[dict[str, str], ...]
    parser_row_count: int
    sources: tuple[str, ...]
    attestations: int
    glosses: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    @property
    def forms(self) -> tuple[str, ...]:
        """Distinct parser segmentations linking the surface to the lemma (≤ 3)."""

        seen: list[str] = []
        for row in self.rows[: self.parser_row_count] or self.rows:
            form = row.get("morphological_parsing", "")
            if form and form != UNRESOLVED and form not in seen:
                seen.append(form)
            if len(seen) == 3:
                break
        return tuple(seen)

    def option(self) -> dict[str, object]:
        description: dict[str, object] = {
            "lemma": self.reading.lemma,
            "pos": self.reading.pos_class,
            "forms": list(self.forms),
            "glosses": list(self.glosses),
            "from": list(self.sources),
        }
        if self.attestations:
            description["reviewed_attestations_elsewhere"] = self.attestations
        if self.parser_row_count:
            description["parser_alternatives"] = self.parser_row_count
        return description


def _field_ok(value: str) -> bool:
    if "\t" in value or (value and value.splitlines() != [value]):
        return False
    return _SEED_MARKER not in value


def group_lexical_candidates(
    evidence: list[Mapping[str, Any]],
    *,
    max_candidates: int,
) -> tuple[LexicalCandidate, ...]:
    """Group candidate-bearing evidence by lexical reading; parser rows first within a group.

    Rows that resolve neither lemma nor POS never form a candidate.
    """

    groups: dict[LexicalReading, dict[str, Any]] = {}
    order: list[LexicalReading] = []

    def add(reading: LexicalReading, row: dict[str, str], source: str, evidence_id: str, attestations: int) -> None:
        if reading not in groups:
            groups[reading] = {"parser": [], "other": [], "sources": [], "attestations": 0, "glosses": [], "evidence_ids": []}
            order.append(reading)
        entry = groups[reading]
        bucket = entry["parser"] if source == "auto-parsing" else entry["other"]
        if row not in bucket:
            bucket.append(row)
        if source not in entry["sources"]:
            entry["sources"].append(source)
        if evidence_id not in entry["evidence_ids"]:
            entry["evidence_ids"].append(evidence_id)
        entry["attestations"] += attestations
        gloss = row.get("gloss", "")
        if gloss and gloss != UNRESOLVED and gloss not in entry["glosses"] and len(entry["glosses"]) < 3:
            entry["glosses"].append(gloss)

    for record in evidence:
        source = _text(record.get("source_id"))
        evidence_id = _text(record.get("evidence_id"))
        if source not in _CANDIDATE_SOURCES or not evidence_id:
            continue
        summary = record.get("summary")
        try:
            data = json.loads(summary) if isinstance(summary, str) else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, Mapping):
            continue
        raw = {
            "morphological_parsing": _text(data.get("morphological_parsing")),
            "dulat": _text(data.get("dulat")),
            "pos": _text(data.get("pos")),
            "gloss": _text(data.get("gloss")),
        }
        if not any(raw.values()):
            continue
        row = {key: value or UNRESOLVED for key, value in raw.items()}
        row["comments"] = ""
        if not all(_field_ok(value) for value in row.values()):
            continue
        reading = LexicalReading(lemma_key(row["dulat"]), pos_class(row["pos"]))
        if reading.lemma == UNRESOLVED and reading.pos_class == UNRESOLVED:
            continue
        attestations = data.get("attestations")
        if isinstance(attestations, bool) or not isinstance(attestations, int) or attestations < 0:
            attestations = 0
        add(reading, row, source, evidence_id, attestations)

    if len(order) > max_candidates:
        raise ProviderPermanentError(
            f"too many lexical candidate readings for one token ({len(order)} > {max_candidates})"
        )
    candidates = []
    for index, reading in enumerate(order, start=1):
        entry = groups[reading]
        candidates.append(
            LexicalCandidate(
                key=f"reading-{index}",
                reading=reading,
                rows=tuple(entry["parser"] + entry["other"]),
                parser_row_count=len(entry["parser"]),
                sources=tuple(entry["sources"]),
                attestations=entry["attestations"],
                glosses=tuple(entry["glosses"]),
                evidence_ids=tuple(entry["evidence_ids"]),
            )
        )
    return tuple(candidates)


def gold_lexical_readings(rows: list[tuple[str, str, str, str]] | tuple) -> tuple[LexicalReading, ...]:
    """Distinct lexical readings over reviewed rows ``(analysis, dulat, pos, gloss)``."""

    seen: list[LexicalReading] = []
    for row in rows:
        reading = LexicalReading(lemma_key(row[1]), pos_class(row[2]))
        if reading not in seen:
            seen.append(reading)
    return tuple(seen)


def _readings(value: Any) -> set[LexicalReading]:
    out: set[LexicalReading] = set()
    for item in value or ():
        if isinstance(item, LexicalReading):
            out.add(item)
        else:
            out.add(LexicalReading(lemma_key(item[0]), pos_class(item[1])))
    return out


_UNRESOLVED_READING = LexicalReading(UNRESOLVED, UNRESOLVED)


@dataclass(frozen=True)
class LexicalScore:
    tokens: int
    exact: int
    lemma_exact: int
    subset: int
    abstained_correct: int
    abstained_wrong: int
    parser_first_exact: int
    parser_all_exact: int
    ceiling: int
    gold_unresolved: int = 0

    def to_dict(self) -> dict[str, object]:
        n = self.tokens or 1
        return {
            "tokens": self.tokens,
            "gold_unresolved": self.gold_unresolved,
            "exact": self.exact,
            "exact_rate": self.exact / n,
            "lemma_exact": self.lemma_exact,
            "lemma_exact_rate": self.lemma_exact / n,
            "subset": self.subset,
            "abstained_correct": self.abstained_correct,
            "abstained_wrong": self.abstained_wrong,
            "parser_first_exact": self.parser_first_exact,
            "parser_first_rate": self.parser_first_exact / n,
            "parser_all_exact": self.parser_all_exact,
            "parser_all_rate": self.parser_all_exact / n,
            "ceiling": self.ceiling,
            "ceiling_rate": self.ceiling / n,
        }


def score_lexical_column(
    *,
    predicted: Mapping[str, Any],
    gold: Mapping[str, Any],
    offered: Mapping[str, Any],
    parser_first: Mapping[str, Any] | None = None,
    parser_all: Mapping[str, Any] | None = None,
) -> LexicalScore:
    """Score stage-1 predictions per token against the reviewed gold.

    A prediction of ``?`` is an abstention: correct when no gold reading was among
    the offered candidates, wrong otherwise. Tokens whose gold is itself unresolved
    (the reviewer left ``?``) carry no reading to be right or wrong about: they are
    counted in ``gold_unresolved`` and excluded from every rate, for the prediction
    and the baselines alike.
    """

    exact = lemma_exact = subset = ab_ok = ab_wrong = pf = pa = ceiling = unresolved = scored = 0
    for token_id, gold_value in gold.items():
        g = _readings(gold_value) - {_UNRESOLVED_READING}
        if not g:
            unresolved += 1
            continue
        scored += 1
        p = _readings(predicted.get(token_id))
        o = _readings(offered.get(token_id)) - {_UNRESOLVED_READING}
        if g & o:
            ceiling += 1
        abstained = p == {_UNRESOLVED_READING} or not p
        if abstained:
            if g & o:
                ab_wrong += 1
            else:
                ab_ok += 1
        else:
            if p == g:
                exact += 1
            if {r.lemma for r in p} == {r.lemma for r in g}:
                lemma_exact += 1
            if p <= g:
                subset += 1
        if parser_first is not None:
            first = parser_first.get(token_id)
            if first is not None and _readings([first]) - {_UNRESOLVED_READING} == g:
                pf += 1
        if parser_all is not None and _readings(parser_all.get(token_id)) - {_UNRESOLVED_READING} == g:
            pa += 1
    return LexicalScore(scored, exact, lemma_exact, subset, ab_ok, ab_wrong, pf, pa, ceiling, unresolved)
