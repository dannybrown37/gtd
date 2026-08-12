"""The one definition of what each GTD view contains.

Every surface -- the TUI tabs, the CLI, the HTTP API and through it the
webapp -- must get its entries from here. When a view is defined twice, the
copies drift silently: an item simply stops appearing on one surface, with
no error anywhere to say so. That is exactly what happened with `/inbox`
(`Status == "Triage"` only, so the phone reported inbox zero while the TUI
showed a backlog) and `/next-steps` (no next-step gate, so it listed items
the TUI considered untriaged).

`tests/test_webapp_parity.py` cannot catch this class of bug -- it compares
*action names*, and has nothing to say about what a list contains. The
defence is structural: there is one definition, so there is nothing to
drift from.

Each view is defined twice over, on purpose, and the two must agree:

- a **Notion filter**, which pre-narrows the query so we aren't pulling the
  whole database over the wire; and
- a **Python predicate**, applied to whatever comes back.

The predicate is the definition. The filter is an optimisation. Notion's
filter language can't express parts of GTD at all (`starts_with` on a
select, for `@Person` contexts), so some rule always has to live in Python
-- and a rule that lives in Python is one that can be tested without a
Notion double. Applying the predicate to already-fetched entries costs
nothing and means a filter that is too loose narrows the query badly rather
than silently widening the view.

Nothing here sorts. Ordering is presentation, and each surface wants its
own (Recurring by follow-up date, Lists by category, the API by due date).
"""

from collections.abc import Sequence
from datetime import datetime

from gtd.notion.client import query_database
from gtd.notion.models import ProjectEntry
from gtd.notion.schema import WAITING_FOR_STATUS, is_agenda_entry


__all__ = [
    'drop_triaged_agenda_items',
    'entries_for_status',
    'in_status_view',
    'inbox_entries',
    'inbox_filter',
    'is_actionable',
    'is_deferred',
    'is_due_today',
    'needs_follow_up_date',
    'next_steps_entries',
    'searchable_entries',
    'status_filter',
]

NEXT_STEP_STATUSES = ['Current Project', 'Recurring']


def _today_str() -> str:
    return datetime.now().strftime('%Y-%m-%d')


# region Next Steps


def _today_filter(today: str) -> dict:
    """Build the Notion filter for today's actionable items.

    An item surfaces when its tickler has come due (Follow-Up Date on or
    before today, or never set) *or* when it is due today or overdue.

    The Due Date disjunct is what stops a snooze from burying a commitment:
    a Follow-Up Date is a note to yourself, a Due Date is a promise to
    someone else, so the deadline has to outrank the deferral. Note the
    asymmetry -- an *unset* Due Date admits nothing, or every snoozed item
    in the database would come back.
    """
    return {
        'or': [
            {
                'and': [
                    _status_clause(NEXT_STEP_STATUSES),
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
                            {
                                'property': 'Due Date',
                                'date': {'on_or_before': today},
                            },
                        ],
                    },
                ],
            },
            _waiting_for_due_clause(today),
        ],
    }


def _waiting_for_due_clause(today: str) -> dict:
    """A delegated item on the day you said you'd chase it.

    Waiting For is otherwise deliberately absent from Next Steps -- a list
    of things other people owe you is a weekly-review artifact, not a daily
    action list. But "chase Sam about the budget" *is* an action, and the
    day its Follow-Up Date comes due is the day to take it. Note the
    asymmetry with the clause above: an *unset* Follow-Up Date admits
    nothing here, or the entire Waiting For list would move in permanently.
    """
    return {
        'and': [
            _status_clause(WAITING_FOR_STATUS),
            {
                'property': 'Follow-Up Date',
                'date': {'on_or_before': today},
            },
        ],
    }


def is_due_today(entry: ProjectEntry, today: str) -> bool:
    """Should this entry surface today, given its tickler and its deadline?

    The Python twin of `_today_filter`'s date clause. Keeping the rule
    expressed once in a pure function is what makes it testable without a
    Notion double, and what lets `next_steps_entries` stay correct even if
    the server-side filter is ever loosened.
    """
    if entry.status == WAITING_FOR_STATUS:
        return bool(entry.follow_up_date and entry.follow_up_date <= today)
    if entry.due_date and entry.due_date <= today:
        return True
    return not entry.follow_up_date or entry.follow_up_date <= today


