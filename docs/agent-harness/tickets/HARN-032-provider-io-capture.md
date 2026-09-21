# HARN-032 — Opt-in capture of the exact provider request and response

## Problem — 2026-09-21

HARN-005 keeps prompt and response text out of telemetry because the trace
store was not assumed to be private. With a self-hosted, loopback-only Langfuse
that guarantee prevents the one thing a reviewer needs most: seeing exactly what
the model was asked (state, options, instructions) and what it answered.

## Change

- `live_providers.ProviderIOCapture` / `ProviderIORecord`: an in-memory log of
  provider wire exchanges (operation, URL, exact request body, exact response
  body; a failed call records the exception type only). Recorded at the
  `_EnvironmentCredentialClient` HTTP boundary, so every provider client gets it
  by passing `capture=`; headers (credentials) are never recorded. Off unless a
  capture object is supplied.
- `telemetry.ObservationProjection` gains optional `input` / `output`.
- `LangfuseSidecar.capture_io`: true only when `CUC_LANGFUSE_CAPTURE_IO` is
  truthy **and** `LANGFUSE_BASE_URL` is a loopback host (`localhost`,
  `127.0.0.1`, `::1`, `*.localhost`). An unset base URL means the SDK's cloud
  default and never qualifies. When false, `input`/`output` are stripped before
  the SDK call regardless of what the projection carries.
- `wrap_column_review_adapters(..., provider_io=lambda: capture.records)`:
  adjudicate/reconcile observations carry the request bodies as `input` and the
  response bodies as `output` for the exchanges made during that operation
  (failed operations included). Harness objects (evidence records, decisions)
  are still never forwarded; only the wire exchange is.

## Verified

Traced 3-token slice of KTU 1.6 I with capture on: four generations in
`events_full` with 5.0–5.6 KB request bodies (state + `reading` choice with
seven options + `ambiguous` noul) and the raw Jev answers (choice,
probabilities, confidence). `events_core` keeps a 200-character preview; the UI
reads the full text.

## Usage

```bash
source ~/langfuse-local/cuc-langfuse.env
export CUC_LANGFUSE_CAPTURE_IO=1
```

Do not set the flag when `LANGFUSE_BASE_URL` points anywhere but this machine;
the sidecar refuses anyway, but the intent is that licensed evidence text and
model prompts never leave the machine.

## Adversarial review — 2026-09-21

APPROVE; hardening applied in the same PR:

- the wrapper itself drops the `provider_io` source unless `sidecar.capture_io`
  is true, so no duck-typed sidecar can receive wire text without the opt-in;
- `capture_io` is a read-only property derived from the flag and the base URL;
- `*.localhost` is no longer treated as loopback (RFC 6761 is not implemented
  by macOS/glibc resolvers); exact `localhost` / `127.0.0.1` / `::1` only;
- `_is_loopback` never raises on a malformed URL; capture bookkeeping never
  changes what a provider call raises or returns.

Noted: the capture list is unbounded in memory (~1.7 MB for a 300-token column
at real body sizes); no artifact serialises it. A runner should create the
capture only when `sidecar.capture_io` is true. If `TYPESAFE_BASE_URL` ever
carried credentials they would appear in the recorded `url`; headers never do.
