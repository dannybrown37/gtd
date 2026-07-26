"""GTD Notion database schema definition — single source of truth."""

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
