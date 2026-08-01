"""Quick-capture items to the GTD inbox (Notion Projects table)."""

import re
from datetime import UTC, datetime

import httpx

from gtd.notion.client import (
    _handle_response,
    add_context,
    get_contexts,
    get_projects_db_id,
    get_token,
    NOTION_API_URL,
    NOTION_VERSION,
)
from gtd.ui import prompt_input

_AGENDA_PATTERN = re.compile(r"@([A-Za-z][\w'-]*)")


def extract_agenda_context(header: str) -> str | None:
    """Return the Context value for the first @Name mention in header.

    Reuses an existing context's casing if one matches case-insensitively
    (so "@chris" and "@Chris" don't create two separate contexts).
    """
    match = _AGENDA_PATTERN.search(header)
    if not match:
        return None
    name = match.group(1)
    candidate = f'@{name[0].upper()}{name[1:]}'
    existing = next(
        (c for c in get_contexts() if c.lower() == candidate.lower()), None
    )
    return existing or candidate


def _create_page(header: str) -> dict:
    """Create a new page in the Projects database with Triage status."""
    db_id = get_projects_db_id()
    url = f'{NOTION_API_URL}/pages'
    headers = {
        'Authorization': f'Bearer {get_token()}',
        'Content-Type': 'application/json',
        'Notion-Version': NOTION_VERSION,
    }

    properties: dict = {
        'Header': {
            'title': [{'text': {'content': header}}],
        },
        'Status': {
            'select': {'name': 'Triage'},
        },
        'Created Date': {
            'date': {
                'start': datetime.now(tz=UTC).strftime(
                    '%Y-%m-%dT%H:%M:%SZ',
                ),
            },
        },
    }

    agenda_context = extract_agenda_context(header)
    if agenda_context:
        if agenda_context not in get_contexts():
            add_context(agenda_context)
        name = agenda_context.removeprefix('@')
        properties['Context'] = {'select': {'name': agenda_context}}
        properties['Status'] = {'select': {'name': 'Current Project'}}
        properties['Success Condition'] = {
            'rich_text': [{'text': {'content': f'Discussed with {name}'}}]
        }
        properties['Next Actionable Step'] = {
            'rich_text': [{'text': {'content': f'Discuss with {name}'}}]
        }

    payload = {
        'parent': {'database_id': db_id},
        'properties': properties,
    }

    response = httpx.post(url, headers=headers, json=payload)
    _handle_response(response)
    return response.json()


def capture_item(header: str | None = None) -> None:
    """Capture a new item to the GTD inbox."""
    if not header:
        header = prompt_input('What needs capturing? ')
        if not header:
            print('Nothing to capture.')
            return

    _create_page(header)
    status = 'Current Project' if _AGENDA_PATTERN.search(header) else 'Triage'
    print(f'✓ Captured: "{header}" → {status}')
