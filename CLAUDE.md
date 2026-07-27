# GTD

A personal productivity CLI at `gtd/` implementing David Allen's **Getting Things Done** (GTD) method, backed by Notion. Entry point: `gtd` (runs the TUI by default).

12-Week Year goal tracking (local-JSON goals/tactics, scoring, the Goals tab) was removed — GTD only now. If you see references to `Goal`, `Tactic`, cadences, or a Goals tab elsewhere, they're stale.

**Install**: `uv tool install -e .` — must be re-run after every code change for the installed `gtd` binary to pick up changes.

## Architecture

```
src/gtd/
├── cli.py          # CLI entry point (click group); gtd / gtd fzf / gtd tui / gtd triage / gtd api / etc.
├── gtd_tui.py      # Unified Textual TUI — GTDApp (main), all tab content widgets
├── tui.py          # Shared Textual widgets: modals, DetailPane, VimListView
├── api.py          # Flask HTTP wrapper for iOS Shortcuts / mobile access
├── storage.py      # Local JSON I/O for weekly review state, areas, list categories (~/.local/share/gtd/)
├── ui.py           # fzf helpers (fzf_on_a_list), CancelAction
└── notion/
    ├── client.py   # Notion REST API client (httpx)
    ├── commands.py # GTD command implementations (update, defer, snooze, done)
    ├── entries.py  # ProjectEntry fetching and filtering; _get_today_entries, _today_filter
    ├── triage.py   # Triage flow logic; TRIAGE_STATUSES
    ├── capture.py  # Inbox capture
    ├── log.py      # Log & reschedule; _is_recurring, _infer_reschedule_days
    ├── today.py    # Today filter logic
    ├── models.py   # ProjectEntry dataclass
    ├── schema.py   # Notion DB schema: STATUSES (includes Recurring), STATUS_ICONS
    ├── config.py   # ~/.config/gtd/ config management
    └── init.py     # DB creation/upgrade; reads NOTION_PROJECTS_DB_ID + NOTION_NOTES_TOKEN env vars
```

## TUI Layout (GTDApp)

Tabs: **Today | Next Steps | Inbox | Projects | Waiting For | Incubation | Recurring | Someday | Lists**

All entry tabs extend `BaseEntryContent(Vertical)` with stable IDs (`#entry-list`, `#entry-detail`, etc.) and shared infrastructure. Override `_build_filter()` to define what Notion entries appear. `TodayContent` overrides `_load_entries()` entirely (uses `_get_today_entries()`).

### Today tab

The Today tab has three sections in the left list:

1. **Weekly habit reminders** (top, always) — shown when not done this week:
   - `● Weekly Review` — `W` opens a guided 6-step flow via `WeeklyReviewScreen` modal. Steps: (1) Triage Inbox, (2) Review Projects, (3) Review Waiting For, (4) Review Someday/Maybe [uses `SomedayBrowseScreen`], (5) Review Areas of Focus, (6) Plan next week's priorities + Review Calendar [manual steps]. State persisted per-week in `weekly_habits.json` under `review_state`; resumes at first incomplete step.
   - Uses `check_action` to show `W` only when habit item is focused
   - Completion stored in `~/.local/share/gtd/weekly_habits.json`; resets each Monday

2. **GTD entries** — standard entries from Notion, separated with `── GTD ──` when habits are present

**`check_action` in TodayContent** — two mutually exclusive modes:
- `_HABIT_ACTIONS = {'complete_habit'}` — only active when habit focused
- `_GTD_ACTIONS = {log, snooze, waiting_for, update_entry, edit_notes, mark_done}` — only active when GTD entry focused
- Returns explicit `True`/`False` (not `None`) for both sets

### Inbox tab

**T** triages selected entry, **A** triages all — both use TUI modals (no fzf). `_triage_one()` chains: `SelectModal(status)` → `SelectModal(context)` → `InputModal(next step)` → `InputModal(due date)` → `InputModal(follow-up)`. Core logic is in `triage_entries(entries)` (public, no `@work`); `action_triage_all` wraps it with `@work`.

**Triage context for "List" status** uses `load_list_categories(LIST_CONTEXTS)` — the same source as the Lists tab — not Notion's raw context options.

### Waiting For tab (Weekly Review)

`WaitingForBrowseScreen` — browse Waiting For items during the Weekly Review step. Actions:
- **`d`** → Project Done — confirms then archives the page
- **`s`** → Change Status — `SelectModal` with all statuses except "Waiting For"; updates Notion on dismiss
- **`esc`** → Done / exit

Changes are collected (`_to_done: list`, `_status_changes: dict[str, str]`) and applied in `_review_waiting_for` after dismissal.

### Other tabs

- **Recurring** — Status == 'Recurring'; `L` log+reschedule (stays in list), `D` drop
- **Waiting For** — Status == 'Waiting For'
- **Incubation** — Current Project + follow_up > today
- **Projects / Someday** — standard status filters

## Weekly Review — Areas of Focus (step 5)

Each area loops until explicitly marked "All good". The prompt repeats for the same area after each capture, so multiple items can be captured before moving on. Escape exits the entire review early.

## CelebrationScreen

`CelebrationScreen(header)` — shown after `action_mark_done` confirms. Cycling emoji animation (4 frames, 0.35s interval), random hype message, auto-dismisses after 2s. Any key skips it. Located in `gtd_tui.py` near the top with `_CELEBRATION_FRAMES` and `_CELEBRATION_MESSAGES` constants.

## SelectModal UX

Two-mode design: opens in **browse mode** (ListView focused, j/k navigate). **Tab** switches to **filter mode** (Input focused, type to filter). Any printable non-j/k key in browse mode jumps to filter and appends char. Default is browse mode.

