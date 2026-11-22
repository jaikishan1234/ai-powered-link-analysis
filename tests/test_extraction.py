"""Tests for HTML content extraction.

The extractor is synchronous and performs no network I/O. These tests pass
in-memory :class:`FetchedPage` objects directly, so no website is contacted.
"""

from __future__ import annotations

import socket

import pytest

from app.services import extraction
from app.services.extraction import Heading
from app.services.fetching import FetchedPage


def _page(
    html: str, url: str = "https://example.com/page"
) -> FetchedPage:
    return FetchedPage(
        url=url,
        status_code=200,
        content_type="text/html",
        html=html,
        redirect_count=0,
    )


def test_extracts_title_description_headings_and_text() -> None:
    html = """
    <!doctype html>
    <html>
      <head>
        <title>  Example   Page </title>
        <meta name="description" content="A  useful description.">
        <meta property="og:description" content="Open Graph description.">
        <script>var ignored = "script text";</script>
        <style>.ignored { color: red; }</style>
      </head>
      <body>
        <header><h1>Site header</h1></header>
        <nav><a href="/">Home</a></nav>
        <main>
          <h1>Welcome</h1>
          <h2>Features</h2>
          <p>First   paragraph.</p>
          <p>Second paragraph with <strong>bold</strong> text.</p>
          <ul><li>One</li><li>Two</li></ul>
          <form><input name="q"><button>Submit</button></form>
          <svg><text>SVG text</text></svg>
        </main>
        <footer>Footer text</footer>
      </body>
    </html>
    """

    result = extraction.extract_content(_page(html))

    assert result.url == "https://example.com/page"
    assert result.title == "Example Page"
    assert result.description == "A useful description."
    assert result.headings == (
        Heading(level=1, text="Welcome"),
        Heading(level=2, text="Features"),
    )
    assert result.text == (
        "Welcome\n\n"
        "Features\n\n"
        "First paragraph.\n\n"
        "Second paragraph with bold text.\n\n"
        "One\n\n"
        "Two"
    )
    assert result.truncated is False


def test_missing_metadata_returns_none_without_inventing_values() -> None:
    html = (
        "<html><head></head><body>"
        "<main><h1>Heading</h1><p>Content</p></main>"
        "</body></html>"
    )

    result = extraction.extract_content(_page(html))

    assert result.title is None
    assert result.description is None
    assert result.headings == (Heading(level=1, text="Heading"),)
    assert result.text == "Heading\n\nContent"


def test_blank_title_and_description_are_treated_as_missing() -> None:
    html = """
    <html>
      <head>
        <title>   </title>
        <meta name="description" content="   ">
      </head>
      <body><p>Content</p></body>
    </html>
    """

    result = extraction.extract_content(_page(html))

    assert result.title is None
    assert result.description is None


def test_open_graph_description_is_a_fallback() -> None:
    html = """
    <html><head>
      <meta property="og:description" content="  Social   description. ">
    </head><body><p>Content</p></body></html>
    """

    result = extraction.extract_content(_page(html))

    assert result.description == "Social description."


def test_meta_description_takes_precedence_over_open_graph() -> None:
    html = """
    <html><head>
      <meta property="og:description" content="Social description.">
      <meta name="description" content="Standard description.">
    </head><body><p>Content</p></body></html>
    """

    result = extraction.extract_content(_page(html))

    assert result.description == "Standard description."


def test_metadata_outside_head_is_ignored() -> None:
    html = """
    <html><head></head><body>
      <title>Body title</title>
      <meta name="description" content="Body description">
      <p>Content</p>
    </body></html>
    """

    result = extraction.extract_content(_page(html))

    assert result.title is None
    assert result.description is None


@pytest.mark.parametrize(
    "html",
    [
        (
            "<html><head><svg><title>Icon title</title></svg></head>"
            "<body><p>Content</p></body></html>"
        ),
        (
            "<html><head></head><body><svg><title>Icon title</title></svg>"
            "<p>Content</p></body></html>"
        ),
    ],
)
def test_svg_title_is_not_page_metadata(html: str) -> None:
    result = extraction.extract_content(_page(html))

    assert result.title is None


def test_template_and_noscript_metadata_are_ignored() -> None:
    html = """
    <html><head>
      <template>
        <title>Template title</title>
        <meta name="description" content="Template description">
      </template>
      <noscript>
        <meta name="description" content="Noscript description">
      </noscript>
    </head><body><p>Content</p></body></html>
    """

    result = extraction.extract_content(_page(html))

    assert result.title is None
    assert result.description is None


@pytest.mark.parametrize(
    "html",
    [
        (
            "<html><body><template><head><title>Template head title</title>"
            "<meta name='description' content='Template head description'>"
            "</head></template><p>Content</p></body></html>"
        ),
        (
            "<html><body><noscript><head><title>Noscript head title</title>"
            "<meta name='description' content='Noscript head description'>"
            "</head></noscript><p>Content</p></body></html>"
        ),
    ],
)
def test_metadata_inside_nested_head_is_ignored(html: str) -> None:
    result = extraction.extract_content(_page(html))

    assert result.title is None
    assert result.description is None


