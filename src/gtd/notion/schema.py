"""GTD Notion database schema definition — single source of truth."""

from datetime import date, timedelta


STATUSES = [
    'Triage',
    'Current Project',
    'Recurring',
    'Waiting For',
    'Someday/Maybe',
    'List',
]

STATUS_ICONS = {
    'Current Project': '🟢',
    'Recurring': '🔄',
    'Triage': '🟣',
    'Waiting For': '🔵',
    'Someday/Maybe': '💭',
    'List': '📋',
}

DB_SCHEMA: dict = {
    'Header': {
        'title': {},
    },
    'Status': {
        'select': {
            'options': [{'name': s} for s in STATUSES],
        },
    },
    'Context': {
        # Options will be managed dynamically in Notion; keep select present
        # without pre-seeded options so the DB can be updated from Notion.
        'select': {},
    },
    'List Category': {
        'select': {},
    },
    'Area': {
        'select': {},
    },
    'Next Actionable Step': {
        'rich_text': {},
    },
    'Success Condition': {
        'rich_text': {},
    },
    'Due Date': {
        'date': {},
    },
    'Follow-Up Date': {
        'date': {},
    },
    'Created Date': {
        'date': {},
    },
}


WAITING_FOR_STATUS = 'Waiting For'

# Delegating something and then never hearing about it again is the failure
# Waiting For exists to prevent, so the tickler is not optional here the way
# it is on every other status. Six surfaces could produce a Waiting For with
# no Follow-Up Date and all six called the date "required" without enforcing
# it, so the default is applied at the single write chokepoint
# (`build_property_update`) rather than validated six times over.
WAITING_FOR_DEFAULT_FOLLOW_UP_DAYS = 7


def default_waiting_for_follow_up(today: date | None = None) -> str:
    """The Follow-Up Date a Waiting For gets when none is given."""
    base = today or date.today()
    due = base + timedelta(days=WAITING_FOR_DEFAULT_FOLLOW_UP_DAYS)
    return due.isoformat()


AGENDA_CONTEXT_PREFIX = '@'

# Agenda items are never Someday/Maybe, never Lists -- something you want to
# raise with a person is by definition live. Triage assigns this without
# asking; `D` on the Inbox tab is the way out for one you don't want.
AGENDA_STATUS = 'Current Project'


def is_agenda_context(context: str | None) -> bool:
    """Is this context an agenda (a person you raise things with)?

    Contexts are named after the tool or place an action needs, except for
    people: `@Sam` means "next time I'm talking to Sam". Agenda items are
    complete with just a context and a date -- see `drop_triaged_agenda_items`.
    """
    if not context:
        return False
    return context.startswith(AGENDA_CONTEXT_PREFIX)


def agenda_person_from_header(header: str | None) -> str | None:
    """The `@Person` an agenda item declares in its own header, if any.

    Capture types the person into the title (`@Sam: raise the budget`), which
    means the Context select always lags behind -- a brand-new person has no
    option yet, so triage would ask you to choose a context that didn't exist
    for an item that had already named it. Treat the prefix as the context.

    Only a *leading* token counts: "Ask @Sam about it" is a normal item that
    happens to mention someone, not an agenda entry. A trailing colon is
    stripped, and a bare sigil (`@`, `@:`) is not a person.
    """
    if not header:
        return None
    first = header.strip().split(maxsplit=1)[0] if header.strip() else ''
    if not first.startswith(AGENDA_CONTEXT_PREFIX):
        return None
    person = first.rstrip(':')
    return person if len(person) > len(AGENDA_CONTEXT_PREFIX) else None


def strip_agenda_person(header: str) -> str:
    """Drop the leading `@Person` from an agenda header, for display.

    Agenda rows are grouped under a `── @Sam ──` heading, so repeating the
    name in every row below it is noise. Never returns empty -- a header that
    is nothing but the person keeps it.
    """
    person = agenda_person_from_header(header)
    if not person:
        return header
    remainder = header.strip()[len(person) :].lstrip(':').strip()
    return remainder or person


def is_agenda_entry(entry: object) -> bool:
    """Is this an agenda item -- something to raise with a person?

    Either signal counts: an `@Person` Context, or an `@Person` prefix on the
    header. The header is what capture writes and triage is what turns it
    into a Context, so between those two moments only the header knows.

    Lives here rather than in `triage.py` so `entries.py` can use it --
    `triage.py` already imports `entries.py`, so the other direction cycles.
    Duck-typed on `.header`/`.context` for the same reason.
    """
    return is_agenda_context(getattr(entry, 'context', None)) or bool(
        agenda_person_from_header(getattr(entry, 'header', None))
    )
