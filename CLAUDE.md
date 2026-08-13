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
├── api.py          # Flask HTTP wrapper for iOS Shortcuts / mobile access; also serves webapp/ as a PWA
├── storage.py      # Local JSON I/O for weekly review state and habit dates (~/.local/share/gtd/)
├── ui.py           # fzf helpers (fzf_on_a_list), CancelAction
├── webapp/         # Static PWA frontend (index.html, app.js, styles.css, manifest.json, icons/) served by api.py
└── notion/
    ├── client.py   # Notion REST API client (httpx)
    ├── commands.py # GTD command implementations (update, defer, snooze, done)
    ├── views.py    # THE definition of every view (next_steps_entries, inbox_entries, entries_for_status)
    ├── entries.py  # fzf entry picker, preview text, interactive field editing
    ├── triage.py   # Triage flow logic; TRIAGE_STATUSES
    ├── capture.py  # Inbox capture
    ├── log.py      # reschedule_only (CLI); _is_recurring, _infer_reschedule_days
    ├── today.py    # Today filter logic
    ├── models.py   # ProjectEntry dataclass
    ├── schema.py   # Notion DB schema: STATUSES (includes Recurring), STATUS_ICONS
    ├── config.py   # ~/.config/gtd/ config management
    └── init.py     # DB creation/upgrade; reads NOTION_PROJECTS_DB_ID + NOTION_NOTES_TOKEN env vars
```

## View definitions

**`notion/views.py` is the only place a view may be defined.** A "view" is
the answer to *which entries belong on this list* — Next Steps, Inbox,
Projects, Someday, one List category. Every surface (TUI tab, CLI command,
HTTP endpoint, and through it the webapp) calls a function from there and
renders the result.

| function | used by |
|---|---|
| `next_steps_entries(today=None)` | Next Steps tab, `GET /next-steps`, `GET /contexts`, `gtd today` |
| `inbox_entries()` | Inbox tab, `GET /inbox`, weekly review step 1, `gtd triage` |
| `entries_for_status(status, *, context, list_category, follow_up, today)` | every other tab, `GET /entries`, `GET /list/<cat>`, review steps 2–4, `gtd done`/`defer`/`waiting` |
| `searchable_entries()` | the command-palette search corpus — a union, not a view |

Each view is expressed **twice, deliberately**: a Notion filter
(`_today_filter`, `inbox_filter`, `status_filter`) that pre-narrows the
query, and a Python predicate (`is_due_today`, `is_actionable`,
`is_deferred`, `in_status_view`, `drop_triaged_agenda_items`) applied to
whatever comes back. **The predicate is the definition; the filter is an
optimisation.** Notion's filter language can't express parts of GTD at all
(no `starts_with` on a select, so `@Person` contexts are unreachable
server-side), so some rule always lives in Python — and a rule in Python is
one that can be tested without a Notion double. A filter that is too loose
therefore fetches too much rather than silently widening a view.

Nothing in `views.py` sorts. Ordering is presentation and each surface
applies its own.

**Why this is a rule and not a preference.** `api.py` used to re-implement
every one of these. `GET /inbox` was `Status == "Triage"` while the TUI
meant six clauses more, so the phone reported inbox zero while the TUI
showed a backlog — most importantly hiding a Current Project with no next
action, the single thing a GTD inbox exists to surface. `GET /next-steps`
omitted the `context and next_step` gate. `GET /contexts` derived the same
view a fourth way and missed the due-date escape hatch, so
`/next-steps?context=X` could return items under a context the picker never
offered. `GET /list/<cat>` filtered on category without `Status == 'List'`.
All four shipped and all four passed CI, because
`tests/test_webapp_parity.py` compares *action names* and has nothing to
say about what a list contains.

`tests/test_view_definitions.py` is the guard: `'property': 'Status'` may
appear in exactly one module, no consumer may parse a page result set, every
tab must declare `VIEW_STATUS` or override `_fetch`, and each view endpoint
must call its named view function. If you need a new view, add it to
`views.py` — do not inline a filter at the call site, the test will fail.

## TUI Layout (GTDApp)

Tabs: **Next Steps | Inbox | Projects | Waiting For | Incubation | Recurring | Someday | Lists**

All entry tabs extend `BaseEntryContent(Vertical)` with stable IDs (`#entry-list`, `#entry-detail`, etc.) and shared infrastructure. A tab declares **which** view it shows (`VIEW_STATUS`, optionally `VIEW_FOLLOW_UP`) and the base class fetches it via `views.entries_for_status`; a tab whose view isn't a status query overrides `_fetch()` to call another function from `notion/views.py` (`InboxContent` → `inbox_entries()`, `NextStepsContent` → `next_steps_entries()`). **Never build a Notion filter in a tab** — see [View definitions](#view-definitions).

