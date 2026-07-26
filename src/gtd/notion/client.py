import os
import sys
from http import HTTPStatus

import httpx

from gtd.notion.config import get_config_value


__all__ = [
    'NotionAPIError',
    'add_context',
    'add_list_category',
    'append_page_note',
    'archive_page',
    'build_property_update',
    'get_contexts',
    'get_list_categories',
    'get_page_body',
    'get_projects_db_id',
    'get_select_options',
    'get_token',
    'query_database',
    'remove_context',
    'remove_list_category',
    'rename_context',
    'rename_list_category',
    'replace_page_body',
    'update_page',
]

NOTION_API_URL = 'https://api.notion.com/v1'
NOTION_VERSION = '2022-06-28'


class NotionAPIError(Exception):
    """Raised when a Notion API call fails with an actionable message."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _handle_response(response: httpx.Response) -> None:
    """Check response and raise NotionAPIError with actionable messages."""
    if response.is_success:
        return
    code = response.status_code
    body = response.text
    match code:
        case HTTPStatus.UNAUTHORIZED:
            msg = (
                'Notion token is invalid or expired. '
                'Run `gtd init` or check NOTION_NOTES_TOKEN.'
            )
        case HTTPStatus.FORBIDDEN:
            msg = (
                'Notion integration lacks permission for this resource. '
                "Check your integration's capabilities and page sharing."
            )
        case HTTPStatus.NOT_FOUND:
            msg = (
                'Notion resource not found — it may have been deleted '
                'or the integration lacks access. '
                'Check NOTION_PROJECTS_DB_ID.'
            )
        case HTTPStatus.CONFLICT:
            msg = (
                'Conflict — the page was modified by another process. '
                'Try again.'
            )
        case HTTPStatus.TOO_MANY_REQUESTS:
            retry_after = response.headers.get('Retry-After', '?')
            msg = f'Notion rate limit hit. Retry after {retry_after}s.'
        case _ if code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            msg = f'Notion server error ({code}). Try again later.'
        case _:
            msg = f'Notion API error {code}: {body[:200]}'
    raise NotionAPIError(msg, code)


def get_token() -> str:
    """Get Notion token from config file or environment."""
    token = get_config_value('token') or os.environ.get('NOTION_NOTES_TOKEN')
    if not token:
        print('Error: Notion token not configured.')
        print('Run `gtd init` to set up, or set NOTION_NOTES_TOKEN.')
        sys.exit(1)
    return token


def get_projects_db_id() -> str:
    """Get GTD Projects database ID from config file or environment."""
    db_id = get_config_value('database_id') or os.environ.get(
        'NOTION_PROJECTS_DB_ID',
    )
    if not db_id:
        print('Error: GTD database not configured.')
        print('Run `gtd init` to set up, or set NOTION_PROJECTS_DB_ID.')
        sys.exit(1)
    return db_id


def _headers() -> dict[str, str]:
    return {
        'Authorization': f'Bearer {get_token()}',
        'Content-Type': 'application/json',
        'Notion-Version': NOTION_VERSION,
    }


_TIMEOUT = httpx.Timeout(30.0)


def _get(url: str, **kw: object) -> httpx.Response:
    return httpx.get(url, headers=_headers(), timeout=_TIMEOUT, **kw)


def _post(url: str, **kw: object) -> httpx.Response:
    return httpx.post(url, headers=_headers(), timeout=_TIMEOUT, **kw)


def _patch(url: str, **kw: object) -> httpx.Response:
    return httpx.patch(url, headers=_headers(), timeout=_TIMEOUT, **kw)


def _delete(url: str, **kw: object) -> httpx.Response:
    return httpx.delete(url, headers=_headers(), timeout=_TIMEOUT, **kw)


def query_database(
    *,
    database_id: str | None = None,
    filter_obj: dict | None = None,
    page_size: int = 100,
) -> list[dict]:
    """Query a Notion database and return all pages."""
    if database_id is None:
        database_id = get_projects_db_id()
    url = f'{NOTION_API_URL}/databases/{database_id}/query'
    payload: dict = {'page_size': page_size}
    if filter_obj:
        payload['filter'] = filter_obj

    all_results = []
    has_more = True
    while has_more:
        response = _post(url, json=payload)
        _handle_response(response)
        data = response.json()
        all_results.extend(data['results'])
        has_more = data.get('has_more', False)
        if has_more:
            payload['start_cursor'] = data['next_cursor']

    return all_results


def get_database_schema(
    *,
    database_id: str | None = None,
) -> dict:
    """Fetch database schema (properties and their options)."""
    if database_id is None:
        database_id = get_projects_db_id()
    url = f'{NOTION_API_URL}/databases/{database_id}'
    response = _get(url)
    _handle_response(response)
    return response.json()


def get_select_options(property_name: str) -> list[str]:
    """Get available options for a select property."""
    schema = get_database_schema()
    prop = schema['properties'].get(property_name, {})
    options = prop.get('select', {}).get('options', [])
    return [o['name'] for o in options]


def get_list_categories() -> list[str]:
    """Get available list categories."""
    return get_select_options('List Category')


def add_list_category(name: str) -> None:
    """Add a new list category to the Notion schema."""
    schema = get_database_schema()
    existing = {
        o['name']
        for o in schema['properties']['List Category']['select']['options']
    }
    if name in existing:
        return

    db_id = get_projects_db_id()
    new_opts = schema['properties']['List Category']['select']['options'] + [
        {'name': name}
    ]
    url = f'{NOTION_API_URL}/databases/{db_id}'
    payload = {
        'properties': {'List Category': {'select': {'options': new_opts}}}
    }
    response = _patch(url, json=payload)
    _handle_response(response)


def remove_list_category(name: str) -> None:
    """Remove a list category from the Notion schema."""
    schema = get_database_schema()
    opts = schema['properties']['List Category']['select']['options']
    new_opts = [o for o in opts if o['name'] != name]

    if len(new_opts) == len(opts):
        return  # Category doesn't exist

    db_id = get_projects_db_id()
    url = f'{NOTION_API_URL}/databases/{db_id}'
    payload = {
        'properties': {'List Category': {'select': {'options': new_opts}}}
    }
    response = _patch(url, json=payload)
    _handle_response(response)


def rename_list_category(old_name: str, new_name: str) -> None:
    """Rename a list category in the Notion schema."""
    schema = get_database_schema()
    opts = schema['properties']['List Category']['select']['options']
    new_opts = [
        {'name': new_name} if o['name'] == old_name else o for o in opts
    ]

    db_id = get_projects_db_id()
    url = f'{NOTION_API_URL}/databases/{db_id}'
    payload = {
        'properties': {'List Category': {'select': {'options': new_opts}}}
    }
    response = _patch(url, json=payload)
    _handle_response(response)


def get_contexts() -> list[str]:
    """Get available contexts."""
    return get_select_options('Context')


def add_context(name: str) -> None:
    """Add a new context to the Notion schema."""
    schema = get_database_schema()
    existing = {
        o['name'] for o in schema['properties']['Context']['select']['options']
    }
    if name in existing:
        return

    db_id = get_projects_db_id()
    new_opts = schema['properties']['Context']['select']['options'] + [
        {'name': name}
    ]
    url = f'{NOTION_API_URL}/databases/{db_id}'
    payload = {'properties': {'Context': {'select': {'options': new_opts}}}}
    response = _patch(url, json=payload)
    _handle_response(response)


def remove_context(name: str) -> None:
    """Remove a context from the Notion schema."""
    schema = get_database_schema()
    opts = schema['properties']['Context']['select']['options']
    new_opts = [o for o in opts if o['name'] != name]

    if len(new_opts) == len(opts):
        return  # Context doesn't exist

    db_id = get_projects_db_id()
    url = f'{NOTION_API_URL}/databases/{db_id}'
    payload = {'properties': {'Context': {'select': {'options': new_opts}}}}
    response = _patch(url, json=payload)
    _handle_response(response)


def rename_context(old_name: str, new_name: str) -> None:
    """Rename a context and update all items with that context."""
    # First, fetch all items with old context (while option still exists)
    pages = query_database(
        filter_obj={'property': 'Context', 'select': {'equals': old_name}}
    )

    # Then update schema (rename the option)
    schema = get_database_schema()
    opts = schema['properties']['Context']['select']['options']
    new_opts = [
        {'name': new_name} if o['name'] == old_name else o for o in opts
    ]

    db_id = get_projects_db_id()
    url = f'{NOTION_API_URL}/databases/{db_id}'
    payload = {'properties': {'Context': {'select': {'options': new_opts}}}}
    response = _patch(url, json=payload)
    _handle_response(response)

    # Finally, update all items with the newly renamed context
    for page in pages:
        props = build_property_update(context=new_name)
        update_page(page['id'], props)


def update_page(page_id: str, properties: dict) -> dict:
    """Update a Notion page's properties."""
    url = f'{NOTION_API_URL}/pages/{page_id}'
    payload = {'properties': properties}
    response = _patch(url, json=payload)
    _handle_response(response)
    return response.json()


