"""Guards on the webapp's mobile viewport handling.

The webapp is a phone-first PWA with no JS test infrastructure, so these are
text assertions over the shipped CSS. They exist because of one concrete bug:
`min-height: 100vh` on `body` made the page taller than iOS Safari's visible
viewport (100vh counts the space behind the URL bar), which pushed the tab bar
below the fold and made the whole document scroll instead of just the list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEBAPP = Path(__file__).parent.parent / 'src' / 'gtd' / 'webapp'


@pytest.fixture(scope='module')
def styles() -> str:
    return (WEBAPP / 'styles.css').read_text()


@pytest.fixture(scope='module')
def index_html() -> str:
    return (WEBAPP / 'index.html').read_text()


def _rule(css: str, selector: str) -> str:
    """Return the declaration block for `selector`, or '' if absent."""
    match = re.search(
        rf'(?:^|\}}|\*/)\s*{re.escape(selector)}\s*\{{(.*?)\}}',
        css,
        re.DOTALL,
    )
    return match.group(1) if match else ''


def test_body_is_pinned_to_the_dynamic_viewport(styles: str) -> None:
    """`body` must size to the *visible* viewport, not `100vh`.

    A bare `100vh` fallback is fine as long as a `dvh` declaration follows it
    to win in browsers that support it.
    """
    body = _rule(styles, 'body')
    assert 'dvh' in body, 'body must size itself with dvh, not vh alone'
    assert 'min-height: 100vh' not in body, (
        'min-height: 100vh lets the body grow past the visible viewport on iOS'
    )


def test_only_the_view_container_scrolls(styles: str) -> None:
    """The document itself must not scroll — `#views` owns scrolling."""
    body = _rule(styles, 'body')
    views = _rule(styles, '#views')
    assert 'overflow: hidden' in body, 'the document must not scroll'
    assert 'overflow-y: auto' in views, '#views must be the scroll container'


def test_scrolling_does_not_rubber_band_the_page(styles: str) -> None:
    """Flicking the list must not drag the whole page with it."""
    views = _rule(styles, '#views')
    assert 'overscroll-behavior' in views


def test_modal_uses_dynamic_viewport_units(styles: str) -> None:
    """The triage modal is tall; capping it in `vh` overflows the same way.

    A `vh` declaration may remain as a fallback, but only *before* the `dvh`
    one — later declarations win, so the reverse order silently disables it.
    """
    modal = _rule(styles, '.modal')
    assert 'dvh' in modal, 'modal max-height must use dvh'
    if 'max-height: 85vh' in modal:
        fallback = modal.index('max-height: 85vh')
        dynamic = modal.index('max-height: 85dvh')
        assert fallback < dynamic, (
            'the vh fallback must come first or it overrides dvh'
        )


def test_safe_area_inset_is_applied_once_at_the_bottom(styles: str) -> None:
    """Bottom inset belongs to the bottom-most element, not to `body` as well.

    Padding `body` *and* the tab bar stacks the inset twice, wasting vertical
    space on exactly the devices that have least of it.
    """
    body = _rule(styles, 'body')
    assert 'padding-bottom: env(safe-area-inset-bottom)' not in body


def test_viewport_meta_covers_the_notch(index_html: str) -> None:
    """`viewport-fit=cover` is what makes safe-area env() vars meaningful."""
    assert 'viewport-fit=cover' in index_html
