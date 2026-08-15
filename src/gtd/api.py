"""Thin Flask wrapper around GTD Notion operations for iOS Shortcuts."""

from __future__ import annotations

from http import HTTPStatus
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, TYPE_CHECKING
from urllib.parse import unquote_plus

if TYPE_CHECKING:
    from collections.abc import Callable

import httpx
from flask import Flask, Response, jsonify, request, send_from_directory
from dateutil import parser as dateparser

from gtd.notion.capture import _create_page
from gtd.notion.client import (
    add_area,
    add_list_category,
    build_property_update,
    get_areas,
    get_contexts,
    get_list_categories,
    get_page_body,
    query_database,
    remove_area,
    remove_list_category,
    rename_area,
    rename_list_category,
    replace_page_body,
    update_page,
    archive_page,
)
from gtd import storage
from gtd.notion.log import _is_recurring
from gtd.notion.models import ProjectEntry, advance_steps
from gtd.notion.schema import STATUSES
from gtd.notion.triage import TRIAGE_STATUSES
from gtd.notion.views import (
    entries_for_status,
    inbox_entries,
    next_steps_entries,
)
from gtd.version import get_version

import logging

# Every one of these helpers hands back `jsonify(...)`, which is a
# Response and not the dict it is built from -- the annotations said
# `tuple[dict, int]` and were simply describing the wrong object.
_ErrorResponse = tuple[Response, int]

NOTION_API_URL = 'https://api.notion.com/v1'
NOTION_API_VERSION = '2022-06-28'

EXCLUDE_THESE = [  # attributes not currently ever needed in iOS Shortcuts
    'created_date',
    'list_category',
    'status',
    'success_condition',
    'updated_date',
]

# Next Steps is a mixed-status view — a Recurring item and a Current Project
# sit side by side — so the client has to be told which it is or it can't
# offer the reschedule choice. `/done` refuses to archive a recurring item
# regardless, but without this the webapp only finds that out the hard way.
_NEXT_STEPS_EXCLUDES = [f for f in EXCLUDE_THESE if f != 'status']

app = Flask(__name__)

WEBAPP_DIR = Path(__file__).parent / 'webapp'

# Configure logging
GTD_DEBUG = os.environ.get('GTD_DEBUG') == '1'
logging.basicConfig(level=logging.DEBUG if GTD_DEBUG else logging.INFO)
logger = logging.getLogger(__name__)


# region Utils


def require_auth(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        expected = os.environ.get('GTD_API_KEY')
        if not expected:
            logger.error('GTD_API_KEY not set on server')
            return jsonify(error='GTD_API_KEY not set on server'), 500
        auth = request.headers.get('Authorization', '')
        # Log request metadata for debugging when enabled
        if GTD_DEBUG:
            try:
                body_text = request.get_data(as_text=True)
            except Exception:
                body_text = '<could not read body>'
            headers_preview = {
                k: ('<redacted>' if k.lower() == 'authorization' else v)
                for k, v in request.headers.items()
            }
            logger.debug(
                'Request %s %s headers=%s body=%s',
                request.method,
                request.path,
                headers_preview,
                body_text,
            )
        if not auth.startswith('Bearer ') or auth[7:] != expected:
            logger.warning(
                'Authorization failed for request to %s', request.path
            )
            return jsonify(error='Invalid API key'), 401
        return fn(*args, **kwargs)

    return wrapper


def _entry_dict(e: ProjectEntry, excluded: list[str] | None = None) -> dict:
    excluded = excluded or []
    return {k: v for k, v in asdict(e).items() if k not in excluded}


def _today_iso() -> str:
    """Today, in the system's local timezone — the same "today" the TUI means.

    This used to pin a hardcoded Eastern zone while every other
    surface used a naive datetime.now(), so a date-gated view could mean
    different days on the phone and in the terminal. Each user runs their
    own instance against their own Notion DB, so the machine's clock is
    already the right answer, and it follows you if you move.
    """
    return datetime.now().date().isoformat()


def _get_page_by_id(page_id: str) -> dict | None:
    """Retrieve a Notion page by ID and return as ProjectEntry dict."""
    url = f'{NOTION_API_URL}/pages/{page_id}'
    token = os.environ.get('NOTION_NOTES_TOKEN', '')
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Notion-Version': NOTION_API_VERSION,
    }
    try:
        response = httpx.get(url, headers=headers, timeout=30.0)
        if response.status_code == HTTPStatus.OK:
            return response.json()
    except Exception:
        print(f'Failed to retrieve page {page_id}, response: {response.text}')
    return None