There used to be a separate "Today" tab and "Next Steps" tab; they were merged (2026-07-30) because they were nearly identical — the only real differences were that Today applied a follow-up-date filter (hiding snoozed items) that Next Steps lacked, and only Today carried the Weekly Review habit row. `NextStepsContent` is what `TodayContent` used to be, renamed and promoted to the first/home tab; the old separate `NextStepsContent` (a plain `Current Project` + due-`Recurring` filter with no date restriction) was deleted outright, along with the now-unused `_active_recurring_filter`/`_recurring_due_clauses` helpers in `notion/entries.py` it was the only caller of.

### Next Steps tab (home tab)

The Next Steps tab has two sections in the left list:

1. **Weekly habit reminders** (top, always listed — done or not):
   - `● Weekly Review` — `W` opens a guided 7-step flow via `WeeklyReviewScreen` modal. **The steps themselves are `storage.REVIEW_STEPS`, not a list in this file or in `gtd_tui.py`** — `_GTD_REVIEW_STEPS` is an alias for it, and the HTTP API serves the same list to the webapp (`api.py` must not import Textual to learn it). Currently: (1) Process Inbox, (2) Review Projects, (3) Review Waiting For, (4) Review Someday/Maybe [uses `SomedayBrowseScreen`], (5) Review Horizons of Focus, (6) Review Calendar, (7) Plan Next Week's Priorities — the last two `manual`. State persisted per-week in `weekly_habits.json` under `review_state`; resumes at first incomplete step. **The webapp has this flow too** — see [Webapp](#webapp-srcgtdwebapp).
   - Pending: red `●` + `not done this week`. Done: green `●` + `last: <when>` (`_habit_last_done_str`). The row never disappears — `_mark_habit_done` flips it in place via `WeeklyHabitItem.refresh_label()`, and `W` stays available to re-run the review.
   - Uses `check_action` to show `W` only when habit item is focused
   - Completion stored in `~/.local/share/gtd/weekly_habits.json`; resets each Monday
   - Because the habit row is always present and always enabled, `repopulate()` (which highlights the first non-disabled item) puts the highlight on it after every list rebuild — including after applying a context filter with `F`. Filtering down to one matching entry does *not* move the highlight onto that entry; it stays on the habit row.

2. **GTD entries** — standard entries from Notion, grouped by context. No divider between the habit rows and the entries.

The header count and its "nothing actionable 🎉" empty state describe the GTD entries only, since a habit row is always present.

**`check_action` in NextStepsContent** — two mutually exclusive modes:
- `_HABIT_ACTIONS = {'complete_habit'}` — only active when habit focused
- `_GTD_ACTIONS = {log, snooze, waiting_for, update_entry, edit_notes, mark_done}` — only active when GTD entry focused
- Returns explicit `True`/`False` (not `None`) for both sets

### Inbox tab

**T** triages selected entry, **A** triages all — both use TUI modals (no fzf). `_triage_one()` chains: `SelectModal(status)` → `SelectModal(context)` → `InputModal(next step)` → `InputModal(due date)` → `InputModal(follow-up)`. Core logic is in `triage_entries(entries)` (public, no `@work`); `action_triage_all` wraps it with `@work`.

**Triage context for "List" status** uses `get_list_categories()` (`notion/client.py`), which reads the `List Category` select options from Notion — the same source as the Lists tab, not Notion's Context options. The CLI triage flow (`notion/triage.py`) reads the same function.

### @Person agendas

