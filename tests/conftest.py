"""Suite-wide safety nets: no test may reach the network or `gfunk`.

Every GTD write path ends at a live Notion database, and `archive_page`
moves a real page to the trash. Credentials are usually present in a
developer's environment *and* in `~/.config/gtd/config.json`, so a test
that forgets to mock does not fail — it mutates the user's own data,
quietly, and the suite still passes.

This blocks the transport every `httpx` call goes through, so a missing
mock raises instead. Patching the transport rather than `httpx.get`/`post`
keeps the existing tests that monkeypatch those module-level functions
working untouched: they never reach a transport at all.

There is deliberately no opt-out marker. Every test in this suite mocks
at a function or transport boundary, so an escape hatch would only ever
be reached by a test that had gone wrong. If a real integration test is
ever wanted, give it its own explicitly-credentialled fixture rather than
a flag any test can set.
"""

from typing import Never

import httpx
import pytest


@pytest.fixture(autouse=True)
def _fake_notion_db_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a dummy DB ID so `get_projects_db_id` never `sys.exit`s."""
    monkeypatch.setenv('NOTION_PROJECTS_DB_ID', 'fake-test-db-id')
    monkeypatch.setenv('NOTION_TOKEN', 'fake-test-token')


class NetworkAccessBlockedError(RuntimeError):
    """Raised when a test tries to make a real HTTP request."""


_MESSAGE = (
    'Blocked a real HTTP request from the test suite ({method} {url}). '
    'Mock the client function you are exercising — a live call here can '
    "archive pages in the developer's own Notion database."
)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(_self: object, req: httpx.Request, *_a: object) -> Never:
        raise NetworkAccessBlockedError(
            _MESSAGE.format(method=req.method, url=req.url),
        )

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', _blocked)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport,
        'handle_async_request',
        _blocked,
    )


class CalendarAccessBlockedError(RuntimeError):
    """Raised when a test tries to shell out to the real `gfunk`."""


_CALENDAR_MESSAGE = (
    'Blocked a real `gfunk` call from the test suite ({argv}). Pass a '
    '`runner=` to `gcal.fetch_events`, or patch `gcal.fetch_events` — a '
    "live call here reads the developer's own Google Calendar."
)


@pytest.fixture(autouse=True)
def _block_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The calendar's twin of `_block_network`.

    `gcal` reaches Google through a subprocess, not `httpx`, so the
    transport guard above never sees it. Without this, any test that
    mounts the TUI reads the developer's real calendar — and on a machine
    with no `gfunk` it would pass anyway, so the gap would only ever
    show up as a mystery failure on someone else's laptop.
    """
    from gtd import gcal

    def _blocked(argv: list[str]) -> Never:
        raise CalendarAccessBlockedError(
            _CALENDAR_MESSAGE.format(argv=' '.join(argv)),
        )

    monkeypatch.setattr(gcal, '_run', _blocked)
    monkeypatch.setattr(gcal.shutil, 'which', lambda _name: '/blocked/gfunk')
