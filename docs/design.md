# Design Notes

## 1. Why NVIDIA NIM

NIM exposes an OpenAI-compatible `/chat/completions` endpoint
(`https://integrate.api.nvidia.com/v1`), so the entire codebase talks to it through the
standard `openai` Python SDK with `base_url` overridden — no NIM-specific SDK lock-in, and
swapping to a self-hosted NIM container later (same API contract) is a one-line config change.

```python
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=settings.nvidia_api_key,
)
```

Model IDs are **not hardcoded** in this repo — set `NIM_MODEL_SMALL` and `NIM_MODEL_LARGE` in
`.env` to whatever's current in your NIM catalog (build.nvidia.com/explore) at deploy time,
since the catalog changes over time and hardcoding a model string is exactly the kind of thing
that silently breaks a pipeline months later. `app/config.py` fails fast at startup if either
is unset, rather than failing confusingly on the first request.

## 2. Tiered model routing (Part 1, technique 2)

| Step | Task shape | Model tier | Why |
|---|---|---|---|
| PLAN | Classify/decide next steps | small | Structured, low-ambiguity decision |
| RETRIEVE | Tool call + summarize | small | Summarization is compression, not deep reasoning |
| REASON | Compare/synthesize/answer | large | The one step that actually needs strong reasoning |
| FORMAT | Reshape into schema | small | Templating, not reasoning |

This alone (before any prompt shrinking) cuts blended cost per query substantially, because 3
of 4 steps run on the cheap tier.

## 3. Reliability patterns (Part 2, built in from the start)

- **Timeouts + retry with backoff** on every NIM call (`app/nim_client.py`), capped retries
  (default 2), jittered backoff — prevents one slow upstream call from becoming a full pipeline
  timeout, and prevents a retry storm from making a rate-limit problem worse.
- **Per-step structured logging** — every step emits one JSON line:
  `{trace_id, step, model, input_tokens, output_tokens, latency_ms, status, error?}`.
  This is the exact tool used in the Part 2 debugging walkthrough: bucket failures by symptom,
  grep by `trace_id`, diff a good run against a bad run step by step.
- **Schema validation at the FORMAT boundary** — a malformed model response is caught server-
  side and logged as `status: malformed_output`, never silently returned to the caller.
- **Idempotency-safe retrieval** — RETRIEVE is a pure function of its input (no side effects),
  so replaying a single step in isolation for debugging is safe and won't duplicate side effects.

## 4. Security implementation notes (maps to `docs/rule.md` §B)

- Rate limiting: in-process token-bucket keyed by API key, swappable for Redis-backed limiting
  if the service is horizontally scaled (noted as a Phase 2 item, not built for a single-
  instance demo).
- Input validation: pydantic v2 models with `Field(max_length=..., pattern=...)` where
  relevant; FastAPI rejects invalid bodies before any handler code runs.
- Secrets: `pydantic-settings` `BaseSettings` reads from environment only; `.env` is git-
  ignored; CI uses GitHub encrypted secrets scoped by environment.
- Dependency scanning: `pip-audit` in CI, Dependabot for update PRs.
- Error handling: single FastAPI exception handler maps every exception type to a generic
  body; full detail goes to the structured logger only.
- Upload safety: extension + MIME allow-list, size cap, server-generated storage filenames,
  no execution/deserialization of upload content.

## 5. What I'd change for real production scale (explicitly deferred, see `docs/phases.md`)

- Redis or a managed rate limiter instead of in-process (needed once you run >1 instance).
- A vector-store-backed RETRIEVE step instead of the mocked retrieval in this repo.
- Streaming responses (SSE) so REASON's output starts rendering before FORMAT finishes.
- An actual eval harness wired into CI (not just run manually) so a token-optimization PR
  can't merge without a quality-regression check passing.
