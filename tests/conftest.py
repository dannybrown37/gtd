"""Suite-wide safety net: no test may reach the network.

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