def is_actionable(entry: ProjectEntry) -> bool:
    """Is this entry triaged enough to act on?

    Recurring items and agenda items are described entirely by their
    header, so requiring a Next Actionable Step would hide them forever --
    they are exempt from providing one during triage.
    """
    return bool(
        entry.status == 'Recurring'
        or is_agenda_entry(entry)
        or (entry.context and entry.next_step)
    )


def next_steps_entries(today: str | None = None) -> list[ProjectEntry]:
    """Everything actionable today: the Next Steps tab and `/next-steps`."""
    today = today or _today_str()
    pages = query_database(filter_obj=_today_filter(today))
    entries = [ProjectEntry.from_page(p) for p in pages]
    return [e for e in entries if is_due_today(e, today) and is_actionable(e)]


# endregion Next Steps

# region Inbox


def inbox_filter() -> dict:
    """The one definition of "inbox": items needing triage.

    List items are reference material, not actions: they legitimately have
    no context, next step, or ISO, so they only count as inbox when they
    are missing the one field they do need, a List Category. Someday/Maybe
    items are exempt for the same reason -- they are organised by Area.

    Agenda items (`@Person` contexts) are exempt from the next-step/ISO
    clauses too, but Notion's select filters have no `starts_with`, so that
    part can't be expressed here -- `drop_triaged_agenda_items` applies it
    client-side. Prefer `inbox_entries`, which applies both.
    """
    not_a_list = {'property': 'Status', 'select': {'does_not_equal': 'List'}}
    # Someday/Maybe is a parked idea: no context, no next step, no ISO -- it
    # is organised by Area alone, so those clauses would trap it in the inbox
    # forever.
    not_someday = {
        'property': 'Status',
        'select': {'does_not_equal': 'Someday/Maybe'},
    }
    incomplete_fields = [
        {'property': 'Context', 'select': {'is_empty': True}},
        {
            'property': 'Next Actionable Step',
            'rich_text': {'is_empty': True},
        },
        {'property': 'Success Condition', 'rich_text': {'is_empty': True}},
    ]
    return {
        'or': [
            {'property': 'Status', 'select': {'equals': 'Triage'}},
            {'property': 'Status', 'select': {'is_empty': True}},
            *(
                {'and': [not_a_list, not_someday, condition]}
                for condition in incomplete_fields
            ),
            {
                'and': [
                    {'property': 'Status', 'select': {'equals': 'List'}},
                    {
                        'property': 'List Category',
                        'select': {'is_empty': True},
                    },
                ],
            },
            {
                'and': [
                    _status_clause(WAITING_FOR_STATUS),
                    {
                        'property': 'Follow-Up Date',
                        'date': {'is_empty': True},
                    },
                ],
            },
        ],
    }


def needs_follow_up_date(entry: ProjectEntry) -> bool:
    """A delegated item with no tickler -- reachable only by looking for it.

    `build_property_update` stamps a default, so nothing can create one of
    these any more. Items that predate that are still in the database, and
    no other view would ever surface them: Next Steps admits Waiting For
    only through a Follow-Up Date that has come due, so one with no date at
    all sits on the Waiting For tab forever, silently.
    """
    return entry.status == WAITING_FOR_STATUS and not entry.follow_up_date


def drop_triaged_agenda_items(
    entries: list[ProjectEntry],
) -> list[ProjectEntry]:
    """Remove already-triaged agenda items from an inbox result set.

    "Mention the budget to Sam" needs no Next Actionable Step and no Success
    Condition -- the header is the whole action. Without this they match
    `inbox_filter`'s missing-field clauses forever and never leave the inbox.
    An agenda item still sitting in Triage (or with no status at all) hasn't
    been processed yet, so it stays.
    """
    return [
        e
        for e in entries
        if needs_follow_up_date(e)
        or not (is_agenda_entry(e) and e.status and e.status != 'Triage')
    ]


def inbox_entries() -> list[ProjectEntry]:
    """Items needing triage: no/Triage status, or missing fields."""
    pages = query_database(filter_obj=inbox_filter())
    return drop_triaged_agenda_items(
        [ProjectEntry.from_page(p) for p in pages]
    )