BLOCK_PREFIXES = {
    'paragraph': '',
    'bulleted_list_item': '• ',
    'numbered_list_item': '· ',
    'to_do': '☐ ',
}


def _extract_block_text(block: dict) -> str | None:
    """Extract text from a supported block type."""
    btype = block['type']
    prefix = BLOCK_PREFIXES.get(btype)
    if prefix is None:
        return None
    texts = block[btype].get('rich_text', [])
    content = ' '.join(t['plain_text'] for t in texts).strip()
    if not content:
        return None
    return f'{prefix}{content}'


def get_page_body(page_id: str) -> str:
    """Fetch the text content from a page's body blocks."""
    url = f'{NOTION_API_URL}/blocks/{page_id}/children'
    response = _get(url)
    _handle_response(response)
    blocks = response.json().get('results', [])
    lines = []
    for block in blocks:
        text = _extract_block_text(block)
        if text is not None:
            lines.append(text)
    return '\n'.join(lines)


def append_page_note(page_id: str, text: str) -> dict:
    """Append a paragraph block to a page's body."""
    url = f'{NOTION_API_URL}/blocks/{page_id}/children'
    payload = {
        'children': [
            {
                'object': 'block',
                'type': 'paragraph',
                'paragraph': {
                    'rich_text': [{'text': {'content': text}}],
                },
            },
        ],
    }
    response = _patch(url, json=payload)
    _handle_response(response)
    return response.json()


