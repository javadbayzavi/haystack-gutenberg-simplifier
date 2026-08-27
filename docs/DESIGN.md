# Design notes

Why this is built the way it is. Organised by concern.

## Rejecting before spending

An oversized book is refused before any model is called. Two layers do it: a HEAD
request for the advertised size, and a hard cap on the streamed body.

Measuring that against the real site was instructive. gutenberg.org gzips its
plain text, so `Content-Length` reports the *compressed* size — 9,523 bytes for a
book whose decoded text is 25,952 (~2.7:1). The budget is denominated in decoded
bytes, so the gate was comparing two different units. Other ids return no
`Content-Length` at all.

The final decision was never wrong: the streaming cap counts decoded bytes and
backstops it. What the mismatch cost was the early rejection the gate exists to
provide, plus a misleading size in the one error message where it did fire. The
fetcher now ignores any advertised length carrying a `Content-Encoding`, so the
code no longer claims a conclusion it cannot draw.

A size gate that trusts the server to volunteer a comparable number is not a gate.

## Retrying only what is worth retrying

A 404 is a permanent fact about a book id and is raised immediately with no
second attempt. Timeouts and 5xx get bounded retries with exponential backoff.
Both behaviours are asserted by counting requests, not by trusting the code.

## Keeping line offsets meaningful

`BookBody.start_line` records where the body began in the original download, and
`raw_line_number()` translates back. The boundary agent reports positions against
the raw text, so this offset is load-bearing rather than decorative.

## Tolerating marker drift

Gutenberg's header markers have changed over decades (`THE` vs `THIS`, `EBOOK` vs
`ETEXT`) and some texts carry none. A book without markers is still a book;
`markers_found` records the uncertainty and the pipeline carries on. A *partial*
marker set is treated as untrusted rather than half-believed.

## Not manufacturing corruption

Decoding tries UTF-8 strictly, then Latin-1 for older texts. It never uses
`errors="replace"`: substituting U+FFFD would manufacture exactly the garbled-text
signal the boundary agent is supposed to detect for real.

## Making the agent loop terminate

`ChunkReader` advances the cursor, counts iterations, and returns `None` once the
budget is spent. The model decides only what it is looking at, never whether to
continue. Termination is therefore a property of code with no model in it.

The test that proves this scripts a generator which asks for another chunk
*forever*; the call still returns, with `budget_exhausted`, in a bounded number of
turns. Two independent stops back each other: the reader stops serving, and the
agent's `max_agent_steps` ceiling ends the run.

Chunks overlap so a marker straddling an edge is seen whole by at least one turn.

### Two tools, not one

The original sketch had a single `read_next_chunk`. The decision now returns
through a second `record_decision` tool call, so its shape is enforced by a JSON
schema instead of parsed out of free text, and the agent gets a precise exit
condition.

### A schema cannot catch a confident lie

The model can call `record_decision` with `found=true` and no line numbers, an
inverted range, a reject reason outside the taxonomy, or a confidence value that
is not a confidence value. Each is normalised into an honest rejection rather than
propagating as bad data — an unrecognised reason becomes `ambiguous_boundaries`,
the truthful reading of "refused for something we have no bucket for". Every one
of those paths is a test.

`detect_boundaries` always returns a `BoundaryState`. There is no book it throws on.

## Degrading: content refusals stand, process failures degrade

`no_story_found`, `corrupted_text` and `inappropriate_content` are judgments
*about the book*. Overriding one ships exactly the output the refusal existed to
prevent, so they are never touched.

`budget_exhausted` and `ambiguous_boundaries` say nothing about the book; they say
the search did not converge. Those degrade to the whole de-boilerplated body,
marked `confidence: low`, with `boundary_fallback_applied: true` in the metadata.
A silent fallback would make a guessed range indistinguishable from a located one.

## Tiers carry guidance, not an age number

"Rewrite this for a 7 year old" leans on whatever the model believes about 7 year
olds. `tiers.py` names sentence length, vocabulary and structure directly, which is
both more reliable and reviewable — a children's librarian can read that file and
tell you it is wrong, which they could not do with an integer.

