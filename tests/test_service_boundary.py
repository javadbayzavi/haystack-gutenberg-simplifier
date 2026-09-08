"""Neither service may import the other.

The narrator side is asserted in tests/narrator/test_boundary.py. This is the
other direction, and it is the easier one to break: the simplifier gained a
narration feature, and importing the narrator's voice names or its request model
would be the obvious way to build it. It calls over HTTP instead, which is what
keeps the two independently deployable and independently versioned.
"""

import ast
import pathlib

SIMPLIFIER_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "gutenberg_simplifier"
FORBIDDEN_PREFIX = "gutenberg_narrator"


def _imported_modules(source: pathlib.Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_there_are_modules_to_check() -> None:
    assert len(list(SIMPLIFIER_ROOT.glob("*.py"))) >= 10


def test_no_simplifier_module_imports_the_narrator() -> None:
    offenders = {
        source.name: sorted(m for m in _imported_modules(source) if _forbidden(m))
        for source in SIMPLIFIER_ROOT.rglob("*.py")
    }
    offenders = {name: modules for name, modules in offenders.items() if modules}

    assert not offenders, f"simplifier modules importing the narrator: {offenders}"


def test_the_pipeline_wrapper_does_not_import_the_narrator() -> None:
    wrapper = (
        pathlib.Path(__file__).resolve().parent.parent
        / "pipelines"
        / "simplify"
        / "pipeline_wrapper.py"
    )

    assert not any(_forbidden(module) for module in _imported_modules(wrapper))


def test_importing_the_simplifier_does_not_pull_in_the_narrator() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import gutenberg_simplifier.app, sys; "
            "leaked = [m for m in sys.modules if m.startswith('gutenberg_narrator')]; "
            "sys.exit(1 if leaked else 0)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, "importing the simplifier pulled in the narrator"


def _forbidden(module: str) -> bool:
    return module == FORBIDDEN_PREFIX or module.startswith(f"{FORBIDDEN_PREFIX}.")
