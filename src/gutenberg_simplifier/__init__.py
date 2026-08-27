"""Age-tiered simplification of Project Gutenberg books."""

from gutenberg_simplifier.boilerplate import strip_gutenberg_boilerplate
from gutenberg_simplifier.errors import (
    BookNotFoundError,
    BookTooLargeError,
    FetchFailedError,
    GutenbergSimplifierError,
)
from gutenberg_simplifier.fetch import DEFAULT_MAX_BOOK_BYTES, fetch_book
from gutenberg_simplifier.models import BookBody, RawBook

__all__ = [
    "DEFAULT_MAX_BOOK_BYTES",
    "BookBody",
    "BookNotFoundError",
    "BookTooLargeError",
    "FetchFailedError",
    "GutenbergSimplifierError",
    "RawBook",
    "fetch_book",
    "strip_gutenberg_boilerplate",
]
