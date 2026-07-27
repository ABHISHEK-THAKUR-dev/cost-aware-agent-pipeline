# Phases

## Phase 0 — Foundations (this repo, done)
- FastAPI service skeleton, config via env vars, structured logging with redaction.
- NIM client wrapper with timeout/retry/backoff.
- 4-step pipeline (PLAN/RETRIEVE/REASON/FORMAT) with `WorkingMemory`-based context scoping.
- Security baseline: rate limiting, input validation, upload safety, generic error handling.
- CI: lint + test + `pip-audit` + `bandit` on every push.
- CD: auto-deploy to staging on merge to `main`; manual promotion to production.
- Docs: this set (prd/architecture/rule/phases/design/memory).

**Exit criteria:** `/v1/query` runs end-to-end against real NIM models, token report shows
the before/after reduction from `docs/memory.md`, CI is green, staging deploy works.

## Phase 1 — Prove quality didn't regress
- Build a fixed eval set (15–25 representative queries with expected answer shape, not exact
  text) and a small script that runs both a "naive" pipeline path and the optimized path,
  diffing outputs.
- Wire the eval script into CI as a required check on any PR touching `app/pipeline.py`,
  `docs/memory.md`, or `docs/rule.md` — token optimizations only merge if the eval still passes.
- Add per-step token/cost dashboards (even a simple logged CSV → chart is enough at this size).

## Phase 2 — Scale-readiness
- Move rate limiting from in-process to Redis-backed, so limits hold under multiple instances.
- Add real retrieval (vector store or real API) behind the same `WorkingMemory` contract —
  the pruning/summarization logic doesn't change, only what feeds it.
- Add request tracing (OpenTelemetry) so `trace_id` correlates across service boundaries, not
  just within one process's logs.
- Load test to find the actual timeout/backoff tuning needed under concurrency (Part 2's
  "intermittent timeout" bucket gets much more likely here — this phase should include
  intentionally reproducing it under load rather than waiting for it in prod).

## Phase 3 — Optional cross-request memory
- Only after Phase 1's eval harness exists: consider session-level memory (remembering prior
  queries in a conversation). This is deliberately gated behind the eval harness because
  memory changes are exactly the kind of change that can silently degrade quality — the
  harness is what catches that instead of a user.
- Requires an explicit data-retention and privacy decision (what's stored, TTL, who can read
  it) before any code — not a "just add a database" afterthought.

## Phase 4 — Production promotion automation
- Replace the manual promote-to-production step with a gated automatic promotion (e.g.,
  staging soak time + smoke tests passing for N minutes → auto-promote), once enough
  confidence exists in the smoke-test coverage to trust it unattended.
- Formalize the rollback plan in `docs/rule.md` into an actual one-command script
  (`./scripts/rollback.sh <previous_tag>`) instead of documented manual steps.

Each phase is intentionally gated on the previous one's exit criteria — the point of writing
this out is that "add memory" or "auto-promote to prod" are dangerous exactly when done before
their prerequisites (an eval harness, a trustworthy smoke test) exist.
