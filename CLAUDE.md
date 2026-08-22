# GTD

A personal productivity CLI implementing David Allen's GTD method, backed by Notion. Entry point: `gtd` (runs the TUI by default).

**Install**: `uv tool install -e .` — must be re-run after every code change.

For design rationale and historical decisions, see `NARRATIVE.md`.

## Architecture

```
src/gtd/
├── cli.py          # CLI entry point (click group)
├── gtd_tui.py      # Textual TUI — GTDApp, all tab content widgets
├── tui.py          # Shared widgets: modals, DetailPane, VimListView
├── api.py          # Flask HTTP API + serves webapp/ as a PWA
├── gcal.py         # Google Calendar, read-only, via the `gfunk` CLI
├── ics.py          # Subscribed iCalendar feeds (work Outlook, etc.)
├── storage.py      # Local JSON I/O (~/.local/share/gtd/)
├── ui.py           # fzf helpers
├── webapp/         # Static PWA frontend (index.html, app.js, styles.css)
└── notion/
    ├── client.py   # Notion REST API client (httpx)
    ├── commands.py # GTD command implementations
    ├── views.py    # THE definition of every view (see below)
    ├── entries.py  # fzf entry picker, preview, field editing
    ├── triage.py   # Triage flow logic
    ├── capture.py  # Inbox capture
    ├── log.py      # reschedule_only; _is_recurring, _infer_reschedule_days
    ├── today.py    # Today filter logic
    ├── models.py   # ProjectEntry dataclass
    ├── schema.py   # Notion DB schema: STATUSES, STATUS_ICONS, agenda helpers
    ├── config.py   # ~/.config/gtd/ config management
    └── init.py     # DB creation/upgrade
```

## Critical Rules

### Views must live in `notion/views.py`

**Never build a Notion filter in a tab or endpoint.** All view definitions go in `views.py`. `tests/test_view_definitions.py` enforces this — `'property': 'Status'` may appear in exactly one module.

| function | used by |
|---|---|
| `next_steps_entries(today=None)` | Next Steps tab, `GET /next-steps`, `GET /contexts` |
| `inbox_entries()` | Inbox tab, `GET /inbox`, weekly review step 1 |
| `entries_for_status(...)` | every other tab, `GET /entries`, `GET /list/<cat>` |
| `searchable_entries()` | command-palette search corpus |

### Webapp parity

The webapp and TUI must stay **feature-equivalent**. Add a TUI binding → implement in webapp + add to `CAPABILITIES` in `app.js`, or add to `TUI_ONLY` in `tests/test_webapp_parity.py` **with a reason**. `TUI_ONLY` reasons are load-bearing — keep them true.

### `_triage_one` lives once on `BaseEntryContent`

Mirrored by `_process_single_entry` in `notion/triage.py` (CLI flow). Patch both or TUI/CLI diverge. Don't re-inline into a subclass.

## TUI Layout

Tabs: **Next Steps | Calendar | Inbox | Projects | Waiting For | Incubation | Recurring | Someday | Lists**

All tabs except **Calendar** extend `BaseEntryContent`; see below.

All entry tabs extend `BaseEntryContent`. A tab declares `VIEW_STATUS` (optionally `VIEW_FOLLOW_UP`) and the base class fetches via `views.entries_for_status`; tabs with different views override `_fetch()` to call another `views.py` function.

### Calendar (read-only, optional)

`CalendarContent` is the one tab that is **not** a `BaseEntryContent` — its rows are days, not Notion pages, so it borrows the layout and element ids but not the base class.

GTD holds no Google credentials. `gcal.py` shells out to `gfunk grind --json` (`/home/danny/projects/gfunk`), which owns the OAuth token and an opt-in `calendar.readonly` scope. Consequences:

- Calendar is **optional everywhere**. No gfunk, no token, or no `--with-calendar` opt-in raises `gcal.CalendarUnavailableError`, whose `hint` every surface renders as an empty state — never an error.
- Binary resolution: `GTD_GFUNK_BIN` env → `gfunk_bin` config key → `gfunk` on PATH.
- Working hours (`calendar_day_start` / `calendar_day_end`, default 08:00–20:00) define what counts as free time.
- `gcal.py` imports nothing from Notion, Textual, or Google. All rendering lives in `gtd_tui.py`; all parsing lives in `gcal.py`.
- The **Next Steps** tab carries a `LoadRibbonItem` showing today's load. It loads on its own `@work(thread=True)`, separate from `_load_entries`, so Notion never waits on a subprocess. It is `disabled=True`, so it can't take the highlight or fire an action, and it is simply absent when there is no calendar.
- `tests/conftest.py` blocks `gcal._run` suite-wide, the same way it blocks httpx. Without it the suite reads the developer's real Google Calendar — and passes anyway on a machine with no `gfunk`.
#### Subscribed feeds (`ics.py`)

