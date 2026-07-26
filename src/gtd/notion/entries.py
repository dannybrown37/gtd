"""Entry listing, selection, and field editing."""

import json as json_mod
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from dateutil import parser as dateparser


__all__ = [
    'list_entries',
    'select_entry',
    'show_triage',
    'update_entry',
    'update_entry_by_ref',
]

from gtd.notion.client import (
    build_property_update,
    get_page_body,
    get_select_options,
    query_database,
    replace_page_body,
    update_page,
)
from gtd.notion.display import format_entry_list
from gtd.notion.models import ProjectEntry
from gtd.notion.schema import STATUSES
from gtd.ui import fzf_on_a_list, prompt_input


_RELATIVE_DAYS = {
    'today': 0,
    'tomorrow': 1,
    'yesterday': -1,
}


def _parse_date_input(raw: str) -> str | None:
    """Parse a date string, returning YYYY-MM-DD or None."""
    lowered = raw.lower().strip()
    if lowered in _RELATIVE_DAYS:
        result = datetime.now() + timedelta(days=_RELATIVE_DAYS[lowered])
        return result.strftime('%Y-%m-%d')
    try:
        parsed = dateparser.parse(raw, fuzzy=True)
    except (ValueError, OverflowError):
        parsed = None
    if parsed:
        return parsed.strftime('%Y-%m-%d')
    print(f'  Could not parse "{raw}", skipping.')
    return None


def _today_filter() -> dict:
    """Build the Notion filter for today's actionable items."""
    today = datetime.now().strftime('%Y-%m-%d')
    active_statuses = ['Current Project', 'Recurring']
    return {
        'and': [
            {
                'or': [
                    {'property': 'Status', 'select': {'equals': s}}
                    for s in active_statuses
                ],
            },
            {
                'or': [
                    {
                        'property': 'Follow-Up Date',
                        'date': {'on_or_before': today},
                    },
                    {
                        'property': 'Follow-Up Date',
                        'date': {'is_empty': True},
                    },
                ],
            },
        ],
    }


def _get_today_entries() -> list[ProjectEntry]:
    """Fetch and filter today's actionable entries."""
    pages = query_database(filter_obj=_today_filter())
    entries = [ProjectEntry.from_page(p) for p in pages]
    return [e for e in entries if e.context and e.next_step]


def _escape_for_shell(text: str) -> str:
    """Escape text for use in single-quoted shell strings."""
    return text.replace("'", "'\\''")


def _entry_preview_text(
    entry: ProjectEntry,
    body: str = '',
) -> str:
    """Build a preview string for an entry."""
    lines = [
        f'── {entry.header.strip()} ──',
        '',
        f'Status:    {entry.status}',
        f'Context:   {entry.context or "(none)"}',
        f'Next step: {entry.next_step or "(none)"}',
        f'Success condition: {entry.success_condition or "(none)"}',
        f'Due:       {entry.due_date or "(none)"}',
        f'Follow-up: {entry.follow_up_date or "(none)"}',
    ]
    if body:
        lines.append('')
        lines.append('── Notes ──')
        for line in body.split('\n'):
            lines.append(f'  {line}')
    return '\n'.join(lines)