def _delete_block(block_id: str) -> None:
    """Delete a single block."""
    url = f'{NOTION_API_URL}/blocks/{block_id}'
    response = _delete(url)
    _handle_response(response)


def _update_block_text(block_id: str, text: str) -> None:
    """Update a paragraph block's text content."""
    url = f'{NOTION_API_URL}/blocks/{block_id}'
    payload = {
        'paragraph': {
            'rich_text': [{'text': {'content': text}}],
        },
    }
    response = _patch(url, json=payload)
    _handle_response(response)


def replace_page_body(page_id: str, text: str) -> None:
    """Replace a page's body with new text, minimizing API calls.

    Only updates blocks that changed, appends new ones, and
    deletes extras.
    """
    url = f'{NOTION_API_URL}/blocks/{page_id}/children'
    response = _get(url)
    _handle_response(response)
    old_blocks = response.json().get('results', [])

    new_lines = text.split('\n') if text.strip() else []

    # Extract old block texts for comparison
    old_texts = []
    for block in old_blocks:
        extracted = _extract_block_text(block)
        old_texts.append(extracted or '')

    # Update existing blocks that changed
    for i, block in enumerate(old_blocks):
        if i < len(new_lines):
            if old_texts[i] != new_lines[i]:
                _update_block_text(block['id'], new_lines[i])
        else:
            # Extra old blocks — delete
            _delete_block(block['id'])

    # Append new blocks beyond old length
    if len(new_lines) > len(old_blocks):
        children = [
            {
                'object': 'block',
                'type': 'paragraph',
                'paragraph': {
                    'rich_text': [
                        {'text': {'content': line}},
                    ],
                },
            }
            for line in new_lines[len(old_blocks) :]
        ]
        for i in range(0, len(children), 100):
            batch = children[i : i + 100]
            resp = _patch(url, json={'children': batch})
            _handle_response(resp)


def build_property_update(  # noqa: C901, PLR0912
    *,
    name: str | None = None,
    status: str | None = None,
    context: str | None = None,
    list_category: str | None = None,
    next_step: str | None = None,
    success_condition: str | None = None,
    due_date: str | None = None,
    follow_up_date: str | None = None,
) -> dict:
    """Build a properties dict for a page update."""
    props: dict = {}
    if name is not None:
        props['Header'] = {'title': [{'text': {'content': name}}]}
    if status is not None:
        props['Status'] = {'select': {'name': status}}
    if context is not None:
        if context == '':
            props['Context'] = {'select': None}
        else:
            props['Context'] = {'select': {'name': context}}
    if list_category is not None:
        if list_category == '':
            props['List Category'] = {'select': None}
        else:
            props['List Category'] = {'select': {'name': list_category}}
    if next_step is not None:
        props['Next Actionable Step'] = {
            'rich_text': [{'text': {'content': next_step}}],
        }
    if success_condition is not None:
        props['Success Condition'] = {
            'rich_text': [{'text': {'content': success_condition}}],
        }
    if due_date is not None:
        if due_date == '':
            props['Due Date'] = {'date': None}
        else:
            props['Due Date'] = {'date': {'start': due_date}}
    if follow_up_date is not None:
        if follow_up_date == '':
            props['Follow-Up Date'] = {'date': None}
        else:
            props['Follow-Up Date'] = {'date': {'start': follow_up_date}}
    return props


def archive_page(page_id: str) -> dict:
    """Move a Notion page to trash."""
    url = f'{NOTION_API_URL}/pages/{page_id}'
    payload = {'in_trash': True}
    response = _patch(url, json=payload)
    _handle_response(response)
    return response.json()