Some calendars — a published Exchange/Outlook feed, for one — hand out an `.ics` URL that Google Calendar refuses to subscribe to, leaving a whole working week invisible to `gcal.py`. `ics.py` reads those directly.

- **`GTD_ICS_URL`** (or the `ics_url` config key) holds one or more feed URLs, comma-separated. **The URL is a bearer secret** — anyone holding it reads the calendar forever, with no sign-in. It must never appear in the repo, in a test fixture, or in a commit.
- Only `http(s)` URLs are accepted (`webcal://` is rewritten). A config value must never become a `file://` read.
- `parse_events` returns dicts in **Google's event shape**, plus a `_source` key. That is the whole trick: `group_days` and every renderer above it merge both calendars with no idea a second source exists. `CalEvent.source` carries it through to the ▪ work / ▫ personal marker.
- Recurrence is delegated to `recurring-ical-events`, not hand-rolled. These feeds carry repeat rules, per-occurrence exclusions, and single-instance overrides that must *suppress* the instance they replace — a hand-rolled expander double-counts those silently. (Verified: it does.)
- Busy rules — only Exchange `FREE`, `TRANSP:TRANSPARENT`, and `STATUS:CANCELLED` are dropped. `TENTATIVE` and `OOF` hold time.
- `fetch_events` **never raises**. One dead subscription must not blank out the calendar.
- `gcal.merged_events()` folds both sources together and returns `(events, hint)`. It raises only when there is nothing at all to show, so a machine with no `gfunk` still sees the work calendar; the tab shows `partial` when one source was unreadable.

- `group_days` is a deliberate **port** of gfunk's `grind_days`, not an import — importing it would pull gfunk's whole CLI module in. `free_spans` / `usable_gaps` are GTD's own addition and the reason the tab exists.

### Key Textual conventions

- **Never use `priority=True` on `GTDApp.BINDINGS`** — breaks modals. `TestQuitIsNotPriority` guards this.
- Use `@work` for all async actions with `push_screen_wait`. `@work(thread=True)` for blocking Notion calls.
- **Never `await` a `@work`-decorated method** — extract core logic into a plain `async def`.
- `check_action` must return explicit `True`/`False`, not `None`.
- Always call `self.app.refresh_bindings()` after selection changes affecting `check_action`.
- Use `remove_list_item(lv, item)` from `tui.py`, never bare `item.remove()`.
- Review browse screen keys must be **uppercase** — `TestReviewKeysAreDeliberate` enforces this.
- Review browse screen action names: escape is `finish_step` (never `done`). `TestReviewStepScoping` enforces distinct descriptions.

### @Person agenda primitives (all in `schema.py`)

`agenda_person_from_header`, `is_agenda_context`, `is_agenda_entry`, `strip_agenda_person`, `AGENDA_STATUS`, `AGENDA_CONTEXT_PREFIX`. Header is source of truth, not Context.

`SelectModal(..., hidden_prefix='@')` hides `@Person` contexts until query contains `@`. Applied to context pickers, **not** to `action_filter_context`.

### Weekly Review

Steps are `storage.REVIEW_STEPS` — the TUI aliases it as `_GTD_REVIEW_STEPS`, and `api.py` serves the same list. The webapp must not hardcode step labels (`tests/test_webapp_review.py` asserts this).

## Data Stores

| Data | Store |
|------|-------|
| GTD projects/inbox | Notion database (`NOTION_PROJECTS_DB_ID`) |
| Weekly habits | `~/.local/share/gtd/weekly_habits.json` |
| Weekly review state | The API server when `GTD_API_URL` + `GTD_API_KEY` are set (one source of truth shared with the webapp), else the local habits file |
| Areas of Focus | Notion `Area` select property |
| List categories | Notion `List Category` select property |
| Config | `~/.config/gtd/config.json` |

## Tooling

- **uv** for deps (`uv run`, `uv sync`)
- **ruff** for lint/format — `uv run ruff check src/` must pass
- **pytest** — `uv run pytest`
- Python 3.12+, Textual >= 0.71, httpx, click, python-dateutil
- Optional: Flask >= 3.1 (`uv pip install "gtd[api]"`)

## Releasing

Fully automated via Conventional Commits. Push to `main` → CI lint/test → commitizen bumps version + tags → `publish.yml` publishes to PyPI via trusted publishing. See `NARRATIVE.md` for prerequisites.