## Segmenting without severing

Cutting mid-paragraph hands the model half a scene and gets half a rewrite, so
segmentation packs whole paragraphs; a paragraph longer than the limit is emitted
whole rather than severed. Each call receives the tail of the previous *rewritten*
output, so names and tense survive a seam.

Both the JSON and streaming paths build these prompts through shared builders, and
a test asserts the two produce byte-identical messages. They were written
independently at first, which is the shape that quietly diverges.

## HTTP codes for facts, envelope fields for judgments

Deterministic failures map to status codes, because HTTP already has vocabulary
for them: 404 unknown book, 413 over budget, 422 bad tier, 502 upstream down.

A book the *agent* refused answers **200 with `status: rejected`**. The request
succeeded and reached an honest conclusion; calling that 4xx would tell a client
its request was malformed when it was not.

Haystack wraps component exceptions in `PipelineRuntimeError` with the original on
`__cause__`, so a wrapper catching `BookNotFoundError` around `pipeline.run()`
never matches and every deliberate rejection would surface as a generic 500.
`api_errors.unwrap` walks the cause chain, cycle-safe.

## Honesty in the envelope

- `estimated_cost_usd` is `null`, not `0.0`, for a model with no price on file. A
  silent zero reads as "this was free", the opposite of "we do not know".
- Accepted boundaries yielding no text are reported as `ambiguous_boundaries`
  rather than dressed up as a model judgment.
- A rejection carries the agent's own reason and confidence unchanged.
- Metadata is present on rejections too, so an operator can explain one.
- `schema_version` on every response: consumers break on shape changes far more
  often than content changes.
- Usage is read under both `prompt_tokens`/`input_tokens` spellings, so an
  unfamiliar integration cannot silently account zero.

## What streams, and what does not

The prose streams token by token — that is what a reader waits on. The phases
before it emit *progress lines*, never model output. The boundary agent is
reasoning about a book, not writing one; streaming its chatter would leak
half-formed judgments and fragments of source text it was told never to reproduce.
Two tests assert neither reaches the stream.

A stream commits to HTTP 200 with its first byte, so failures after that cannot be
status codes and are written as sentences. The chat surface talks to a person; the
REST surface talks to a program. The same oversized book answers `413` on one and
a readable line on the other.

Model failures are handled per phase because the two differ. A boundary-phase
failure degrades to a sentence — nothing of value has streamed. A failure *during*
the prose is different: re-raising would drop the connection and leave a partial
story that reads exactly like a finished one, so the stream says it stopped early
and names the part. Both log the real cause; neither shows it to the reader.

Abandonment cancels the in-flight call rather than leaving it billing tokens into
a closed socket. Readers close tabs mid-story; that is the normal case.

## Chat parsing

"read me 14838 for a 5 year old" contains two numbers, and picking wrong silently
fetches the wrong book. Ages are consumed by explicit patterns first, an
explicitly labelled id always wins, and whatever survives is the id.

## Evaluating decisions, not prose

Asserting rewritten text tests the model's word choice, which drifts between runs
and model versions and says nothing about whether the pipeline works. What must
hold is that a manual is refused, a garbled scan is refused, and a clean story is
not.

Expectations are **sets**. A story that stops mid-sentence may honestly be reported
as ambiguous *or* accepted with the text that exists. Encoding "either of these,
but never `corrupted_text` and never `no_story_found`" is more useful than
inventing one right answer and loosening the test later when it fails for a good
reason.

Evals run with the fallback **disabled** — it turns a non-converging search into an
accepted low-confidence result, which is right for serving a request and wrong for
measuring the agent.

`make eval` refuses to run without a key. An eval that mocks the thing being
evaluated is a regression test in a costume; it would print a green table that
means nothing. The harness itself is tested for detecting failure: a wrong answer
fails, a crashing case fails without aborting the run, and the table names what
broke.

