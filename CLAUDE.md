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
├── storage.py      # Local JSON I/O for weekly review state and habit dates (~/.local/share/gtd/)
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

The Today tab has two sections in the left list:

1. **Weekly habit reminders** (top, always listed — done or not):
   - `● Weekly Review` — `W` opens a guided 6-step flow via `WeeklyReviewScreen` modal. Steps: (1) Triage Inbox, (2) Review Projects, (3) Review Waiting For, (4) Review Someday/Maybe [uses `SomedayBrowseScreen`], (5) Review Areas of Focus, (6) Plan next week's priorities + Review Calendar [manual steps]. State persisted per-week in `weekly_habits.json` under `review_state`; resumes at first incomplete step.
   - Pending: red `●` + `not done this week`. Done: green `●` + `last: <when>` (`_habit_last_done_str`). The row never disappears — `_mark_habit_done` flips it in place via `WeeklyHabitItem.refresh_label()`, and `W` stays available to re-run the review.
   - Uses `check_action` to show `W` only when habit item is focused
   - Completion stored in `~/.local/share/gtd/weekly_habits.json`; resets each Monday

2. **GTD entries** — standard entries from Notion, grouped by context. No divider between the habit rows and the entries.

The Today header count and its "nothing actionable 🎉" empty state describe the GTD entries only, since a habit row is always present.

**`check_action` in TodayContent** — two mutually exclusive modes:
- `_HABIT_ACTIONS = {'complete_habit'}` — only active when habit focused
- `_GTD_ACTIONS = {log, snooze, waiting_for, update_entry, edit_notes, mark_done}` — only active when GTD entry focused
- Returns explicit `True`/`False` (not `None`) for both sets

### Inbox tab

**T** triages selected entry, **A** triages all — both use TUI modals (no fzf). `_triage_one()` chains: `SelectModal(status)` → `SelectModal(context)` → `InputModal(next step)` → `InputModal(due date)` → `InputModal(follow-up)`. Core logic is in `triage_entries(entries)` (public, no `@work`); `action_triage_all` wraps it with `@work`.

**Triage context for "List" status** uses `get_list_categories()` (`notion/client.py`), which reads the `List Category` select options from Notion — the same source as the Lists tab, not Notion's Context options. The CLI triage flow (`notion/triage.py`) reads the same function.

### Waiting For tab (Weekly Review)

`WaitingForBrowseScreen` — browse Waiting For items during the Weekly Review step. Actions:
- **`U`** → Update project — prompts for a field, updates Notion
- **`S`** → Change Status — `SelectModal` with all statuses except "Waiting For"; updates Notion on dismiss
- **`esc`** / **`q`** → `action_finish_step` — finishes the review step

**Review keys are capitals** — every per-item key on the three browse screens is an uppercase letter (`U`/`S`/`E`/`D`/`A`), mirroring the main app, so lowercase navigation/global keys (`h j k l q`) can never fire a destructive action by accident. `q` is bound to `finish_step` on the browse screens and `cancel` on `WeeklyReviewScreen` — it used to quit the entire app mid-review (see the priority-binding note under Textual Conventions). `TestReviewKeysAreDeliberate` enforces both.

**Key-scope labelling (all three browse screens)** — `ProjectsBrowseScreen`, `WaitingForBrowseScreen` and `SomedayBrowseScreen` each have keys at two different scopes, and both used to be described as "Done", which made them indistinguishable. The escape action is named `finish_step` (never `done`), and each screen's footer is two lines: `this item — ...` for the per-entry keys, `this step — esc: done reviewing <thing>` for the exit. Keep new bindings on the right line, and don't reuse a description across two visible bindings — `TestReviewStepScoping` enforces this.

Changes are collected (`_to_done: list`, `_status_changes: dict[str, str]`) and applied in `_review_waiting_for` after dismissal.

`ProjectsBrowseScreen` collects three things — `_to_someday`, `_step_updates`, `_to_drop` (the `D` key, confirm-then-archive) — dismissed as a 3-tuple via `_result()` and applied in `_review_projects`.

