# HARN-033 — Stage 1 only: lexical linking + POS with Jev

Decided 2026-09-22. The parsing decision is split into stages with minimal
inputs: (1) lexical linking + POS category, (2) form linking + morphological
features, (3) gloss, (4) morphological encoding. Stage 1 is valuable on its own
and is what the automatic parser is weakest at, so the Jev pipeline and its
evaluation are narrowed to it.

## Research — 2026-09-22

Profile of the first full-stage Jev run on KTU 1.6 I (299 tokens):

- a request averaged 28.7 KB (≈18.5k tokens): whole-column token list 61%,
  prior-decision history 25%, candidate options 5%, DULAT 1.5%, Burns 2.1%,
  EUPT 0.9%, Tropper 0.5%, instructions 2.7%;
- 59% of DULAT citations sent per token concern other words on the same line
  (the reverse index is line-keyed); Burns rows are line-level too;
- of 27 `none-of-these` abstentions, 11 were correct (gold not offered; the
  parser had no reading in 7), and 13 of the remaining 16 were a plurality
  artefact: the gold reading was offered as 2–5 near-identical options
  differing only in case or gloss wording, the probability mass split across
  them, and `none-of-these` won with p as low as 0.22.

Conclusions: for the lexical decision the column listing, the history and the
grammar pointers carry nothing; case/gloss variants must not be separate
options; abstention must never win by plurality.

## Definitions

- **Lemma key**: the DULAT field, whitespace-normalised (`ġr (III)`, `/m-ġ-y/`,
  `-n (IV)`); `?` and empty are "unresolved".
- **POS class**: the first whitespace token of the POS field (`n.`, `vb`,
  `prep.`, `DN`, `conj.`, …; `Subordinating`→`subordinating`). The verb stem
  is form-level (round 2 decision) and, like number, gender, case, state and
  conjugation, is not part of stage 1.
- **Lexical reading**: `(lemma key, POS class)`. A token's gold is the set of
  lexical readings over its reviewed rows; the prediction is the set over the
  kept alternatives.

## Plan

### `harness.lexical_stage`

- `pos_class(pos_field)`, `lemma_key(dulat_field)`, `LexicalReading`.
- `group_lexical_candidates(evidence, max_candidates)`: candidate-bearing
  evidence (auto-parsing rows, corpus-parallel readings, legacy analyses)
  grouped by lexical reading; each candidate keeps its rows (parser rows first),
  sources, attestation count, up to three distinct glosses as identification
  aid, and the evidence ids behind it. Legacy analyses carry the lemma from the
  analysis string and POS `?`.
- `gold_lexical_readings(rows)`, `score_lexical_column(...)` → exact-set,
  lemma-only exact, abstentions split into correct (gold not offered) and
  wrong, plus parser baselines (first alternative; all alternatives) and the
  ceiling (gold reading among the offered candidates).

### Jev client, stage `lexical`

`JevDecisionPolicy(stage="lexical")`:

- **state**: the token; the line's tokens (surfaces) and the neighbouring line
  on each side; DULAT citations at the line whose label matches a candidate
  lemma (others summarised as a count); EUPT vocalisation/translation for the
  line; Burns rows only when the headword matches the surface; same-line prior
  lexical decisions (surface → lemma) as context. No column listing, no
  history, no Tropper.
- **questions**: `lexeme` (choice over lexical candidates + `none-of-these`)
  and `ambiguous` (noul).
- **answer**: chosen candidate → `analyses` = the parser's morphology strings
  for that reading (parallel rows only when the parser has none),
  `reviewed_rows` = those rows; alternatives above threshold as further
  readings; `none-of-these` only when p(none) > 0.5 (abstention cannot win by
  plurality — applied to the full stage too); the `jev` block records the
  stage, the chosen reading and the per-reading probabilities.
- reconcile: unchanged (one noul per token, batched), phrased for the lexeme.

### Runner script