def _page_entry(page: dict) -> ProjectEntry | None:
    """A raw Notion page as a ProjectEntry, or None if it isn't shaped so."""
    try:
        return ProjectEntry.from_page(page)
    except (KeyError, TypeError):
        return None


def _entry_response(page_id: str) -> tuple[Any, int]:
    """Fetch a page and render it as an entry dict, or an error tuple."""
    page = _get_page_by_id(page_id)
    if not page:
        return jsonify(error=f'Entry {page_id} not found'), 404
    return jsonify(_entry_dict(ProjectEntry.from_page(page))), 200


# The subset of ProjectEntry fields a client may write, mapped to the
# `build_property_update` keyword that sets them. Anything outside this map is
# rejected rather than ignored, so a typo surfaces as a 400 instead of a
# silently skipped update.
WRITABLE_FIELDS = {
    'header': 'name',
    'status': 'status',
    'context': 'context',
    'list_category': 'list_category',
    'area': 'area',
    'next_step': 'next_step',
    'success_condition': 'success_condition',
    'due_date': 'due_date',
    'follow_up_date': 'follow_up_date',
}


def _parse_iso_date(value: str) -> str | None:
    """Return `value` as an ISO date string, or None if unparseable."""
    try:
        return dateparser.parse(value).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


# endregion Utils

# region Triage Helpers


def _validate_triage_status(status: str) -> _ErrorResponse | None:
    """Validate status value. Returns error tuple if invalid, None if valid."""
    if status not in TRIAGE_STATUSES:
        msg = (
            f'Invalid status "{status}". Must be one of: '
            f'{", ".join(TRIAGE_STATUSES)}'
        )
        return jsonify(error=msg), 400
    return None


def _validate_and_set_context_or_list(
    status: str, context: str, list_category: str
) -> _ErrorResponse | tuple[dict, None]:
    """Validate and set context or list_category.

    Returns (error_response, 400) if invalid, or (kwargs_dict, None) if valid.
    Performs case-insensitive matching and logs failures for debugging.
    """
    kwargs: dict = {}
    error: _ErrorResponse | None = None

    if status == 'List':
        if not list_category:
            error = (
                jsonify(error='list_category is required for List status'),
                400,
            )
        else:
            try:
                available = get_list_categories()
            except Exception:
                logger.exception('Failed to fetch list categories from Notion')
                return jsonify(error='Could not retrieve list categories'), 500

            normalized = {c.strip().lower(): c for c in available}
            key = list_category.strip().lower()
            if key not in normalized:
                msg = (
                    f'Invalid list_category "{list_category}". '
                    f'Valid categories: {", ".join(sorted(available))}'
                )
                logger.debug(
                    'Triage validation failed for list_category: %s',
                    list_category,
                )
                error = (jsonify(error=msg), 400)
            else:
                kwargs['list_category'] = normalized[key]
    elif not context:
        error = (
            jsonify(error=f'context is required for {status} status'),
            400,
        )
    else:
        try:
            available = get_contexts()
        except Exception:
            logger.exception('Failed to fetch contexts from Notion')
            return jsonify(error='Could not retrieve contexts'), 500

        normalized = {c.strip().lower(): c for c in available}
        key = context.strip().lower()
        if key not in normalized:
            msg = (
                f'Invalid context "{context}". '
                f'Valid contexts: {", ".join(sorted(available))}'
            )
            logger.debug('Triage validation failed for context: %s', context)
            error = (jsonify(error=msg), 400)
        else:
            kwargs['context'] = normalized[key]

    return error if error is not None else (kwargs, None)


def _parse_triage_dates(
    due_date: str | None, follow_up_date: str | None
) -> _ErrorResponse | tuple[dict, None]:
    """Parse and validate date strings.

    Returns (error_response, 400) if parsing fails, or (kwargs_dict, None).
    """
    kwargs: dict = {}

    if due_date:
        try:
            parsed = dateparser.parse(due_date, fuzzy=True)
        except (ValueError, TypeError) as e:
            msg = f'Could not parse due_date "{due_date}": {e}'
            return jsonify(error=msg), 400
        if parsed is None:
            msg = f'Could not parse due_date "{due_date}"'
            return jsonify(error=msg), 400
        kwargs['due_date'] = parsed.strftime('%Y-%m-%d')

    if follow_up_date:
        try:
            parsed = dateparser.parse(follow_up_date, fuzzy=True)
        except (ValueError, TypeError) as e:
            msg = f'Could not parse follow_up_date "{follow_up_date}": {e}'
            return jsonify(error=msg), 400
        if parsed is None:
            msg = f'Could not parse follow_up_date "{follow_up_date}"'
            return jsonify(error=msg), 400
        kwargs['follow_up_date'] = parsed.strftime('%Y-%m-%d')

    return kwargs, None


