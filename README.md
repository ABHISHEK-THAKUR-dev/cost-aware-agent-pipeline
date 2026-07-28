# Cost-Aware Agent Pipeline

A small, real FastAPI service demonstrating three things together: **token/cost-aware agent
design**, **debuggable multi-step pipelines**, and **disciplined CI/CD** — built on the
NVIDIA NIM API. This was built for a technical interview assignment; every claim below is
backed by code in this repo and a real, live deployment, not just prose.

**Live staging:** `https://cost-aware-agent-pipeline.onrender.com` — try
`https://cost-aware-agent-pipeline.onrender.com/docs` directly.
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

Measured against a live NIM call (not just mocked), a full 4-step run against a supplied
document came in at **684 total input tokens** — see the before/after table in
`docs/memory.md` §5 for the full breakdown and the quality tradeoff of each change (mainly:
summarization can drop a fine detail the summarizer didn't flag, mitigated by the on-demand
raw-context escape hatch rather than deleting it outright).

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

### Real debugging log from building this

The process above isn't hypothetical — building and deploying this exact repo required working
through several real, live issues, each isolated the way `docs/design.md` describes: read the
actual error first, form one specific hypothesis, test it, move on only once ruled out.

| Symptom | Root cause | Fix |
| --- | --- | --- |
| `502` on `/v1/query`, generic error to client | Wrong NIM model ID (upstream returned 404) | Confirmed the exact model ID from NVIDIA's own catalog page instead of guessing |
| Request hung 190+ seconds before failing | Two retry layers stacked — the OpenAI SDK's own retries plus this repo's manual retry loop | Set `max_retries=0` on the SDK client so only one retry layer runs |
| CI failed: `ModuleNotFoundError: No module named 'app'` | `pytest` invoked directly instead of `python -m pytest`, so the project root wasn't on the import path | Changed CI's test step to `python -m pytest` |
| Docker build failed: `repository name must be lowercase` | GHCR image tags were built from `github.repository`, which includes the (mixed-case) GitHub username | Lowercased the repo name in a dedicated workflow step before tagging |
| Deploy hook call failed with HTTP 405 | Used `curl -X POST`; Render's deploy hooks expect `GET` | Removed `-X POST` — curl defaults to GET |
| Workflow failed to even parse: `error in your yaml syntax` | An echo string got split across two lines during a manual edit, breaking YAML's block-literal indentation | Rewrote the step as a single-line string |

## Part 3 — CI/CD (see `.github/workflows/`)

- `ci.yml` — on every push: ruff lint, bandit static security scan, pip-audit dependency scan,
  full test suite. All four gate merges; none are advisory-only.
- `deploy-staging.yml` — triggers after CI succeeds on `main`, builds a Docker image tagged
  with the commit SHA (this tag *is* "last known good" for rollback), pushes to GHCR, triggers
  a real deploy on Render via a deploy hook, waits for rollout, then smoke-tests `/v1/health`
  against the live staging URL. This is a real, live deployment — see the staging URL at the
  top of this README.
- `main` is protected by a GitHub ruleset: pull requests are required, the `CI` status check
  must pass before a PR can merge, force pushes are blocked, and branch deletion is restricted.
  Every fix from a certain point in this repo's history went through that PR flow, visible in
  the commit and PR history on GitHub — including every fix in the debugging log above.
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
| --- | --- |
| Rate limiting | `app/security.py::TokenBucketLimiter`, per-API-key, 429 + `Retry-After` |
| Input validation | `app/schemas.py` (pydantic), reject-not-sanitize |
| Secrets | `app/config.py` (env-only), `.env` git-ignored, GH Actions encrypted secrets scoped per environment |
| Dependency vulnerabilities | `pip-audit` in CI, Dependabot weekly PRs — **caught and fixed real CVEs during this build** (see below) |
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

Both `/v1/upload`'s accept and reject paths were verified live: a `.txt` file uploads and
stores successfully (200), a `.jpg` file is rejected before it ever reaches disk (415).

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
python -m pytest tests/ -v
python -m ruff check app/ tests/
python -m bandit -r app/
python -m pip_audit -r requirements.txt
```

## What I'd do next

See `docs/phases.md` in full, but top of the list: wire a fixed eval set into CI so any change
to `app/pipeline.py` or the memory/pruning logic is quality-gated automatically, not just
token-gated — a cost win that quietly breaks answer quality isn't actually a win. After that,
add code owners / required reviewers to the branch ruleset once this is a team project rather
than a solo one, and formalize the rollback plan into an actual one-command script rather than
documented manual steps.
