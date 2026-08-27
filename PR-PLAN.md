# Gutenberg Simplifier — PR Plan

Practice project: Haystack + Hayhooks, production-shaped.
Scenario: given a Project Gutenberg book id, produce an age-tiered simplified
version of the story, served over HTTP, with explicit reject behaviour.

## Ordering principle

Deploy through Hayhooks at PR 2 (walking skeleton), not at the end. Every PR
after that ships behind a real HTTP endpoint — no big-bang integration.

Each PR is independently demoable.

---

## PR 0 — Repo scaffolding
- `git init`, `.gitignore` (`.myenv/`, `.env`)
- Pinned deps: `haystack-ai`, `anthropic-haystack`, `hayhooks`
- `src/gutenberg_simplifier/` package layout
- pytest + ruff + mypy, `Makefile`, README stating the scenario
- Delete scratch `src/simple_connect.py`
- `ANTHROPIC_API_KEY` from env only, never hardcoded

**Done when:** `make lint test` green on an empty suite.

## PR 1 — Fetch + boilerplate strip (zero LLM)
- `fetch_book(book_id) -> RawBook`: timeout, retry, typed errors (404 -> `BookNotFound`)
- `max_size` rejection gate BEFORE any expensive work
- `strip_gutenberg_boilerplate()` on `*** START/END OF THE PROJECT GUTENBERG EBOOK ***`
- Checked-in fixtures; the single network test marked + skipped by default

**Done when:** `pytest` green offline; CLI prints line/char counts for a real book.
**Why first:** deterministic layer, and it produces the real line counts needed to
pick chunk size in PR 3.

## PR 2 — Walking skeleton: Haystack pipeline + Hayhooks deploy
- Wrap PR 1 in a real `Pipeline` with a custom `@component` fetcher
- Naive single-prompt simplify (no agent yet)
- `PipelineWrapper(BasePipelineWrapper)` with `setup()` + `run_api()`
- `hayhooks run`, deploy, verify `/docs`, curl it

**Done when:** curl returns simplified text for a real book id.

## PR 3 — Boundary-detection Agent  [centerpiece]
- Deterministic `ChunkReader`: fixed chunks + overlap, absolute line offsets,
  **owns `max_iterations`** (termination guaranteed regardless of model behaviour)
- Haystack `Agent` with one tool `read_next_chunk`
- Structured `BoundaryState`: found_start/start_line/found_end/end_line/reject/
  reject_reason/confidence
- Reject taxonomy as an enum: `CORRUPTED_TEXT`, `INAPPROPRIATE_CONTENT`,
  `NO_STORY_FOUND`, `AMBIGUOUS_BOUNDARIES`, `BUDGET_EXHAUSTED`
- Generator makes judgement only — never reproduces content

**Key test:** a stubbed generator that never terminates still halts. That test is
the answer to "how do you keep an agent loop safe."
**Numbers (chunk size / overlap / max_iterations) decided here**, from PR 1 data.

## PR 4 — Simplification + age tiers + structured output
- Age tier enum, parameterized prompt via `ChatPromptBuilder`
- Chunked simplification over the resolved `[start, end]` range + reassembly
- Versioned result envelope: `status` / `reasons` / `metadata` / `content`
- Token + cost accounting in metadata

## PR 5 — Streaming
- `run_chat_completion_async` + `async_streaming_generator`
- OpenAI-compatible `/v1/chat/completions`
- Document WHAT streams: simplified text streams as tokens; the boundary phase
  emits progress events, not raw model output

**Done when:** `curl -N` streams; a stock OpenAI client connects.

## PR 6 — Failure modes + evals
- Golden fixtures: clean / OCR-garbled / truncated mid-story / oversized /
  non-story (a manual)
- Assert the DECISION (status + reject reason), never the prose
- Fallback: agent failure degrades to naive boundaries with `confidence: low`
  and a warning — not a 500
- Retries with backoff, budget caps

**Done when:** `make eval` prints a pass/fail table.

## PR 7 — Observability + hardening
- Structured JSON logs, trace id threaded end-to-end
- OTel tracing (Hayhooks ships it)
- Metrics: latency, tokens, reject-reason distribution
- Pydantic validation at the edge, payload limits, auth header
- `/health/live` vs `/health/ready` (ready = pipeline loaded AND API key present)

**Done when:** one request produces one trace with a span per component.

## PR 8 — Container + minikube
- Multi-stage Dockerfile, non-root
- Deployment, Service, ConfigMap, Secret for the API key
- Probes wired to PR 7's endpoints, resource requests/limits

**Done when:** the exact curl from PR 2 works against the cluster via port-forward.

## PR 9 (optional) — Concurrency
- `run_api_async`, parallel chunk simplification behind a semaphore, backpressure

---

## Open decisions
- Chunk size / overlap / `max_iterations` — deferred to PR 3, decided from PR 1's
  measured line counts rather than guessed up front
- Age tier boundaries (e.g. 5-7 vs 8-10) — decided in PR 4
- Test book(s) — pick a small public-domain children's book in PR 1; confirm the
  Gutenberg id against the site rather than trusting a remembered number
