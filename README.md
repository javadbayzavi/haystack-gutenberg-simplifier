# haystack-gutenberg-simplifier

[![CI](https://github.com/javadbayzavi/haystack-gutenberg-simplifier/actions/workflows/ci.yml/badge.svg)](https://github.com/javadbayzavi/haystack-gutenberg-simplifier/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

Takes a book from [Project Gutenberg](https://www.gutenberg.org), finds where the
story actually starts and ends, rewrites it for a given reading age, and serves
it over HTTP with [Haystack](https://haystack.deepset.ai) and
[Hayhooks](https://github.com/deepset-ai/hayhooks).

It exercises the parts of an LLM pipeline that are awkward in production rather
than the parts that demo well: bounded agent loops, explicit rejection instead of
bad output, streaming, evaluation of decisions, tracing, and deployment.

## How it works

```
fetch → strip boilerplate → locate the story → rewrite it → envelope
```

**Fetch** enforces a size budget before any model is called. **Strip** removes
Gutenberg's licence header and footer. **Locate** runs a Haystack `Agent` over
the body in overlapping chunks; a deterministic reader owns the iteration budget,
so the loop terminates regardless of what the model does. The agent returns
boundaries or a typed refusal. **Rewrite** simplifies the resolved range for a
reading age, segment by segment, carrying continuity across seams.

Refusals are first-class. A book can be rejected as `corrupted_text`,
`inappropriate_content`, `no_story_found`, `ambiguous_boundaries` or
`budget_exhausted`, and the response says which.

## Quickstart

```bash
python3 -m venv .myenv
.myenv/bin/pip install -e ".[all,dev]"
make check
```

Inspect a book without involving a model:

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

`--json` for machine-readable output, `--max-bytes` to change the budget. Exit
codes are meaningful: `2` not found, `3` over budget, `4` fetch failed.

## Running the service

```bash
cp .env.example .env          # then put your real key in it
export ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY .env | cut -d= -f2-)
make serve
```

| Endpoint | Auth | Purpose |
|---|---|---|
| `/health/live` | none | process is running |
| `/health/ready` | none | pipeline loaded and API key present |
| `/metrics` | bearer | Prometheus exposition |
| `/simplify/run` | bearer | JSON envelope |
| `/v1/chat/completions` | bearer | streaming chat |

Auth is off unless `GUTENBERG_API_TOKEN` is set. Traces export only when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set; without it spans are still created, so
instrumentation behaves the same whether or not a collector is reachable.

### JSON

```bash
curl -s -X POST http://localhost:1416/simplify/run \
  -H 'Content-Type: application/json' \
  -d '{"book_id": 14838, "tier": "preschool"}'
```

`tier` is `preschool` (3–5), `early_reader` (6–8) or `middle_grade` (9–11).
Every response carries a `schema_version`, a `status` of `ok` or `rejected`, and
metadata including token usage and estimated cost.

### Streaming

OpenAI-compatible, so any OpenAI client or a UI like open-webui can point at it:

```bash
curl -sN -X POST http://localhost:1416/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"simplify","messages":[{"role":"user","content":"simplify 14838 for a 5 year old"}],"stream":true}'
```

Accepted phrasings include a bare id, `book 14838`, `14838 preschool`,
`14838 for a 7 year old`, `14838 aged 9`, `14838 for my 6yo`. Without an age it
defaults to `early_reader`.

## Deploying

A multi-stage image and a Helm chart, targeted at
[kind](https://kind.sigs.k8s.io):

```bash
make kind-load
kubectl create namespace gutenberg-simplifier
helm install gs deploy/helm/gutenberg-simplifier \
  -n gutenberg-simplifier \
  --set secrets.anthropicApiKey="$ANTHROPIC_API_KEY" \
  --set secrets.apiToken=local-dev-token
```

`make helm-validate` renders the chart and checks it against the cluster API
without creating anything.

## Evaluating

```bash
make eval-dry   # harness check; proves nothing about the model
make eval       # the golden set against a real model; needs ANTHROPIC_API_KEY
```

The golden set in `evals/fixtures/` covers a clean story, one buried under
dedications and a contents page, a garbled scan, a technical manual, and a story
that stops mid-sentence. Cases assert **decisions**, never prose.

## Development

```bash
make check         # lint + typecheck + tests
make test          # offline suite only
make test-network  # hits gutenberg.org and a real model; deselected by default
make format
```

The default suite never touches the network or a model: the HTTP client and both
chat generators are injectable, and the agent is driven by scripted stubs. CI
runs the same `make` targets on Python 3.11 and 3.13, builds the container and
starts it under a read-only root filesystem, and validates the Helm chart —
all without a key, a GPU or a cluster.

## Narration (optional add-on)

A companion service turns simplified prose into narrated audio. It is a
**separate service, not a feature**, because the two scale on opposite signals:
the simplifier waits on an upstream API, while synthesis is compute-bound.
Bundling them would scale one on the other's bottleneck.

```bash
NARRATOR_ENGINE=stub .myenv/bin/python -m gutenberg_narrator.app
```

```bash
curl -s -X POST http://localhost:1417/narrate \
  -H 'Content-Type: application/json' \
  -d '{"text":"Once upon a time there was a rabbit.","voice":"preschool"}' \
  --output story.wav
```

| Endpoint | Auth | Purpose |
|---|---|---|
| `/health/live`, `/health/ready` | none | process up; engine loaded |
| `/metrics` | bearer | Prometheus exposition |
| `/voices` | bearer | available voice profiles |
| `/narrate` | bearer | streams `audio/wav` |

Auth is off unless `NARRATOR_API_TOKEN` is set.

### Engines

| `NARRATOR_ENGINE` | Needs | Output |
|---|---|---|
| `stub` (default) | nothing | silence, sized at a natural speaking rate |
| `hosted` | `TTS_API_KEY` | real speech from an OpenAI-compatible `/v1/audio/speech` |

**The stub is the default and is what developers and CI run** — not merely a
test double. The whole service is exercisable with no model, no GPU and no API
key, and a contributor working on the HTTP layer never downloads weights. A
deployment that forgets to choose an engine produces silence rather than an
unexpected bill.

The hosted engine points at any provider speaking the OpenAI speech API
(`TTS_BASE_URL`), asks for raw PCM so units concatenate without decoding, maps
each reading age to a provider voice (`TTS_VOICE_PRESCHOOL` and friends), and
retries only what is worth retrying. It refuses to construct without a
credential, so a missing key fails the deployment rather than every request —
and `/health/ready` reports `credential_present` so the pod drains instead of
crash-looping.

Self-hosting a model (Chatterbox, Piper, XTTS) is an implementation of the same
`SynthesisEngine` protocol. What changes is not the architecture but the
deployment: weights to distribute, a much longer startup, GPU scheduling, and a
CPU signal that becomes meaningful where it was useless for the simplifier.

**The separation is enforced, not just intended.** The narrator imports nothing
from the simplifier (a test scans the AST and checks `sys.modules` after import),
and its image carries none of the simplifier's dependencies — 253MB against
522MB, with a CI job that fails if Haystack ever appears in it.

### Deploying the narrator

Its own chart and its own release, so either service can be upgraded or rolled
back without the other:

```bash
make narrator-kind-load
helm install gn deploy/helm/gutenberg-narrator -n gutenberg-simplifier \
  --set engine=hosted \
  --set secrets.ttsApiKey="$TTS_API_KEY" \
  --set secrets.apiToken=local-dev-token
```

`engine=stub` (the default) needs no credential and creates no Secret. The chart
**refuses to render** a hosted engine without one, rather than deploying pods
that would fail readiness on every replica.

The two charts differ in two places that matter. The grace period is longer here
(300s), because a narration runs for as long as its audio and a rollout must not
cut a listener off mid-story. And the HPA note inverts: CPU is a meaningless
signal for the simplifier, which waits on an API — but for a *self-hosted*
engine it tracks load closely, while the stub and hosted engines look idle under
load just like the simplifier does.

### Composing the two

The simplifier gains one endpoint, `POST /simplify/narrate`, which simplifies a
book and streams it as audio. It is **inert unless `NARRATOR_URL` is set** — with
no narrator configured it answers `501` and the service is otherwise byte-for-byte
the same, which is asserted rather than assumed.

```bash
helm install gs deploy/helm/gutenberg-simplifier -n gutenberg-simplifier \
  --set secrets.anthropicApiKey="$ANTHROPIC_API_KEY" \
  --set narrator.url=http://gn-gutenberg-narrator:1417
```

**Neither service imports the other**, in either direction, enforced by tests
that scan the AST and check `sys.modules` after import. The simplifier reaches
the narrator over HTTP, which is the same decision as running them separately,
expressed in code: a shared library would tie their release cycles together, and
a URL does not.

The images are pruned of each other's code as well as each other's dependencies
— one `pyproject` builds one distribution containing both packages, so each
Dockerfile removes the other, and CI asserts both directions.

## Known limitations

- **Novel-length books are refused.** The boundary budget covers a children's
  book with margin; a novel exhausts it and degrades to whole-body boundaries at
  low confidence. Scanning inward from both ends would fix it.
- **Agent context grows with each chunk read.** Haystack's `Agent` keeps the
  whole conversation, so a long search carries every chunk it has seen.
- **Threadpool concurrency ceiling.** Each in-flight request holds one Starlette
  worker for the minutes a rewrite takes. Real async — `AsyncPipeline` plus
  `run_async` on every component — would remove it.
- **Segments are rewritten sequentially**, because each carries continuity from
  the previous rewrite.

Design rationale and the reasoning behind these tradeoffs is in
[docs/DESIGN.md](docs/DESIGN.md).

## Licence

[MIT](LICENSE). Books fetched are public domain via Project Gutenberg and
subject to its terms.
