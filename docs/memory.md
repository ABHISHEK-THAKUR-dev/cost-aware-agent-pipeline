# Memory / Context Management

This is the core of the token-cost fix from Part 1. "Memory" here means: what does each
pipeline step actually see, and who decides that.

## 1. The baseline problem

The naive pattern that produced ~100K input tokens/query:

```python
history = []
for step in steps:
    history.append(call_model(system_prompt + history + step_prompt))
    # every step re-sends the FULL transcript so far, plus full raw tool output
```

By step 4, you're paying for steps 1–3's entire raw output *again*, every time, including
verbose tool responses the later steps never actually needed in full.

## 2. This project's model: a `WorkingMemory` object, not a transcript

`app/pipeline.py` holds a single `WorkingMemory` dataclass for the life of one request:

```python
@dataclass
class WorkingMemory:
    query: str
    plan: str | None = None          # short, from PLAN step
    retrieved_summary: str | None = None   # PRUNED, from RETRIEVE step
    reasoning_result: str | None = None    # from REASON step
    full_retrieved_raw: str | None = None  # kept OUT of prompts, available on demand
```

Each step's prompt is built from **only the fields it declares it needs**, not the whole
object and not the raw history. `RETRIEVE`'s raw output is summarized into
`retrieved_summary` immediately and the raw payload is kept server-side
(`full_retrieved_raw`) — available if a later step explicitly asks for more detail via a
`need_more_context` flag, but not sent by default.

## 3. Three techniques, each documented with its tradeoff

| Technique | What it does | Token effect | Quality tradeoff |
|---|---|---|---|
| **Field-scoped prompts** | Each step gets only its declared inputs, not full history | Cuts cross-step duplication almost entirely | None if fields are chosen correctly; risk is under-scoping (a step missing something it needed) — mitigated by the `need_more_context` escape hatch |
| **Summarize-on-write** | RETRIEVE output is summarized to ~200–400 tokens immediately, not carried raw | Biggest single win — raw tool output was the largest chunk of the 100K | Small risk of losing a fine detail the summarizer didn't flag as important — mitigated by keeping raw available on demand, not deleted |
| **Tiered model + prompt caching** | Cheap model for PLAN/FORMAT; static system prompt/tool schemas sent as a cacheable prefix | Reduces cost per token and total tokens billed as "new" | None on output quality if step boundaries are correct — this is a routing/infra change, not a content change |

## 4. What's explicitly NOT kept in memory (v1)

- No cross-request memory — every `/v1/query` call is stateless. This is a deliberate
  Part-1-scope decision (see `docs/prd.md` Non-goals) — cross-session memory adds a whole
  separate cost/privacy surface (what do you store, for how long, who can read it) that's out
  of scope for a token-optimization exercise. `docs/phases.md` has it as a Phase 3 candidate,
  with the retrieval-cost tradeoff called out explicitly before it's built.
- No silent truncation. If something has to be dropped to fit a budget, it is summarized, and
  the fact that it was reduced is recorded in the step's log line — a debugger should never
  have to guess whether "missing detail" was a summarization choice or a bug.

## 5. Before/after (illustrative, one sample query)

Query: *"Compare the refund policies of these two uploaded vendor contracts and flag conflicts."*

| | Naive (before) | This design (after) |
|---|---|---|
| PLAN step input | n/a (no plan step) | ~600 tokens (query + rules) |
| RETRIEVE step input/output | full contracts re-sent at every later step (~40K tokens each occurrence) | contracts summarized once to ~500 tokens each, raw kept server-side |
| REASON step input | full history + full raw contracts (~90K tokens) | plan + retrieved summaries (~2.5K tokens) |
| FORMAT step input | full transcript again (~95K+ tokens) | reasoning result only (~1K tokens) |
| **Total input tokens** | **~100K** | **~5–7K** typical, worst case ~15K if `need_more_context` fires |

The eval-set quality check (see `docs/phases.md`) is what turns "we think this is fine" into
"we measured it's fine" — token cuts without an eval pass are a guess, not a result.
