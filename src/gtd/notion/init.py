"""Database initialization and schema management for GTD CLI."""

import os
import re

import httpx

from gtd.notion.client import NOTION_API_URL, NOTION_VERSION, _handle_response
from gtd.notion.config import load_config, save_config
from gtd.notion.schema import DB_SCHEMA, STATUSES
from gtd.ui import prompt_input

_HTTP_OK = 200


def _auth_headers(token: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Notion-Version': NOTION_VERSION,
    }


def _parse_notion_id(raw: str) -> str | None:
    """Extract a 32-char hex Notion ID from various input formats.

    Handles:
      - Raw ID: 87461ba78e4149c7b6a1295e8bbc298d
      - UUID: 87461ba7-8e41-49c7-b6a1-295e8bbc298d
      - Title-prefixed: Home-Base-87461ba78e4149c7b6a1295e8bbc298d
      - Full URL: https://notion.so/Home-Base-87461ba78e4149c7b6a1295e8bbc298d
    """
    raw = raw.strip().rstrip('/')
    # Take the last path segment if it's a URL
    raw = raw.split('/')[-1]
    # Strip query params
    raw = raw.split('?')[0]
    # The ID is always the last 32 hex chars (possibly with dashes)
    match = re.search(
        r'([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?'
        r'[0-9a-f]{4}-?[0-9a-f]{12})$',
        raw,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).replace('-', '')
    return None


def _validate_token(token: str) -> bool:
    """Check that the token can authenticate with Notion."""
    url = f'{NOTION_API_URL}/users/me'
    response = httpx.get(url, headers=_auth_headers(token))
    return response.status_code == _HTTP_OK


def _validate_parent_page(token: str, page_id: str) -> bool:
    """Check that the parent page exists and is accessible."""
    # Try /pages first, then /blocks (covers more page types)
    headers = _auth_headers(token)
    for endpoint in ('pages', 'blocks'):
        url = f'{NOTION_API_URL}/{endpoint}/{page_id}'
        response = httpx.get(url, headers=headers)
        if response.status_code == _HTTP_OK:
            return True
    # Show the actual error for debugging
    try:
        detail = response.json().get('message', response.text)
    except Exception:
        detail = response.text
    print(f'(HTTP {response.status_code}: {detail})')
    return False


def _create_database(token: str, parent_page_id: str) -> str:
    """Create the GTD Projects database and return its ID."""
    url = f'{NOTION_API_URL}/databases'
    payload = {
        'parent': {'type': 'page_id', 'page_id': parent_page_id},
        'title': [
            {'type': 'text', 'text': {'content': 'GTD Projects'}},
        ],
        'properties': DB_SCHEMA,
    }
    response = httpx.post(
        url,
        headers=_auth_headers(token),
        json=payload,
    )
    _handle_response(response)
    return response.json()['id']


def _get_existing_schema(token: str, db_id: str) -> dict:
    """Fetch the current database schema."""
    url = f'{NOTION_API_URL}/databases/{db_id}'
    response = httpx.get(url, headers=_auth_headers(token))
    _handle_response(response)
    return response.json()


def _upgrade_schema(token: str, db_id: str) -> list[str]:
    """Patch an existing database to match the expected schema.

    Returns a list of changes made.
    """
    existing = _get_existing_schema(token, db_id)
    existing_props = existing.get('properties', {})
    changes: list[str] = []
    patch_props: dict = {}

    for prop_name, prop_def in DB_SCHEMA.items():
        if prop_name not in existing_props:
            patch_props[prop_name] = prop_def
            changes.append(f'Added property: {prop_name}')
        elif 'select' in prop_def:
            existing_opts = {
                o['name']
                for o in existing_props[prop_name]
                .get('select', {})
                .get('options', [])
            }
            expected_opts = [
                o['name'] for o in prop_def['select'].get('options', [])
            ]
            missing = [o for o in expected_opts if o not in existing_opts]
            if missing:
                cur = existing_props[prop_name]['select']['options']
                merged = cur + [{'name': m} for m in missing]
                patch_props[prop_name] = {
                    'select': {'options': merged},
                }
                changes.append(
                    f'Added options to {prop_name}: {", ".join(missing)}',
                )

    if patch_props:
        url = f'{NOTION_API_URL}/databases/{db_id}'
        response = httpx.patch(
            url,
            headers=_auth_headers(token),
            json={'properties': patch_props},
        )
        _handle_response(response)

    return changes


