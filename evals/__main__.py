"""Entry point: ``python -m evals`` or ``make eval``.

The real run needs a model. An eval that mocks the thing being evaluated is a
regression test wearing a costume, so the default path refuses to pretend: with
no API key it says so and exits non-zero rather than reporting a green table.

``--dry-run`` exists to prove the harness itself works -- fixtures load, cases
compare, the table renders, the exit code follows the results -- without
claiming anything about the model.
"""

import argparse
import sys

from evals.fakes import ScriptedEvalGenerator
from evals.runner import api_key_present, format_table, run_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals", description="Run the golden set.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="exercise the harness with a scripted generator; proves nothing about the model",
    )
    parser.add_argument("--model", default=None, help="override the model id")
    args = parser.parse_args(argv)

    if args.dry_run:
        print("DRY RUN - scripted generator, not a real evaluation.\n")
        outcomes = run_all(ScriptedEvalGenerator())
    else:
        if not api_key_present():
            print(
                "ANTHROPIC_API_KEY is not set.\n"
                "The golden set measures a real model; running it against a stub would\n"
                "report a green table that means nothing. Set the key, or use --dry-run\n"
                "to check the harness itself.",
                file=sys.stderr,
            )
            return 2

        from gutenberg_simplifier.pipeline import DEFAULT_MODEL, default_generator

        model = args.model or DEFAULT_MODEL
        print(f"Running the golden set against {model}.\n")
        outcomes = run_all(default_generator(model))

    print(format_table(outcomes))
    return 0 if all(outcome.passed for outcome in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
