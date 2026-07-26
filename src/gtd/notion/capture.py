"""Quick-capture items to the GTD inbox (Notion Projects table)."""

from datetime import UTC, datetime

import httpx

from gtd.notion.client import (
    _handle_response,
    get_projects_db_id,
    get_token,
    NOTION_API_URL,
    NOTION_VERSION,
)
from gtd.ui import prompt_input


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
    print(f'✓ Captured: "{header}" → Triage')
