"""HTML content extraction.

The extractor accepts a :class:`~app.services.fetching.FetchedPage` that was
produced by the Stage 2 fetcher and turns its HTML into a bounded, normalized
representation suitable for later analysis. It never performs network requests
and never invents content: missing metadata stays ``None`` and missing text
stays empty.

Extraction is intentionally heuristic. It reads title and description metadata
only from the document head, removes common boilerplate while preserving
article-level headers, preserves direct text around nested blocks, and caps the
cleaned text with the configurable ``EXTRACT_MAX_TEXT_CHARS`` setting. Elements
with the HTML ``hidden`` attribute are removed; ``aria-hidden`` is not treated
as visual hiding, and CSS-based hiding is not evaluated.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from bs4 import BeautifulSoup
from bs4.element import (
    Comment,
    Doctype,
    NavigableString,
    PageElement,
    Tag,
)

from app.core.config import settings
from app.services.fetching import FetchedPage

_HEADING_TAGS: Final = ("h1", "h2", "h3", "h4", "h5", "h6")
_HEADING_TAG_NAMES: Final = frozenset(_HEADING_TAGS)

# Elements whose text is not part of the main page content. The title and
# metadata are removed only after they have been read. ``head`` itself is kept
# because malformed documents sometimes nest body content inside it.
# Article-level headers are handled separately so they can be preserved.
_REMOVED_TAGS: Final = (
    "title",
    "script",
    "style",
    "noscript",
    "template",
    "nav",
    "footer",
    "aside",
    "form",
    "button",
    "input",
    "select",
    "option",
    "textarea",
    "svg",
    "canvas",
    "iframe",
    "object",
    "embed",
    "audio",
    "video",
    "link",
    "meta",
)
_REMOVED_TAG_NAMES: Final = frozenset(_REMOVED_TAGS)

# Metadata is only read from a genuine document head and only when it is not
# nested inside content that is not page metadata. ``head`` is included so a
# nested head is never mistaken for the document head.
_NON_METADATA_ANCESTORS: Final = frozenset(
    {"head", "body", "svg", "template", "noscript"}
)
_TITLE_EXCLUDED_TAGS: Final = _NON_METADATA_ANCESTORS | _REMOVED_TAG_NAMES

_BOILERPLATE_ROLES: Final = frozenset(
    {"navigation", "banner", "contentinfo", "complementary", "search"}
)

# Semantic text blocks are emitted as a single block, including any nested
# inline content. Everything else is walked so direct text around nested
# blocks is preserved without duplication.
_BLOCK_TAGS: Final = (
    *_HEADING_TAGS,
    "p",
    "li",
    "blockquote",
    "pre",
    "figcaption",
    "dd",
    "dt",
    "td",
    "th",
)
_BLOCK_TAG_NAMES: Final = frozenset(_BLOCK_TAGS)

_INLINE_TAGS: Final = frozenset(
    {
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "cite",
        "code",
        "data",
        "del",
        "dfn",
        "em",
        "i",
        "img",
        "ins",
        "kbd",
        "mark",
        "q",
        "rp",
        "rt",
        "ruby",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
        "var",
        "wbr",
    }
)


@dataclass(frozen=True, slots=True)
class Heading:
    """A single heading in the extracted content."""

    level: int
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """Normalized content extracted from a fetched HTML page."""

    url: str
    title: str | None
    description: str | None
    headings: tuple[Heading, ...]
    text: str
    truncated: bool


def extract_content(page: FetchedPage) -> ExtractedPage:
    """Extract metadata and visible text from an already fetched page.

    The function is synchronous and performs no I/O. Malformed HTML, missing
    metadata, and empty documents are handled gracefully by returning ``None``
    or empty values rather than raising.

    Raises ``TypeError`` when ``page`` is not a
    :class:`~app.services.fetching.FetchedPage`.
    """
    if not isinstance(page, FetchedPage):
        raise TypeError("extract_content() requires a FetchedPage.")

    soup = BeautifulSoup(page.html, "html.parser")

    title = _extract_title(soup)
    description = _extract_description(soup)
    _remove_boilerplate(soup)

    headings, extracted_text = _extract_usable_content(soup)
    text, truncated = _truncate(
        extracted_text, settings.extract_max_text_chars
    )

    return ExtractedPage(
        url=page.url,
        title=title,
        description=description,
        headings=headings,
        text=text,
        truncated=truncated,
    )


def _text_excluding(tag: Tag, excluded: frozenset[str]) -> str:
    """Collect normalized text without descending into excluded subtrees."""
    parts: list[str] = []
    stack: list[Iterator[PageElement]] = [iter(tag.children)]

    while stack:
        try:
            child = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue

        if isinstance(child, (Comment, Doctype)):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
            continue
        if isinstance(child, Tag) and child.name not in excluded:
            stack.append(iter(child.children))

    return _normalize(" ".join(parts))


def _extract_title(soup: BeautifulSoup) -> str | None:
    """Return the first usable ``<title>`` from the genuine document head."""
    head = _metadata_head(soup)
    if head is None:
        return None

    # Walk the head in document order, skipping subtrees that cannot contain
    # genuine metadata. Nested titles are inspected without folding their
    # text into an enclosing title.
    stack: list[Iterator[PageElement]] = [iter(head.children)]
    while stack:
        try:
            child = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue

        if not isinstance(child, Tag):
            continue
        if child.name in _NON_METADATA_ANCESTORS:
            continue
        if child.name == "title":
            normalized = _text_excluding(child, _TITLE_EXCLUDED_TAGS)
            if normalized:
                return normalized
            # An empty title may still contain nested titles; inspect them
            # in document order without re-reading the same text.
            stack.append(iter(child.children))
            continue
        stack.append(iter(child.children))

    return None


def _extract_description(soup: BeautifulSoup) -> str | None:
    """Return the head meta description, falling back to Open Graph metadata."""
    return _meta_content(soup, "name", "description") or _meta_content(
        soup, "property", "og:description"
    )


def _meta_content(soup: BeautifulSoup, attribute: str, value: str) -> str | None:
    """Find the first matching head meta tag with non-empty content."""
    head = _metadata_head(soup)
    if head is None:
        return None

    for meta in head.find_all("meta"):
        if _has_non_metadata_ancestor(meta, head):
            continue
        raw = meta.get(attribute)
        if not isinstance(raw, str) or raw.strip().lower() != value:
            continue
        content = meta.get("content")
        if isinstance(content, str):
            normalized = _normalize(content)
            if normalized:
                return normalized
    return None


def _metadata_head(soup: BeautifulSoup) -> Tag | None:
    """Return the first genuine document head, if any."""
    for head in soup.find_all("head"):
        parent = head.parent
        if parent is soup:
            return head
        if isinstance(parent, Tag) and parent.name == "html":
            return head
    return None


def _has_non_metadata_ancestor(tag: Tag, head: Tag) -> bool:
    """Return whether metadata is nested in ignored content or outside head."""
    for parent in tag.parents:
        if parent is head:
            return False
        if isinstance(parent, Tag) and parent.name in _NON_METADATA_ANCESTORS:
            return True
    return True


def _remove_boilerplate(soup: BeautifulSoup) -> None:
    """Remove non-content elements, boilerplate roles, and hidden elements."""
    for tag in soup.find_all(_REMOVED_TAGS):
        tag.decompose()

    article_headers = _article_header_ids(soup)

    for tag in soup.find_all("header"):
        # ``find_all`` snapshots the tree, so a header may already have been
        # removed along with an ancestor. Detached tags have no parent and
        # must not be queried further. Checking ``parent`` is O(1), unlike
        # ``Tag.decomposed`` which can fall back to a subtree search.
        if tag.parent is None:
            continue
        if id(tag) not in article_headers:
            tag.decompose()

    for tag in soup.find_all(True):
        if tag.parent is None:
            continue
        if tag.name in _REMOVED_TAG_NAMES:
            continue

        role = tag.get("role")
        is_article_header = tag.name == "header" and id(tag) in article_headers
        if (
            isinstance(role, str)
            and role.strip().lower() in _BOILERPLATE_ROLES
            and not is_article_header
        ):
            tag.decompose()
            continue

        if tag.has_attr("hidden"):
            tag.decompose()


def _article_header_ids(soup: BeautifulSoup) -> set[int]:
    """Return the ids of headers that have an article ancestor."""
    ids: set[int] = set()
    stack: list[tuple[Tag | BeautifulSoup, bool]] = [(soup, False)]

    while stack:
        node, in_article = stack.pop()
        for child in node.children:
            if not isinstance(child, Tag):
                continue
            child_in_article = in_article or child.name == "article"
            if child.name == "header" and child_in_article:
                ids.add(id(child))
            stack.append((child, child_in_article))

    return ids


def _content_roots(soup: BeautifulSoup) -> tuple[Tag | BeautifulSoup, ...]:
    """Return candidate content roots in preference order."""
    roots: list[Tag | BeautifulSoup] = []
    seen: set[int] = set()

    for candidate in (
        soup.find("main"),
        _find_by_role(soup, "main"),
        soup.find("article"),
        soup.body,
        soup,
    ):
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        roots.append(candidate)

    return tuple(roots)


def _extract_usable_content(
    soup: BeautifulSoup,
) -> tuple[tuple[Heading, ...], str]:
    """Extract from the first preferred root that yields usable text."""
    for root in _content_roots(soup):
        headings = _extract_headings(root)
        text = _extract_text(root)
        if text:
            return headings, text

    return (), ""


def _find_by_role(soup: BeautifulSoup, role: str) -> Tag | None:
    """Return the first tag whose ``role`` attribute contains ``role``."""
    for tag in soup.find_all(True):
        value = tag.get("role")
        if isinstance(value, str) and role in value.lower().split():
            return tag
    return None


def _extract_headings(root: Tag | BeautifulSoup) -> tuple[Heading, ...]:
    """Collect unique headings in document order.

    Text is read without descending into nested headings, so malformed
    heading-inside-heading markup is linear rather than repeatedly walking
    overlapping subtrees.
    """
    headings: list[Heading] = []
    seen: set[str] = set()

    for tag in root.find_all(_HEADING_TAGS):
        text = _text_excluding(tag, _HEADING_TAG_NAMES)
        if not text or text in seen:
            continue
        seen.add(text)
        headings.append(Heading(level=int(tag.name[1]), text=text))

    return tuple(headings)


def _extract_text(root: Tag | BeautifulSoup) -> str:
    """Collect unique text blocks, preserving direct text around nested blocks."""
    blocks: list[str] = []
    seen: set[str] = set()
    buffer: list[str] = []

    def flush() -> None:
        text = _normalize("".join(buffer))
        buffer.clear()
        if text and text not in seen:
            seen.add(text)
            blocks.append(text)

    def add_block(value: str) -> None:
        text = _normalize(value)
        if text and text not in seen:
            seen.add(text)
            blocks.append(text)

    # Iterate with an explicit stack so deeply nested (or malformed) documents
    # cannot exhaust the Python call stack. A ``None`` marker flushes the
    # buffer when a block-level container has been fully walked.
    stack: list[Iterator[PageElement] | None] = [iter(root.children)]

    while stack:
        iterator = stack[-1]
        if iterator is None:
            flush()
            stack.pop()
            continue

        try:
            child = next(iterator)
        except StopIteration:
            stack.pop()
            continue

        if isinstance(child, (Comment, Doctype)):
            continue
        if isinstance(child, NavigableString):
            buffer.append(str(child))
            continue
        if not isinstance(child, Tag) or child.parent is None:
            continue

        if child.name in _BLOCK_TAG_NAMES:
            flush()
            add_block(child.get_text(" ", strip=True))
            continue

        if child.name == "br":
            buffer.append(" ")
            continue

        if child.name in _INLINE_TAGS:
            stack.append(iter(child.children))
            continue

        # Block-level container: keep its direct text and nested blocks
        # separate so neither is lost or duplicated.
        flush()
        stack.append(None)
        stack.append(iter(child.children))

    flush()
    return "\n\n".join(blocks)


def _normalize(value: str) -> str:
    """Collapse all whitespace runs into single spaces."""
    return " ".join(value.split())


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """Cap text at ``limit`` characters without adding content."""
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip(), True