# endregion Inbox

# region By status


def _status_clause(status: str | Sequence[str]) -> dict:
    statuses = [status] if isinstance(status, str) else list(status)
    if len(statuses) == 1:
        return {'property': 'Status', 'select': {'equals': statuses[0]}}
    return {
        'or': [
            {'property': 'Status', 'select': {'equals': s}} for s in statuses
        ],
    }


def _follow_up_clause(follow_up: str, today: str) -> dict:
    """Deferred (`future`) vs. actionable now (`due`).

    `due` has to admit an *unset* Follow-Up Date -- most entries never get
    one, and they are all actionable now.
    """
    if follow_up == 'future':
        return {'property': 'Follow-Up Date', 'date': {'after': today}}
    return {
        'or': [
            {'property': 'Follow-Up Date', 'date': {'on_or_before': today}},
            {'property': 'Follow-Up Date', 'date': {'is_empty': True}},
        ],
    }


def status_filter(
    status: str | Sequence[str],
    *,
    context: str | None = None,
    list_category: str | None = None,
    follow_up: str | None = None,
    today: str | None = None,
) -> dict:
    """The Notion filter behind `entries_for_status`."""
    clauses = [_status_clause(status)]
    if follow_up in {'future', 'due'}:
        clauses.append(_follow_up_clause(follow_up, today or _today_str()))
    if context:
        clauses.append(
            {'property': 'Context', 'select': {'equals': context}},
        )
    if list_category:
        clauses.append(
            {
                'property': 'List Category',
                'select': {'equals': list_category},
            },
        )
    if len(clauses) == 1:
        return clauses[0]
    return {'and': clauses}


def is_deferred(entry: ProjectEntry, today: str) -> bool:
    """Snoozed into the future -- the Incubation view.

    An *unset* Follow-Up Date is not deferred: most entries never get one,
    and they are all actionable now.
    """
    return bool(entry.follow_up_date and entry.follow_up_date > today)


def in_status_view(
    entry: ProjectEntry,
    status: str | Sequence[str],
    *,
    context: str | None = None,
    list_category: str | None = None,
    follow_up: str | None = None,
    today: str | None = None,
) -> bool:
    """The Python twin of `status_filter`."""
    statuses = {status} if isinstance(status, str) else set(status)
    if entry.status not in statuses:
        return False
    if context and entry.context != context:
        return False
    if list_category and entry.list_category != list_category:
        return False
    if follow_up == 'future':
        return is_deferred(entry, today or _today_str())
    if follow_up == 'due':
        return not is_deferred(entry, today or _today_str())
    return True


def entries_for_status(
    status: str | Sequence[str],
    *,
    context: str | None = None,
    list_category: str | None = None,
    follow_up: str | None = None,
    today: str | None = None,
) -> list[ProjectEntry]:
    """Entries in one or more statuses: the per-status tabs and `/entries`.

    `follow_up` narrows to `'future'` (deferred, the Incubation tab) or
    `'due'` (actionable now); anything else is ignored.
    """
    today = today or _today_str()
    pages = query_database(
        filter_obj=status_filter(
            status,
            context=context,
            list_category=list_category,
            follow_up=follow_up,
            today=today,
        ),
    )
    entries = [ProjectEntry.from_page(p) for p in pages]
    return [
        e
        for e in entries
        if in_status_view(
            e,
            status,
            context=context,
            list_category=list_category,
            follow_up=follow_up,
            today=today,
        )
    ]


def searchable_entries() -> list[ProjectEntry]:
    """Every entry the global search can reach.

    Not a view -- a corpus. It is the union of every status a tab shows,
    plus statusless captures, deliberately unfiltered by date so a search
    finds a snoozed item.
    """
    statuses = [
        'Triage',
        'Current Project',
        'Waiting For',
        'Recurring',
        'Someday/Maybe',
    ]
    pages = query_database(
        filter_obj={
            'or': [
                *_status_clause(statuses)['or'],
                {'property': 'Status', 'select': {'is_empty': True}},
            ],
        },
    )
    return [ProjectEntry.from_page(p) for p in pages]


# endregion By status
