from collections.abc import Callable

from gutenberg_simplifier.boilerplate import strip_gutenberg_boilerplate
from gutenberg_simplifier.models import RawBook


def _book(text: str) -> RawBook:
    return RawBook(book_id=1, text=text, source_url="https://example.test/1", size_bytes=len(text))


def test_strips_header_and_footer(fixture_text: Callable[[str], str]) -> None:
    body = strip_gutenberg_boilerplate(_book(fixture_text("with_markers.txt")))

    assert body.markers_found is True
    assert "This ebook is for the use of anyone" not in body.text
    assert "FULL PROJECT GUTENBERG LICENSE" not in body.text
    assert "a rabbit who lived under a hedge" in body.text


def test_body_starts_and_ends_on_content(fixture_text: Callable[[str], str]) -> None:
    body = strip_gutenberg_boilerplate(_book(fixture_text("with_markers.txt")))

    assert body.lines[0] == "A VERY SMALL TALE"
    assert body.lines[-1] == "THE END"


def test_start_line_maps_back_to_raw_text(fixture_text: Callable[[str], str]) -> None:
    raw = _book(fixture_text("with_markers.txt"))
    body = strip_gutenberg_boilerplate(raw)

    # Every body line must sit at the offset the body claims for it.
    for index, line in enumerate(body.lines):
        assert raw.lines[body.raw_line_number(index)] == line


def test_accepts_legacy_etext_markers(fixture_text: Callable[[str], str]) -> None:
    body = strip_gutenberg_boilerplate(_book(fixture_text("legacy_markers.txt")))

    assert body.markers_found is True
    assert body.lines == ("An older tale, set in an older encoding.",)


def test_missing_markers_keep_the_whole_text(fixture_text: Callable[[str], str]) -> None:
    text = fixture_text("no_markers.txt")
    body = strip_gutenberg_boilerplate(_book(text))

    assert body.markers_found is False
    assert body.start_line == 0
    assert "no machine-readable markers" in body.text


def test_start_marker_without_end_marker_runs_to_eof() -> None:
    text = "\n".join(
        [
            "front matter",
            "*** START OF THE PROJECT GUTENBERG EBOOK ORPHAN ***",
            "the story",
        ]
    )
    body = strip_gutenberg_boilerplate(_book(text))

    assert body.markers_found is False  # partial marker set is reported, not trusted
    assert body.lines == ("the story",)
    assert body.start_line == 2


def test_empty_text_yields_empty_body() -> None:
    body = strip_gutenberg_boilerplate(_book(""))

    assert body.lines == ()
    assert body.line_count == 0
    assert body.markers_found is False