`agent/scripts/jev_lexical_stage.py --tablet "KTU 1.6" --column I
[--evidence dulat,eupt,...] [--dry-run]`: loads the column, builds the
collector for the requested policy, runs the HARN-022 live runtime with the
lexical-stage client, scores against the reviewed gold with the lexical
scorer, and writes a metrics-only report (JSON + markdown) under
`agent/reports/jev-lexical/` (ignored). Tracing and I/O capture follow the
HARN-005/031/032 environment flags. Stub completion gates until 028b.

## TDD gates

1. `pos_class`/`lemma_key` on the real vocabulary (`vb G prefc. 3 m. sg.` →
   `vb`; `n. f. sg. cstr. gen.` → `n.`; `?`; `Subordinating functor`);
2. grouping collapses case/gloss variants into one candidate, keeps homonyms
   apart, orders parser rows first, records attestations and glosses;
3. request body for the lexical stage contains the line and neighbours but not
   the column listing, no history beyond the same line, no Tropper, only
   matching DULAT/Burns; size bounded;
4. answer mapping: chosen reading → parser rows; abstention needs p(none) > 0.5
   (`gm` case: p(none)=0.22 with five variants → reading chosen); alternatives;
   legacy-only reading; forward-compatible payload;
5. scorer: exact, lemma-only, correct vs wrong abstention, baselines, ceiling,
   on a small fixture with a multi-row gold token;
6. script: dry run with the test-double transport writes a report with no
   resource text.

## Non-goals

Stages 2–4; changing the HARN-018 state contract; the 028b gates/CLI.

## Results — 2026-09-22, KTU 1.6 column I (299 tokens, all sources, jev-1.13.0)

Lexical exactness = predicted set of (lemma, POS class) equals the gold set;
an abstention counts as correct only when no gold reading was offered.

| arm | exact | lemma-only | wrong abstentions | input tokens |
|---|---|---|---|---|
| parser, all alternatives | 73.9% | | | |
| parser, first alternative | 79.6% | | | |
| full-stage Jev (HARN-029, for comparison, morphology-set metric) | 77.3% | | 16 | 5.3M |
| stage 1 round 1: trimmed inputs, one option per lexeme + none | 72.2% | 80.9% | 29 | 427k |
| stage 1 round 2: + parser forms in options, aleph note, Noul for single candidates, abstention needs confidence ≥ 0.5 | 75.3% | 80.9% | 28 | 419k |
| stage 1 round 3: + single candidates accepted without a call, alternatives only when Jev reports ambiguity | **87.3%** | **90.3%** | **0** | 252k |
| ceiling (gold among offered) | 94.3% | | | |

What the rounds taught:

- **Single-candidate tokens (171/299)**: the parser's only lexeme is right in
  168 (98.2%). A "this lexeme or none" choice put 0.51–0.94 on none for `b`,
  `ảrṣ`, `hdm`; a yes/no Noul was calibrated but useless at this base rate (28
  false vetoes at p < 0.5 against 2 true misses at 0.07 and 0.23). The parser's
  reading is accepted; `--verify-singles` records the fit as a review-priority
  signal only, in line with the skill's rule that worklists prioritise attention
  and never decide.
- **Multi-candidate tokens (119/299)**: Jev 79.0% exact vs parser-first 56.3%,
  ceiling 95.0%. This is where stage 1 earns its keep.
- **Alternatives**: keeping every lexeme above the probability threshold cost
  nine exact matches and gained none (one token in the column has two gold
  lexical readings). Alternatives are kept only when the `ambiguous` question
  answers yes.
- **Forms**: `apsh`→`ảps`, `ṣpˤn`→`ṣpn`, `psltm`→`pslt (I)` were rejected until
  the option showed the parser's segmentation (`aps/+h`) and the instructions
  explained the aleph diacritics; the form is stage-1 evidence, the features are
  not.
- Remaining misses are real lexical/POS decisions (`ỉl` DN vs n., `ym (II)` DN
  vs n., `l` homonyms, `ảḥd` adj. vs num.) and 9 tokens with no candidate at
  all (parser unresolved), which no closed-choice backend can fix; those feed
  the parser-improvement loop (HARN-017).

Next: the per-source ablation arms on this column (each external source
disabled in turn), then a second column to check the numbers hold.
