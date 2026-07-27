# Cost-Aware Agent Pipeline

A small, real FastAPI service demonstrating three things together: **token/cost-aware agent
design**, **debuggable multi-step pipelines**, and **disciplined CI/CD** — built on the
NVIDIA NIM API. This was built for a technical interview assignment; every claim below is
backed by code in this repo, not just prose.

```
docs/
  prd.md            what this is and why, success metrics
  architecture.md    system design, request flow, trust boundaries
  rule.md            agent operating rules + security rules (rate limit, validation,
                      secrets, dependency scanning, error handling, upload safety)
  design.md          NIM integration details, tiered-model routing, reliability patterns
  memory.md          the actual token-optimization design (Part 1 of the assignment)
  phases.md          what's built vs. deliberately deferred, and why
app/                 the service itself
tests/               15 tests covering pipeline behavior, security, and the API layer
.github/workflows/   CI (lint/test/scan) and CD (build + deploy to staging)
```

Read `docs/` in that order — `prd.md` → `architecture.md` → `memory.md` → `rule.md` →
`design.md` → `phases.md` — it's written to build up the picture piece by piece, the same way
I'd walk through it on a call.

## Part 1 — Token/cost optimization (see `docs/memory.md`)

The naive version of this pipeline replays full conversation history and raw tool output at
every step — that's the ~100K-token baseline described in the assignment. Two concrete fixes,
both implemented in `app/pipeline.py`:

1. **Field-scoped working memory instead of a growing transcript.** Each step pulls only the
   specific fields it declares it needs from a `WorkingMemory` object, not the full history.
2. **Summarize-on-write for retrieved/tool content.** Raw retrieval output is summarized to
   ~100 words immediately; the raw payload is kept server-side and only pulled back in if a
   step explicitly asks for more (`need_more_context`), so it isn't paid for by default.
3. **(Bonus) Tiered model routing.** PLAN/RETRIEVE/FORMAT run on a small NIM model; only REASON
   uses the larger one — cost drops even before token count does.

Measured with a mocked NIM client (`tests/test_pipeline.py`), a query against a large supplied
document goes from "raw content repeated at every step" to "summarized once, referenced after"
— see the before/after table in `docs/memory.md` §5 for the full breakdown and the quality
tradeoff of each change (mainly: summarization can drop a fine detail the summarizer didn't
flag, mitigated by the on-demand raw-context escape hatch rather than deleting it outright).

`tests/test_pipeline.py::test_raw_retrieved_content_not_sent_to_reason_step` is a regression
guard specifically for this — it fails if someone reintroduces the "just concatenate
everything" pattern later.

## Part 2 — Debugging (see `docs/design.md` §3 and `docs/architecture.md` §2)

The debugging process this repo is built to support:

1. **Bucket by symptom first** (timeout / malformed output / silent wrong data) — they're
   usually different root causes, not one bug.
2. **Structured, per-step logs with a shared `trace_id`** (`app/logging_conf.py`) — every step
   logs model, input/output tokens, latency, and status as one JSON line, so a failed request
   can be diffed against a good one step-by-step instead of guessed at.
3. **Timeouts + bounded retry with backoff** on every NIM call (`app/nim_client.py`) so one
   slow upstream call doesn't cascade into a full pipeline timeout.
4. **Schema validation at the FORMAT boundary** — malformed model output is caught and logged
   server-side (`status: malformed_output`-shaped errors via `PipelineError`), never silently
   returned to the caller.
5. **No silent failure paths** — every exception has exactly one place it's turned into a
   response: `app/errors.py`. There's no code path where an error is swallowed and a default
   value returned instead, which is usually how "silently succeeds with wrong data" happens.

## Part 3 — CI/CD (see `.github/workflows/`)

- `ci.yml` — on every push: ruff lint, bandit static security scan, pip-audit dependency scan,
  full test suite. All four gate merges; none are advisory-only.
- `deploy-staging.yml` — triggers after CI succeeds on `main`, builds a Docker image tagged
  with the commit SHA (this tag *is* "last known good" for rollback), pushes to GHCR, deploys
  to staging, then smoke-tests `/v1/health`.
- **Production is not auto-deployed.** Promotion is a deliberate manual step that re-tags the
  already-smoke-tested staging image — see `docs/phases.md` Phase 4 for why, and `docs/rule.md`
  for the rollback plan this makes possible.
- **Secrets**: GitHub Actions encrypted secrets, scoped per `environment` (`staging` /
  `production`), never hardcoded, never logged (`app/logging_conf.py` redacts anything
  key/token/secret/password-shaped before a log line is emitted).
- **Rollback — first 5 minutes:** re-deploy the previous commit-SHA-tagged image (already
  built, already smoke-tested), confirm `/v1/health` and error rate recover, *then* investigate
  root cause with prod stable. This only works because every staging deploy is an immutably
  tagged artifact, not a floating `latest`.

## Security (see `docs/rule.md` §B — all implemented, not just described)

| Requirement | Where |
|---|---|
| Rate limiting | `app/security.py::TokenBucketLimiter`, per-API-key, 429 + `Retry-After` |
| Input validation | `app/schemas.py` (pydantic), reject-not-sanitize |
| Secrets | `app/config.py` (env-only), `.env` git-ignored, GH Actions encrypted secrets |
| Dependency vulnerabilities | `pip-audit` in CI, Dependabot weekly PRs — **caught and fixed two real CVEs during this build** (see below) |
| Error handling / info leakage | `app/errors.py` — generic bodies + `trace_id`, no stack traces to client |
| File upload safety | `app/security.py::validate_and_store_upload` — extension allow-list, size cap enforced during streaming read, server-generated filenames (no path traversal), no execution/deserialization of content |

Worth calling out on the call: `pip-audit` in CI isn't decorative here — running it while
building this repo flagged 14 known CVEs across `starlette` and `python-multipart` in the
initially-pinned versions. Bumped both to patched versions (see `requirements.txt`), re-ran
the audit, and it came back clean. That's the workflow working as intended, not a hypothetical.

`bandit` also flagged two real findings during the build: a hardcoded `/tmp` path (fixed by
making the upload directory configurable, not hardcoded) and use of `random` for retry jitter
(swapped to `secrets.randbelow`, since consistent tooling here avoids the false-flag even
though retry jitter has no real security sensitivity).

## Running it

```bash
cp .env.example .env
# fill in NVIDIA_API_KEY and pick model IDs from https://build.nvidia.com/explore
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

```bash
curl -X POST localhost:8000/v1/query \
  -H "content-type: application/json" \
  -H "x-api-key: <matches API_KEYS in .env, if set>" \
  -d '{"query": "What is the refund window?", "context_documents": ["Refunds accepted within 30 days of purchase."]}'
```

Tests (no real NIM key needed — every model call is mocked):

```bash
pytest tests/ -v
ruff check app/ tests/
bandit -r app/
pip-audit -r requirements.txt
```

## Wiring up a real deploy target

`deploy-staging.yml` builds and pushes a tagged image to GHCR; the actual "deploy this image
somewhere" step is left as a documented placeholder because the target platform (Fly, ECS,
Render, a VM, etc.) wasn't specified for this exercise. Swapping in a real target is a two-line
change: replace the placeholder `run:` block with your platform's CLI/API call referencing the
same `needs.build-and-push.outputs.image_tag`, and point the smoke-test `curl` at your real
staging URL.

## What I'd do next

See `docs/phases.md` in full, but top of the list: wire a fixed eval set into CI so any change
to `app/pipeline.py` or the memory/pruning logic is quality-gated automatically, not just
token-gated — a cost win that quietly breaks answer quality isn't actually a win.
