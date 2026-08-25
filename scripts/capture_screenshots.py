"""Capture SVG screenshots of every GTD TUI tab, for docs/README.

Runs the real GTDApp headlessly via Textual's Pilot test harness against
whatever NOTION_PROJECTS_DB_ID points at — normally the demo database
created by scripts/seed_demo_data.py. No extra tooling required: Textual's
App.export_screenshot() ships in core.

The tab list is discovered from the running app, not hardcoded, so a new
TabPane screenshots itself. tests/test_capture_screenshots.py enforces that.

Usage:
    NOTION_NOTES_TOKEN=... NOTION_PROJECTS_DB_ID=<demo db id> \\
        uv run python scripts/capture_screenshots.py [--with-private]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from gtd.gtd_tui import GTDApp
from textual.widgets import TabPane, TabbedContent

_OUT_DIR = Path(__file__).resolve().parent.parent / 'docs' / 'screenshots'
_SIZE = (130, 45)

# Existing README <img> paths that predate slug-from-id, kept so a
# refactor never rewrites a link. Only exceptions belong here — every
# other tab derives its filename from its id.
SLUG_OVERRIDES = {
    'tab-waiting': 'waiting-for',
    'tab-snoozed': 'incubation',
}

# Tabs fed by the user's real calendars, not the demo Notion database.
# Seeding cannot fake them, so capturing one publishes real meetings.
PRIVATE_TABS = frozenset({'tab-calendar'})


def _slug(tab_id: str) -> str:
    return SLUG_OVERRIDES.get(tab_id, tab_id.removeprefix('tab-'))


def discover_tabs(tab_ids: list[str]) -> list[tuple[str, str]]:
    """Pair each tab id with the screenshot filename stem it writes."""
    return [(tab_id, _slug(tab_id)) for tab_id in tab_ids]


def select_tabs(
    tabs: list[tuple[str, str]],
    *,
    with_private: bool,
) -> list[tuple[str, str]]:
    if with_private:
        return tabs
    return [
        (tab_id, slug) for tab_id, slug in tabs if tab_id not in PRIVATE_TABS
    ]


async def _capture(*, with_private: bool) -> None:
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    app = GTDApp()
    async with app.run_test(size=_SIZE) as pilot:
        # Every tab kicks off a threaded Notion fetch on mount; give them
        # time to land before the first screenshot.
        await pilot.pause(2)

        tabs = app.query_one('#tabs', TabbedContent)
        tab_ids = [pane.id for pane in tabs.query(TabPane) if pane.id]
        targets = select_tabs(
            discover_tabs(tab_ids), with_private=with_private
        )

        for tab_id, name in targets:
            tabs.active = tab_id
            await pilot.pause(0.5)
            svg = app.export_screenshot(title='gtd')
            out_path = _OUT_DIR / f'{name}.svg'
            out_path.write_text(svg)
            print(f'  wrote {out_path.relative_to(_OUT_DIR.parent.parent)}')

    skipped = (
        sorted(set(PRIVATE_TABS) & set(tab_ids)) if not with_private else []
    )
    if skipped:
        print(
            f'\nSkipped {", ".join(skipped)} — pass --with-private to include.'
        )
        print(
            'Those render your real calendar; check the SVG before committing.'
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--with-private',
        action='store_true',
        help='also capture the Calendar tab, which shows your real meetings',
    )
    args = parser.parse_args()

    if not os.environ.get('NOTION_NOTES_TOKEN') or not os.environ.get(
        'NOTION_PROJECTS_DB_ID',
    ):
        print('Set NOTION_NOTES_TOKEN and NOTION_PROJECTS_DB_ID (demo db).')
        sys.exit(1)
    asyncio.run(_capture(with_private=args.with_private))
    print(f'\n✓ Screenshots written to {_OUT_DIR}')


if __name__ == '__main__':
    main()
