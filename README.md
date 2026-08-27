# haystack-gutenberg-simplifier

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
.myenv/bin/pip install -e ".[dev]"
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
chat generators are injectable, and the agent is driven by scripted stubs.

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

Project code is for demonstration. Books fetched are public domain via Project
Gutenberg and subject to its terms.
