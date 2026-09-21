# HARN-031 — Provider model and usage on parsing observations

## Problem — 2026-09-21

After the first traced Jev run the local Langfuse showed "Model costs $0.00".
The HARN-005 sidecar emitted adjudicate/reconcile observations as plain `agent`
/ `span` spans with no `model` and no `usage_details`, and Langfuse had no price
definition for `jev-1.13.0`. Langfuse prices only `generation` observations that
name a model matching a definition and carry usage.

## Change

- `telemetry.ObservationProjection` gains optional `model` and
  `usage {input, output}` (validated, defaulted, backward compatible).
- `langfuse_sidecar.wrap_column_review_adapters(adapters, sidecar,
  provider_calls=None)`: when a zero-argument source of HARN-022
  `ProviderCallArtifact`s is given (e.g. `lambda: runtime.calls`), the
  adjudicate and reconcile wrappers attribute the calls made during that
  operation: the observation becomes a `generation` with the exact model of the
  last successful call, the summed input/output usage of the successful calls,
  and `provider_calls` / `provider_retries` counts in metadata. Failed
  operations carry counts but no usage. Without the source nothing changes.
- No prompt or response text is forwarded; usage and model identity only.
- Pricing stays in Langfuse, not in the harness: the local instance holds a
  model definition `jev-1.13.0` matching `jev-1.13.0|jev-latest|jev-preview`,
  $0.042 per 1M input tokens, output $0 (docs.typesafe.ai/models, 2026-09-21).

## Verified

Traced 3-token slice of KTU 1.6 I: four generations in ClickHouse
(`events_core`) with `provided_model_name = jev-1.13.0`, usage per call and
`total_cost` summing to $0.000348 = 8,295 input tokens × $0.042/M.

## Follow-up

The live benchmark runtime builds its adapters internally; wiring the sidecar
(and `provider_calls`) into `run_live_benchmark` / the 028b CLI is part of
028b so whole-column runs are traced and priced without a hand-built graph.
