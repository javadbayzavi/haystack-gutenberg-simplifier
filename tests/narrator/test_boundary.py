"""The narrator must not depend on the simplifier.

They are separate services because they scale on different signals: one waits on
an upstream API, the other is compute-bound. An add-on that imports its host
cannot be deployed, versioned or scaled without it, which makes it not an add-on.

The temptation is concrete and specific: the narrator's voice profiles are keyed
by the simplifier's reading-age tiers, and importing that enum would be one line.
This test is what stops that line.
"""

import ast
import pathlib

NARRATOR_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src" / "gutenberg_narrator"
FORBIDDEN_PREFIX = "gutenberg_simplifier"


def _imported_modules(source: pathlib.Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)

    return modules


def test_the_package_has_modules_to_check() -> None:
    """Guards against the test passing because it found nothing."""
    assert len(list(NARRATOR_ROOT.glob("*.py"))) >= 5


def test_no_narrator_module_imports_the_simplifier() -> None:
    offenders = {
        source.name: sorted(module for module in _imported_modules(source) if _forbidden(module))
        for source in NARRATOR_ROOT.rglob("*.py")
    }
    offenders = {name: modules for name, modules in offenders.items() if modules}

    assert not offenders, f"narrator modules importing the simplifier: {offenders}"


def test_the_package_imports_cleanly_on_its_own() -> None:
    """A dependency could also arrive at runtime rather than through an import."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import gutenberg_narrator, sys; "
            "leaked = [m for m in sys.modules if m.startswith('gutenberg_simplifier')]; "
            "sys.exit(1 if leaked else 0)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, "importing the narrator pulled in the simplifier"


def _forbidden(module: str) -> bool:
    return module == FORBIDDEN_PREFIX or module.startswith(f"{FORBIDDEN_PREFIX}.")
