# haystack-gutenberg-simplifier

A showcase project: take a book from [Project Gutenberg](https://www.gutenberg.org),
find where the story actually starts and ends, rewrite it for a given reading age,
and serve the whole thing as a production-shaped API with
[Haystack](https://haystack.deepset.ai) and
[Hayhooks](https://github.com/deepset-ai/hayhooks).

It exists to exercise the parts of an LLM pipeline that are awkward in production
rather than the parts that demo well: bounded agent loops, explicit rejection
instead of bad output, streaming, evaluation of decisions, tracing, and deployment.

## Status

Built to **PR 3** of [PR-PLAN.md](PR-PLAN.md). The pipeline deploys behind an
HTTP endpoint, and the boundary-detection agent is implemented and tested. The
agent is not yet wired into the served endpoint — that happens in PR 4, where
simplification runs over the range the agent resolves.

| PR | Scope | State |
|----|-------|-------|
| 0 | Repo scaffolding, lint/type/test gates | done |
| 1 | Fetch + boilerplate strip, zero LLM | done |
| 2 | Haystack pipeline behind a Hayhooks endpoint | done |
| 3 | Boundary-detection agent with a bounded loop | done |
| 4 | Age-tiered simplification, structured output | next |
| 5 | Streaming | |
| 6 | Failure modes and evals | |
| 7 | Observability and hardening | |
| 8 | Container and minikube | |

## Quickstart

```bash
python3 -m venv .myenv
.myenv/bin/pip install -e ".[dev]"
make check
```

Report what a book looks like after boilerplate removal:

```bash
.myenv/bin/python -m gutenberg_simplifier 14838
```

```
book_id                    14838
source_url                 https://www.gutenberg.org/cache/epub/14838/pg14838.txt
size_bytes                 25952
raw_lines                  603
body_lines                 218
body_start_line            30
boilerplate_markers_found  True
body_characters            5866
```

Add `--json` for machine-readable output, `--max-bytes` to change the size budget.
Exit codes are meaningful: `2` not found, `3` over budget, `4` fetch failed.

## Design notes for PR 1

**Reject before you spend.** An oversized book is refused before any LLM call.
Two layers do it: a HEAD request for the advertised size, and a hard cap on the
streamed body.

Measuring against the real site turned up something worth keeping in the notes.
gutenberg.org gzips its plain text, so `Content-Length` reports the *compressed*
size — 9,523 bytes for a book whose decoded text is 25,952 (~2.7:1). The budget
is denominated in decoded bytes, so the gate was comparing two different units.
Other ids return no `Content-Length` at all.

To be precise about the impact: the final decision was never wrong. The
streaming cap counts decoded bytes, so an over-budget gzipped book was still
rejected — just after paying for the full download the gate was supposed to
avoid. What the mismatch actually cost was the gate's whole purpose, plus a
misleading error message on the one path where it did fire (reporting a
compressed number as if it were the book's size). The fetcher now ignores any
advertised length carrying a `Content-Encoding`, so the code no longer claims a
conclusion it cannot draw.

The HEAD is kept — it still rejects unknown ids without transferring a body, and
the gate is correct for origins that report uncompressed lengths — but it is an
optimisation, not the guarantee. The guarantee is the cap. A size gate that
trusts the server to volunteer a comparable number is not a gate.

**Retry only what is worth retrying.** A 404 is a permanent fact about a book id
and is raised immediately, with no second attempt. Timeouts and 5xx get bounded
retries with exponential backoff. Both behaviours are asserted in tests by
counting requests, not by trusting the implementation.

**Line offsets survive stripping.** `BookBody.start_line` records where the body
began in the original download, and `raw_line_number()` translates back. The
boundary agent in PR 3 reports positions against the raw text, so this offset is
load-bearing rather than decorative.

**Missing markers are reported, not raised.** Gutenberg's header markers have
drifted over decades (`THE` vs `THIS`, `EBOOK` vs `ETEXT`) and some texts carry
none. A book without markers is still a book; `markers_found` records the
uncertainty and the pipeline carries on. A *partial* marker set is treated as
untrusted rather than half-believed.

**No `errors="replace"` on decode.** Substituting U+FFFD would manufacture
exactly the garbled-text signal the boundary agent is supposed to detect for
real. UTF-8 is tried strictly, then Latin-1 for older texts.

## Design notes for PR 2

**Components are thin adapters.** Fetching and stripping stay plain functions;
`@component` classes only translate to Haystack's socket model. PR 3's agent
needs the same reader logic outside a pipeline run, so the logic must not live
inside a component.

**Haystack wraps component exceptions.** Every exception a component raises
arrives as a `PipelineRuntimeError` with the original on `__cause__`. A wrapper
catching `BookNotFoundError` around `pipeline.run()` never matches — every
deliberate rejection would have surfaced as a generic 500. `api_errors.unwrap`
walks the cause chain (cycle-safe), and the mapping lives in `src/` rather than
in the wrapper so it can be tested without a server.

**HTTP codes for facts, envelope fields for judgments.** Deterministic failures
map to status codes, because HTTP already has vocabulary for them: 404 unknown
book, 413 over budget, 502 upstream failure, 422 from schema validation. The
structured `status`/`reason` envelope is deliberately *not* used yet — it earns
its place in PR 3, where the agent produces judgments ("boundaries ambiguous",
"text looked corrupted") that no status code expresses honestly.

**The generator is injectable.** `build_simplification_pipeline(generator=...)`
takes a stub in tests, so the full wiring is exercised with no API key and no
network. The HTTP client is injectable for the same reason.

## Design notes for PR 3

**The reader owns termination, not the model.** `ChunkReader` advances the
cursor, counts iterations, and returns `None` once the budget is spent. The
model decides only what it is looking at. Termination is therefore a property
of code with no model in it, and the test that proves it scripts a generator
that asks for another chunk *forever* — the call still returns, with
`BUDGET_EXHAUSTED`, in a bounded number of turns. Two independent stops back
each other: the reader stops serving, and the agent's `max_agent_steps` ceiling
ends the run.

**Two tools, not one.** The design sketched a single `read_next_chunk`. The
decision now comes back through a second `record_decision` tool call, so its
shape is enforced by a JSON schema instead of parsed out of free text, and the
agent gets a precise exit condition.

**A schema cannot catch a confident lie.** The model can call `record_decision`
with `found=true` and no line numbers, an inverted range, a reject reason
outside the taxonomy, or a confidence value that is not a confidence value.
Each of those is normalised into an honest rejection rather than propagating as
bad data — an unrecognised reason becomes `ambiguous_boundaries`, which is the
truthful reading of "refused for something we have no bucket for". Every one of
those paths is a test.

**Rejections are reported, never raised.** `detect_boundaries` always returns a
`BoundaryState`. There is no book it throws on.

### Two costs this design carries, stated plainly

**Novel-length books are refused, not handled.** The iteration budget defaults
to 40 reads (~2,100 lines at the default stride). Peter Rabbit needs 4. Alice
needs 62 and is rejected as `BUDGET_EXHAUSTED`. That is deliberate for a
children's-book pipeline — the alternative is silently spending 60+ model turns
per request — but it is a real limit, not a solved problem. The obvious fix is
scanning inward from both ends rather than a single forward pass, since the
story's end is near the file's end; that is a change to the loop shape and is
not in this PR.

**The agent's context grows with every chunk read.** Haystack's `Agent` keeps
the whole conversation, so turn N carries all N chunks. The original sketch
passed only a small running state forward, which does not grow. Using the
`Agent` abstraction — which is the point of the exercise — costs that. Prompt
caching or a hand-written state-carrying loop are the mitigations, and neither
is implemented here.

## Running the service

Set the key first — `.env` is gitignored:

```bash
cp .env.example .env   # then put your real key in it
export ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY .env | cut -d= -f2-)
.myenv/bin/hayhooks run
```

The pipeline in `pipelines/simplify/` is discovered automatically. Then:

```bash
curl -s -X POST http://localhost:1416/simplify/run -H 'Content-Type: application/json' -d '{"book_id": 14838}'
```

Interactive schema at `http://localhost:1416/docs`.

## Development

```bash
make check         # lint + typecheck + tests
make test          # offline suite only
make test-network  # the one test that hits gutenberg.org, deselected by default
make format
```

The default suite never touches the network: the fetcher is tested end to end
through `httpx.MockTransport`, with no extra mocking dependency.

## Licence

Project code is for demonstration. Books fetched are public domain via Project
Gutenberg and subject to its terms.