def test_head_metadata_after_ignored_nested_title_is_used() -> None:
    html = """
    <html><head>
      <template><title>Template title</title></template>
      <title>Real title</title>
      <meta name="description" content="Real description">
    </head><body><p>Content</p></body></html>
    """

    result = extraction.extract_content(_page(html))

    assert result.title == "Real title"
    assert result.description == "Real description"


def test_genuine_head_after_fake_template_head_is_used() -> None:
    html = """
    <html>
      <template>
        <head>
          <title>Template title</title>
          <meta name="description" content="Template description">
        </head>
      </template>
      <head>
        <title>Real title</title>
        <meta name="description" content="Real description">
      </head>
      <body><p>Content</p></body>
    </html>
    """

    result = extraction.extract_content(_page(html))

    assert result.title == "Real title"
    assert result.description == "Real description"


def test_deeply_nested_empty_titles_are_ignored_efficiently() -> None:
    depth = 10_000
    html = (
        "<html><head>"
        + "<title>" * depth
        + "</title>" * depth
        + "</head><body><p>Content</p></body></html>"
    )

    result = extraction.extract_content(_page(html))

    assert result.title is None
    assert result.text == "Content"


def test_malformed_html_is_handled_gracefully() -> None:
    html = (
        "<html><head><title>Broken Page</title><body>"
        "<main><h1>Heading</h1><p>First paragraph"
        "<p>Second paragraph</main>"
    )

    result = extraction.extract_content(_page(html))

    assert result.title == "Broken Page"
    assert result.headings == (Heading(level=1, text="Heading"),)
    assert "First paragraph" in result.text
    assert "Second paragraph" in result.text


def test_deeply_nested_html_is_extracted_without_recursion() -> None:
    depth = 1500
    html = (
        "<body>"
        + "<div>" * depth
        + "deep text"
        + "</div>" * depth
        + "</body>"
    )

    result = extraction.extract_content(_page(html))

    assert result.text == "deep text"


def test_deeply_nested_headings_are_extracted_without_overlapping_traversal() -> None:
    depth = 10_000
    html = (
        "<body>"
        + "<h1>" * depth
        + "Deep heading"
        + "</h1>" * depth
        + "</body>"
    )

    result = extraction.extract_content(_page(html))

    assert result.headings == (Heading(level=1, text="Deep heading"),)
    assert result.text == "Deep heading"


def test_nested_headings_use_their_own_text() -> None:
    html = "<main><h1>Outer<h2>Inner</h2></h1></main>"

    result = extraction.extract_content(_page(html))

    assert result.headings == (
        Heading(level=1, text="Outer"),
        Heading(level=2, text="Inner"),
    )


def test_navigation_header_footer_and_boilerplate_are_removed() -> None:
    html = """
    <html><body>
      <main>
        <header><p>Header text</p></header>
        <nav><p>Navigation text</p></nav>
        <div role="navigation"><p>Role navigation text</p></div>
        <aside><p>Aside text</p></aside>
        <div hidden><p>Hidden attribute text</p></div>
        <form><p>Form text</p></form>
        <svg><text>SVG text</text></svg>
        <p>Main content</p>
        <footer><p>Footer text</p></footer>
      </main>
    </body></html>
    """

    result = extraction.extract_content(_page(html))

    assert result.text == "Main content"


def test_article_header_is_preserved() -> None:
    html = """
    <article>
      <header>
        <h1>Article title</h1>
        <p>By Author</p>
      </header>
      <p>Body text</p>
    </article>
    """

    result = extraction.extract_content(_page(html))

    assert result.headings == (Heading(level=1, text="Article title"),)
    assert result.text == "Article title\n\nBy Author\n\nBody text"


def test_deeply_nested_article_headers_are_preserved() -> None:
    depth = 10_000
    html = (
        "<article>"
        + "<header>" * depth
        + "<h1>Article title</h1>"
        + "</header>" * depth
        + "<p>Body text</p>"
        + "</article>"
    )

    result = extraction.extract_content(_page(html))

    assert result.headings == (Heading(level=1, text="Article title"),)
    assert result.text == "Article title\n\nBody text"


def test_aria_hidden_content_is_not_treated_as_visually_hidden() -> None:
    html = """
    <main>
      <div aria-hidden="true"><p>ARIA hidden text</p></div>
      <p>Visible text</p>
    </main>
    """

    result = extraction.extract_content(_page(html))

    assert result.text == "ARIA hidden text\n\nVisible text"


def test_css_hidden_content_is_not_evaluated() -> None:
    html = """
    <main>
      <div style="display:none">CSS hidden text</div>
      <p>Visible text</p>
    </main>
    """

    result = extraction.extract_content(_page(html))

    assert result.text == "CSS hidden text\n\nVisible text"