def _apply_triage_updates(
    page_id: str, kwargs: dict[str, str]
) -> _ErrorResponse:
    """Apply triage updates to Notion and return updated entry.

    Returns (jsonified_entry, 200) or (jsonified_error, 500).
    """
    try:
        props = build_property_update(**kwargs)
        update_page(page_id, props)
    except (ValueError, RuntimeError, OSError) as err:
        return jsonify(error=f'Update failed: {err}'), 500

    # Return updated entry
    updated_page = _get_page_by_id(page_id)
    if updated_page:
        updated_entry = ProjectEntry.from_page(updated_page)
        return jsonify(_entry_dict(updated_entry)), 200

    return jsonify(error='Could not retrieve updated entry'), 500


# endregion Triage Helpers

# Endpoint definitions should be alphabetical


def _pages_with_select(prop: str, value: str) -> list[dict]:
    """Every page whose `prop` select equals `value`."""
    return query_database(
        filter_obj={'property': prop, 'select': {'equals': value}},
    )


def _resolve_area(name: str) -> tuple[str | None, list[str]]:
    """Map `name` to its canonical Area, case-insensitively.

    Returns `(canonical_or_None, all_areas)` so callers can 404 on an unknown
    area without fetching the list twice.
    """
    available = get_areas()
    return {a.lower(): a for a in available}.get(name.strip().lower()), (
        available
    )


@app.get('/areas')
@require_auth
def areas() -> Any:
    """List the Areas of Focus. Mirrors the TUI's Someday tab grouping."""
    try:
        return jsonify(areas=sorted(get_areas()))
    except Exception:
        logger.exception('Failed to fetch areas for /areas')
        return jsonify(error='Could not retrieve areas'), 500


@app.post('/areas')
@require_auth
def post_area() -> Any:
    """Create an Area of Focus. Body: {"name": "..."}. Mirrors `+`."""
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify(error='name is required'), 400
    try:
        existing, _ = _resolve_area(name)
        if existing:
            return jsonify(error=f'Area "{existing}" already exists'), 409
        add_area(name)
        return jsonify(areas=sorted(get_areas())), 201
    except Exception:
        logger.exception('Failed to add area %s', name)
        return jsonify(error='Could not add area'), 500


@app.delete('/areas/<name>')
@require_auth
def delete_area(name: str) -> Any:
    """Delete an Area of Focus. Mirrors the TUI's `-`."""
    try:
        canonical, _ = _resolve_area(unquote_plus(name))
        if not canonical:
            return jsonify(error=f'Unknown area "{name}"'), 404
        # Removing the select option would orphan every entry still on it.
        occupied = len(_pages_with_select('Area', canonical))
        if occupied:
            return jsonify(
                error=(
                    f'Area "{canonical}" still has {occupied} item(s). '
                    f'Move or drop them before removing it.'
                ),
                count=occupied,
            ), 409
        remove_area(canonical)
        return jsonify(areas=sorted(get_areas())), 200
    except Exception:
        logger.exception('Failed to remove area %s', name)
        return jsonify(error='Could not remove area'), 500


@app.patch('/areas/<name>')
@require_auth
def patch_area(name: str) -> Any:
    """Rename an Area of Focus. Body: {"new_name": "..."}. Mirrors `)`."""
    body = request.get_json(force=True, silent=True) or {}
    new_name = (body.get('new_name') or '').strip()
    if not new_name:
        return jsonify(error='new_name is required'), 400
    try:
        canonical, available = _resolve_area(unquote_plus(name))
        if not canonical:
            return jsonify(error=f'Unknown area "{name}"'), 404
        collision = {a.lower() for a in available} - {canonical.lower()}
        if new_name.lower() in collision:
            return jsonify(error=f'Area "{new_name}" already exists'), 409
        # Entries keep the old value after the option is renamed, so collect
        # them first and rewrite each one.
        pages = query_database(
            filter_obj={
                'property': 'Area',
                'select': {'equals': canonical},
            },
        )
        rename_area(canonical, new_name)
        for page in pages:
            update_page(page['id'], build_property_update(area=new_name))
        return jsonify(areas=sorted(get_areas())), 200
    except Exception:
        logger.exception('Failed to rename area %s', name)
        return jsonify(error='Could not rename area'), 500


