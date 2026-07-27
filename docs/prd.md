# PRD — Cost-Aware Agent Pipeline

## 1. Problem
A multi-step agentic pipeline (plan → retrieve → reason → format) built on NVIDIA NIM-hosted
LLMs was burning ~100K input tokens per query because it replayed full history and raw tool
output at every step. It worked, but was slow and expensive at scale, and failures inside the
chain (timeouts, malformed JSON, silently wrong data) were hard to diagnose because there was
no per-step observability.

## 2. Goal
Ship a small, production-shaped service that:
- Answers a user query through a 3–4 step agent pipeline using NVIDIA NIM models.
- Cuts token usage per query by ≥60% versus the naive "replay everything" baseline, without
  a measurable drop in answer quality on a fixed eval set.
- Makes intermittent pipeline failures debuggable in minutes, not hours, via structured
  per-step logs and correlation IDs.
- Is safe to expose on the internet: rate limited, input-validated, no secret or stack-trace
  leakage, dependency-scanned, and (if file upload is enabled) upload-safe.
- Deploys via CI/CD with a documented rollback path.

## 3. Non-goals
- Not building a general-purpose agent framework. This is a reference implementation of the
  patterns, sized for an interview/demo repo, not a product.
- Not implementing full multi-tenant auth/billing. A single API-key-gated service is enough
  to demonstrate the patterns; auth is stubbed with one clear extension point.
- Not fine-tuning or hosting models. NIM is used as a hosted inference API only.

## 4. Users
- Primary: a developer/operator running this as a backend service behind a frontend or CLI.
- Secondary (for this exercise): an interviewer reading the repo to evaluate engineering
  judgment on cost, reliability, and deployment discipline.

## 5. Core user story
"As a caller, I POST a natural-language query to `/v1/query`. The service plans the steps
needed, calls NIM-hosted models to execute them (using the cheapest model that can do each
step correctly), prunes/summarizes intermediate context instead of replaying it in full, and
returns a final answer plus a token/cost report for that request."

## 6. Requirements

### Functional
- `POST /v1/query` — runs the pipeline end-to-end, returns answer + per-step trace + token
  usage summary.
- `GET /v1/health` — liveness/readiness for deploy checks and rollback decisions.
- `POST /v1/upload` — optional, gated, for pipelines that need a document as input; enforces
  the file-upload-safety rules in `rule.md`.
- Every response includes a `trace_id` that maps to structured logs for that request.

### Non-functional
- p50 latency budget: under 6s for a 3-step query against a mid-size NIM model.
- Token budget: ≤35K input tokens per query at steady state (down from ~100K baseline).
- Availability: staging deploy on every merge to `main`, gated by tests + lint + security scan.
- Security: see `docs/rule.md` §Security Rules — rate limiting, input validation, secret
  handling, dependency scanning, error handling, upload safety are all mandatory, not optional.

## 7. Success metrics
| Metric | Baseline | Target |
|---|---|---|
| Input tokens/query | ~100,000 | ≤35,000 |
| Answer quality (eval set, pass rate) | 100% (reference) | ≥95% of reference |
| p50 latency | unmeasured | <6s |
| Mean time to isolate a pipeline failure | unmeasured (ad hoc) | <10 min using logs alone |
| Deploy-to-rollback time on a bad prod deploy | unmeasured | <5 min |

## 8. Out of scope for v1
- Multi-user auth/roles, persistent conversation memory across sessions, streaming responses,
  and model fine-tuning are explicitly deferred — see `docs/phases.md` for what's next.