def test_main_content_root_is_preferred_over_body_text() -> None:
    html = """
    <html><body>
      <nav><p>Menu item</p></nav>
      <div role="main">
        <h1>Main heading</h1>
        <p>Main text</p>
      </div>
    </body></html>
    """

    result = extraction.extract_content(_page(html))

    assert result.headings == (Heading(level=1, text="Main heading"),)
    assert result.text == "Main heading\n\nMain text"


def test_article_is_used_when_no_main_element_exists() -> None:
    html = """
    <html><body>
      <p>Outside article</p>
      <article><p>Inside article</p></article>
    </body></html>
    """

    result = extraction.extract_content(_page(html))

    assert result.text == "Inside article"


def test_empty_main_falls_back_to_article() -> None:
    html = "<body><main></main><article><p>Article content</p></article></body>"

    result = extraction.extract_content(_page(html))

    assert result.text == "Article content"


def test_empty_main_falls_back_to_body() -> None:
    html = "<body><main></main><p>Body content</p></body>"

    result = extraction.extract_content(_page(html))

    assert result.text == "Body content"


def test_main_emptied_by_boilerplate_falls_back_to_body() -> None:
    html = """
    <body>
      <main><nav><p>Menu</p></nav></main>
      <p>Body content</p>
    </body>
    """

    result = extraction.extract_content(_page(html))

    assert result.text == "Body content"


@pytest.mark.parametrize(
    "html",
    [
        "",
        "   ",
        "<html><head><title> </title></head><body>   </body></html>",
        "<html><body><script>var value = 1;</script><style>.x {}</style></body></html>",
    ],
)
def test_empty_content_yields_empty_result(html: str) -> None:
    result = extraction.extract_content(_page(html))

    assert result.title is None
    assert result.description is None
    assert result.headings == ()
    assert result.text == ""
    assert result.truncated is False


def test_duplicate_text_and_headings_are_removed() -> None:
    html = """
    <main>
      <h1>About</h1>
      <p>Same text.</p>
      <p>Same   text.</p>
      <p>Same text.</p>
      <h2>About</h2>
      <blockquote><p>Quote text.</p></blockquote>
    </main>
    """

    result = extraction.extract_content(_page(html))

    assert result.headings == (Heading(level=1, text="About"),)
    assert result.text == "About\n\nSame text.\n\nQuote text."


def test_whitespace_is_normalized() -> None:
    html = "<main><p>  Hello \n\t  world  </p></main>"

    result = extraction.extract_content(_page(html))

    assert result.text == "Hello world"


def test_mixed_div_preserves_direct_text() -> None:
    html = "<main><div>Intro text<p>Paragraph</p>Outro text</div></main>"

    result = extraction.extract_content(_page(html))

    assert result.text == "Intro text\n\nParagraph\n\nOutro text"


def test_mixed_main_preserves_direct_text() -> None:
    html = "<main>Lead text<p>Paragraph</p>Trailing text</main>"

    result = extraction.extract_content(_page(html))

    assert result.text == "Lead text\n\nParagraph\n\nTrailing text"


def test_nested_paragraph_content_is_not_duplicated() -> None:
    html = (
        "<main><div>Intro <strong>bold</strong>"
        "<p>Nested paragraph</p>Outro</div></main>"
    )

    result = extraction.extract_content(_page(html))

    assert result.text == "Intro bold\n\nNested paragraph\n\nOutro"


def test_plain_container_text_is_extracted() -> None:
    result = extraction.extract_content(_page("<body><div>Plain div text</div></body>"))

    assert result.text == "Plain div text"


def test_inline_text_without_blocks_is_extracted() -> None:
    result = extraction.extract_content(_page("<body><span>Inline text</span></body>"))

    assert result.text == "Inline text"


def test_text_is_truncated_to_the_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extraction.settings, "extract_max_text_chars", 10)
    html = "<main><p>abcdefghij</p><p>klmnopqrst</p></main>"

    result = extraction.extract_content(_page(html))

    assert result.text == "abcdefghij"
    assert len(result.text) <= 10
    assert result.truncated is True


def test_text_at_the_exact_limit_is_not_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full_text = "abcdefghij\n\nklmnopqrst"
    monkeypatch.setattr(extraction.settings, "extract_max_text_chars", len(full_text))

    result = extraction.extract_content(
        _page("<main><p>abcdefghij</p><p>klmnopqrst</p></main>")
    )

    assert result.text == full_text
    assert result.truncated is False


def test_source_url_is_preserved() -> None:
    result = extraction.extract_content(
        _page("<p>Content</p>", url="https://example.com/landing")
    )

    assert result.url == "https://example.com/landing"


def test_extraction_never_uses_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("Extraction must not use the network")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    monkeypatch.setattr(socket, "create_connection", fail)

    result = extraction.extract_content(_page("<p>Offline content</p>"))

    assert result.text == "Offline content"


def test_extraction_requires_a_fetched_page() -> None:
    with pytest.raises(TypeError):
        extraction.extract_content("<html></html>")  # type: ignore[arg-type]