@app.get('/version')
@require_auth
def version() -> Any:
    """Return the running gtd-tui version."""
    return jsonify(version=get_version())


@app.get('/inbox')
@require_auth
def inbox() -> Any:
    """Get everything needing triage (inbox)."""
    entries = inbox_entries()
    entries.sort(key=lambda e: (e.due_date or '9999-99-99', e.header))
    return jsonify([_entry_dict(e) for e in entries])


@app.get('/entries')
@require_auth
def entries() -> Any:
    """List entries by status, backing the webapp's per-status tabs.

    Query params: `status` (required), `context`, and `follow_up` — `future`
    selects deferred items (the Incubation tab), `due` selects everything
    actionable now.
    """
    status = request.args.get('status')
    if not status:
        return jsonify(error='status query parameter is required'), 400
    status = unquote_plus(status)
    if status not in STATUSES:
        return jsonify(
            error=f'Invalid status "{status}". Valid: {", ".join(STATUSES)}',
        ), 400

    context = request.args.get('context')
    found = entries_for_status(
        status,
        context=unquote_plus(context) if context else None,
        follow_up=request.args.get('follow_up'),
        today=_today_iso(),
    )

    found.sort(key=lambda e: (e.due_date or '9999-99-99', e.header.lower()))
    return jsonify([_entry_dict(e) for e in found])


@app.patch('/entry/<page_id>')
@require_auth
def patch_entry(page_id: str) -> Any:
    """Update any subset of an entry's fields. Mirrors the TUI's `U`."""
    body = request.get_json(force=True, silent=True) or {}
    unknown = set(body) - set(WRITABLE_FIELDS)
    if unknown:
        return jsonify(
            error=f'Unknown field(s): {", ".join(sorted(unknown))}',
        ), 400
    if not body:
        return jsonify(error='No fields to update'), 400

    kwargs = {WRITABLE_FIELDS[k]: v for k, v in body.items()}
    props = build_property_update(**kwargs)
    try:
        update_page(page_id, props)
    except (ValueError, RuntimeError, OSError) as err:
        return jsonify(error=f'Update failed: {err}'), 500
    return _entry_response(page_id)


@app.get('/entry/<page_id>/notes')
@require_auth
def get_notes(page_id: str) -> Any:
    """Read an entry's page body. Mirrors the TUI's `N`."""
    try:
        return jsonify(notes=get_page_body(page_id))
    except (ValueError, RuntimeError, OSError) as err:
        return jsonify(error=f'Could not read notes: {err}'), 500


@app.put('/entry/<page_id>/notes')
@require_auth
def put_notes(page_id: str) -> Any:
    """Replace an entry's page body. Body: {"notes": "..."}."""
    body = request.get_json(force=True, silent=True) or {}
    if 'notes' not in body:
        # An empty string is a legitimate erase; an absent key is a bug.
        return jsonify(error='notes is required'), 400
    try:
        replace_page_body(page_id, body['notes'])
    except (ValueError, RuntimeError, OSError) as err:
        return jsonify(error=f'Could not save notes: {err}'), 500
    return jsonify(saved=True), 200


@app.post('/entry/<page_id>/complete-step')
@require_auth
def complete_step(page_id: str) -> Any:
    """Tick off the entry's current step, renumbering the rest. TUI's `X`."""
    page = _get_page_by_id(page_id)
    if not page:
        return jsonify(error=f'Entry {page_id} not found'), 404
    entry = ProjectEntry.from_page(page)
    if not entry.next_step.strip():
        return jsonify(error='Entry has no step to complete'), 400

    remaining = advance_steps(entry.next_step)
    try:
        update_page(page_id, build_property_update(next_step=remaining))
    except (ValueError, RuntimeError, OSError) as err:
        return jsonify(error=f'Could not complete step: {err}'), 500
    return _entry_response(page_id)


