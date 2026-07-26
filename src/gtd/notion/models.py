"""Parse Notion page properties into simple data structures."""

from __future__ import annotations

import re
from dataclasses import dataclass


__all__ = ['ProjectEntry', 'advance_steps', 'format_steps', 'parse_steps']

_NUMBERED_RE = re.compile(r'^\d+[.)]\s+(.+)$')


def parse_steps(text: str) -> list[str]:
    """Split a steps string into individual step texts (numbering stripped)."""
    if not text:
        return []
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    result = []
    for line in lines:
        m = _NUMBERED_RE.match(line)
        result.append(m.group(1) if m else line)
    return result


def format_steps(steps: list[str]) -> str:
    """Format a list of step strings as a numbered list."""
    return '\n'.join(f'{i + 1}. {s}' for i, s in enumerate(steps))


def advance_steps(text: str) -> str:
    """Remove the first step and renumber the rest. Returns '' when empty."""
    steps = parse_steps(text)
    if not steps:
        return ''
    return format_steps(steps[1:])


@dataclass
class ProjectEntry:
    """A single entry from the GTD Projects table."""

    page_id: str
    header: str
    status: str
    context: str
    next_step: str
    success_condition: str
    due_date: str | None
    follow_up_date: str | None
    created_date: str
    list_category: str = ''
    updated_date: str = ''

    @classmethod
    def from_page(cls: type[ProjectEntry], page: dict) -> ProjectEntry:
        props = page['properties']
        return cls(
            page_id=page['id'],
            header=_get_title(props.get('Header', {})),
            status=_get_select(props.get('Status', {})),
            context=_get_select(props.get('Context', {})),
            next_step=_get_rich_text(
                props.get('Next Actionable Step', {}),
            ),
            success_condition=_get_rich_text(
                props.get('Success Condition', {}),
            ),
            due_date=_get_date(props.get('Due Date', {})),
            follow_up_date=_get_date(props.get('Follow-Up Date', {})),
            created_date=page.get('created_time', ''),
            list_category=_get_select(props.get('List Category', {})),
            updated_date=page.get('last_edited_time', ''),
        )

    @property
    def steps(self) -> list[str]:
        """Parsed list of steps from next_step field."""
        return parse_steps(self.next_step)

    @property
    def current_step(self) -> str:
        """The first (active) step, or the raw next_step if not numbered."""
        s = self.steps
        return s[0] if s else ''

    @property
    def step_count(self) -> int:
        return len(self.steps)


def _get_title(prop: dict) -> str:
    title_list = prop.get('title', [])
    if not title_list:
        return ''
    return title_list[0].get('plain_text', '')


def _get_select(prop: dict) -> str:
    select = prop.get('select')
    if not select:
        return ''
    return select.get('name', '')


def _get_rich_text(prop: dict) -> str:
    texts = prop.get('rich_text', [])
    if not texts:
        return ''
    return ' '.join(t.get('plain_text', '') for t in texts).strip()


def _get_date(prop: dict) -> str | None:
    date = prop.get('date')
    if not date:
        return None
    return date.get('start')
