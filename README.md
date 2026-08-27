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

Built to **PR 1** of [PR-PLAN.md](PR-PLAN.md). The deterministic stage — fetching
and boilerplate removal — is complete and tested. No LLM involvement yet.

| PR | Scope | State |
|----|-------|-------|
| 0 | Repo scaffolding, lint/type/test gates | done |
| 1 | Fetch + boilerplate strip, zero LLM | done |
| 2 | Haystack pipeline behind a Hayhooks endpoint | next |
| 3 | Boundary-detection agent with a bounded loop | |
| 4 | Age-tiered simplification, structured output | |
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
streamed body. Measured caveat — gutenberg.org answers HEAD with `200` and *no*
`content-length`, so against the real site only the second layer fires. The HEAD
is kept because it rejects unknown ids without transferring a body, but the
streaming cap is the actual guarantee. A size gate that trusts the server to
volunteer its size is not a gate.

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