@app.post('/entry/<page_id>/snooze')
@require_auth
def snooze(page_id: str) -> Any:
    """Push an entry's follow-up date out. Mirrors the TUI's `T`.

    Body may carry `date` (explicit ISO date) or `days` (offset from today);
    with neither, it defers to tomorrow.
    """
    body = request.get_json(force=True, silent=True) or {}
    if 'date' in body:
        target = _parse_iso_date(str(body['date']))
        if not target:
            return jsonify(error=f'Unparseable date: {body["date"]}'), 400
    else:
        try:
            days = int(body.get('days', 1))
        except (TypeError, ValueError):
            return jsonify(error='days must be an integer'), 400
        today = datetime.fromisoformat(_today_iso()).date()
        target = (today + timedelta(days=days)).isoformat()

    try:
        update_page(page_id, build_property_update(follow_up_date=target))
    except (ValueError, RuntimeError, OSError) as err:
        return jsonify(error=f'Snooze failed: {err}'), 500
    return _entry_response(page_id)


@app.post('/capture')
@require_auth
def capture() -> Any:
    """Add an item to the inbox. Body: {"header": "..."}."""
    body = request.get_json(force=True)
    header = (body.get('header') or '').strip()
    if not header:
        return jsonify(error='header is required'), 400
    page = _create_page(header)
    return jsonify(page_id=page['id'], header=header), 201


@app.get('/contexts')
@require_auth
def contexts() -> Any:
    """Get active contexts.

    'Work' only shown during work hours (7am-5:30pm, Mon-Fri).
    """
    work_start_hour = 7
    work_end_hour = 17
    work_end_minute = 30
    friday = 4

    # The contexts you can actually act in *now*, so this has to be the
    # Next Steps view exactly. Deriving it separately meant `/next-steps`
    # could return an item under a context `/contexts` never offered --
    # unreachable through the picker.
    entries = next_steps_entries(_today_iso())
    active_contexts = {e.context for e in entries if e.context}

    # Filter out 'Work' if not during work hours
    now = datetime.now()
    weekday = now.weekday()  # 0=Monday, 6=Sunday
    hour = now.hour
    minute = now.minute

    is_work_hours = (
        weekday <= friday
        and hour >= work_start_hour
        and not (
            hour > work_end_hour
            or (hour == work_end_hour and minute >= work_end_minute)
        )
    )

    if not is_work_hours:
        active_contexts.discard('Work')

    return jsonify(contexts=sorted(active_contexts))


@app.get('/next-steps')
@require_auth
def next_steps() -> Any:
    """Get actionable next steps, optionally filtered by context."""
    entries = next_steps_entries(_today_iso())
    context = request.args.get('context')
    if context:
        context = unquote_plus(context)
        entries = [e for e in entries if e.context == context]
    for entry in entries:
        entry.next_step = entry.next_step.split('\n')[0].replace('1. ', '')
    entries.sort(key=lambda e: (e.context or '\xff', e.header.lower()))
    entries.sort(
        key=lambda e: (
            e.follow_up_date or '9999-99-99',
            e.due_date or '9999-99-99',
        ),
    )
    return jsonify([_entry_dict(e, _NEXT_STEPS_EXCLUDES) for e in entries])


@app.get('/triage-schema')
@require_auth
def triage_schema() -> Any:
    """Get schema for triage workflow: statuses and contexts per status.

    Returns canonical options fetched live from Notion so Shortcuts can
    validate user choices before calling /triage.
    """
    try:
        available_contexts = get_contexts()
        list_categories = get_list_categories()
    except Exception:
        logger.exception('Failed to fetch Notion schema for triage-schema')
        return jsonify(error='Could not fetch schema from Notion'), 500

    schema = {
        'statuses': TRIAGE_STATUSES,
        'contexts_by_status': {
            'Current Project': sorted(available_contexts),
            'Recurring': sorted(available_contexts),
            'Waiting For': sorted(available_contexts),
            'Someday/Maybe': sorted(available_contexts),
            'List': sorted(list_categories),
        },
        'list_categories': sorted(list_categories),
    }
    return jsonify(schema)


@app.get('/list-categories')
@require_auth
def list_categories() -> Any:
    """Return canonical list categories from Notion (for debugging/UI use)."""
    try:
        cats = get_list_categories()
        return jsonify(list_categories=sorted(cats))
    except Exception:
        logger.exception(
            'Failed to fetch list categories for /list-categories'
        )
        return jsonify(error='Could not retrieve list categories'), 500


