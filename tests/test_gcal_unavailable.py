"""The gfunk subprocess boundary.

The point of every test here: a machine with no gfunk, no token, or no
Calendar opt-in must produce a `CalendarUnavailableError` carrying a hint a
human can act on — never a raw `subprocess` traceback surfacing in a TUI
tab.
"""

import json
import subprocess

import pytest

from gtd import gcal


class FakeRun:
    """Stands in for `subprocess.run` without ever spawning anything."""

    def __init__(
        self, *, stdout='[]', stderr='', returncode=0, raises=None
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.raises = raises
        self.argv = None

    def __call__(self, argv) -> subprocess.CompletedProcess:
        self.argv = argv
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            argv, self.returncode, self.stdout, self.stderr
        )


EVENTS = [
    {
        'summary': 'Standup',
        'start': {'dateTime': '2026-08-24T09:00:00'},
        'end': {'dateTime': '2026-08-24T09:30:00'},
    }
]


def test_fetch_events_parses_gfunk_json():
    runner = FakeRun(stdout=json.dumps(EVENTS))

    assert gcal.fetch_events(runner=runner) == EVENTS


def test_fetch_events_passes_the_window_to_gfunk():
    runner = FakeRun()

    gcal.fetch_events(days=14, since_days=7, runner=runner)

    assert runner.argv[1:] == [
        'grind',
        '--json',
        '--days',
        '14',
        '--since',
        '7',
    ]


def test_fetch_events_never_uses_a_shell():
    """argv is a list so a calendar name can't become a command."""
    runner = FakeRun()

    gcal.fetch_events(runner=runner)

    assert isinstance(runner.argv, list)


def test_missing_gfunk_binary_is_a_hint_not_a_traceback():
    runner = FakeRun(raises=FileNotFoundError('gfunk'))

    with pytest.raises(gcal.CalendarUnavailableError) as caught:
        gcal.fetch_events(runner=runner)

    assert 'gfunk' in caught.value.hint


def test_not_opted_in_repeats_gfunks_own_advice():
    runner = FakeRun(
        returncode=1,
        stderr="Calendar isn't connected yet.\ngfunk mount-up --with-calendar",
    )

    with pytest.raises(gcal.CalendarUnavailableError) as caught:
        gcal.fetch_events(runner=runner)

    assert '--with-calendar' in caught.value.hint


def test_a_nonzero_exit_surfaces_the_first_line_of_stderr():
    runner = FakeRun(returncode=2, stderr='Token expired\nsecond line')

    with pytest.raises(gcal.CalendarUnavailableError) as caught:
        gcal.fetch_events(runner=runner)

    assert 'Token expired' in caught.value.hint
    assert 'second line' not in caught.value.hint


def test_a_nonzero_exit_with_no_stderr_still_has_a_hint():
    runner = FakeRun(returncode=1, stderr='')

    with pytest.raises(gcal.CalendarUnavailableError) as caught:
        gcal.fetch_events(runner=runner)

    assert caught.value.hint


@pytest.mark.parametrize(
    'stdout',
    [
        pytest.param('not json at all', id='garbage'),
        pytest.param('', id='empty'),
        pytest.param('{"items": []}', id='object-not-list'),
        pytest.param('null', id='null'),
    ],
)
def test_unreadable_stdout_is_unavailable_not_a_crash(stdout):
    runner = FakeRun(stdout=stdout)

    with pytest.raises(gcal.CalendarUnavailableError):
        gcal.fetch_events(runner=runner)


def test_a_hang_becomes_a_timeout_hint():
    runner = FakeRun(raises=subprocess.TimeoutExpired('gfunk', 30))

    with pytest.raises(gcal.CalendarUnavailableError) as caught:
        gcal.fetch_events(runner=runner)

    assert 'timed out' in caught.value.hint.lower()


def test_an_os_error_is_unavailable_too():
    runner = FakeRun(raises=PermissionError('denied'))

    with pytest.raises(gcal.CalendarUnavailableError):
        gcal.fetch_events(runner=runner)


def test_the_hint_is_the_exception_message():
    """So a surface can render `str(exc)` and still be useful."""
    runner = FakeRun(raises=FileNotFoundError('gfunk'))

    with pytest.raises(gcal.CalendarUnavailableError) as caught:
        gcal.fetch_events(runner=runner)

    assert str(caught.value) == caught.value.hint


def test_gfunk_bin_prefers_the_env_override(monkeypatch):
    monkeypatch.setenv('GTD_GFUNK_BIN', '/opt/gfunk/bin/gfunk')

    assert gcal.gfunk_bin() == '/opt/gfunk/bin/gfunk'


def test_gfunk_bin_falls_back_to_config(monkeypatch):
    monkeypatch.delenv('GTD_GFUNK_BIN', raising=False)
    monkeypatch.setattr(
        gcal,
        'get_config_value',
        lambda k: '/cfg/gfunk' if k == 'gfunk_bin' else None,
    )

    assert gcal.gfunk_bin() == '/cfg/gfunk'


def test_gfunk_bin_defaults_to_path(monkeypatch):
    monkeypatch.delenv('GTD_GFUNK_BIN', raising=False)
    monkeypatch.setattr(gcal, 'get_config_value', lambda _: None)

    assert gcal.gfunk_bin() == 'gfunk'


def test_working_hours_default(monkeypatch):
    monkeypatch.setattr(gcal, 'get_config_value', lambda _: None)

    assert gcal.working_hours() == (
        gcal.DEFAULT_DAY_START,
        gcal.DEFAULT_DAY_END,
    )


def test_working_hours_are_configurable(monkeypatch):
    hours = {'calendar_day_start': '09:30', 'calendar_day_end': '18:00'}
    monkeypatch.setattr(gcal, 'get_config_value', hours.get)

    start, end = gcal.working_hours()

    assert (start.hour, start.minute) == (9, 30)
    assert end.hour == 18


def test_a_nonsense_configured_time_falls_back_rather_than_crashing(
    monkeypatch,
):
    monkeypatch.setattr(gcal, 'get_config_value', lambda _: 'half past nine')

    assert gcal.working_hours() == (
        gcal.DEFAULT_DAY_START,
        gcal.DEFAULT_DAY_END,
    )
