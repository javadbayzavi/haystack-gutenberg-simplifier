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

Built to **PR 7** of [PR-PLAN.md](PR-PLAN.md). Two surfaces are deployed — a
JSON endpoint returning a versioned envelope, and an OpenAI-compatible chat
endpoint that streams — behind health checks, bearer auth, Prometheus metrics
and OpenTelemetry tracing, with a golden-set eval harness and a degradation
policy for boundary searches that do not converge.

| PR | Scope | State |
|----|-------|-------|
| 0 | Repo scaffolding, lint/type/test gates | done |
| 1 | Fetch + boilerplate strip, zero LLM | done |
| 2 | Haystack pipeline behind a Hayhooks endpoint | done |
| 3 | Boundary-detection agent with a bounded loop | done |
| 4 | Age-tiered simplification, structured output | done |
| 5 | Streaming | done |
| 6 | Failure modes and evals | done |
| 7 | Observability and hardening | done |
| 8 | Container and minikube | next |

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

## Design notes for PR 4

**Tiers carry guidance, not an age number.** "Rewrite this for a 7 year old"
leans on whatever the model believes about 7 year olds. `tiers.py` names the
sentence length, vocabulary and structure directly, which is both more reliable
and reviewable — a children's librarian can read that file and tell you it is
wrong, which they could not do with an integer.

**Segments break at paragraphs and carry continuity.** Cutting mid-paragraph
hands the model half a scene and gets half a rewrite, so segmentation packs
whole paragraphs; a paragraph longer than the limit is emitted whole rather
than severed. Each call also receives the tail of the previous *rewritten*
output, so names and tense survive a seam. That makes the loop sequential and
therefore slower — parallelising it is PR 9, and would need a different
continuity mechanism than "what you just wrote".

**The PR 2 error split paid off here.** Deterministic failures are HTTP codes:
404 unknown book, 413 over budget, 422 bad tier, 502 upstream. A book the
*agent* refused answers **200 with `status: rejected`** and a reason — the
request succeeded and reached an honest conclusion, and calling that 4xx would
tell a client its request was malformed when it was not.

**Unknown cost is null, not zero.** `estimated_cost_usd` is `None` for a model
with no price on file. A silent `0.0` reads as "this was free", which is the
opposite of "we do not know".

**A rejected book costs zero rewrite calls.** The simplifier short-circuits on
a refusal, and a test asserts no model call was made — not merely that the
output was empty.

**`schema_version` on every response.** Consumers of an LLM pipeline break on
shape changes far more often than content changes; this is the cheapest way to
let a client notice.

## Design notes for PR 5

**What streams, and what does not.** The prose streams token by token — that is
the part a reader waits on. The phases before it emit *progress lines*, never
model output. The boundary agent is reasoning about a book, not writing one;
streaming its chatter would leak half-formed judgments and fragments of source
text it was explicitly told not to reproduce. So those phases report **that**
they are happening, never what the model is saying. Two tests assert exactly
that: the agent's own notes never appear in the stream, and neither does the
source text.

**A stream cannot return a status code.** The moment the first byte leaves, the
response is 200. So every failure after that point is written as a sentence
instead. This is not a workaround — the chat surface talks to a person and the
REST surface talks to a program, and each gets the failure form it can use. The
same book that answers `413` on `/simplify/run` answers with a readable line
here.

**A failing model is handled per phase, and the two differ.** A boundary-phase
failure degrades to "I could not read this book just now" — nothing of value
has streamed yet. A failure *during* the prose is different: re-raising would
drop the connection and leave a partial story that reads exactly like a
finished one, so the stream says it stopped early and names the part. Both log
the real cause; neither shows it to the reader.

**Abandonment cancels the call.** Readers close tabs mid-story — that is the
normal case, not an exceptional one. The streaming helper cancels the in-flight
model call rather than leaving it billing tokens into a closed socket, and a
test asserts the cancellation actually happened.

**Chat parsing is where these surfaces break.** "read me 14838 for a 5 year
old" contains two numbers, and picking wrong silently fetches the wrong book.
Ages are consumed by explicit patterns first, an explicitly labelled id always
wins, and whatever survives is the id. `test_chat.py` covers the phrasings
people actually type, including the ones that must be refused.

## Design notes for PR 6

**Refusals of content stand; failures of process degrade.** This is the whole
fallback policy. `no_story_found`, `corrupted_text` and
`inappropriate_content` are *judgments about the book* — overriding one ships
exactly the output the refusal existed to prevent, so they are never touched.
`budget_exhausted` and `ambiguous_boundaries` say nothing about the book; they
say the search did not converge. Those degrade to the whole de-boilerplated
body, marked `confidence: low`, with `boundary_fallback_applied: true` in the
metadata. A silent fallback would make a guessed range indistinguishable from a
located one, so it is flagged in the response and stated in the notes.

**The evals assert decisions, never prose.** Asserting rewritten text tests the
model's word choice, which drifts between runs and model versions and tells you
nothing about whether the pipeline works. What must hold is that a manual is
refused, a garbled scan is refused, and a clean story is not.