def _resolve_list_category(name: str) -> tuple[str | None, list[str]]:
    """Map `name` to its canonical List Category, case-insensitively."""
    available = get_list_categories()
    return {c.lower(): c for c in available}.get(name.strip().lower()), (
        available
    )


@app.post('/list-categories')
@require_auth
def post_list_category() -> Any:
    """Create a list category. Body: {"name": "..."}. Mirrors `+`."""
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify(error='name is required'), 400
    try:
        existing, _ = _resolve_list_category(name)
        if existing:
            return jsonify(error=f'Category "{existing}" already exists'), 409
        add_list_category(name)
        return jsonify(list_categories=sorted(get_list_categories())), 201
    except Exception:
        logger.exception('Failed to add list category %s', name)
        return jsonify(error='Could not add list category'), 500


@app.delete('/list-categories/<name>')
@require_auth
def delete_list_category(name: str) -> Any:
    """Delete an empty list category. Mirrors the TUI's `-`."""
    try:
        canonical, _ = _resolve_list_category(unquote_plus(name))
        if not canonical:
            return jsonify(error=f'Unknown category "{name}"'), 404
        # Removing the select option would orphan every item still in it.
        occupied = len(_pages_with_select('List Category', canonical))
        if occupied:
            return jsonify(
                error=(
                    f'Category "{canonical}" still has {occupied} item(s). '
                    f'Move or drop them before removing it.'
                ),
                count=occupied,
            ), 409
        remove_list_category(canonical)
        return jsonify(list_categories=sorted(get_list_categories())), 200
    except Exception:
        logger.exception('Failed to remove list category %s', name)
        return jsonify(error='Could not remove list category'), 500


@app.patch('/list-categories/<name>')
@require_auth
def patch_list_category(name: str) -> Any:
    """Rename a list category. Body: {"new_name": "..."}. Mirrors `)`."""
    body = request.get_json(force=True, silent=True) or {}
    new_name = (body.get('new_name') or '').strip()
    if not new_name:
        return jsonify(error='new_name is required'), 400
    try:
        canonical, available = _resolve_list_category(unquote_plus(name))
        if not canonical:
            return jsonify(error=f'Unknown category "{name}"'), 404
        collision = {c.lower() for c in available} - {canonical.lower()}
        if new_name.lower() in collision:
            return jsonify(error=f'Category "{new_name}" already exists'), 409
        # Entries keep the old value after the option is renamed, so collect
        # them first and rewrite each one.
        pages = _pages_with_select('List Category', canonical)
        rename_list_category(canonical, new_name)
        for page in pages:
            update_page(
                page['id'], build_property_update(list_category=new_name)
            )
        return jsonify(list_categories=sorted(get_list_categories())), 200
    except Exception:
        logger.exception('Failed to rename list category %s', name)
        return jsonify(error='Could not rename list category'), 500


@app.post('/list/<category>')
@require_auth
def post_list_item(category: str) -> Any:
    """Add an item to a list category. Body: {"header", "next_step"}."""
    body = request.get_json(force=True, silent=True) or {}
    header = (body.get('header') or '').strip()
    if not header:
        return jsonify(error='header is required'), 400
    next_step = (body.get('next_step') or '').strip()
    try:
        canonical, available = _resolve_list_category(unquote_plus(category))
    except Exception:
        logger.exception('Failed to fetch list categories')
        return jsonify(error='Could not retrieve list categories'), 500
    if not canonical:
        return jsonify(
            error=(
                f'Invalid list category "{category}". '
                f'Valid categories: {", ".join(sorted(available))}'
            )
        ), 404
    try:
        page = _create_page(header)
        update_page(
            page['id'],
            build_property_update(
                status='List',
                list_category=canonical,
                next_step=next_step or None,
            ),
        )
    except Exception:
        logger.exception('Failed to add item to list %s', canonical)
        return jsonify(error='Could not add list item'), 500
    return jsonify(
        page_id=page['id'], header=header, list_category=canonical
    ), 201


@app.get('/list/<category>')
@require_auth
def get_list(category: str) -> Any:
    """Get all entries in a specific list category."""
    try:
        available = get_list_categories()
    except Exception:
        logger.exception('Failed to fetch list categories')
        return jsonify(error='Could not retrieve list categories'), 500

    normalized = {c.strip().lower(): c for c in available}
    key = category.strip().lower()
    if key not in normalized:
        msg = (
            f'Invalid list category "{category}". '
            f'Valid categories: {", ".join(sorted(available))}'
        )
        return jsonify(error=msg), 404

    canonical_category = normalized[key]
    # Status too, not just the category: the Lists tab is `Status == 'List'`,
    # so filtering on the category alone showed the phone entries the TUI
    # never listed.
    entries = entries_for_status('List', list_category=canonical_category)
    extra_excludes = [
        *EXCLUDE_THESE,
        'follow_up_date',
        'due_date',
        'context',
    ]

    entries.sort(key=lambda e: (e.due_date or '9999-99-99', e.header))
    return jsonify([_entry_dict(e, extra_excludes) for e in entries])


