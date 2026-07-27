# Architecture

## 1. High-level flow

```
Client
  │  POST /v1/query {query}
  ▼
FastAPI app (app/main.py)
  │  ── rate limit check (app/security.py)
  │  ── input validation (app/schemas.py, pydantic)
  ▼
Pipeline orchestrator (app/pipeline.py)
  │
  ├─ Step 1: PLAN     → small/cheap NIM model → decides which steps are needed
  ├─ Step 2: RETRIEVE  → tool/data fetch (mocked in this repo) → raw output PRUNED
  │                       before it re-enters context (see docs/memory.md)
  ├─ Step 3: REASON    → larger NIM model, sees only pruned/summarized context
  └─ Step 4: FORMAT    → small/cheap NIM model → final schema-validated answer
  │
  ▼
Structured logger (app/logging_conf.py) — every step logs:
  trace_id, step name, input tokens, output tokens, latency_ms, model used, status
  ▼
Response: {answer, trace_id, token_report, step_trace}
```

## 2. Why this shape

- **Tiered models per step.** PLAN and FORMAT are cheap/structured tasks (classification,
  templating) — routed to a small NIM model. REASON is the only step that needs a large model.
  This alone cuts cost even before touching token counts (see `docs/design.md` §Optimizations).
- **Context does not accumulate raw.** Each step receives a *summary* of prior steps, not the
  full transcript. The orchestrator owns a "working memory" object (see `docs/memory.md`) and
  decides what each step actually needs, instead of concatenating everything by default.
- **NIM client is isolated.** `app/nim_client.py` is the only file that talks to
  `https://integrate.api.nvidia.com/v1`. Everything else calls it through a typed interface, so
  swapping providers or models later doesn't touch pipeline logic.
- **Structured logs, not print statements.** Every step emits one JSON log line with a shared
  `trace_id`. This is what makes Part 2-style debugging (intermittent timeout / malformed
  output / silent wrong data) tractable — you can `grep trace_id` and see the exact step, model,
  token counts, and latency where things diverged.

## 3. Components

| Component | File | Responsibility |
|---|---|---|
| API layer | `app/main.py` | Routes, request/response wiring, exception handlers |
| Config | `app/config.py` | Loads env vars via pydantic-settings, never hardcodes secrets |
| Security | `app/security.py` | Rate limiting, upload validation, header hardening |
| Schemas | `app/schemas.py` | Pydantic request/response models — first line of input validation |
| NIM client | `app/nim_client.py` | OpenAI-compatible client pointed at NIM, retry/backoff, timeout |
| Pipeline | `app/pipeline.py` | Orchestrates PLAN → RETRIEVE → REASON → FORMAT, owns working memory |
| Logging | `app/logging_conf.py` | Structured JSON logging with correlation IDs |
| Errors | `app/errors.py` | Central exception → HTTP mapping, no stack traces to client |

## 4. Deployment shape

```
GitHub push ──► CI (lint, test, pip-audit, bandit)
                     │ pass
                     ▼
merge to main ──► CI re-run ──► build ──► deploy to STAGING ──► smoke test /v1/health
                                                    │
                                          manual promote (documented, not automatic)
                                                    ▼
                                                PRODUCTION
```

Staging is auto-deployed on merge to `main`. Production promotion is a deliberate, separate
step (see `docs/phases.md` and the CI/CD workflow) — this is the gate that makes the rollback
plan in `docs/rule.md` actually work, because "last known good" is always an explicit,
tagged artifact.

## 5. Data flow and trust boundaries

- Client input never reaches the LLM prompt unvalidated — it passes through `schemas.py`
  (type/length/shape checks) before the pipeline sees it.
- Tool/retrieval output is treated as untrusted content, not instructions — it's inserted into
  prompts inside a clearly delimited, labeled block, never concatenated into the system prompt.
- Model output going back to the client is schema-validated (Step 4 output) before being
  returned, so a malformed model response fails loudly server-side instead of leaking to the
  caller as-is.
