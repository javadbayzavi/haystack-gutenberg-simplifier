"""Command line entry point for the deterministic stage.

Its job is measurement, not presentation: the line counts it reports are the
input to choosing chunk size, overlap and the agent's iteration budget, and the
size budget wants tuning against real books rather than a guess.

    python -m gutenberg_simplifier 11
    python -m gutenberg_simplifier 11 --json
"""

import argparse
import json
import sys
from typing import Any

from gutenberg_simplifier.boilerplate import strip_gutenberg_boilerplate
from gutenberg_simplifier.errors import (
    BookNotFoundError,
    BookTooLargeError,
    FetchFailedError,
)
from gutenberg_simplifier.fetch import DEFAULT_MAX_BOOK_BYTES, fetch_book

EXIT_OK = 0
EXIT_NOT_FOUND = 2
EXIT_TOO_LARGE = 3
EXIT_FETCH_FAILED = 4


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        raw = fetch_book(args.book_id, max_bytes=args.max_bytes)
    except BookNotFoundError as exc:
        return _fail(str(exc), EXIT_NOT_FOUND)
    except BookTooLargeError as exc:
        return _fail(str(exc), EXIT_TOO_LARGE)
    except FetchFailedError as exc:
        return _fail(str(exc), EXIT_FETCH_FAILED)

    body = strip_gutenberg_boilerplate(raw)
    stats: dict[str, Any] = {
        "book_id": raw.book_id,
        "source_url": raw.source_url,
        "size_bytes": raw.size_bytes,
        "raw_lines": len(raw.lines),
        "body_lines": body.line_count,
        "body_start_line": body.start_line,
        "boilerplate_markers_found": body.markers_found,
        "body_characters": len(body.text),
    }

    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        _print_report(stats, body.lines)
    return EXIT_OK


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gutenberg-simplifier",
        description="Fetch a Project Gutenberg book and report its post-boilerplate shape.",
    )
    parser.add_argument("book_id", type=int, help="Project Gutenberg book id, e.g. 11")
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BOOK_BYTES,
        help=f"size budget; books over it are rejected (default: {DEFAULT_MAX_BOOK_BYTES})",
    )
    parser.add_argument("--json", action="store_true", help="emit the stats as JSON")
    return parser.parse_args(argv)


def _print_report(stats: dict[str, Any], body_lines: tuple[str, ...]) -> None:
    width = max(len(key) for key in stats)
    for key, value in stats.items():
        print(f"{key:<{width}}  {value}")

    if not body_lines:
        print("\n(body is empty after stripping boilerplate)")
        return

    print("\nfirst 3 body lines:")
    for line in body_lines[:3]:
        print(f"  | {line}")
    print("last 3 body lines:")
    for line in body_lines[-3:]:
        print(f"  | {line}")


def _fail(message: str, code: int) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