def _reschedule(page_id: str, raw_date: str) -> tuple[Response, int]:
    """Push a recurring item's next follow-up out instead of archiving it."""
    target = _parse_iso_date(raw_date)
    if not target:
        return jsonify(error=f'Unparseable date: {raw_date}'), 400
    try:
        update_page(page_id, build_property_update(follow_up_date=target))
    except (ValueError, RuntimeError, OSError) as err:
        return jsonify(error=f'Reschedule failed: {err}'), 500
    return jsonify(rescheduled=target), 200


def _recurring_refusal(
    page_id: str, page_data: dict, body: dict
) -> tuple[Response, int] | None:
    """409 if archiving this page would silently destroy a recurring item."""
    if body.get('confirm_recurring'):
        return None
    entry = _page_entry(page_data)
    if not entry or not _is_recurring(entry):
        return None
    return jsonify(
        error=f'"{entry.header.strip()}" is recurring',
        recurring=True,
        header=entry.header,
        page_id=page_id,
    ), 409


@app.post('/done/<page_id>')
@require_auth
def done(page_id: str) -> Any:
    """Mark an entry done (archives the page).

    Body may carry `reschedule` (an ISO date), which sets the next follow-up
    instead of archiving — how a Recurring item is completed without losing it.

    A recurring entry is refused (409) unless the caller either reschedules it
    or passes `confirm_recurring`. The TUI asks *Reschedule vs Permanently
    complete* before archiving; enforcing that here rather than in the client
    means no caller can skip it — the webapp's own action sheet did, because
    `/next-steps` doesn't send `status` and it had nothing to branch on.
    """
    page_data = _get_page_by_id(page_id)
    if not page_data:
        return jsonify(error=f'Entry {page_id} not found'), 404

    body = request.get_json(force=True, silent=True) or {}
    if body.get('reschedule'):
        return _reschedule(page_id, str(body['reschedule']))

    refusal = _recurring_refusal(page_id, page_data, body)
    if refusal:
        return refusal

    try:
        archive_page(page_id)
    except (ValueError, RuntimeError, OSError) as err:
        return jsonify(error=f'Mark done failed: {err}'), 500
    return jsonify(deleted=True), 200


# region Weekly review


def _review_payload() -> dict:
    """The weekly review's checklist state.

    Deliberately local-only: it reads `~/.local/share/gtd/weekly_habits.json`
    and never touches Notion, so a Notion outage can't take the checklist
    down, and the phone and the TUI share one set of ticks. The per-step work
    is done through the existing entry endpoints.
    """
    done = storage.load_review_state(len(storage.REVIEW_STEPS))
    return {
        'week_start': storage.current_week_start(),
        'steps': [
            {
                'index': i,
                'label': label,
                'action': action,
                'done': done[i],
            }
            for i, (label, action) in enumerate(storage.REVIEW_STEPS)
        ],
        'last_done': storage.get_weekly_habit_date(
            storage.WEEKLY_REVIEW_HABIT
        ),
        'done_this_week': storage.habit_done_this_week(
            storage.WEEKLY_REVIEW_HABIT
        ),
    }


@app.get('/review')
@require_auth
def review() -> Any:
    """Get this week's weekly review checklist and its progress."""
    return jsonify(_review_payload())


@app.post('/review/step/<int:index>')
@require_auth
def review_step(index: int) -> Any:
    """Check or uncheck one weekly review step. Body: `{"done": bool}`."""
    body = request.get_json(force=True, silent=True) or {}
    if 'done' not in body:
        return jsonify(error='Body must carry "done"'), 400
    if not isinstance(body['done'], bool):
        return jsonify(error='"done" must be true or false'), 400
    try:
        storage.set_review_step(index, done=body['done'])
    except IndexError as err:
        return jsonify(error=str(err)), 404
    return jsonify(_review_payload())