A header starting with `@Name` (`@Sam: raise the budget`) declares an *agenda item* — something to raise with a person, not a task. See the README's "@Person agendas" section for the user-facing contract; this is what maintaining it requires.

**The four primitives, all in `notion/schema.py`** (not `triage.py` — `triage.py` imports `entries.py`, so anything `entries.py` needs would cycle):

| | |
|---|---|
| `agenda_person_from_header(header)` | Leading `@token` → `'@Sam'`; colon optional/stripped; bare `@` rejected. Only a *leading* token counts. |
| `is_agenda_context(context)` | The Context select option starts with `@`. |
| `is_agenda_entry(entry)` | Either signal. **Prefer this** — duck-typed on `.header`/`.context`. |
| `strip_agenda_person(header)` | Drops the prefix for display. Never returns empty. |
| `AGENDA_STATUS` | `'Current Project'`. |
| `AGENDA_CONTEXT_PREFIX` | `'@'`. |

**Why the header, not the Context, is the source of truth**: capture writes the person into the *title*, so the Context select always lags — a brand-new person has no option yet. Keying only off Context meant triage prompted for a context that didn't exist for an item that had already named it. Triage now derives it and calls `add_context()` (idempotent) to create the option silently.

**Exempting agenda items from a field means fixing every consumer that requires it.** This bit twice — the pattern to watch for:

- `inbox_filter()` counts an empty next step / success condition as "needs triage" → agenda items never leave the Inbox. Notion select filters have no `starts_with`, so this **cannot** be expressed server-side; `drop_triaged_agenda_items()` applies it client-side. Both halves now live inside `inbox_entries()`, so no caller can apply one without the other. Items still in Triage/statusless are deliberately kept.
- `is_actionable()` (`notion/views.py`) gates on `context and next_step` → agenda items showed on Projects but not Next Steps. `is_agenda_entry` is an escape hatch there alongside `Recurring`, which exists for the identical reason.
- `NextStepListItem._format` printed a dim `(no step)` placeholder above the real content. Agenda rows now render single-line with `→` and the person stripped.

**Triaging to Someday/Maybe asks for Area, not Context** — and asks for nothing else. A parked idea has no next action, no due date, and no context (Context = "what tool do I need to act on this"); the Someday tab groups by `Area`, so that's the only field triage collects, then it saves and returns. This means `inbox_filter()` must exempt `Someday/Maybe` from its empty-context/next-step/success-condition clauses (it does, server-side via `not_someday`) or every Someday item bounces straight back into the Inbox — the same trap agenda items hit.

**`_triage_one` lives once, on `BaseEntryContent`**, and is mirrored by `_process_single_entry` in `notion/triage.py` (the fzf CLI flow). Patch both or the TUI and the CLI diverge.

It used to be duplicated verbatim on `NextStepsContent` and `InboxContent` — along with `triage_entries`, `action_triage_entry` and `action_triage_all` — and this file instructed you to patch every copy. Only `InboxContent` ever bound `T`/`A`, so the `NextStepsContent` copy (307 lines) was unreachable; the weekly review's triage step reaches the same code by querying `InboxContent` directly (`WeeklyReviewScreen._run_step`). All four are now defined once on the base class. Don't re-inline them into a subclass.

Skipping the Status prompt also removed the inline *Delete* option for agenda items; `D` on the Inbox tab is the remaining exit.

**`SelectModal(..., hidden_prefix='@')`** (`tui.py`) keeps `@Person` contexts out of the browse list until the query contains `@`; the placeholder advertises it. Applied to the context pickers (triage + update), deliberately **not** to `action_filter_context` — filtering to "everything for one person" is a real use, and that list only contains contexts present in the current view.

`BaseEntryContent._fetch()` is the hook for a tab whose view isn't a plain status query. It must return the result of a `notion/views.py` function, never a query the tab composes itself.

### Waiting For always carries a tickler

A Waiting For with no Follow-Up Date is invisible: Next Steps reaches that status **only** through a due Follow-Up Date, so an unset one leaves the item sitting on its own tab until you go looking. Six surfaces could create that state (TUI `action_waiting_for`, TUI `_triage_one`, `notion/triage.py`, `commands.set_waiting_for`, the webapp triage modal, and a bare `PATCH /entry {status: "Waiting For"}`) and every one of them *called* the date required without enforcing it.

