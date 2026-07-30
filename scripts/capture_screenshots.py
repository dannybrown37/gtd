"""Capture SVG screenshots of every GTD TUI tab, for docs/README.

Runs the real GTDApp headlessly via Textual's Pilot test harness against
whatever NOTION_PROJECTS_DB_ID points at — normally the demo database
created by scripts/seed_demo_data.py. No extra tooling required: Textual's
App.export_screenshot() ships in core.

Usage:
    NOTION_NOTES_TOKEN=... NOTION_PROJECTS_DB_ID=<demo db id> \\
        uv run python scripts/capture_screenshots.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from gtd.gtd_tui import GTDApp
from textual.widgets import TabbedContent

_OUT_DIR = Path(__file__).resolve().parent.parent / 'docs' / 'screenshots'
_SIZE = (130, 45)

_TABS = [
    ('tab-today', 'today'),
    ('tab-next-steps', 'next-steps'),
    ('tab-inbox', 'inbox'),
    ('tab-projects', 'projects'),
    ('tab-waiting', 'waiting-for'),
    ('tab-snoozed', 'incubation'),
    ('tab-recurring', 'recurring'),
    ('tab-someday', 'someday'),
    ('tab-lists', 'lists'),
]


async def _capture() -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    app = GTDApp()
    async with app.run_test(size=_SIZE) as pilot:
        # Every tab kicks off a threaded Notion fetch on mount; give them
        # time to land before the first screenshot.
        await pilot.pause(2)

        tabs = app.query_one('#tabs', TabbedContent)
        for tab_id, name in _TABS:
            tabs.active = tab_id
            await pilot.pause(0.5)
            svg = app.export_screenshot(title='gtd')
            out_path = _OUT_DIR / f'{name}.svg'
            out_path.write_text(svg)
            print(f'  wrote {out_path.relative_to(_OUT_DIR.parent.parent)}')


def main() -> None:
    if not os.environ.get('NOTION_NOTES_TOKEN') or not os.environ.get(
        'NOTION_PROJECTS_DB_ID',
    ):
        print('Set NOTION_NOTES_TOKEN and NOTION_PROJECTS_DB_ID (demo db).')
        sys.exit(1)
    asyncio.run(_capture())
    print(f'\n✓ Screenshots written to {_OUT_DIR}')


if __name__ == '__main__':
    main()
