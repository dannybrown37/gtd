"""Guards on the webapp's snooze presets.

"Next Monday" is the one preset that isn't a fixed day offset, and the whole
point of it is that it's *locked to the following Monday* -- pressing it on a
Monday snoozes a full week out, never to today (a snooze that resolves to
today is a no-op that silently leaves the item on Next Steps).

There's no JS test runner in this repo, so the behaviour is exercised by
shelling out to node against the shipped `nextMondayISO`, and the wiring is a
text assertion over `app.js`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).parent.parent / 'src' / 'gtd' / 'webapp' / 'app.js'
NODE = shutil.which('node')


@pytest.fixture(scope='module')
def app_js() -> str:
    return APP_JS.read_text()


def _function_source(js: str, name: str) -> str:
    match = re.search(
        rf'^function {name}\(.*?^\}}', js, re.DOTALL | re.MULTILINE
    )
    assert match, f'{name}() not found in app.js'
    return match.group(0)


def test_next_monday_button_is_offered(app_js: str) -> None:
    assert 'Next Monday' in app_js
    assert 'nextMondayISO(' in app_js


def test_next_monday_is_not_a_fixed_day_offset(app_js: str) -> None:
    """A `data-days` preset lands on the wrong weekday six days in seven."""
    snooze = re.search(
        r'function openSnoozeModal\(.*?^\}', app_js, re.DOTALL | re.MULTILINE
    )
    assert snooze
    button = re.search(r'<button[^>]*>Next Monday</button>', snooze.group(0))
    assert button, 'Next Monday button missing from the snooze modal'
    assert 'data-days' not in button.group(0)


@pytest.mark.skipif(NODE is None, reason='node not installed')
@pytest.mark.parametrize(
    ('today', 'expected'),
    [
        ('2026-08-10', '2026-08-17'),  # Monday -> the *following* Monday
        ('2026-08-11', '2026-08-17'),  # Tuesday
        ('2026-08-12', '2026-08-17'),  # Wednesday
        ('2026-08-13', '2026-08-17'),  # Thursday
        ('2026-08-14', '2026-08-17'),  # Friday
        ('2026-08-15', '2026-08-17'),  # Saturday
        ('2026-08-16', '2026-08-17'),  # Sunday
    ],
)
def test_next_monday_lands_on_the_following_monday(
    app_js: str, today: str, expected: str
) -> None:
    script = '\n'.join(
        [
            _function_source(app_js, 'localISO'),
            _function_source(app_js, 'nextMondayISO'),
            f'console.log(nextMondayISO(new Date("{today}T12:00")));',
        ]
    )
    assert NODE
    out = subprocess.run(  # noqa: S603  -- script is built from our own app.js
        [NODE, '--input-type=module', '-e', script],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == expected, json.dumps(out.stderr)