def select_entry(
    entries: list[ProjectEntry],
    *,
    prompt: str = 'Select',
    include_body: bool = True,
) -> ProjectEntry | None:
    """Show an fzf picker with preview for a list of entries."""
    if not entries:
        return None

    entry_map = {e.header.strip(): e for e in entries}
    headers = list(entry_map.keys())

    preview_data = {
        e.header.strip(): {
            'props': _entry_preview_text(e),
            'page_id': e.page_id if include_body else None,
            'body': None,
        }
        for e in entries
    }

    fd, data_path = tempfile.mkstemp(suffix='.json')
    with open(fd, 'w') as f:  # noqa: PTH123
        f.write(json_mod.dumps(preview_data))

    fd2, script_path = tempfile.mkstemp(suffix='.py')
    with open(fd2, 'w') as f:  # noqa: PTH123
        f.write(
            'import json, sys, os, fcntl\n'
            f'DATA = "{data_path}"\n'
            'key = sys.argv[1]\n'
            'with open(DATA) as f:\n'
            '    fcntl.flock(f, fcntl.LOCK_SH)\n'
            '    d = json.load(f)\n'
            'entry = d.get(key, {})\n'
            'if not entry:\n'
            '    sys.exit(0)\n'
            'print(entry["props"])\n'
            'page_id = entry.get("page_id")\n'
            'if not page_id:\n'
            '    sys.exit(0)\n'
            'body = entry.get("body")\n'
            'if body is None:\n'
            '    import httpx\n'
            '    token = os.environ.get("NOTION_NOTES_TOKEN", "")\n'
            '    url = f"https://api.notion.com/v1/blocks/{page_id}'
            '/children"\n'
            '    h = {"Authorization": f"Bearer {token}",'
            ' "Notion-Version": "2022-06-28"}\n'
            '    prefixes = {"paragraph": "", '
            '"bulleted_list_item": "• ",'
            ' "numbered_list_item": "· ",'
            ' "to_do": "☐ "}\n'
            '    try:\n'
            '        resp = httpx.get(url, headers=h)\n'
            '        blocks = resp.json().get("results", [])\n'
            '        lines = []\n'
            '        for b in blocks:\n'
            '            bt = b["type"]\n'
            '            if bt in prefixes:\n'
            '                texts = b[bt].get("rich_text", [])\n'
            '                c = " ".join('
            't["plain_text"] for t in texts).strip()\n'
            '                if c:\n'
            '                    lines.append(prefixes[bt] + c)\n'
            '        body = chr(10).join(lines)\n'
            '    except Exception:\n'
            '        body = ""\n'
            '    d[key]["body"] = body\n'
            '    with open(DATA, "w") as f:\n'
            '        fcntl.flock(f, fcntl.LOCK_EX)\n'
            '        json.dump(d, f)\n'
            'if body and body.strip():\n'
            '    print()\n'
            '    print("── Notes ──")\n'
            '    for line in body.split(chr(10)):\n'
            '        print(f"  {line}")\n',
        )

    try:
        python = sys.executable
        selection = fzf_on_a_list(
            headers,
            prompt=prompt,
            preview=f'{python} {script_path} {{}}',
        )
    finally:
        Path(data_path).unlink(missing_ok=True)
        Path(script_path).unlink(missing_ok=True)

    if not selection:
        return None
    return entry_map[selection]


def _edit_notes(entry: ProjectEntry) -> None:
    """Open the editor to edit a page's notes."""
    body = get_page_body(entry.page_id)
    editor = os.environ.get('EDITOR', 'nvim')
    fd, tmp_path = tempfile.mkstemp(suffix='.md')
    with open(fd, 'w') as f:  # noqa: PTH123
        f.write(body)
    subprocess.run(  # noqa: S603
        [editor, tmp_path],
        check=False,
    )
    new_body = Path(tmp_path).read_text()
    Path(tmp_path).unlink(missing_ok=True)
    if new_body != body:
        replace_page_body(entry.page_id, new_body)
        print(f'  ✓ Notes updated for "{entry.header.strip()}"')
    else:
        print('  (no changes)')


