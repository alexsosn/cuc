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
- **Candidates** come from the parser's rows and from reviewed parallels. The
  legacy expert review holds surface-spelled analyses with no DULAT link and no
  POS (`krt`, `il (I)`, `xxxx`): they reach the model as context and never form
  an option (review H1/H2 — as options they duplicated the parser's lexeme under
  another spelling, could never be exact, and a damaged string became a curated
  row without a call).
- **Gold-unresolved tokens** (the reviewer left `?`) carry no reading to be
  right or wrong about: they are counted separately and excluded from every
  rate, for the prediction and the baselines alike (review M1).

## Plan

### `harness.lexical_stage`

- `pos_class(pos_field)`, `lemma_key(dulat_field)`, `LexicalReading`.
- `group_lexical_candidates(evidence, max_candidates)`: candidate-bearing
  evidence (auto-parsing rows, corpus-parallel readings) grouped by lexical
  reading; each candidate keeps its rows (parser rows first), sources,
  attestation count, up to three distinct glosses as identification aid, and
  the evidence ids behind it. Rows resolving neither lemma nor POS never form a
  candidate.
- `gold_lexical_readings(rows)`, `score_lexical_column(...)` → exact-set,
  lemma-only exact, abstentions split into correct (gold not offered) and
  wrong, plus parser baselines (first alternative; all alternatives) and the
  ceiling (gold reading among the offered candidates).

### Jev client, stage `lexical`

`JevDecisionPolicy(stage="lexical")`:

- **state**: the token; the line's tokens (surfaces) and the neighbouring line
  on each side; DULAT citations at the line whose entry *headword* matches a
  candidate lemma (others summarised as a count — the citation label is the
  cited phrase, not the headword; the adapter now carries `headword` and
  `headword_pos` from the search index's `entries_fts`, review M2); EUPT
  vocalisation/translation for the line; Burns rows whose headword matches the
  surface after aleph/ayin normalisation and without `(DN)`-style qualifiers
  (review M3); legacy analyses of the surface; same-line prior lexical
  decisions (surface → lemma) as context. No column listing, no history, no
  Tropper.
- **questions**: `lexeme` (choice over lexical candidates + `none-of-these`)
  and `ambiguous` (noul).
- **answer**: chosen candidate → `analyses` = the parser's morphology strings
  for that reading (parallel rows only when the parser has none),
  `reviewed_rows` = those rows; alternatives kept only when the `ambiguous`
  question answers yes; `none-of-these` only when p(none) > 0.5 *and* the
  reported confidence is ≥ 0.5 (an absent confidence blocks abstention too,
  review L1; abstention cannot win by plurality — applied to the full stage
  too); the `jev` block records the stage, the chosen reading and the
  per-reading probabilities.
- **single candidate**: accepted without a call only when the parser offered
  it (`parser_row_count > 0`, the 98% base rate below); a parallel-only single
  is asked as a choice against `none-of-these`.
- **zero-call answers** go through `TypeSafeJevClient.resolve_locally`; the
  HARN-022 runtime consults that hook before reserving budget, so no request,
  tokens or call artifact are booked for them, and the trial artifact reports
  them as `local_resolutions` (review M4).
- reconcile: one noul per token, batched; in the lexical stage each decision is
  shown as its `(lemma, POS class)` and the question is phrased for the lexeme,
  so case/number differences cannot raise a finding (review L3).

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
   legacy analyses as context only; forward-compatible payload;
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
| stage 1 round 3: + single candidates accepted without a call, alternatives only when Jev reports ambiguity | 87.3% | 90.3% | 0 | 252k |
| stage 1 round 4 (review fixes: DULAT by headword, Burns normalised, legacy as context, gold-`?` excluded) | **89.2%** | **92.2%** | **0** | 265k |
| ceiling (gold among offered) | 94.3% → 95.3% (round 4 denominator) | | | |

Rounds 1–3 are over 299 tokens; round 4 and its baselines are over the 296
tokens whose gold is resolved (parser-first 79.4%, parser-all 73.6%). Round 4
made 120 provider requests and 180 local resolutions; the earlier rounds
reported 300 requests because the runtime booked the zero-call answers.

What the rounds taught:

- **Single-candidate tokens (171/299)**: the parser's only lexeme is right in
  168 (98.2%). A "this lexeme or none" choice put 0.51–0.94 on none for `b`,
  `ảrṣ`, `hdm`; a yes/no Noul was calibrated but useless at this base rate (28
  false vetoes at p < 0.5 against 2 true misses at 0.07 and 0.23). The parser's
  reading is accepted; `--verify-singles` records the fit as a review-priority
  signal only, in line with the skill's rule that worklists prioritise attention
  and never decide.
- **Multi-candidate tokens (119/299)**: Jev 79.0% exact vs parser-first 56.3%,
  ceiling 95.0% (round 4: 80.7%, 96/119). This is where stage 1 earns its keep.
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

Gold quirks the scorer counts as misses, left as they are (review L5): two
reviewed rows whose POS starts with the stem (`G impv. m. sg.`, `G inf. abs.` →
class `G`), three with POS `→`, one empty POS, and composite rows (`km | ḫmšt`,
`a | b`) where `pos_class` takes the first POS. They belong to the reviewed-data
normalisation noted in HARN-028.

## Second column — 2026-09-22, KTU 1.14 column I (155 tokens, 138 with resolved gold, all sources incl. legacy review)

| arm | exact | lemma-only | wrong abstentions | requests | input tokens |
|---|---|---|---|---|---|
| parser, first alternative | 83.3% | | | | |
| parser, all alternatives | 84.1% | | | | |
| stage 1 round 4 | 83.3% | 87.0% | 0 | 37 (+119 local) | 142k |
| ceiling | 92.0% | | | | |

Here Jev equals the parser: on the 35 multi-candidate tokens both score 23. Of
the 12 misses, five are tokens where the reviewer kept two lexical readings
(`ỉl` DN and n., `nhr`, `ḥtkh` ×2, `yʕn`) and `ambiguous` did not fire; three
are POS-class granularity in the parser's rows (`prep./conj./adv.` vs `prep.`,
`conj./interr.` vs `conj.`); three are gold that no candidate can reach (`a kt`
reviewed as one word `ảt`, `k` reviewed as `km | ḫmšt`); one is a genuine
lexical miss (`lm` interr. for `l` prep.). Single candidates: the parser is
right in 92/98 (93.9%; 1.6 I: 98.2%), and three of the six misses are again
two-reading golds. So the zero-call rule holds on a tablet with a legacy review
and weaker parser coverage; the multi-gold tokens are the next thing to look at
(the `ambiguous` question is not catching them).

Next: the per-source ablation arms on both columns (each external source
disabled in turn), then the `ambiguous` calibration on multi-gold tokens.
