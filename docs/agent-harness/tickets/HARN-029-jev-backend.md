# HARN-029 — Jev (TypeSafe System One) as the first model backend

Decided 2026-09-21: the first model to run through the parsing harness is Jev.

## Research — 2026-09-21

Sources: https://docs.typesafe.ai/introduction, `/api.md`, `/models.md`,
`/primitives/choice.md`, `/concepts/state.md`, `/llms.txt`.

- `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer $TYPESAFE_API_KEY`.
- Request: `state` (string | object | array; one state shared by every question
  in the request), `model`, `questions` map of `{type, instructions, criteria}`.
  `choice`: criteria = option → description (string or JSON), ≤ 255 options;
  answer `{choice, probabilities (sum 1), confidence}`. `noul`: answer
  `{noul}` = probability of yes. `score`: 2–10 ordered levels.
- Response: `model` = the pinned version that answered (aliases resolve),
  `answers`, `usage {input_tokens, output_tokens}`.
- Models: `jev-1.13.0` pinned; `jev-latest`, `jev-preview` aliases. 64k
  context, 32k state budget per request, input-only pricing, no token-count
  endpoint, 429/529 retryable. English is the primary training language.

### Consequences for the harness

Jev does not generate text, so it cannot invent an analysis, a DULAT entry or a
gloss. Its decision space must be **closed**: every option is a complete
candidate curated row. Candidates come from the evidence the HARN-028 collector
already supplies — the automatic-parsing alternatives (full rows), the
aggregated corpus-parallel readings (full rows), and the legacy expert review
(analysis only; POS/gloss stay `?` with a comment) — plus an explicit
`none-of-these` option, as the Choice docs recommend.

Preserving defensible ambiguity maps directly onto the probability
distribution: every candidate whose probability reaches a threshold is kept as
an alternative row, the chosen option first. A `noul` question asked in the
same request ("more than one candidate is defensible here") is recorded in the
summary and lowers the threshold when affirmative. Nothing else in the graph
changes: Jev is wrapped as a HARN-022 `ProviderJSONClient`, so exact-version
identity, budget, retries and redacted call artifacts apply unchanged.

Reconciliation: Jev cannot author findings. The reconcile operation asks one
`noul` per token in a single request — "the chosen reading of this token is
inconsistent with the other occurrences of the same surface in this column" —
and turns affirmative answers into `CorpusReconciliationFinding`s scoped to the
column; those above a revisit threshold request one revisit. That is a real,
bounded reconciliation pass rather than a stub.

Token counting: there is no count endpoint. `count_input_tokens` returns a
conservative byte-based estimate (UTF-8 bytes / 3, transliteration is
multi-byte) so the HARN-022 budget can reject before the call; actual usage
comes back in the response.

## Plan

`harness.typesafe_jev`:

- `TypeSafeJevClient(_EnvironmentCredentialClient)`, `provider = "typesafe"`,
  `api_key_env = "TYPESAFE_API_KEY"`, injected `http_json` transport as the
  other clients.
- `JevDecisionPolicy(alternative_threshold=0.25, ambiguous_threshold=0.15,
  revisit_threshold=0.7, finding_threshold=0.5, max_candidates=64)`.
- `extract_candidates(payload) -> tuple[Candidate, ...]`: from evidence records
  by `source_id`, deduplicated by (analysis, dulat, pos, gloss), each bound to
  the evidence ids that support it; plus `none-of-these`.
- adjudicate: state = column snapshot text, the token, non-candidate evidence
  (dulat/eupt/tropper/burns summaries), prior decisions, skill context, revisit
  request; questions = `reading` (choice) + `ambiguous` (noul). Answer →
  `{analyses, evidence_ids, summary, reviewed_rows, jev: {choice,
  probabilities, confidence, ambiguous}}`; `reviewed_rows` is forward-compatible
  with HARN-027 structured rows for 028b.
- reconcile: one request, `noul` per token; findings with `requires_revisit` per
  thresholds; evidence ids = the ids behind the token's latest decision.
- `none-of-these` → analyses `("?",)`, rows `?` with the comment "no candidate
  reading accepted; needs hand review", evidence ids = all candidate evidence.
- `jev_binding(spec, requested_model, exact_model_version, client)` helper
  building a `LiveProviderBinding`.

## TDD gates (RED first)

1. request body: endpoint, bearer header from `TYPESAFE_API_KEY` only, `model`,
   `state` object with column/token/evidence/prior decisions, `questions` with
   exactly `reading` (choice) and `ambiguous` (noul); no schema/gold/evaluator
   data in the state; the state carries no local paths;
2. candidates: auto rows, parallel readings and legacy analyses become
   options; duplicates collapse; `none-of-these` always present; more than
   `max_candidates` fails closed; evidence ids bound per option;
3. answer mapping: chosen first; alternatives above threshold kept in
   probability order; `ambiguous` lowers the threshold; below-threshold options
   dropped; `none-of-these` → `?` row with comment; `evidence_ids` ⊆ supplied
   evidence; `analyses` deduplicated and consistent with `reviewed_rows`;
4. identity: response `model` forwarded verbatim so HARN-022 rejects drift;
   `usage` mapped; missing/invalid answer shapes → `ProviderPermanentError`;
5. reconcile: one request with one noul per token; findings/revisits per
   thresholds; no revisit for tokens below threshold; empty column state
   handled;
6. token estimate: monotone in state size, never zero for a non-empty state;
7. end to end with the HARN-022 runtime and a fake transport: a two-token
   column completes through `run_live_benchmark` with `execution_kind =
   live-provider` semantics, budget accounting from the fake usage, and the
   redacted call artifacts carry no state text.

## Non-goals

Live paid calls in CI; the Python `typesafe-sdk` dependency (the HTTP surface
is small and the harness already owns transport, retries and redaction);
Score-based candidate ranking (later experiment).