### Someday tab

`SomedayContent` groups and sorts by **Area** (`ProjectEntry.area`), not Context — Context answers "what tool do I need to act on this," which is meaningless for something explicitly not actionable yet; Area answers "which part of my life is this in," which is what matters when reviewing Someday/Maybe. This mirrors the Lists tab's `List Category` pattern exactly: `Area` is its own Notion select property (`schema.py`), with its own CRUD in `client.py` (`get_areas`/`add_area`/`remove_area`/`rename_area`) and its own `ProjectEntry.area` field — entirely independent of Context.

- `_rebuild_list()` shows every known area (even empty ones, labelled `(empty)`) plus a trailing `(no area)` bucket for unassigned entries — same shape as `ListsContent._rebuild_list`.
- Keys: `(` assign/change an entry's Area (`SelectModal`, `(no area)` to clear), `+`/`-`/`)` add/remove/rename an Area itself (identical mechanics and key choices to Lists' category CRUD). Rename propagates to every entry carrying the old value, same as `action_rename_category`.
- `L` (→ List) writes the chosen category into the `List Category` property via `build_property_update(list_category=...)` — it used to incorrectly write into `Context`, which polluted the Context select with list-category values.

### Other tabs

- **Recurring** — Status == 'Recurring'; `L` log+reschedule (stays in list), `D` drop
- **Waiting For** — Status == 'Waiting For'
- **Incubation** — Current Project + follow_up > today
- **Projects** — standard status filter

## Weekly Review — Areas of Focus (step 5)

