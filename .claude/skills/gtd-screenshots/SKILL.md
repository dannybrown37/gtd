---
name: gtd-screenshots
description: Invoke when GTD's README or docs screenshots need regenerating or fixing — "update the screenshots", "the README shows the old TUI", "add the Calendar tab to the docs images", "regenerate docs/screenshots". Drives the real TUI headlessly against a demo Notion database and writes docs/screenshots/*.svg.
---

# GTD docs screenshots

`README.md` embeds one SVG per TUI tab from `docs/screenshots/`. They are generated, never
hand-made, and they run against a **demo Notion database of fictional data** — never the
user's real GTD data.

Two scripts, run in order. The first prints an ID the second one consumes.

## 1. Seed the demo database

```bash
NOTION_NOTES_TOKEN=... NOTION_PROJECTS_DB_ID=<real db id> \
    uv run python scripts/seed_demo_data.py
```

Creates or reseeds **"GTD Projects (Demo)"** as a sibling of the real Projects database, under
the same parent page. It reads the real database only to find that parent page — it never
writes to it, and it never touches `~/.config/gtd/config.json`.

The demo database's ID is cached in `.gtd_demo_db.json` (gitignored), so reruns reseed the same
database instead of littering the workspace with new ones. The script prints the ID.

Skip this step if the demo database is already seeded and the schema has not changed. It is the
slow, network-heavy half; capture is the half you actually iterate on.

## 2. Capture

```bash
NOTION_NOTES_TOKEN=... NOTION_PROJECTS_DB_ID=<demo db id> \
    uv run python scripts/capture_screenshots.py
```

Note the variable is the **demo** ID this time. That substitution is the entire trick — the app
has no idea it is being screenshotted.

It runs the real `GTDApp` through Textual's `Pilot` harness at a pinned `130x45`, walks the
tabs, and writes `docs/screenshots/<name>.svg` via `App.export_screenshot()`. No extra tooling;
`export_screenshot` ships in Textual core.

Do not change the `(130, 45)` size casually. Every image churns in the diff when it moves, and
review becomes worthless.

## Tabs discover themselves

The tab list is **not** hardcoded — `discover_tabs()` reads the live `TabPane` ids out of the
mounted app, so a new tab screenshots itself with no edit here. It used to be a constant, and
it silently missed the Calendar tab for a whole release.

Two things still need a human:

- **`SLUG_OVERRIDES`** maps a tab id to its filename, and exists only for the two ids whose
  README paths predate this scheme (`tab-waiting` → `waiting-for`, `tab-snoozed` →
  `incubation`). Do not add to it for a new tab; let the id decide the name.
- **An `<img>` in `README.md`**, and **demo data in `seed_demo_data.py`** that makes the new tab
  non-empty. A tab with no demo data screenshots as a blank box and teaches the reader nothing.

`tests/test_capture_screenshots.py` guards all of this: every tab is discovered, slugs are
unique, no override is stale, and README image paths still resolve.

## Calendar shows real meetings

`PRIVATE_TABS` holds `tab-calendar`, and it is **skipped by default**. The tab is fed by
`gcal.py`/`ics.py`, not Notion, so `seed_demo_data.py` cannot fake it — capturing it renders
whatever is actually in the user's Google Calendar and Outlook feed.

`--with-private` opts in. Only pass it with the user watching, and read the resulting SVG for
names, clients, and meeting titles before it goes anywhere near a commit.

## Secrets

`NOTION_NOTES_TOKEN` and `GTD_ICS_URL` are bearer secrets. Take them from the environment; never
write them into a script, a test, or a commit.

## Finish

- Actually look at the output — a run that "succeeded" can still be eight empty tabs.
- `README.md` line ~55 states the data is fictional. Keep that true.
- Do not commit. The user commits.

The general pattern behind this, for other repos, is `skill-tree:tui-screenshots`.