@app.post('/review/reset')
@require_auth
def review_reset() -> Any:
    """Clear this week's weekly review progress. Mirrors the TUI's `X`."""
    storage.reset_review_state()
    return jsonify(_review_payload())


@app.post('/review/complete')
@require_auth
def review_complete() -> Any:
    """Mark the weekly review itself done for this week."""
    storage.set_weekly_habit_date(storage.WEEKLY_REVIEW_HABIT)
    return jsonify(_review_payload())


# endregion


@app.post('/triage/<page_id>')
@require_auth
def triage(page_id: str) -> Any:  # noqa: PLR0911,C901
    """Atomically triage an entry with full data.

    Request body:
    {
        "status": "Current Project" | "Waiting For" | "Someday/Maybe" |
                  "List" | "Recurring" | "Delete",
        "context": "Work" | "Home" | ...,
        "list_category": "Books to Read" | ...,
        "next_step": "...",
        "success_condition": "...",
        "due_date": "2026-08-01" or null,
        "follow_up_date": "2026-08-05" or null
    }

    Returns: updated ProjectEntry or error
    """
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        # Fall back to raw body text
        body_text = request.get_data(as_text=True)
        logger.debug(
            'Failed to parse JSON body for triage %s: %s', page_id, body_text
        )
        return jsonify(error='Request body must be valid JSON'), 400

    # Log incoming triage request for debugging
    if GTD_DEBUG:
        logger.debug('Triage request for page_id=%s body=%s', page_id, body)

    status = (body.get('status') or '').strip()
    context = (body.get('context') or '').strip()
    list_category = (body.get('list_category') or '').strip()
    next_step = (body.get('next_step') or '').strip()
    success_condition = (body.get('success_condition') or '').strip()
    due_date_str = (
        (body.get('due_date') or '').strip() if body.get('due_date') else None
    )
    follow_up_str = (
        (body.get('follow_up_date') or '').strip()
        if body.get('follow_up_date')
        else None
    )

    # Get the entry to verify it exists
    page_data = _get_page_by_id(page_id)
    if not page_data:
        logger.debug('Triage: entry not found %s', page_id)
        return jsonify(error=f'Entry {page_id} not found'), 404
    # Handle Delete
    if status == 'Delete':
        try:
            archive_page(page_id)
            result = jsonify(deleted=True), 200
        except (ValueError, RuntimeError, OSError) as err:
            result = jsonify(error=f'Delete failed: {err}'), 500
        return result

    # Validate status
    error = _validate_triage_status(status)
    if error:
        return error

    # Validate and set context/list_category
    context_result = _validate_and_set_context_or_list(
        status, context, list_category
    )
    # If validation returned an error tuple (Response, status_code), return it.
    if context_result[1] is not None:
        return context_result

    kwargs: dict[str, str] = context_result[0]  # type: ignore[assignment]
    if not status:
        return jsonify(error='status is required'), 400
    kwargs['status'] = status

    # Set optional fields
    if next_step:
        kwargs['next_step'] = next_step
    if success_condition:
        kwargs['success_condition'] = success_condition

    # Parse dates
    dates_result = _parse_triage_dates(due_date_str, follow_up_str)
    if dates_result[1] is not None:  # Error case
        return dates_result
    kwargs.update(dates_result[0])

    # Apply updates and return result
    try:
        return _apply_triage_updates(page_id, kwargs)
    except Exception:
        logger.exception(
            'Unexpected error while applying triage updates for %s', page_id
        )
        return jsonify(error='Internal server error'), 500


# endregion

# region Web App


@app.get('/')
def webapp_index() -> Any:
    """Serve the mobile web app shell."""
    return send_from_directory(WEBAPP_DIR, 'index.html')


@app.get('/manifest.json')
def webapp_manifest() -> Any:
    """Serve the PWA manifest."""
    return send_from_directory(WEBAPP_DIR, 'manifest.json')


@app.get('/app.js')
def webapp_script() -> Any:
    """Serve the web app's client-side JS."""
    return send_from_directory(WEBAPP_DIR, 'app.js')


@app.get('/styles.css')
def webapp_styles() -> Any:
    """Serve the web app's stylesheet."""
    return send_from_directory(WEBAPP_DIR, 'styles.css')


@app.get('/icons/<path:filename>')
def webapp_icons(filename: str) -> Any:
    """Serve the web app's PWA icons."""
    return send_from_directory(WEBAPP_DIR / 'icons', filename)


# endregion
