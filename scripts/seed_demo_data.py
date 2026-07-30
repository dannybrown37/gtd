"""Create/reseed a demo GTD Notion database with fake data, for screenshots.

Creates a "GTD Projects (Demo)" database as a sibling of the real Projects
database (same parent page), remembers its ID in .gtd_demo_db.json
(gitignored), and reseeds it with fictional entries covering every TUI tab.
Never touches the real database or ~/.config/gtd/config.json.

Usage:
    NOTION_NOTES_TOKEN=... NOTION_PROJECTS_DB_ID=... \\
        uv run python scripts/seed_demo_data.py

Prints the demo database ID on success — pass it as NOTION_PROJECTS_DB_ID to
scripts/capture_screenshots.py.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from gtd.notion.client import NOTION_API_URL, NOTION_VERSION, _handle_response
from gtd.notion.schema import DB_SCHEMA

_HTTP_OK = 200

_CACHE_PATH = Path(__file__).resolve().parent.parent / '.gtd_demo_db.json'
_DEMO_TITLE = 'GTD Projects (Demo)'

_CONTEXTS = ['Computer', 'Home', 'Phone', 'Errands', 'Work', 'Creative']
_AREAS = ['Health', 'Career', 'Home', 'Finances', 'Learning']
_LIST_CATEGORIES = [
    'Books to Read',
    'Shows to Watch',
    'Restaurants to Try',
    'Gift Ideas',
    'Travel Bucket List',
]


def _headers(token: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Notion-Version': NOTION_VERSION,
    }


def _get_parent_page_id(token: str, real_db_id: str) -> str:
    url = f'{NOTION_API_URL}/databases/{real_db_id}'
    response = httpx.get(url, headers=_headers(token))
    _handle_response(response)
    parent = response.json()['parent']
    if parent.get('type') != 'page_id':
        msg = f'Real database has unsupported parent type: {parent}'
        raise RuntimeError(msg)
    return parent['page_id']


def _demo_schema() -> dict:
    schema = json.loads(json.dumps(DB_SCHEMA))
    schema['Context'] = {
        'select': {'options': [{'name': c} for c in _CONTEXTS]},
    }
    schema['Area'] = {'select': {'options': [{'name': a} for a in _AREAS]}}
    schema['List Category'] = {
        'select': {'options': [{'name': lc} for lc in _LIST_CATEGORIES]},
    }
    return schema


def _create_demo_db(token: str, parent_page_id: str) -> str:
    url = f'{NOTION_API_URL}/databases'
    payload = {
        'parent': {'type': 'page_id', 'page_id': parent_page_id},
        'title': [{'type': 'text', 'text': {'content': _DEMO_TITLE}}],
        'properties': _demo_schema(),
    }
    response = httpx.post(url, headers=_headers(token), json=payload)
    _handle_response(response)
    return response.json()['id']


def _db_exists(token: str, db_id: str) -> bool:
    url = f'{NOTION_API_URL}/databases/{db_id}'
    response = httpx.get(url, headers=_headers(token))
    return response.status_code == _HTTP_OK


def _find_or_create_demo_db(token: str, real_db_id: str) -> str:
    if _CACHE_PATH.exists():
        cached = json.loads(_CACHE_PATH.read_text()).get('database_id')
        if cached and _db_exists(token, cached):
            print(f'  Reusing existing demo database {cached}')
            return cached

    parent_page_id = _get_parent_page_id(token, real_db_id)
    print(f'  Creating "{_DEMO_TITLE}" under parent page {parent_page_id}...')
    db_id = _create_demo_db(token, parent_page_id)
    _CACHE_PATH.write_text(json.dumps({'database_id': db_id}, indent=2) + '\n')
    print(f'  Created demo database {db_id}')
    return db_id


def _wipe_existing_entries(token: str, db_id: str) -> None:
    url = f'{NOTION_API_URL}/databases/{db_id}/query'
    response = httpx.post(
        url,
        headers=_headers(token),
        json={'page_size': 100},
    )
    _handle_response(response)
    pages = response.json()['results']
    for page in pages:
        patch_url = f'{NOTION_API_URL}/pages/{page["id"]}'
        httpx.patch(
            patch_url,
            headers=_headers(token),
            json={'in_trash': True},
        )
    if pages:
        print(f'  Cleared {len(pages)} existing demo entries')


def _date(offset_days: int) -> str:
    return (datetime.now() + timedelta(days=offset_days)).strftime('%Y-%m-%d')


def _entry(
    header: str,
    status: str,
    *,
    context: str = '',
    next_step: str = '',
    success_condition: str = '',
    due: str | None = None,
    follow_up: str | None = None,
    area: str = '',
    list_category: str = '',
) -> dict:
    props: dict = {
        'Header': {'title': [{'text': {'content': header}}]},
        'Status': {'select': {'name': status}},
        'Created Date': {
            'date': {'start': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')},
        },
    }
    if context:
        props['Context'] = {'select': {'name': context}}
    if next_step:
        props['Next Actionable Step'] = {
            'rich_text': [{'text': {'content': next_step}}],
        }
    if success_condition:
        props['Success Condition'] = {
            'rich_text': [{'text': {'content': success_condition}}],
        }
    if due:
        props['Due Date'] = {'date': {'start': due}}
    if follow_up:
        props['Follow-Up Date'] = {'date': {'start': follow_up}}
    if area:
        props['Area'] = {'select': {'name': area}}
    if list_category:
        props['List Category'] = {'select': {'name': list_category}}
    return props


def _demo_entries() -> list[dict]:
    return [
        # Current Project — active (shows in Today + Next Steps + Projects)
        _entry(
            'Finish Q3 budget review',
            'Current Project',
            context='Work',
            next_step='Pull latest actuals from finance dashboard',
            success_condition='Budget doc approved by manager',
            due=_date(3),
        ),
        _entry(
            'Plan weekend camping trip',
            'Current Project',
            context='Home',
            next_step='Book campsite for October',
        ),
        _entry(
            'Repaint garage door',
            'Current Project',
            context='Home',
            next_step='Buy exterior paint and drop cloths',
        ),
        _entry(
            'Migrate blog to new host',
            'Current Project',
            context='Computer',
            next_step='Export posts from old CMS',
        ),
        _entry(
            'Renew passport',
            'Current Project',
            context='Errands',
            next_step='Take passport photo at pharmacy',
            due=_date(30),
        ),
        _entry(
            'Prep quarterly presentation',
            'Current Project',
            context='Work',
            next_step='Draft outline slides',
            due=_date(5),
        ),
        # Current Project — snoozed (shows in Incubation, not Today)
        _entry(
            'Redesign personal website',
            'Current Project',
            context='Creative',
            next_step='Sketch new homepage layout',
            follow_up=_date(14),
        ),
        _entry(
            'Research home solar panels',
            'Current Project',
            context='Home',
            next_step='Get three contractor quotes',
            follow_up=_date(21),
        ),
        # Recurring — due today (shows in Today + Recurring)
        _entry(
            'Water the office plants',
            'Recurring',
            context='Home',
            next_step='Water and check soil moisture',
            follow_up=_date(0),
        ),
        _entry(
            'Weekly grocery run',
            'Recurring',
            context='Errands',
            next_step='Restock groceries for the week',
            follow_up=_date(-1),
        ),
        _entry(
            'Change HVAC filter',
            'Recurring',
            context='Home',
            next_step='Swap the furnace filter',
        ),
        # Recurring — not due yet (Recurring tab only)
        _entry(
            'Backup laptop to NAS',
            'Recurring',
            context='Computer',
            next_step='Run the backup script',
            follow_up=_date(5),
        ),
        # Waiting For
        _entry(
            'Feedback on proposal from Alex',
            'Waiting For',
            context='Work',
            next_step='Waiting on Alex to review the draft proposal',
        ),
        _entry(
            'Contractor quote for fence repair',
            'Waiting For',
            context='Home',
            next_step='Waiting on contractor to send written quote',
        ),
        _entry(
            'Signed lease copy from landlord',
            'Waiting For',
            context='Home',
            next_step='Waiting on landlord to send signed copy',
        ),
        # Someday/Maybe
        _entry('Train for a half marathon', 'Someday/Maybe', area='Health'),
        _entry('Learn woodworking basics', 'Someday/Maybe', area='Learning'),
        _entry(
            'Get certified in project management',
            'Someday/Maybe',
            area='Career',
        ),
        _entry(
            'Build a backyard vegetable garden',
            'Someday/Maybe',
            area='Home',
        ),
        _entry(
            'Open a high-yield savings account',
            'Someday/Maybe',
            area='Finances',
        ),
        _entry('Try a digital minimalism month', 'Someday/Maybe'),
        # List
        _entry(
            'Project Hail Mary',
            'List',
            list_category='Books to Read',
        ),
        _entry('Severance', 'List', list_category='Shows to Watch'),
        _entry('The Bear', 'List', list_category='Shows to Watch'),
        _entry(
            'That new ramen place downtown',
            'List',
            list_category='Restaurants to Try',
        ),
        _entry(
            'Noise-cancelling headphones for Dad',
            'List',
            list_category='Gift Ideas',
        ),
        _entry(
            'Portugal coastal road trip',
            'List',
            list_category='Travel Bucket List',
        ),
        # Fresh inbox captures
        _entry('Ask about extending gym membership', 'Triage'),
        _entry('Look into tax-advantaged accounts', 'Triage'),
        _entry('Follow up on doctor referral', 'Triage'),
        _entry('Idea: automate weekly status email', 'Triage'),
    ]


def _create_page(token: str, db_id: str, properties: dict) -> None:
    url = f'{NOTION_API_URL}/pages'
    payload = {'parent': {'database_id': db_id}, 'properties': properties}
    response = httpx.post(url, headers=_headers(token), json=payload)
    _handle_response(response)


def main() -> None:
    token = os.environ.get('NOTION_NOTES_TOKEN')
    real_db_id = os.environ.get('NOTION_PROJECTS_DB_ID')
    if not token or not real_db_id:
        print('Set NOTION_NOTES_TOKEN and NOTION_PROJECTS_DB_ID.')
        sys.exit(1)

    demo_db_id = _find_or_create_demo_db(token, real_db_id)
    _wipe_existing_entries(token, demo_db_id)

    entries = _demo_entries()
    print(f'  Seeding {len(entries)} demo entries...')
    for props in entries:
        _create_page(token, demo_db_id, props)
    print(f'\n✓ Demo database ready: {demo_db_id}')
    print(f'  NOTION_PROJECTS_DB_ID={demo_db_id}')


if __name__ == '__main__':
    main()