## Observability

**Liveness and readiness answer different questions.** *Live* means the process is
not wedged — if it fails, restarting helps. *Ready* means this instance can serve:
pipeline deployed and API key present. A missing key must fail readiness, never
liveness — restarting cannot conjure a secret, so failing liveness produces an
endless crash loop while failing readiness correctly drains the pod.
`/health/ready` reports each check separately, because one boolean will not say
which half broke.

**Health is exempt from auth; `/metrics` is not.** A kubelet has no credentials, so
gating health makes every pod permanently unready. `/metrics` stays behind the
token because it reveals request volume and spend.

**Content never enters a trace.** Haystack's `set_content_tag` is overridden to an
unconditional no-op: the content here is the body of a book and the prose written
from it. Non-scalar tags are summarised (`<list len=5000>`), never dumped.

**Hayhooks' request-id middleware is replaced, not supplemented.** It mints a fresh
id per request and ignores an inbound `X-Request-ID`, which breaks correlation as
soon as a caller upstream has assigned one.

**Tracing setup order is load-bearing.** Hayhooks calls its own `configure_tracing()`
inside `create_app()`, and that call no-ops *only when tracing is already enabled*.
Ours must run first or its OTLP bootstrap wins and our tracer is silently never
used. A test asserts ours survives — Hayhooks wraps it in a live-buffer proxy
rather than replacing it, so the assertion follows the wrapper chain rather than
checking identity.

**`reject_reason` is a metric label** because a rise in `no_story_found`,
`budget_exhausted` and `corrupted_text` call for three different responses, where a
single rejection rate would show one number moving. Label values come from closed
enums only; an unbounded label turns a metrics backend into a memory leak.

## Container and chart

**Three probes, three questions.** `startupProbe` holds the other two off while
Python boots and Hayhooks scans the pipelines directory — without it a slow start
is indistinguishable from a hang and the kubelet restarts forever.
`livenessProbe` tests **no dependency**: one that checked the model API would
restart every pod during a single upstream outage, turning a degradation into an
outage. `readinessProbe` does check configuration.

**The grace period is 180s, not 30.** A streamed rewrite runs for minutes and the
default would sever in-flight streams on every rollout. A `preStop` pause lets
endpoint removal propagate before the process starts refusing work.

**`readOnlyRootFilesystem: true` with exactly two writable mounts**, found by
running the container under `--read-only` rather than guessing.

**Haystack writes to `$HOME` at import time.** Its telemetry module creates
`~/.haystack/config.yaml` when the package is imported, which crashed the non-root
image with `PermissionError`. `HAYSTACK_TELEMETRY_ENABLED=False` is set in both the
image and the ConfigMap.

**No service account token is mounted.** This workload calls no Kubernetes API.

**The chart refuses to render without a key** rather than deploying something that
can never pass readiness. `secrets.existingSecret` is the path for anywhere real: a
Helm-created Secret leaves its values in the release history in cluster storage.

**Config changes roll the pods** via a checksum annotation, because a `helm upgrade`
that only changes a ConfigMap otherwise leaves every pod on the old values.

**The HPA targets CPU and says why that is weak.** This service waits on an upstream
API far more than it computes, so it looks idle under load. KEDA on concurrency is
the better trigger; CPU is the portable default.

## Concurrency, and why it is not built

Hayhooks' `deploy_utils._execute_pipeline_run` already does
`await run_in_threadpool(pipeline_wrapper.run_api, ...)`, so a sync `run_api` never
blocks the event loop. A `run_api_async` built on `asyncio.to_thread` would be the
same mechanism moved into this repo.

Real async — `AsyncPipeline` plus `run_async` on all four components — *would* buy
something: each in-flight request holds one of Starlette's ~40 threadpool workers
for the minutes a rewrite takes, so that pool is the concurrency ceiling. That is a
genuine limit and it is not addressed.

Parallel segments were rejected on their own merits: continuity is what makes the
loop sequential, and a children's book runs to 1–4 segments.