## SomedayBrowseScreen

`ModalScreen` used during Weekly Review step 4 (Review Someday/Maybe). Shows a scrollable list of Someday items — scroll with j/k, optionally **a** to activate or **d** to drop any item. No forced per-item decision; user browses at will and dismisses when done.

## Data Stores

| Data | Store | Location |
|------|-------|----------|
| GTD projects/inbox | Notion database | `NOTION_PROJECTS_DB_ID` env var |
| Weekly habit completion | Local JSON | `~/.local/share/gtd/weekly_habits.json` |
| Areas of Focus | Local JSON | `~/.local/share/gtd/areas.json` |
| List categories | Local JSON | `~/.local/share/gtd/list_categories.json` |
| GTD config | Local JSON | `~/.config/gtd/config.json` |

## Areas of Focus

`load_areas()` / `save_areas(areas)` in `storage.py` manage `areas.json` — a list of `{name: str, notes: str}` dicts. `load_areas()` returns `[]` when the file is missing.

**CLI commands** (`gtd areas`):
- `gtd areas` — list all areas (name + notes if present); prints "No areas defined" when empty
- `gtd areas add "Health"` — add new area; `--notes "..."` sets optional description; duplicate names rejected (case-insensitive)
- `gtd areas remove "Health"` — remove area by name (case-insensitive)
- `gtd areas notes "Health" "some notes"` — update notes field for existing area

## Key Models

**ProjectEntry** (Notion-backed): `page_id`, `header`, `status`, `context`, `next_step`, `due_date`, `follow_up_date`

**STATUSES** (schema.py): includes `'Recurring'` — items surface on Today when follow_up_date ≤ today; `action_mark_done` on recurring items offers Reschedule vs Permanently complete. Run `gtd init --upgrade` to add new statuses to an existing Notion DB.

## Shared Action Helpers (gtd_tui.py module-level)

- `_shared_log_and_reschedule(app, entry, notes_cache)` — opens editor, saves notes, infers or prompts reschedule date, updates Notion. Returns new date string or None.
- `_shared_edit_notes(app, entry, notes_cache, refresh_cb)` — opens editor, saves notes only.
- `_prompt_and_get_props(app, entry, field)` — prompts for a single field update, returns props dict.

## Textual Conventions

- `VimListView(ListView)` — adds j/k/G/gg bindings; k at index 0 posts `FocusTabBar`. `G`/`gg` move the *highlight* to the last/first enabled item (skipping `SeparatorListItem`s), not just the scroll offset. `gg` is a two-key sequence: the first `g` sets `_awaiting_second_g`, and `on_key` clears it on any other key.
- `DetailPane(ScrollableContainer)` — `can_focus = False` so Tab skips it
- `SeparatorListItem(ListItem)` — `disabled=True`, used as visual dividers; supports markup in label
- `WeeklyHabitItem(ListItem)` — habit reminder item with `habit_key` and `habit_label` attrs
- Modals: `InputModal`, `SelectModal`, `ConfirmModal`, `TwoFieldModal`, `SomedayBrowseScreen` — all `ModalScreen`
- `ENABLE_COMMAND_PALETTE = False` on `GTDApp`
- Use `@work` for ALL async actions that call `push_screen_wait` — required in both standalone and embedded contexts. `@work(thread=True)` for blocking Notion calls.
- **Never `await` a `@work`-decorated method** — it returns a `Worker` object. Extract core logic into a plain `async def` and have both `@work` action and other callers use that.
- Always call `self.app.refresh_bindings()` after selection changes that affect `check_action`
- `check_action` must return explicit `True`/`False` (not `None`) for actions you control — `None` means "defer to parent" which can cause unexpected behaviour with duplicate key bindings
- **`SplitFooter`** — subclass of `Footer`; separates contextual bindings (left) from global app bindings (right) with a ` ─── ` separator. Global section always sourced from `self.app.BINDINGS` directly (not overridable by child widgets).
- **Left pane width**: `40%` via CSS — dynamic, scales with terminal width.
- `EntryListItem` does **not** show context in the list label — context is only shown in the detail pane.

## HTTP API (api.py)

A Flask app for mobile/iOS Shortcuts access. Requires the `api` optional dependency group.

**Install**: `uv pip install "gtd[api]"`  
**Run**: `gtd api` (default: `0.0.0.0:8000`) or `gtd api --port 9000`  
**Auth**: Bearer token — set `GTD_API_KEY` env var on the server; pass as `Authorization: Bearer <key>` header.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/capture` | Add item to inbox (`{"header": "..."}`) |
| `GET` | `/today` | Today's actionable entries |
| `GET` | `/inbox` | All Triage entries |
| `POST` | `/done/{page_id}` | Archive (complete) an entry |
| `POST` | `/snooze/{page_id}` | Snooze (`{"days": 1}` or `{"until": "Friday"}`) |
| `PATCH` | `/entry/{page_id}` | Update fields: `status`, `context`, `next_step`, `due_date`, `follow_up_date` |
| `GET` | `/statuses` | List valid GTD statuses |

All responses are JSON. Entry objects match `ProjectEntry` fields.

## Tooling

- **uv** for dependency management (`uv run`, `uv sync`)
- **ruff** for lint/format — `uv run ruff check src/` must pass before shipping
- **pytest** for tests — `uv run pytest`
- Python 3.12+, Textual ≥ 0.71, Pydantic ≥ 2, httpx, click, python-dateutil
- Optional: Flask ≥ 3.1 (install with `uv pip install "gtd[api]"`)
- After any code change: `uv tool install -e .` to update the installed `gtd` binary