**Expectations are sets, not single values.** A story that stops mid-sentence
may honestly be reported as ambiguous *or* accepted with the text that exists.
Encoding "either of these, but never `corrupted_text` and never
`no_story_found`" is more useful than inventing one right answer and loosening
the test later when it fails for a good reason.

**Evals run with the fallback disabled.** The fallback turns a non-converging
search into an accepted low-confidence result — correct for serving a request,
wrong for measuring the agent, since it would quietly convert every ambiguous
case into a pass.

**`make eval` refuses to run without a key.** An eval that mocks the thing being
evaluated is a regression test in a costume; it would print a green table that
means nothing. `--dry-run` exists to check the harness itself and says on every
run that it proves nothing about the model.

**The harness is tested for detecting failure.** A harness that always reports
PASS is worse than none, so `tests/test_evals.py` asserts that a wrong answer
fails, a crashing case fails without aborting the run, and the table names what
broke.

## Design notes for PR 7

**Liveness and readiness answer different questions.** *Live* means the process
is not wedged — if it fails, restarting helps. *Ready* means this instance can
serve: pipeline deployed **and** API key present. A missing key must fail
readiness, never liveness — restarting a container cannot conjure a secret, so
failing liveness there produces an endless crash loop while failing readiness
correctly takes the pod out of rotation and leaves it alone. `/health/ready`
reports each check separately, because a single boolean will not tell you which
half broke at 3am.

**Health is exempt from auth; `/metrics` is not.** A kubelet has no
credentials, so gating health makes every pod permanently unready. `/metrics`
stays behind the token because it reveals request volume and spend.

**Content never enters a trace.** Haystack's `set_content_tag` attaches queries
and answers to spans and is off unless an env var enables it. Here it is
overridden to an unconditional no-op: the "content" is the body of a book and
the prose rewritten from it, and a trace backend is the wrong place for either.
Non-scalar tags are summarised (`<list len=5000>`), never dumped.

**We replace Hayhooks' request-id middleware, not supplement it.** Hayhooks
mints a fresh id per request and ignores an inbound `X-Request-ID`, which
breaks correlation as soon as a caller upstream has assigned one. Ours runs
outermost and takes precedence.

**Tracing setup order is load-bearing.** Hayhooks calls its own
`configure_tracing()` inside `create_app()`, and that call no-ops *only when
tracing is already enabled*. Ours must run first or its OTLP bootstrap wins and
our tracer is silently never used. A test asserts our tracer survives — Hayhooks
wraps it in a live-buffer proxy rather than replacing it, so the assertion
follows the wrapper chain rather than checking identity.

**`reject_reason` is a metric label for a reason.** A rise in `no_story_found`
means people are requesting the wrong books; `budget_exhausted` means the
iteration budget is mistuned; `corrupted_text` means Gutenberg served something
odd. Three different responses to what would otherwise look like one rejection
rate going up. Label values come from closed enums only — an unbounded label
turns a metrics backend into a memory leak.

## Operating

```bash
make serve   # the full app: pipelines + health + metrics + auth
```

| Endpoint | Auth | Purpose |
|---|---|---|
| `/health/live` | none | process is running |
| `/health/ready` | none | pipeline loaded and API key present |
| `/metrics` | bearer | Prometheus exposition |
| `/simplify/run` | bearer | JSON envelope |
| `/v1/chat/completions` | bearer | streaming chat |

Auth is **off** unless `GUTENBERG_API_TOKEN` is set. Traces export only when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set; without it spans are still created, so
instrumentation behaves identically whether or not a collector is reachable.

A single request produces one trace: a `haystack.pipeline.run` span, a
`haystack.component.run` span per component, and nested `haystack.agent.step`
spans for the boundary loop — every one stamped with the request id.

## Evaluating

```bash
make eval-dry   # harness check; proves nothing about the model
make eval       # the golden set against a real model; needs ANTHROPIC_API_KEY
```

The golden set lives in `evals/fixtures/`: a clean story, one buried under
dedications and a contents page, a garbled scan, a technical manual, and a
story that stops mid-sentence.

## Streaming surface

The chat endpoint is OpenAI-compatible, so any OpenAI client or a UI like
open-webui can point at it. `model` is the pipeline name:

```bash
curl -sN -X POST http://localhost:1416/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"simplify","messages":[{"role":"user","content":"simplify 14838 for a 5 year old"}],"stream":true}'
```

Accepted phrasings include a bare id, `book 14838`, `14838 preschool`,
`14838 for a 7 year old`, `14838 aged 9`, `14838 for my 6yo`. Without an age or
tier it defaults to `early_reader`.

## Running the service

Set the key first — `.env` is gitignored:

```bash
cp .env.example .env   # then put your real key in it
export ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY .env | cut -d= -f2-)
.myenv/bin/hayhooks run
```

The pipeline in `pipelines/simplify/` is discovered automatically. Then:

```bash
curl -s -X POST http://localhost:1416/simplify/run -H 'Content-Type: application/json' -d '{"book_id": 14838, "tier": "preschool"}'
```

`tier` is one of `preschool` (3–5), `early_reader` (6–8), `middle_grade` (9–11).

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