The fix is a **default at the single write chokepoint, not validation at six call sites** — that spread is exactly how the `/inbox` divergence above happened:

- `build_property_update()` (`client.py`): writing `status == 'Waiting For'` with no Follow-Up Date in the same call stamps `default_waiting_for_follow_up()` (today + `WAITING_FOR_DEFAULT_FOLLOW_UP_DAYS`, 1 week). Both live in `schema.py` — `client.py` imports it and it imports nothing, so there's no cycle.
- Keyed off the **Status write**, not the page's current status, so an unrelated edit to an item already waiting can't reset its clock.
- This deliberately **outranks the empty-string-clears convention**: `follow_up_date=''` alongside `status='Waiting For'` falls back to the default rather than blinding the item.
- The prompts stay non-blocking but pre-fill that same date so it's visible and overridable. The webapp duplicates the constant in `app.js`; `test_webapp_parity.py` pins the two numbers together, since divergence would show one date and save another.

Two view consequences, both in `views.py`:

- `_today_filter` admits two populations — `(Current Project | Recurring) AND any date signal` (including *no* date), plus `_waiting_for_due_clause()`: `Waiting For AND Follow-Up Date on_or_before today`. Note the asymmetry — an *unset* Follow-Up Date admits nothing in the second branch, or the whole Waiting For list would move into Next Steps permanently. `is_due_today` mirrors it with a leading `WAITING_FOR_STATUS` branch.

  **Notion accepts exactly two levels of filter nesting**, so this is emitted as a *flat* `or` of `and` pairs — one `{status AND date signal}` per combination — not the `or → and → or` it reads as. Written the natural way (which shipped, and broke Next Steps on every surface: 400 in the TUI, 500 in the webapp) Notion rejects the entire query with `body.filter.or[0].and[0].title should be defined` — it stops descending after two levels and expects a property filter where it finds a compound. Consequence: `_status_clause` with more than one status is itself an `or`, so it can only ever appear at the top level of a filter. `TestNotionNestingLimit` checks the depth of every filter `views.py` builds.
- Items predating the default are rescued by `needs_follow_up_date()`, surfaced via `inbox_filter()` the same way a Current Project with no next action is. `drop_triaged_agenda_items()` exempts them, or an agenda-shaped Waiting For would be dropped straight back out.

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
- **An Area is only removable while empty** — `-` refuses (a warning notification, no confirm-anyway escape hatch) if any entry still carries it. Deleting the Notion select option leaves those entries pointing at a value that no longer exists, invisible under every grouping. The same rule applies to List categories and is enforced server-side too (`DELETE /areas/<name>` and `DELETE /list-categories/<name>` both 409 with a `count`).
- Keys: `(` assign/change an entry's Area (`SelectModal`, `(no area)` to clear), `+`/`-`/`)` add/remove/rename an Area itself (identical mechanics and key choices to Lists' category CRUD). Rename propagates to every entry carrying the old value, same as `action_rename_category`.
- `L` (→ List) writes the chosen category into the `List Category` property via `build_property_update(list_category=...)` — it used to incorrectly write into `Context`, which polluted the Context select with list-category values.

### Other tabs

- **Recurring** — Status == 'Recurring'; `D` drop. Rescheduling happens through `D: Done` → *Reschedule* (see below), not a separate key — there is no `L` binding here.
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

