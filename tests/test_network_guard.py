"""The suite's network guard is itself load-bearing, so it gets tests.

If this file passes but `conftest.py`'s fixture has silently stopped
applying, an un-mocked Notion call in any other test would hit the live
database instead of failing.
"""

import httpx
import pytest

from tests.conftest import NetworkAccessBlockedError


@pytest.mark.parametrize(
    'call',
    [
        pytest.param(
            lambda: httpx.get('https://api.notion.com/v1/x'), id='get'
        ),
        pytest.param(
            lambda: httpx.post('https://api.notion.com/v1/x', json={}),
            id='post',
        ),
        pytest.param(
            lambda: httpx.patch('https://api.notion.com/v1/x', json={}),
            id='patch',
        ),
        pytest.param(
            lambda: httpx.delete('https://api.notion.com/v1/x'),
            id='delete',
        ),
        pytest.param(
            lambda: httpx.Client().get('https://api.notion.com/v1/x'),
            id='client',
        ),
    ],
)
def test_real_requests_are_blocked(call: object) -> None:
    with pytest.raises(NetworkAccessBlockedError):
        call()


def test_archive_page_cannot_reach_notion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The destructive path specifically — this is why the guard exists."""
    from gtd.notion import client

    monkeypatch.setattr(client, 'get_token', lambda: 'fake-token')
    with pytest.raises(NetworkAccessBlockedError):
        client.archive_page('page-1')


def test_blocked_message_names_the_request() -> None:
    with pytest.raises(NetworkAccessBlockedError, match='PATCH'):
        httpx.patch('https://api.notion.com/v1/pages/abc', json={})


class TestCalendarGuard:
    """The calendar reaches Google by subprocess, so it needs its own net.

    Without this the suite reads the developer's real Google Calendar,
    and — worse — passes anyway on a machine that has no `gfunk`.
    """

    def test_fetch_events_cannot_reach_gfunk(self) -> None:
        from tests.conftest import CalendarAccessBlockedError
        from gtd import gcal

        with pytest.raises(CalendarAccessBlockedError):
            gcal.fetch_events()

    def test_the_block_survives_a_present_binary(self) -> None:
        """`which` is stubbed too, or the guard is skipped before it runs."""
        from tests.conftest import CalendarAccessBlockedError
        from gtd import gcal

        with pytest.raises(CalendarAccessBlockedError):
            gcal.fetch_events(days=1, since_days=1)

    def test_the_blocked_message_names_the_command(self) -> None:
        from gtd import gcal

        with pytest.raises(RuntimeError, match='grind'):
            gcal.fetch_events()