def _collect_field_updates(  # noqa: C901, PLR0912
    entry: ProjectEntry,
    fields: list[str],
) -> dict:
    """Prompt for updated values for the selected fields."""
    kwargs: dict = {}

    if 'Name' in fields:
        new_name = prompt_input(
            f'Name (current: {entry.header.strip()}): ',
        )
        if new_name is not None:
            kwargs['name'] = new_name

    if 'Next actionable step' in fields:
        step = prompt_input(
            f'Next step (current: {entry.next_step or "none"}): ',
        )
        if step is not None:
            kwargs['next_step'] = step

    if 'Success condition' in fields:
        outcome = prompt_input(
            'Success condition'
            f' (current: {entry.success_condition or "none"}): ',
        )
        if outcome is not None:
            kwargs['success_condition'] = outcome

    if 'Context' in fields:
        contexts = get_select_options('Context')
        ctx = fzf_on_a_list(
            contexts,
            prompt=f'"{entry.header}" → Context',
        )
        if ctx:
            kwargs['context'] = ctx

    if 'Status' in fields:
        status = fzf_on_a_list(
            STATUSES,
            prompt=f'"{entry.header}" → Status',
        )
        if status:
            kwargs['status'] = status
            if status == 'Waiting For':
                waiting_on = prompt_input(
                    'Who/what are you waiting on? '
                    f'(current: {entry.next_step or "none"}): ',
                )
                if waiting_on is not None:
                    kwargs['next_step'] = waiting_on
                followup = prompt_input(
                    'Follow-up date (e.g. Friday, in 3 days): ',
                )
                if followup:
                    parsed = _parse_date_input(followup)
                    if parsed:
                        kwargs['follow_up_date'] = parsed

    if 'Due date' in fields:
        due = prompt_input(
            'Due date (blank to clear, e.g. Jul 15): ',
        )
        if due is not None:
            if due == '':
                kwargs['due_date'] = ''
            else:
                result = _parse_date_input(due)
                if result:
                    kwargs['due_date'] = result

    if 'Follow-up date' in fields:
        fup = prompt_input('Follow-up date (blank to clear): ')
        if fup is not None:
            if fup == '':
                kwargs['follow_up_date'] = ''
            else:
                result = _parse_date_input(fup)
                if result:
                    kwargs['follow_up_date'] = result

    return kwargs


def _edit_entry_fields(entry: ProjectEntry) -> None:
    """Prompt for field edits and push to Notion."""
    body = get_page_body(entry.page_id)
    preview = _escape_for_shell(_entry_preview_text(entry, body))

    fields = fzf_on_a_list(
        [
            'Name',
            'Next actionable step',
            'Success condition',
            'Edit notes',
            'Context',
            'Status',
            'Due date',
            'Follow-up date',
        ],
        multiple=True,
        prompt=f'"{entry.header.strip()}" → Edit fields',
        preview=f"echo '{preview}'",
    )
    if not fields:
        return

    if 'Edit notes' in fields:
        _edit_notes(entry)

    prop_fields = [f for f in fields if f != 'Edit notes']
    if not prop_fields:
        return

    kwargs = _collect_field_updates(entry, prop_fields)
    if not kwargs:
        return

    props = build_property_update(**kwargs)
    update_page(entry.page_id, props)
    print(f'  ✓ "{entry.header.strip()}" updated')


def update_entry() -> None:
    """Interactively update fields on an existing project."""
    active_statuses = [
        s for s in get_select_options('Status') if s != 'Triage'
    ]
    if not active_statuses:
        print('No statuses configured.')
        return

    pages = query_database(
        filter_obj={
            'or': [
                {'property': 'Status', 'select': {'equals': s}}
                for s in active_statuses
            ],
        },
    )
    entries = [ProjectEntry.from_page(p) for p in pages]

    if not entries:
        print('No projects to update.')
        return

    entry = select_entry(entries, prompt='Update')
    if not entry:
        return

    _edit_entry_fields(entry)


def update_entry_by_ref(entry: ProjectEntry) -> None:
    """Update fields on a specific entry (no picker)."""
    _edit_entry_fields(entry)


def list_entries(
    *,
    status: str | None = None,
    context: str | None = None,
    verbose: bool = False,
) -> None:
    """List entries from the GTD Projects table."""
    filter_conditions = []
    if status:
        filter_conditions.append(
            {
                'property': 'Status',
                'select': {'equals': status},
            }
        )
    if context:
        filter_conditions.append(
            {
                'property': 'Context',
                'select': {'equals': context},
            }
        )

    filter_obj = None
    if len(filter_conditions) == 1:
        filter_obj = filter_conditions[0]
    elif len(filter_conditions) > 1:
        filter_obj = {'and': filter_conditions}

    pages = query_database(filter_obj=filter_obj)
    entries = [ProjectEntry.from_page(p) for p in pages]

    if not status:
        for s in STATUSES:
            group = [e for e in entries if e.status == s]
            if group:
                print(f'\n── {s} ({len(group)}) ──')
                print(format_entry_list(group, verbose=verbose))
        print()
    else:
        print(f'\n── {status} ({len(entries)}) ──')
        print(format_entry_list(entries, verbose=verbose))
        print()


def show_triage(*, verbose: bool = False) -> None:
    """Show items in Triage status."""
    list_entries(status='Triage', verbose=verbose)