`_review_areas` iterates `get_areas()` (Notion, not local JSON — see [Areas of Focus](#areas-of-focus)). Each area loops until explicitly marked "All good". The prompt repeats for the same area after each capture, so multiple items can be captured before moving on. Escape exits the entire review early.

## CelebrationScreen

`CelebrationScreen(header, *, messages, frames, duration, quoted)` — cycling emoji animation (0.35s interval), a random hype message, auto-dismisses after `duration`. Any key skips it. Located in `gtd_tui.py` near the top with the `_CELEBRATION_*`, `_STEP_MESSAGES` and `_REVIEW_FINALE_*` constants.

Three callers, each with its own message pool:
- `action_mark_done` — defaults (`_CELEBRATION_MESSAGES`, 2s, header quoted)
- every completed Weekly Review step — `_STEP_MESSAGES`, 1.5s, header is `<step label>  ·  n/total steps`
- the last review step — `_REVIEW_FINALE_MESSAGES` + `_REVIEW_FINALE_FRAMES`, 3s; replaces the per-step fanfare rather than stacking on it

`WeeklyReviewScreen._celebrate_step` picks between the last two by counting done steps. Step labels carry console markup (`[dim](3 items)[/dim]`), so it runs them through `_plain()` — the celebration Statics are `markup=False`.

## SelectModal UX

Two-mode design: opens in **browse mode** (ListView focused, j/k navigate). **Tab** switches to **filter mode** (Input focused, type to filter). Any printable non-j/k key in browse mode jumps to filter and appends char. Default is browse mode.

## SomedayBrowseScreen

`ModalScreen` used during Weekly Review step 4 (Review Someday/Maybe). Shows a scrollable list of Someday items — scroll with j/k, optionally **A** to activate or **D** to drop any item. No forced per-item decision; user browses at will and dismisses when done.

## Data Stores

| Data | Store | Location |
|------|-------|----------|
| GTD projects/inbox | Notion database | `NOTION_PROJECTS_DB_ID` env var |
| Weekly habit completion | Local JSON | `~/.local/share/gtd/weekly_habits.json` |
| Areas of Focus | Notion select options | `Area` property on the projects DB |
| List categories | Notion select options | `List Category` property on the projects DB |
| GTD config | Local JSON | `~/.config/gtd/config.json` |

## Areas of Focus

`Area` is a Notion select property on the projects DB (`schema.py`), CRUD'd via `client.py`'s `get_areas()`/`add_area()`/`remove_area()`/`rename_area()` — the exact same mechanics as `List Category` (see [Someday tab](#someday-tab)). No local JSON, no notes field; the only description of an area is its name.

**CLI commands** (`gtd areas`):
- `gtd areas` — list all areas; prints "No horizons defined" when empty
- `gtd areas add "Health"` — add new area; duplicate names rejected (case-insensitive)
- `gtd areas remove "Health"` — remove area by name (case-insensitive)

**TUI**: the Someday/Maybe tab also manages areas directly (`+`/`-`/`)` keys) — CLI and TUI both operate on the same Notion select options, so neither is authoritative over the other.

## Key Models

**ProjectEntry** (Notion-backed): `page_id`, `header`, `status`, `context`, `next_step`, `due_date`, `follow_up_date`, `list_category`, `area`

**STATUSES** (schema.py): includes `'Recurring'` — items surface on Today when follow_up_date ≤ today; `action_mark_done` on recurring items offers Reschedule vs Permanently complete. Run `gtd init --upgrade` to add new statuses to an existing Notion DB.

## Shared Action Helpers (gtd_tui.py module-level)

- `_shared_log_and_reschedule(app, entry, notes_cache)` — opens editor, saves notes, infers or prompts reschedule date, updates Notion. Returns new date string or None.
- `_shared_edit_notes(app, entry, notes_cache, refresh_cb)` — opens editor, saves notes only.
- `_prompt_and_get_props(app, entry, field)` — prompts for a single field update, returns props dict.

## Textual Conventions

- `VimListView(ListView)` — adds j/k/G/gg bindings; k at index 0 posts `FocusTabBar`. `G`/`gg` move the *highlight* to the last/first enabled item (skipping `SeparatorListItem`s), not just the scroll offset. `gg` is a two-key sequence: the first `g` sets `_awaiting_second_g`, and `on_key` clears it on any other key.
- `DetailPane(ScrollableContainer)` — `can_focus = False` so Tab skips it
- `SeparatorListItem(ListItem)` — `disabled=True`, used as visual dividers; supports markup in label
- `WeeklyHabitItem(ListItem)` — habit reminder item with `habit_key` and `habit_label` attrs; `_label_markup()` renders the row and `refresh_label()` re-renders it in place. Do **not** name such a method `_render` — that's a `Widget` internal and overriding it breaks rendering.
- **Removing list items** — always use `remove_list_item(lv, item)` (`tui.py`), never bare `item.remove()`. `ListItem.remove()` leaves `ListView.index` alone, so removing the highlighted item leaves the index on whatever slides into its place without ever highlighting it — the list looks unhighlighted while actions still operate on it. Most visible with one item left, where j/k can't re-fire the watcher. The helper mirrors `ListView.pop`'s index fixup and skips separators.
- Modals: `InputModal`, `SelectModal`, `ConfirmModal`, `TwoFieldModal`, `SomedayBrowseScreen` — all `ModalScreen`
- `ENABLE_COMMAND_PALETTE = False` on `GTDApp`
- **Never use `priority=True` on `GTDApp.BINDINGS`** — Textual checks priority bindings from the App *down through* modals (`App._check_bindings` walks `reversed(screen._binding_chain)`, which includes the app even when a `ModalScreen` is open). `q`/`h`/`l` were priority, so `q` quit the whole app from inside the weekly review and `h`/`l` switched tabs behind open modals. Without priority they still resolve fine on the main screen (the non-priority pass walks the chain up to the app) and stay out of modals' way; `TestQuitIsNotPriority` guards both directions.
- Use `@work` for ALL async actions that call `push_screen_wait` — required in both standalone and embedded contexts. `@work(thread=True)` for blocking Notion calls.
- **Never `await` a `@work`-decorated method** — it returns a `Worker` object. Extract core logic into a plain `async def` and have both `@work` action and other callers use that.
- Always call `self.app.refresh_bindings()` after selection changes that affect `check_action`
- `check_action` must return explicit `True`/`False` (not `None`) for actions you control — `None` means "defer to parent" which can cause unexpected behaviour with duplicate key bindings
- **`SplitFooter`** — subclass of `Footer`; separates contextual bindings (left) from global app bindings (right) with a ` ─── ` separator. Global section always sourced from `self.app.BINDINGS` directly (not overridable by child widgets).
- **Left pane width**: `40%` via CSS — dynamic, scales with terminal width.
- `EntryListItem` does **not** show context in the list label — context is only shown in the detail pane.

## HTTP API (api.py)

A Flask app giving mobile/iOS Shortcuts access to GTD without a terminal —
capture, check today's items, triage the inbox, etc. There is no central
server: each user runs their own instance against their own Notion
integration/database, so no GTD data is ever shared with or stored by a
third party.

**Install**: `uv pip install "gtd[api]"`  
**Run**: `gtd api` (default: `0.0.0.0:8000`) or `gtd api --port 9000`  
**Auth**: Bearer token — set `GTD_API_KEY` env var on the server; pass as `Authorization: Bearer <key>` header.

The endpoint list is intentionally not duplicated here — it drifts. The
README's "HTTP API" section has an always-current table auto-generated from
`api.py`'s route decorators by `scripts/update_readme.py` (run via the
`sync-readme` pre-commit hook whenever `api.py` changes). To document a new
endpoint, give its Flask view function a one-line docstring — that's what
lands in the table.

All responses are JSON. Entry objects match `ProjectEntry` fields.

## Tooling

- **uv** for dependency management (`uv run`, `uv sync`)
- **ruff** for lint/format — `uv run ruff check src/` must pass before shipping
- **pytest** for tests — `uv run pytest`
- Python 3.12+, Textual ≥ 0.71, Pydantic ≥ 2, httpx, click, python-dateutil
- Optional: Flask ≥ 3.1 (install with `uv pip install "gtd[api]"`)
- After any code change: `uv tool install -e .` to update the installed `gtd` binary

## Releasing to PyPI

Release is fully automated — the only thing that drives it is commit message format.
Write commits as Conventional Commits (`feat:`, `fix:`, `chore:`, etc.) and everything
else happens in CI:

1. Push to `main` → `ci.yml`'s `lint`/`test` jobs run.
2. If they pass, the `bump` job runs `commitizen-tools/commitizen-action`, which reads
   commits since the last tag, decides the version bump, updates `pyproject.toml` +
   `CHANGELOG.md`, commits (`bump: ...`), and pushes the tag. If no commits since the
   last tag warrant a bump (e.g. a docs/chore-only push), this is a silent no-op
   (`no_raise: "21"` — `NO_COMMITS_TO_BUMP` isn't a failure).
3. The pushed `v*` tag triggers `publish.yml`, which builds and publishes to PyPI via
   trusted publishing (OIDC, no stored token).

Versioning config lives in `[tool.commitizen]` in `pyproject.toml`
(`version_provider = "pep621"` — bumps `[project].version` directly; `tag_format =
"v$version"` matches the publish trigger; `major_version_zero = true` keeps bumps in
the `0.x` range pre-1.0). Pre-commitizen commit history doesn't follow Conventional
Commits — that's fine, only commits since the last tag are considered.

**Repo prerequisites for the `bump` job to be able to push:**
- Settings → Actions → General → Workflow permissions must be "Read and write
  permissions" (otherwise `GITHUB_TOKEN` can't push the bump commit/tag).
- If `main` has branch protection requiring PRs/status checks, the bump job's direct
  push will be rejected unless `github-actions[bot]` is allowed to bypass it.
- The PyPI trusted publisher (pypi.org → project `gtd-tui` → repo `dannybrown37/gtd`,
  workflow `publish.yml`, environment `pypi`) must be registered before the first tag
  push, or `publish.yml` will fail with an auth error.

You can still bump manually with `uv run cz bump` if needed — CI does the same thing.