def _resolve_token(config: dict) -> str | None:
    """Get token from config, env var, or prompt."""
    token = config.get('token') or os.environ.get('NOTION_NOTES_TOKEN')
    if token:
        return token
    return prompt_input('Notion integration token: ')


def _run_upgrade(config: dict) -> None:
    """Upgrade an existing database schema."""
    db_id = config.get('database_id') or os.environ.get(
        'NOTION_PROJECTS_DB_ID'
    )
    if not db_id:
        print('No existing database configured. Run `gtd init` first.')
        return

    token = _resolve_token(config)
    if not token:
        return

    print(f'  Upgrading database {db_id}...\n')
    changes = _upgrade_schema(token, db_id)
    if changes:
        for change in changes:
            print(f'  ✓ {change}')
        print(f'\n  ✓ Schema upgraded ({len(changes)} change(s))')
    else:
        print('  ✓ Schema already up to date')


def _run_fresh_init(config: dict) -> None:  # noqa: PLR0915
    """Create a new GTD database from scratch."""
    print('══════════════════════════════════')
    print('       🚀 GTD Setup')
    print('══════════════════════════════════\n')

    existing_db = config.get('database_id')
    if existing_db:
        print(f'  ⚠ Existing database: {existing_db}')
        confirm = prompt_input('  Overwrite? (y/N): ')
        if not confirm or confirm.lower() != 'y':
            print('  Cancelled.')
            return
        print()

    # Step 1: Token
    print('  Step 1: Notion Integration Token')
    print('  ─────────────────────────────────')
    token = config.get('token', '')
    if token:
        print(f'  Using saved token: {token[:8]}...')
    else:
        print('  1. Go to https://www.notion.so/my-integrations')
        print('  2. Click "New integration"')
        print('  3. Name it (e.g. "GTD"), select your workspace')
        print('  4. Copy the "Internal Integration Secret"\n')
        token = prompt_input('  Paste token here: ')
        if not token:
            return

    print('  Validating token...', end=' ', flush=True)
    if not _validate_token(token):
        print('✗')
        print('  Error: Token is invalid or expired.')
        return
    print('✓\n')

    # Step 2: Parent page
    print('  Step 2: Choose a Parent Page')
    print('  ────────────────────────────')
    print('  The GTD database will live inside a Notion page.')
    print()
    print('  1. Open (or create) the page in Notion')
    print('  2. Click ••• (top right) → Connections')
    print('     → Add your integration')
    print('  3. Copy the page link: ••• → Copy link')
    print()
    raw_input = prompt_input(
        '  Paste page URL or ID here: ',
    )
    if not raw_input:
        return

    parent_id = _parse_notion_id(raw_input)
    if not parent_id:
        print('  Error: Could not extract a Notion ID from input.')
        return

    print('  Validating page access...', end=' ', flush=True)
    if not _validate_parent_page(token, parent_id):
        print('✗\n')
        print('  Troubleshooting:')
        print('  • Open the page → ••• → Connections')
        print('  • Remove and re-add your integration')
        print('  • Use ••• → Copy link to get the URL')
        print('    (the browser URL may differ)')
        return
    print('✓\n')

    # Create
    print('  Creating GTD Projects database...', end=' ', flush=True)
    db_id = _create_database(token, parent_id)
    print('✓\n')

    # Save config
    config['token'] = token
    config['database_id'] = db_id
    save_config(config)

    _print_setup_summary(db_id)


def _print_setup_summary(db_id: str) -> None:
    """Print post-init summary."""
    print(f'  Database ID: {db_id}')
    print('  Config saved to: ~/.config/gtd/config.json')
    print()
    print('  Properties created:')
    for prop_name in DB_SCHEMA:
        print(f'    • {prop_name}')
    print(f'\n  Statuses: {", ".join(STATUSES)}')
    print()
    print('══════════════════════════════════')
    print('  ✓ Setup complete! Run `gtd` to start.')
    print('══════════════════════════════════\n')


def init_database(*, upgrade: bool = False) -> None:
    """Interactive database initialization or upgrade."""
    config = load_config()

    if upgrade:
        _run_upgrade(config)
    else:
        _run_fresh_init(config)