**TUI**: the Someday/Maybe tab also manages areas directly (`+`/`-`/`)` keys). **Webapp**: the Someday view manages them too (see [Webapp](#webapp-srcgtdwebapp)). CLI, TUI and webapp all operate on the same Notion select options, so none is authoritative over the others.

## Key Models

**ProjectEntry** (Notion-backed): `page_id`, `header`, `status`, `context`, `next_step`, `due_date`, `follow_up_date`, `list_category`, `area`

**STATUSES** (schema.py): includes `'Recurring'` — items surface on Next Steps when follow_up_date ≤ today; `action_mark_done` on recurring items offers Reschedule vs Permanently complete. Run `gtd init --upgrade` to add new statuses to an existing Notion DB.

## Shared Action Helpers (gtd_tui.py module-level)

- `_shared_reschedule_only(app, entry)` — sets a recurring entry's next follow-up date and nothing else. Infers the date from a cadence header prefix (`Daily:`/`Weekly:`/`2x/week:`/`3x/week:`, see `_infer_reschedule_days`) and only asks a `ConfirmModal` to accept it; declining falls through to the manual `InputModal` rather than cancelling. Returns new date string or None.

  It replaced `_shared_log_and_reschedule`, which opened `$EDITOR` on the notes body and prompted for a Context before reaching the date — three interactions for one decision — and applied an inferred date silently with no confirmation. That function's only live caller was `action_mark_done`'s *Reschedule* branch; the two other callers (`BaseEntryContent.action_log_and_reschedule`, `NextStepsContent.action_log`) had no key binding on any widget and were deleted with it. `TestNoDeadRescheduleActions` guards against re-adding an unbound action of that shape.

  **The CLI now follows the same model.** `notion/log.py` kept a parallel `_log_and_reschedule_entry` for another release — same `$EDITOR`-then-reschedule shape, same silent inferred date — reachable only from `gtd log`, its `Log & Reschedule` menu entry, and the same-named action inside `gtd today`. None of the three existed in the TUI or the webapp, so the log-a-note-first combo was retired outright: `gtd log`, `log_and_reschedule`, `_log_and_reschedule_entry` and the dead `_infer_cadence` are gone. Rescheduling itself survives as `reschedule_only(entry)` — infer from the cadence prefix, confirm `(Y/n)`, fall through to the manual prompt when declined — and `gtd today`'s action is now plain **Reschedule**. `tests/test_cli_reschedule.py` pins both halves: that the old names stay gone, and that `reschedule_only` never reaches for `$EDITOR`, `subprocess` or `replace_page_body`.
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

### Webapp (src/gtd/webapp/)

`api.py` also serves a static PWA frontend from `src/gtd/webapp/` — `index.html`, `app.js`,
`styles.css`, `manifest.json`, `icons/` — via `GET /`, `/app.js`, `/styles.css`,
`/manifest.json`, `/icons/<filename>`. This is a real browser-facing UI, not just a JSON API;
"deploying the API" means deploying this too, since they're the same Flask process.

**The webapp and the TUI must stay feature-equivalent.** They're two front ends over the
same Notion data; the UIs differ (thumbs vs. keys) but the *capability set* must not. A
feature added to either one belongs in the other **in the same change**.

`tests/test_webapp_parity.py` enforces this, and is deliberately asymmetric because a
symmetric check is worthless:

- The **TUI side is derived** — it walks the real `BINDINGS` on `GTDApp`, the content
  widgets **and the four Weekly Review screens**, so a new binding is noticed with zero
  bookkeeping.
- The **webapp side is declared** — the `CAPABILITIES` array at the top of `app.js`.

So the failure that actually happens (add a TUI key, forget the webapp) fails CI. Two
hand-maintained lists compared to each other would drift together and always pass — don't
"simplify" it into that.

When you add a TUI binding, either implement it in the webapp and add its action name to
`CAPABILITIES`, or add it to `TUI_ONLY` in the test **with a reason**. `TUI_ONLY` is for
things that genuinely can't cross (keyboard navigation, `quit`, modal-internal
`save`/`cancel`) — there is no longer any deferred scope in it.

**A `TUI_ONLY` reason is load-bearing, so keep it true.** `complete_step` sat there
labelled `'Weekly Review not yet ported'` and it is not a review action at all — it is
`BaseEntryContent`'s `X`, which advances a numbered next-step list via
`models.advance_steps`. The wrong reason hid a real per-entry gap behind an unrelated
deferral for as long as the deferral lasted. It is now `POST /entry/<id>/complete-step`
(the endpoint calls `advance_steps`, so the renumbering is not reimplemented in JS) plus
a *Complete current step* row in the action sheet.

**Weekly Review in the webapp** — a `review` view (`kind: 'review'`, `loadReview`) that is
a checklist over `GET /review`, with the per-step work reusing the views and the action
sheet the tabs already have. Four things about it are deliberate:

- **The step list is never written in `app.js`.** It comes from `storage.REVIEW_STEPS`
  through the endpoint, so the phone and the terminal cannot disagree about what the
  review is. `tests/test_webapp_review.py` asserts no step label appears in the JS.
- **The `/review` endpoints are local-state only and never touch Notion** — they read and
  write the same `~/.local/share/gtd/weekly_habits.json` the TUI uses, so a tick made on
  the phone shows up in the terminal, and a Notion outage can't take the checklist down.
- **Changes apply immediately, not batched.** The TUI collects `_to_someday` /
  `_status_changes` / `_to_drop` and applies them when the modal is dismissed; that works
  because a modal *has* a dismissal. A webapp screen doesn't, and a backgrounded phone
  would lose the batch.
- **Drill-downs open the full entry action sheet** rather than mirroring each browse
  screen's restricted key set. It's a superset of those keys, so `someday`, `drop` and
  `change_status` are answered with no new per-step UI.

Leaving a step and finishing a step stay distinct, the same split the TUI's two-line
browse footer makes: the `‹` header returns without ticking, `✓ Done reviewing X` ticks.
Ticking the last step fires `POST /review/complete`, which writes the same habit key the
TUI's `W` does. The Next Steps view carries the matching always-present habit row
(red `●` + "not done this week" / green `●` + last-done), and it's chrome — if `/review`
fails the row is skipped rather than blanking the actionable list.

**Areas of Focus in the webapp** — the Someday view has its own loader (`loadSomeday`,
`kind: 'someday'`) rather than the generic `/entries` one, because it has to group by
Area the way the TUI's left pane does. Area chips filter the list; with *All* selected
the list renders Area section headers (every known Area, empty ones included, then a
trailing `(no area)` bucket) carrying ✎/✕ for rename/remove, plus a `+ New area` row.
Assigning an Area is a row in the entry action sheet. All four map to the TUI's `(`,
`+`, `-`, `)` and are backed by `/areas` (see the README's endpoint table) — except
assignment, which is just `PATCH /entry/<id> {"area": ...}`.

`PATCH /areas/<name>` renames the Notion select option **and** rewrites every entry
still carrying the old value, mirroring `action_rename_area`; renaming the option alone
leaves entries pointing at a value that no longer exists.

**List categories in the webapp** — the Lists view shows one category at a time (chips
pick it) and renders a `renderListSection` header for it carrying ✎/✕ (rename/remove),
an `+ Add to <category>` row and a trailing `+ New category` row — the TUI's `)`, `-`,
`A` and `+`. Backed by `POST/DELETE/PATCH /list-categories` and `POST /list/<category>`
(which creates the page and stamps Status=List + the category on it). `PATCH
/list-categories/<name>` rewrites every entry on the old value, exactly as `PATCH
/areas/<name>` does.

Webapp structure: `VIEWS` in `app.js` maps one entry per TUI tab; a single generic list
view renders them all, backed by `GET /entries?status=…`. Per-entry actions live in an
action sheet (`openActionSheet`) rather than a keymap. Navigation is a full-screen
hamburger menu — eight tabs can't hold a 44px touch target in a bottom bar.

**Packaging gotcha**: `webapp/`'s files are non-`.py` static assets, so `[tool.setuptools.packages.find]`
in `pyproject.toml` alone does *not* bundle them into the built wheel — that only governs which
Python packages are included. They must also be listed under `[tool.setuptools.package-data]`
(`gtd = ["webapp/*", "webapp/icons/*"]`). If a new file type/subdirectory is added under
`webapp/`, extend that glob too, or `uv tool install "gtd-tui[api]"` from PyPI will install a
`gtd api` that 404s on the webapp routes despite the code being correct — the routes exist,
the files they serve don't. This bit us once (webapp/ was committed but never actually shipped
in any published wheel until this was added) — verify with a local `uv build --wheel` and
inspect the resulting `.whl` for `gtd/webapp/*` entries before trusting a release ships it.

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
