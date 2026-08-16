const KEY_STORAGE = 'gtd_api_key';

// Capabilities this webapp implements, keyed to the TUI action names they
// mirror. tests/test_webapp_parity.py reads this array and fails when the TUI
// grows an action with no counterpart here — the two UIs are meant to stay
// feature-equivalent, not merely similar. Add to this list only when the
// behaviour actually exists below.
const CAPABILITIES = [
  'update_entry',
  'edit_notes',
  'edit_steps',
  'wait_tomorrow',
  'mark_done',
  'drop_entry',
  'triage_entry',
  'triage_all',
  'complete_step',
  'filter_context',
  'filter_list',
  'move_someday',
  'move_to_list',
  'activate',
  'capture',
  'refresh',
  'set_area',
  'add_area',
  'remove_area',
  'rename_area',
  'add_item',
  'update_item',
  'move_item',
  'add_category',
  'remove_category',
  'rename_category',
  // Weekly Review. `toggle` ticks or launches a step, `finish_step` is the
  // "Done reviewing X" control each drill-down carries, `reset` un-ticks the
  // week, and `complete_habit` is the Weekly Review row on Next Steps. The
  // drill-downs reuse the entry action sheet, which is where `someday`,
  // `drop` and `change_status` (Update a field → Status) are answered.
  'toggle_step',
  'finish_step',
  'reset',
  'complete_habit',
  'someday',
  'drop',
  'change_status',
];

// Chip value standing in for "entries with no Area at all"; the empty string
// is already taken by the All chip. Area names are trimmed server-side, so a
// real one can never start with a space.
const NO_AREA = ' (no area)';

// Same trick for the context chips on the status-backed views. An entry with
// no context is common on Projects (it is exactly what triage has not reached
// yet), so the bucket is worth being able to select.
const NO_CONTEXT = ' (no context)';

// Each view is a tab in the TUI. `status`/`followUp` drive the generic
// /entries endpoint; `kind` selects the loader for the ones that differ.
const VIEWS = {
  'next-steps':  { label: 'Next Steps',    kind: 'next-steps' },
  'inbox':       { label: 'Inbox',         kind: 'inbox' },
  'capture':     { label: 'Capture',       kind: 'capture' },
  'projects':    { label: 'Projects',      kind: 'entries', status: 'Current Project' },
  'waiting-for': { label: 'Waiting For',   kind: 'entries', status: 'Waiting For' },
  'incubation':  { label: 'Incubation',    kind: 'entries', status: 'Current Project', followUp: 'future' },
  'recurring':   { label: 'Recurring',     kind: 'entries', status: 'Recurring' },
  'someday':     { label: 'Someday/Maybe', kind: 'someday',  status: 'Someday/Maybe' },
  'lists':       { label: 'Lists',         kind: 'lists' },
  'review':      { label: 'Weekly Review', kind: 'review' },
};

const UPDATE_FIELDS = [
  ['header', 'Title'],
  ['status', 'Status'],
  ['context', 'Context'],
  ['area', 'Area'],
  ['list_category', 'List Category'],
  ['next_step', 'Next Step'],
  ['success_condition', 'Success Condition'],
  ['due_date', 'Due Date'],
  ['follow_up_date', 'Follow-up Date'],
];

const DATE_FIELDS = new Set(['due_date', 'follow_up_date']);

const state = {
  apiKey: localStorage.getItem(KEY_STORAGE) || '',
  activeView: 'next-steps',
  currentContext: '',
  currentCategory: '',
  currentArea: '',
  areas: [],
  categories: [],
  schema: null,
  entries: [],
  // The review checklist as `GET /review` last returned it, and which step
  // is drilled into (null = the checklist itself).
  review: null,
  reviewStep: null,
};

const $ = (sel) => document.querySelector(sel);
const modalBackdrop = $('#modal-backdrop');
const modal = $('#modal');
const navBackdrop = $('#nav-backdrop');
const navSheet = $('#nav-sheet');
const navFab = $('#nav-fab');

function showToast(message, isError) {
  const el = document.createElement('div');
  el.textContent = message;
  el.className = 'toast' + (isError ? ' toast-error' : '');
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

function openModal(html) {
  modal.innerHTML = html;
  modalBackdrop.classList.remove('hidden');
}

function closeModal() {
  modalBackdrop.classList.add('hidden');
  modal.innerHTML = '';
}

modalBackdrop.addEventListener('click', (e) => {
  if (e.target === modalBackdrop && state.apiKey) closeModal();
});

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatDate(iso) {
  if (!iso) return '';
  const [, m, d] = iso.split('-');
  return `${m}/${d}`;
}

// toISOString() is UTC, so it rolls over to tomorrow every evening west of
// Greenwich -- an item due today read as overdue after ~8pm Eastern. Format
// off the local date parts instead, so the browser agrees with the machine
// running the API.
function localISO(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function todayISO() {
  return localISO(new Date());
}

// Locked to the *following* Monday: on a Monday this is a week out, not today.
// A snooze that resolves to today is a no-op that leaves the item on Next Steps.
function nextMondayISO(from = new Date()) {
  const d = new Date(from);
  d.setDate(d.getDate() + ((8 - d.getDay()) % 7 || 7));
  return localISO(d);
}

function addDaysISO(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return localISO(d);
}

async function apiFetch(path, options = {}) {
  const headers = Object.assign({}, options.headers, {
    Authorization: `Bearer ${state.apiKey}`,
  });
  if (options.body) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    showToast('API key rejected — re-enter it', true);
    openSettingsModal();
    throw new Error('unauthorized');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function reportError(err) {
  if (err.message !== 'unauthorized') showToast(err.message, true);
}

// region Settings / API key

function openSettingsModal() {
  const canCancel = !!state.apiKey;
  openModal(`
    <h2>GTD API Key</h2>
    <form id="settings-form" action="#" method="post">
      <label for="api-key-input">Bearer token from your GTD_API_KEY server env var</label>
      <!-- Password managers only offer to fill/save a credential that has a username
           field alongside the password one, so give them a fixed identity to key off. -->
      <input type="text" id="api-key-user" name="username" value="gtd" readonly
             autocomplete="username" class="visually-hidden" tabindex="-1" aria-hidden="true" />
      <input id="api-key-input" name="password" type="password"
             autocomplete="current-password" placeholder="Enter API key" />
      <div class="modal-actions">
        ${canCancel ? '<button type="button" class="secondary-btn" id="settings-cancel">Cancel</button>' : ''}
        <button type="submit" class="primary-btn" id="settings-save">Save</button>
      </div>
    </form>
  `);
  const input = $('#api-key-input');
  input.focus();
  if (canCancel) $('#settings-cancel').addEventListener('click', closeModal);
  $('#settings-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const value = input.value.trim();
    if (!value) {
      showToast('Enter a key first', true);
      return;
    }
    const previous = state.apiKey;
    state.apiKey = value;
    try {
      await apiFetch('/contexts');
    } catch (err) {
      state.apiKey = previous;
      showToast('Could not verify that key', true);
      return;
    }
    localStorage.setItem(KEY_STORAGE, value);
    closeModal();
    loadActiveView();
  });
}

$('#settings-btn').addEventListener('click', openSettingsModal);

// endregion

// region Navigation

// Fetched once and cached; the version only changes when the server restarts.
let cachedVersion = '';

async function showNavVersion() {
  const slot = $('#nav-version');
  if (!cachedVersion) {
    try {
      cachedVersion = (await apiFetch('/version')).version || '';
    } catch {
      return;
    }
  }
  slot.textContent = cachedVersion ? `v${cachedVersion}` : '';
}

// Rendered bottom-up: the sheet rises from the thumb, so the views reached most
// often (Next Steps, Inbox — the head of VIEWS) must land nearest the bottom.
// Do not "tidy" this back to insertion order.
function buildNavMenu() {
  $('#nav-items').innerHTML = Object.entries(VIEWS)
    .reverse()
    .map(([id, v]) => {
      const active = id === state.activeView;
      return (
        `<button class="nav-item${active ? ' active' : ''}" data-view="${id}"` +
        `${active ? ' aria-current="page"' : ''}>` +
        `<span>${escapeHtml(v.label)}</span>` +
        `<span aria-hidden="true">${active ? '✓' : ''}</span></button>`
      );
    })
    .join('');
  navSheet.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      closeNav();
      switchView(btn.dataset.view);
    });
  });
}

function openNav() {
  buildNavMenu();
  navBackdrop.classList.remove('hidden');
  navFab.setAttribute('aria-expanded', 'true');
  showNavVersion();
  (navSheet.querySelector('.nav-item.active') || navSheet.querySelector('.nav-item'))?.focus();
}

function closeNav() {
  if (navBackdrop.classList.contains('hidden')) return;
  navBackdrop.classList.add('hidden');
  navFab.setAttribute('aria-expanded', 'false');
  navFab.focus();
}

navFab.addEventListener('click', () => {
  if (navBackdrop.classList.contains('hidden')) openNav();
  else closeNav();
});

// Anything that isn't a view button dismisses — the scrim, the sheet's own
// padding, the version line. Only .nav-item is interactive in here, so a
// target test beats an `=== navBackdrop` test: the sheet's dead space is
// visually "outside the menu" and a thumb lands there constantly.
navBackdrop.addEventListener('click', (e) => {
  if (!e.target.closest('.nav-item')) closeNav();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeNav();
});

function switchView(view) {
  state.activeView = view;
  state.currentContext = '';
  state.currentCategory = '';
  state.currentArea = '';
  state.reviewStep = null;
  $('#view-title').textContent = VIEWS[view].label;
  // The title doubles as a breadcrumb inside the Weekly Review; the FAB always
  // names the view, so it is deliberately not updated by the review drill-down.
  $('#nav-fab-label').textContent = VIEWS[view].label;
  const isCapture = VIEWS[view].kind === 'capture';
  $('#view-capture').classList.toggle('hidden', !isCapture);
  $('#view-list').classList.toggle('hidden', isCapture);
  loadActiveView();
}

// Scrolling down is reading, so the FAB gets out of the way; scrolling up or
// pausing brings it back. Skipped entirely for anyone who opted out of motion.
if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const views = $('#views');
  let lastScroll = 0;
  let idleTimer = null;
  views.addEventListener(
    'scroll',
    () => {
      const y = views.scrollTop;
      navFab.classList.toggle('tucked', y > lastScroll && y > 24);
      lastScroll = y;
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => navFab.classList.remove('tucked'), 500);
    },
    { passive: true },
  );
}

// endregion

// region List rendering

function setEmpty(message) {
  const empty = $('#list-empty');
  empty.textContent = message;
  empty.classList.toggle('hidden', !message);
}

function entryRow(entry, onTap) {
  const li = document.createElement('li');
  li.className = 'entry';
  li.dataset.pageId = entry.page_id;
  const overdue = entry.due_date && entry.due_date < todayISO();
  // Each bit is HTML, not text -- escape as it goes in.
  const bits = [
    entry.context && escapeHtml(entry.context),
    entry.area && escapeHtml(entry.area),
    // An overdue date is the one piece of meta that has to stand out: it
    // surfaces through a snooze, so it must not read like any other date.
    entry.due_date &&
      `<span${overdue ? ' class="overdue"' : ''}>due ${escapeHtml(formatDate(entry.due_date))}</span>`,
    entry.follow_up_date && `→ ${escapeHtml(formatDate(entry.follow_up_date))}`,
  ].filter(Boolean);
  li.innerHTML = `
    <div class="entry-main">
      <div class="entry-header">${escapeHtml(entry.header)}</div>
      ${entry.next_step ? `<div class="entry-sub">${escapeHtml(entry.next_step)}</div>` : ''}
      ${bits.length ? `<div class="entry-meta">${bits.join(' · ')}</div>` : ''}
    </div>
    <span class="chevron" aria-hidden="true">›</span>
  `;
  li.addEventListener('click', () => onTap(entry, li));
  return li;
}

function renderEntries(entries, { onTap }) {
  const list = $('#entry-list');
  list.innerHTML = '';
  entries.forEach((entry) => list.appendChild(entryRow(entry, onTap)));
}

// The TUI's Someday pane lists every known Area as a section header — empty
// ones included, so an Area you just made is visibly there — with the
// unassigned entries in a trailing bucket. Section headers carry the rename
// and remove affordances the TUI binds to `)` and `-`.
function renderAreaSections(entries, areas, { onTap }) {
  const list = $('#entry-list');
  list.innerHTML = '';

  const section = (label, count, area) => {
    const li = document.createElement('li');
    li.className = 'group-header';
    li.innerHTML = `
      <span class="group-label">${escapeHtml(label)}</span>
      <span class="group-count">${count ? count : '(empty)'}</span>
      ${
        area
          ? `<button class="group-btn" data-act="rename" aria-label="Rename ${escapeHtml(area)}">✎</button>
             <button class="group-btn" data-act="remove" aria-label="Remove ${escapeHtml(area)}">✕</button>`
          : ''
      }
    `;
    if (area) {
      li.querySelector('[data-act="rename"]').addEventListener('click', () =>
        openRenameAreaModal(area)
      );
      li.querySelector('[data-act="remove"]').addEventListener('click', () =>
        confirmRemoveArea(area, count)
      );
    }
    list.appendChild(li);
  };

  areas.forEach((area) => {
    const items = entries.filter((e) => e.area === area);
    section(area, items.length, area);
    items.forEach((entry) => list.appendChild(entryRow(entry, onTap)));
  });

  const unassigned = entries.filter((e) => !e.area);
  if (unassigned.length) {
    section('(no area)', unassigned.length, null);
    unassigned.forEach((entry) => list.appendChild(entryRow(entry, onTap)));
  }

  const add = document.createElement('li');
  add.className = 'group-add';
  add.innerHTML = '<button class="group-add-btn">+ New area</button>';
  add.querySelector('button').addEventListener('click', openNewAreaModal);
  list.appendChild(add);
}

// The Lists tab shows one category at a time (chips pick it), so its header
// row carries that category's rename/remove affordances — the TUI's `)`/`-` —
// plus the add-item row (`A`) and a trailing new-category row (`+`).
function renderListSection(category, entries, { onTap }) {
  const list = $('#entry-list');
  list.innerHTML = '';

  const header = document.createElement('li');
  header.className = 'group-header';
  header.innerHTML = `
    <span class="group-label">${escapeHtml(category)}</span>
    <span class="group-count">${entries.length ? entries.length : '(empty)'}</span>
    <button class="group-btn" data-act="rename" aria-label="Rename ${escapeHtml(category)}">✎</button>
    <button class="group-btn" data-act="remove" aria-label="Remove ${escapeHtml(category)}">✕</button>
  `;
  header.querySelector('[data-act="rename"]').addEventListener('click', () =>
    openRenameCategoryModal(category)
  );
  header.querySelector('[data-act="remove"]').addEventListener('click', () =>
    confirmRemoveCategory(category, entries.length)
  );
  list.appendChild(header);

  entries.forEach((entry) => list.appendChild(entryRow(entry, onTap)));

  const addItem = document.createElement('li');
  addItem.className = 'group-add';
  addItem.innerHTML = `<button class="group-add-btn">+ Add to ${escapeHtml(category)}</button>`;
  addItem.querySelector('button').addEventListener('click', () =>
    openAddListItemModal(category)
  );
  list.appendChild(addItem);

  const addCategory = document.createElement('li');
  addCategory.className = 'group-add';
  addCategory.innerHTML = '<button class="group-add-btn">+ New category</button>';
  addCategory.querySelector('button').addEventListener('click',
    openNewCategoryModal
  );
  list.appendChild(addCategory);
}

// With no categories at all there is nothing to render a section for, but the
// user still needs a way out of that state.
function renderNewCategoryOnly() {
  const list = $('#entry-list');
  list.innerHTML = '';
  const li = document.createElement('li');
  li.className = 'group-add';
  li.innerHTML = '<button class="group-add-btn">+ New category</button>';
  li.querySelector('button').addEventListener('click', openNewCategoryModal);
  list.appendChild(li);
}

function removeEntryRow(pageId) {
  const li = $(`#entry-list [data-page-id="${pageId}"]`);
  if (li) li.remove();
  state.entries = state.entries.filter((e) => e.page_id !== pageId);
  if (!$('#entry-list').children.length) setEmpty('Nothing here 🎉');
}

function renderChips(values, current, onPick) {
  const container = $('#chips');
  container.classList.remove('hidden');
  container.innerHTML = values
    .map((v) => {
      const active = v.value === current ? ' active' : '';
      return `<button class="chip${active}" data-value="${escapeHtml(v.value)}">${escapeHtml(v.label)}</button>`;
    })
    .join('');
  container.querySelectorAll('.chip').forEach((chip) => {
    chip.addEventListener('click', () => onPick(chip.dataset.value));
  });
}

// endregion

// region Loaders

function loadActiveView() {
  if (!state.apiKey) return;
  const view = VIEWS[state.activeView];
  $('#chips').classList.add('hidden');
  setEmpty('');
  if (view.kind === 'capture') return;
  if (view.kind === 'next-steps') return loadNextSteps();
  if (view.kind === 'inbox') return loadInbox();
  if (view.kind === 'lists') return loadLists();
  if (view.kind === 'someday') return loadSomeday();
  if (view.kind === 'review') return loadReview();
  return loadEntries(view);
}

// The TUI can reload the current tab from anywhere; without this the only way
// to re-fetch was to navigate away and back.
async function refreshActiveView() {
  const btn = $('#refresh-btn');
  if (btn.disabled) return;
  btn.disabled = true;
  btn.classList.add('spinning');
  try {
    await loadActiveView();
  } finally {
    btn.classList.remove('spinning');
    btn.disabled = false;
  }
}

$('#refresh-btn').addEventListener('click', refreshActiveView);

async function loadNextSteps() {
  try {
    const { contexts } = await apiFetch('/contexts');
    renderChips(
      [{ value: '', label: 'All' }, ...contexts.map((c) => ({ value: c, label: c }))],
      state.currentContext,
      (value) => {
        state.currentContext = value;
        loadNextSteps();
      }
    );
  } catch (err) {
    // Chips are a nicety; the list fetch below surfaces real failures.
  }
  try {
    const path = state.currentContext
      ? `/next-steps?context=${encodeURIComponent(state.currentContext)}`
      : '/next-steps';
    state.entries = await apiFetch(path);
    renderEntries(state.entries, { onTap: openActionSheet });
    // The empty state describes the GTD entries only — the habit row below is
    // always present, so "nothing actionable" would otherwise be a lie.
    setEmpty(state.entries.length ? '' : 'Nothing actionable 🎉');
  } catch (err) {
    reportError(err);
  }
  try {
    prependReviewHabitRow(await apiFetch('/review'));
  } catch (err) {
    // The habit row is chrome; losing it must not blank the actionable list.
  }
}

// The TUI's `F` offers only the contexts present in the tab it was pressed on,
// not every context in Notion — a chip that filters to nothing is noise.
function contextsInView(entries) {
  return [...new Set(entries.filter((e) => e.context).map((e) => e.context))].sort();
}

// `state.entries` deliberately stays the *unfiltered* fetch: `removeEntryRow`
// prunes it as actions complete, and a narrowed copy would silently drop the
// rest of the list the moment the user cleared the chip.
function renderEntriesForContext() {
  const shown = state.currentContext
    ? state.entries.filter((e) =>
        state.currentContext === NO_CONTEXT
          ? !e.context
          : e.context === state.currentContext
      )
    : state.entries;
  renderEntries(shown, { onTap: openActionSheet });
  setEmpty(shown.length ? '' : 'Nothing here 🎉');
}

async function loadEntries(view) {
  const params = new URLSearchParams({ status: view.status });
  if (view.followUp) params.set('follow_up', view.followUp);
  try {
    state.entries = await apiFetch(`/entries?${params}`);
  } catch (err) {
    reportError(err);
    return;
  }

  const hasUncontexted = state.entries.some((e) => !e.context);
  const available = [
    ...contextsInView(state.entries),
    ...(hasUncontexted ? [NO_CONTEXT] : []),
  ];
  // A refresh can empty out the context that was filtered on; leaving it set
  // would show an empty list with no chip highlighted to explain why.
  if (state.currentContext && !available.includes(state.currentContext)) {
    state.currentContext = '';
  }
  renderChips(
    [
      { value: '', label: 'All' },
      ...available.map((c) => ({
        value: c,
        label: c === NO_CONTEXT ? '(no context)' : c,
      })),
    ],
    state.currentContext,
    (value) => {
      state.currentContext = value;
      renderEntriesForContext();
    }
  );
  renderEntriesForContext();
}

async function loadInbox() {
  try {
    state.entries = await apiFetch('/inbox');
    renderEntries(state.entries, { onTap: openTriageModal });
    setEmpty(state.entries.length ? '' : 'Inbox is empty 🎉');
  } catch (err) {
    reportError(err);
  }
}

async function loadSomeday() {
  try {
    ({ areas: state.areas } = await apiFetch('/areas'));
  } catch (err) {
    // Areas are the grouping, not the data; a failure degrades to a flat list.
    state.areas = [];
  }
  try {
    state.entries = await apiFetch(
      `/entries?status=${encodeURIComponent('Someday/Maybe')}`
    );
  } catch (err) {
    reportError(err);
    return;
  }

  const hasUnassigned = state.entries.some((e) => !e.area);
  renderChips(
    [
      { value: '', label: 'All' },
      ...state.areas.map((a) => ({ value: a, label: a })),
      ...(hasUnassigned ? [{ value: NO_AREA, label: '(no area)' }] : []),
    ],
    state.currentArea,
    (value) => {
      state.currentArea = value;
      loadSomeday();
    }
  );

  if (state.currentArea) {
    const shown = state.entries.filter((e) =>
      state.currentArea === NO_AREA ? !e.area : e.area === state.currentArea
    );
    renderEntries(shown, { onTap: openActionSheet });
    setEmpty(shown.length ? '' : 'Nothing in this area');
    return;
  }
  renderAreaSections(state.entries, state.areas, { onTap: openActionSheet });
  setEmpty(
    state.entries.length || state.areas.length ? '' : 'No someday items'
  );
}

async function loadLists() {
  let categories;
  try {
    ({ list_categories: categories } = await apiFetch('/list-categories'));
  } catch (err) {
    reportError(err);
    return;
  }
  state.categories = categories;
  if (!categories.length) {
    setEmpty('No list categories defined');
    renderNewCategoryOnly();
    return;
  }
  if (!categories.includes(state.currentCategory)) {
    state.currentCategory = categories[0];
  }
  renderChips(
    categories.map((c) => ({ value: c, label: c })),
    state.currentCategory,
    (value) => {
      state.currentCategory = value;
      loadLists();
    }
  );
  try {
    const path = `/list/${encodeURIComponent(state.currentCategory)}`;
    state.entries = await apiFetch(path);
    renderListSection(state.currentCategory, state.entries, {
      onTap: openActionSheet,
    });
    setEmpty('');
  } catch (err) {
    reportError(err);
  }
}

// endregion

// region Action sheet

function openActionSheet(entry) {
  const isRecurring = entry.status === 'Recurring';
  const isSomeday = entry.status === 'Someday/Maybe';
  const isList = state.activeView === 'lists';
  openModal(`
    <h2>${escapeHtml(entry.header)}</h2>
    ${entry.next_step ? `<p class="entry-meta">${escapeHtml(entry.next_step)}</p>` : ''}
    <div class="action-stack">
      <button class="action-btn" data-act="update">Update a field</button>
      ${entry.next_step ? '<button class="action-btn" data-act="complete-step">Complete current step</button>' : ''}
      <button class="action-btn" data-act="steps">Edit next step</button>
      <button class="action-btn" data-act="notes">Notes</button>
      <button class="action-btn" data-act="snooze">Snooze</button>
      ${isSomeday ? '<button class="action-btn" data-act="area">Assign Area</button>' : ''}
      ${isSomeday ? '<button class="action-btn" data-act="activate">Activate</button>' : ''}
      ${!isSomeday && !isList ? '<button class="action-btn" data-act="someday">Move to Someday</button>' : ''}
      <button class="action-btn" data-act="list">Move to a List</button>
      <button class="action-btn primary-action" data-act="done">${isRecurring ? 'Done (reschedule)' : 'Done'}</button>
      <button class="action-btn danger-action" data-act="drop">Drop</button>
    </div>
    <div class="modal-actions">
      <button class="secondary-btn" id="sheet-cancel">Cancel</button>
    </div>
  `);
  $('#sheet-cancel').addEventListener('click', closeModal);
  modal.querySelectorAll('.action-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const act = btn.dataset.act;
      if (act === 'update') openUpdateFieldPicker(entry);
      if (act === 'complete-step') completeCurrentStep(entry);
      if (act === 'steps') openStepsModal(entry);
      if (act === 'notes') openNotesModal(entry);
      if (act === 'snooze') openSnoozeModal(entry);
      if (act === 'area') openAreaPicker(entry);
      if (act === 'activate') setStatus(entry, 'Current Project');
      if (act === 'someday') setStatus(entry, 'Someday/Maybe');
      if (act === 'list') openMoveToListModal(entry);
      if (act === 'done') isRecurring ? openRescheduleModal(entry) : markDone(entry);
      if (act === 'drop') confirmDrop(entry);
    });
  });
}

// The renumbering lives server-side in `notion/models.advance_steps`, the
// same function the TUI's `X` calls — a second definition of "what a step
// list is" written in JS is exactly the drift this repo keeps paying for.
async function completeCurrentStep(entry) {
  try {
    const updated = await apiFetch(`/entry/${entry.page_id}/complete-step`, {
      method: 'POST',
    });
    closeModal();
    const left = (updated.next_step || '').trim();
    showToast(left ? 'Step done ✓' : 'All steps complete ✓');
    loadActiveView();
  } catch (err) {
    reportError(err);
  }
}

async function patchEntry(entry, body, successMessage) {
  try {
    await apiFetch(`/entry/${entry.page_id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
    closeModal();
    showToast(successMessage);
    loadActiveView();
  } catch (err) {
    reportError(err);
  }
}

function setStatus(entry, status) {
  patchEntry(entry, { status }, `Moved to ${status}`);
}

async function ensureSchema() {
  if (!state.schema) state.schema = await apiFetch('/triage-schema');
  return state.schema;
}

function openUpdateFieldPicker(entry) {
  openModal(`
    <h2>Update</h2>
    <div class="action-stack">
      ${UPDATE_FIELDS.map(
        ([key, label]) =>
          `<button class="action-btn" data-field="${key}">${label}</button>`
      ).join('')}
    </div>
    <div class="modal-actions">
      <button class="secondary-btn" id="field-cancel">Cancel</button>
    </div>
  `);
  $('#field-cancel').addEventListener('click', () => openActionSheet(entry));
  modal.querySelectorAll('[data-field]').forEach((btn) => {
    btn.addEventListener('click', () => openFieldEditor(entry, btn.dataset.field));
  });
}

async function openFieldEditor(entry, field) {
  const label = UPDATE_FIELDS.find(([k]) => k === field)[1];
  const current = entry[field] || '';

  // Select-backed fields get option buttons rather than free text, so the
  // webapp can't invent values the Notion schema doesn't have.
  let options = null;
  if (field === 'status' || field === 'context' || field === 'list_category') {
    try {
      const schema = await ensureSchema();
      if (field === 'status') options = schema.statuses;
      if (field === 'context') options = schema.contexts_by_status['Current Project'];
      if (field === 'list_category') options = schema.list_categories;
    } catch (err) {
      reportError(err);
      return;
    }
  }

  const inputType = DATE_FIELDS.has(field) ? 'date' : 'text';
  openModal(`
    <h2>${label}</h2>
    ${
      options
        ? `<div class="option-grid" id="field-options">${options
            .map(
              (o) =>
                `<button type="button" class="option-btn${o === current ? ' active' : ''}" data-value="${escapeHtml(o)}">${escapeHtml(o)}</button>`
            )
            .join('')}</div>`
        : `<input id="field-input" type="${inputType}" value="${escapeHtml(current)}" />`
    }
    <div class="modal-actions">
      <button class="secondary-btn" id="field-back">Back</button>
      ${options ? '' : '<button class="primary-btn" id="field-save">Save</button>'}
    </div>
  `);
  $('#field-back').addEventListener('click', () => openUpdateFieldPicker(entry));
  if (options) {
    modal.querySelectorAll('.option-btn').forEach((btn) => {
      btn.addEventListener('click', () =>
        patchEntry(entry, { [field]: btn.dataset.value }, `${label} updated`)
      );
    });
  } else {
    $('#field-input').focus();
    $('#field-save').addEventListener('click', () =>
      patchEntry(entry, { [field]: $('#field-input').value }, `${label} updated`)
    );
  }
}

function openStepsModal(entry) {
  openModal(`
    <h2>Next step</h2>
    <textarea id="steps-input" placeholder="What's the next action?">${escapeHtml(entry.next_step || '')}</textarea>
    <div class="modal-actions">
      <button class="secondary-btn" id="steps-back">Back</button>
      <button class="primary-btn" id="steps-save">Save</button>
    </div>
  `);
  $('#steps-back').addEventListener('click', () => openActionSheet(entry));
  $('#steps-save').addEventListener('click', () =>
    patchEntry(entry, { next_step: $('#steps-input').value }, 'Step updated')
  );
}

async function openNotesModal(entry) {
  let notes = '';
  try {
    ({ notes } = await apiFetch(`/entry/${entry.page_id}/notes`));
  } catch (err) {
    reportError(err);
    return;
  }
  openModal(`
    <h2>Notes</h2>
    <textarea id="notes-input" class="notes-area">${escapeHtml(notes)}</textarea>
    <div class="modal-actions">
      <button class="secondary-btn" id="notes-back">Back</button>
      <button class="primary-btn" id="notes-save">Save</button>
    </div>
  `);
  $('#notes-back').addEventListener('click', () => openActionSheet(entry));
  $('#notes-save').addEventListener('click', async () => {
    try {
      await apiFetch(`/entry/${entry.page_id}/notes`, {
        method: 'PUT',
        body: JSON.stringify({ notes: $('#notes-input').value }),
      });
      closeModal();
      showToast('Notes saved');
    } catch (err) {
      reportError(err);
    }
  });
}

function openSnoozeModal(entry) {
  openModal(`
    <h2>Snooze</h2>
    <div class="action-stack">
      <button class="action-btn" data-days="1">Tomorrow</button>
      <button class="action-btn" data-days="3">In 3 days</button>
      <button class="action-btn" data-date="${nextMondayISO()}">Next Monday</button>
      <button class="action-btn" data-days="7">Next week</button>
    </div>
    <label for="snooze-date">Or pick a date</label>
    <input id="snooze-date" type="date" value="${todayISO()}" />
    <div class="modal-actions">
      <button class="secondary-btn" id="snooze-back">Back</button>
      <button class="primary-btn" id="snooze-save">Save</button>
    </div>
  `);
  $('#snooze-back').addEventListener('click', () => openActionSheet(entry));
  const send = async (body) => {
    try {
      await apiFetch(`/entry/${entry.page_id}/snooze`, {
        method: 'POST',
        body: JSON.stringify(body),
      });
      closeModal();
      showToast('Snoozed');
      loadActiveView();
    } catch (err) {
      reportError(err);
    }
  };
  modal.querySelectorAll('[data-days]').forEach((btn) => {
    btn.addEventListener('click', () => send({ days: Number(btn.dataset.days) }));
  });
  modal.querySelectorAll('.action-btn[data-date]').forEach((btn) => {
    btn.addEventListener('click', () => send({ date: btn.dataset.date }));
  });
  $('#snooze-save').addEventListener('click', () => send({ date: $('#snooze-date').value }));
}

async function openMoveToListModal(entry) {
  let categories;
  try {
    ({ list_categories: categories } = await apiFetch('/list-categories'));
  } catch (err) {
    reportError(err);
    return;
  }
  openModal(`
    <h2>Move to a List</h2>
    <div class="option-grid">
      ${categories
        .map(
          (c) =>
            `<button type="button" class="option-btn" data-value="${escapeHtml(c)}">${escapeHtml(c)}</button>`
        )
        .join('')}
    </div>
    <div class="modal-actions">
      <button class="secondary-btn" id="movelist-back">Back</button>
    </div>
  `);
  $('#movelist-back').addEventListener('click', () => openActionSheet(entry));
  modal.querySelectorAll('.option-btn').forEach((btn) => {
    btn.addEventListener('click', () =>
      patchEntry(
        entry,
        { status: 'List', list_category: btn.dataset.value },
        'Moved to list'
      )
    );
  });
}

// region Areas of Focus

async function openAreaPicker(entry) {
  let areas;
  try {
    ({ areas } = await apiFetch('/areas'));
  } catch (err) {
    reportError(err);
    return;
  }
  openModal(`
    <h2>Assign Area</h2>
    <div class="option-grid">
      ${['(no area)', ...areas]
        .map(
          (a) =>
            `<button type="button" class="option-btn${a === (entry.area || '(no area)') ? ' active' : ''}" data-value="${escapeHtml(a)}">${escapeHtml(a)}</button>`
        )
        .join('')}
    </div>
    <div class="modal-actions">
      <button class="secondary-btn" id="area-back">Back</button>
    </div>
  `);
  $('#area-back').addEventListener('click', () => openActionSheet(entry));
  modal.querySelectorAll('.option-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const value = btn.dataset.value;
      patchEntry(
        entry,
        { area: value === '(no area)' ? '' : value },
        `Area set to ${value}`
      );
    });
  });
}

async function mutateAndReload(path, options, successMessage) {
  try {
    await apiFetch(path, options);
    closeModal();
    showToast(successMessage);
    loadActiveView();
  } catch (err) {
    reportError(err);
  }
}

function openNewAreaModal() {
  openModal(`
    <h2>New area</h2>
    <input id="area-name" type="text" placeholder="Area name" />
    <div class="modal-actions">
      <button class="secondary-btn" id="area-cancel">Cancel</button>
      <button class="primary-btn" id="area-save">Add</button>
    </div>
  `);
  $('#area-name').focus();
  $('#area-cancel').addEventListener('click', closeModal);
  $('#area-save').addEventListener('click', () => {
    const name = $('#area-name').value.trim();
    if (!name) return;
    mutateAndReload(
      '/areas',
      { method: 'POST', body: JSON.stringify({ name }) },
      `Added area "${name}"`
    );
  });
}

function openRenameAreaModal(area) {
  openModal(`
    <h2>Rename area</h2>
    <input id="area-name" type="text" value="${escapeHtml(area)}" />
    <div class="modal-actions">
      <button class="secondary-btn" id="area-cancel">Cancel</button>
      <button class="primary-btn" id="area-save">Rename</button>
    </div>
  `);
  $('#area-name').focus();
  $('#area-cancel').addEventListener('click', closeModal);
  $('#area-save').addEventListener('click', () => {
    const newName = $('#area-name').value.trim();
    if (!newName || newName === area) return closeModal();
    mutateAndReload(
      `/areas/${encodeURIComponent(area)}`,
      { method: 'PATCH', body: JSON.stringify({ new_name: newName }) },
      `Renamed to "${newName}"`
    );
  });
}

// An Area is never removed out from under its entries — the server refuses
// with a 409 too, this just says so without a round trip.
function confirmRemoveArea(area, count) {
  if (count) {
    showToast(
      `"${area}" still has ${count} item(s) — move or drop them first`,
      true
    );
    return;
  }
  openModal(`
    <h2>Remove area?</h2>
    <p class="entry-meta">${escapeHtml(area)}</p>
    <p class="entry-meta">It has no items.</p>
    <div class="modal-actions">
      <button class="secondary-btn" id="area-cancel">Cancel</button>
      <button class="primary-btn danger-action" id="area-remove">Remove</button>
    </div>
  `);
  $('#area-cancel').addEventListener('click', closeModal);
  $('#area-remove').addEventListener('click', () =>
    mutateAndReload(
      `/areas/${encodeURIComponent(area)}`,
      { method: 'DELETE' },
      `Removed area "${area}"`
    )
  );
}

// endregion

// region List categories

function openNewCategoryModal() {
  openModal(`
    <h2>New list category</h2>
    <input id="cat-name" type="text" placeholder="Category name" />
    <div class="modal-actions">
      <button class="secondary-btn" id="cat-cancel">Cancel</button>
      <button class="primary-btn" id="cat-save">Add</button>
    </div>
  `);
  $('#cat-name').focus();
  $('#cat-cancel').addEventListener('click', closeModal);
  $('#cat-save').addEventListener('click', () => {
    const name = $('#cat-name').value.trim();
    if (!name) return;
    state.currentCategory = name;
    mutateAndReload(
      '/list-categories',
      { method: 'POST', body: JSON.stringify({ name }) },
      `Added category "${name}"`
    );
  });
}

function openRenameCategoryModal(category) {
  openModal(`
    <h2>Rename category</h2>
    <input id="cat-name" type="text" value="${escapeHtml(category)}" />
    <div class="modal-actions">
      <button class="secondary-btn" id="cat-cancel">Cancel</button>
      <button class="primary-btn" id="cat-save">Rename</button>
    </div>
  `);
  $('#cat-name').focus();
  $('#cat-cancel').addEventListener('click', closeModal);
  $('#cat-save').addEventListener('click', () => {
    const newName = $('#cat-name').value.trim();
    if (!newName || newName === category) return closeModal();
    state.currentCategory = newName;
    mutateAndReload(
      `/list-categories/${encodeURIComponent(category)}`,
      { method: 'PATCH', body: JSON.stringify({ new_name: newName }) },
      `Renamed to "${newName}"`
    );
  });
}

// Same rule as Areas: a category with items in it can't be removed, or the
// items are left pointing at a select option that no longer exists.
function confirmRemoveCategory(category, count) {
  if (count) {
    showToast(
      `"${category}" still has ${count} item(s) — move or drop them first`,
      true
    );
    return;
  }
  openModal(`
    <h2>Remove category?</h2>
    <p class="entry-meta">${escapeHtml(category)}</p>
    <p class="entry-meta">It has no items.</p>
    <div class="modal-actions">
      <button class="secondary-btn" id="cat-cancel">Cancel</button>
      <button class="primary-btn danger-action" id="cat-remove">Remove</button>
    </div>
  `);
  $('#cat-cancel').addEventListener('click', closeModal);
  $('#cat-remove').addEventListener('click', () => {
    state.currentCategory = '';
    mutateAndReload(
      `/list-categories/${encodeURIComponent(category)}`,
      { method: 'DELETE' },
      `Removed category "${category}"`
    );
  });
}

function openAddListItemModal(category) {
  openModal(`
    <h2>Add to ${escapeHtml(category)}</h2>
    <input id="item-name" type="text" placeholder="Item" />
    <input id="item-extra" type="text" placeholder="Extra info (optional)" />
    <div class="modal-actions">
      <button class="secondary-btn" id="item-cancel">Cancel</button>
      <button class="primary-btn" id="item-save">Add</button>
    </div>
  `);
  $('#item-name').focus();
  $('#item-cancel').addEventListener('click', closeModal);
  $('#item-save').addEventListener('click', () => {
    const header = $('#item-name').value.trim();
    if (!header) return;
    const nextStep = $('#item-extra').value.trim();
    mutateAndReload(
      `/list/${encodeURIComponent(category)}`,
      {
        method: 'POST',
        body: JSON.stringify({ header, next_step: nextStep }),
      },
      `Added "${header}"`
    );
  });
}

// endregion

function openRescheduleModal(entry) {
  openModal(`
    <h2>${escapeHtml(entry.header)}</h2>
    <p class="entry-meta">Recurring — reschedule it, or finish it for good.</p>
    <div class="action-stack">
      <button class="action-btn" data-days="1">Tomorrow</button>
      <button class="action-btn" data-days="7">Next week</button>
    </div>
    <label for="resched-date">Or pick a date</label>
    <input id="resched-date" type="date" value="${addDaysISO(1)}" />
    <div class="modal-actions">
      <button class="secondary-btn" id="resched-back">Back</button>
      <button class="primary-btn" id="resched-save">Reschedule</button>
    </div>
    <button class="action-btn danger-action" id="resched-complete">Complete permanently</button>
  `);
  $('#resched-back').addEventListener('click', () => openActionSheet(entry));
  const send = async (date) => {
    try {
      await apiFetch(`/done/${entry.page_id}`, {
        method: 'POST',
        body: JSON.stringify({ reschedule: date }),
      });
      closeModal();
      showToast('Rescheduled');
      loadActiveView();
    } catch (err) {
      reportError(err);
    }
  };
  modal.querySelectorAll('[data-days]').forEach((btn) => {
    btn.addEventListener('click', () => send(addDaysISO(Number(btn.dataset.days))));
  });
  $('#resched-save').addEventListener('click', () => send($('#resched-date').value));
  $('#resched-complete').addEventListener('click', () =>
    markDone(entry, { force: true })
  );
}

// `force` is the webapp's half of the TUI's *Permanently complete* branch.
// Without it the server refuses (409) to archive a recurring item, which is
// the backstop for views like Next Steps where the row's status may not even
// have reached the client.
async function markDone(entry, { force = false } = {}) {
  try {
    await apiFetch(`/done/${entry.page_id}`, {
      method: 'POST',
      body: JSON.stringify({ confirm_recurring: force }),
    });
    closeModal();
    removeEntryRow(entry.page_id);
    showToast('Done ✓');
  } catch (err) {
    if (err.data && err.data.recurring) {
      openRescheduleModal(entry);
      return;
    }
    reportError(err);
  }
}

function confirmDrop(entry) {
  openModal(`
    <h2>Drop this?</h2>
    <p class="entry-meta">${escapeHtml(entry.header)}</p>
    <p class="entry-meta">It will be archived in Notion.</p>
    <div class="modal-actions">
      <button class="secondary-btn" id="drop-cancel">Cancel</button>
      <button class="primary-btn danger-action" id="drop-confirm">Drop</button>
    </div>
  `);
  $('#drop-cancel').addEventListener('click', () => openActionSheet(entry));
  $('#drop-confirm').addEventListener('click', () => markDone(entry));
}

// endregion

// region Capture

$('#capture-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = $('#capture-input');
  const header = input.value.trim();
  const status = $('#capture-status');
  if (!header) return;
  try {
    await apiFetch('/capture', {
      method: 'POST',
      body: JSON.stringify({ header }),
    });
    input.value = '';
    status.textContent = 'Captured ✓';
    setTimeout(() => { status.textContent = ''; }, 2000);
  } catch (err) {
    if (err.message !== 'unauthorized') status.textContent = err.message;
  }
});

// endregion

// region Inbox / Triage

let triageState = null;

async function openTriageModal(entry) {
  let schema;
  try {
    schema = await ensureSchema();
  } catch (err) {
    reportError(err);
    return;
  }
  triageState = {
    entry,
    status: '',
    context: '',
    list_category: '',
    next_step: '',
    success_condition: '',
    due_date: '',
    follow_up_date: '',
  };
  renderTriageModal(schema);
}

const WAITING_FOR_DEFAULT_FOLLOW_UP_DAYS = 7;

function defaultWaitingFollowUp() {
  return addDaysISO(WAITING_FOR_DEFAULT_FOLLOW_UP_DAYS);
}

function optionGrid(name, options, selected) {
  return `<div class="option-grid" data-field="${name}">${options
    .map(
      (o) =>
        `<button type="button" class="option-btn${o === selected ? ' active' : ''}" data-value="${escapeHtml(o)}">${escapeHtml(o)}</button>`
    )
    .join('')}</div>`;
}

function renderTriageModal(schema) {
  const t = triageState;
  const isDelete = t.status === 'Delete';
  const isList = t.status === 'List';
  const showContext = t.status && !isDelete && !isList;
  const showRest = t.status && !isDelete;
  const isWaiting = t.status === 'Waiting For';
  // A Waiting For with no tickler never comes back into Next Steps. The
  // server defaults it anyway (build_property_update), so pre-fill the same
  // date here rather than let the user save a blank they can't see the cost of.
  if (isWaiting && !t.follow_up_date) t.follow_up_date = defaultWaitingFollowUp();

  openModal(`
    <h2>${escapeHtml(t.entry.header)}</h2>
    <div>
      <label>Status</label>
      ${optionGrid('status', schema.statuses, t.status)}
    </div>
    ${showContext ? `
      <div>
        <label>Context</label>
        ${optionGrid('context', schema.contexts_by_status[t.status] || [], t.context)}
      </div>` : ''}
    ${isList ? `
      <div>
        <label>List Category</label>
        ${optionGrid('list_category', schema.list_categories, t.list_category)}
      </div>` : ''}
    ${showRest ? `
      <div>
        <label>Next Step</label>
        <textarea id="triage-next-step" placeholder="Next step...">${escapeHtml(t.next_step)}</textarea>
      </div>
      <div>
        <label>Success Condition (optional)</label>
        <input id="triage-success" type="text" value="${escapeHtml(t.success_condition)}" />
      </div>
      <div>
        <label>Due Date (optional)</label>
        <input id="triage-due" type="date" value="${t.due_date}" />
      </div>
      <div>
        <label>Follow-up Date ${isWaiting ? '(required)' : '(optional)'}</label>
        <input id="triage-followup" type="date" value="${t.follow_up_date}" />
      </div>` : ''}
    ${isDelete ? '<p class="entry-meta">This will permanently delete the entry.</p>' : ''}
    <div class="modal-actions">
      <button class="secondary-btn" id="triage-cancel">Cancel</button>
      <button class="primary-btn" id="triage-save">${isDelete ? 'Delete' : 'Save'}</button>
    </div>
  `);

  modal.querySelectorAll('.option-grid').forEach((grid) => {
    const field = grid.dataset.field;
    grid.querySelectorAll('.option-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        t[field] = btn.dataset.value;
        if (field === 'status') {
          t.context = '';
          t.list_category = '';
        }
        renderTriageModal(schema);
      });
    });
  });

  if (showRest) {
    $('#triage-next-step').addEventListener('input', (e) => { t.next_step = e.target.value; });
    $('#triage-success').addEventListener('input', (e) => { t.success_condition = e.target.value; });
    $('#triage-due').addEventListener('input', (e) => { t.due_date = e.target.value; });
    $('#triage-followup').addEventListener('input', (e) => { t.follow_up_date = e.target.value; });
  }

  $('#triage-cancel').addEventListener('click', closeModal);
  $('#triage-save').addEventListener('click', saveTriage);
}

async function saveTriage() {
  const t = triageState;
  if (!t.status) {
    showToast('Choose a status', true);
    return;
  }
  if (t.status === 'List' && !t.list_category) {
    showToast('Choose a list category', true);
    return;
  }
  if (t.status !== 'List' && t.status !== 'Delete' && !t.context) {
    showToast('Choose a context', true);
    return;
  }
  const body = { status: t.status };
  if (t.status !== 'Delete') {
    if (t.context) body.context = t.context;
    if (t.list_category) body.list_category = t.list_category;
    if (t.next_step) body.next_step = t.next_step;
    if (t.success_condition) body.success_condition = t.success_condition;
    if (t.due_date) body.due_date = t.due_date;
    if (t.follow_up_date) body.follow_up_date = t.follow_up_date;
  }
  try {
    await apiFetch(`/triage/${t.entry.page_id}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    closeModal();
    showToast('Triaged ✓');
    removeEntryRow(t.entry.page_id);
  } catch (err) {
    reportError(err);
  }
}

// endregion

// region Weekly review

// A checklist over local state (`GET /review`) whose per-step work reuses the
// views and the action sheet the tabs already have. Two things differ from the
// TUI on purpose:
//
//  * The TUI collects each browse screen's changes and applies them when the
//    modal is dismissed. A webapp screen has no dismissal, and a backgrounded
//    phone would lose the batch, so every change here is applied immediately.
//  * The drill-downs open the full action sheet rather than mirroring each
//    browse screen's restricted key set. It is a superset of those keys, so
//    the capability set matches with no new per-step UI.
//
// The step list itself is never written here — it comes from `storage.py`
// via the endpoint, so the TUI and the webapp can't disagree about what the
// review is.

const REVIEW_STEP_STATUS = {
  projects: 'Current Project',
  waiting: 'Waiting For',
  someday: 'Someday/Maybe',
};

async function loadReview() {
  try {
    state.review = await apiFetch('/review');
  } catch (err) {
    reportError(err);
    return;
  }
  if (state.reviewStep === null) {
    renderReviewChecklist();
    return;
  }
  await renderReviewStep(state.review.steps[state.reviewStep]);
}

function renderReviewChecklist() {
  const review = state.review;
  const list = $('#entry-list');
  list.innerHTML = '';
  setEmpty('');
  $('#view-title').textContent = VIEWS.review.label;

  const total = review.steps.length;
  const done = review.steps.filter((s) => s.done).length;

  const header = document.createElement('li');
  header.className = 'group-header';
  header.innerHTML = `
    <span class="group-label">Week of ${escapeHtml(formatDate(review.week_start))}</span>
    <span class="group-count">${done}/${total}</span>
    <button class="group-btn" data-act="reset" aria-label="Reset review progress">↺</button>
  `;
  header
    .querySelector('[data-act="reset"]')
    .addEventListener('click', confirmResetReview);
  list.appendChild(header);

  review.steps.forEach((step) => {
    const li = document.createElement('li');
    li.className = 'entry review-step' + (step.done ? ' done' : '');
    const drillable = step.action !== 'manual' && !step.done;
    li.innerHTML = `
      <span class="review-tick">${step.done ? '✓' : '○'}</span>
      <div class="entry-main">
        <div class="entry-header">${escapeHtml(step.label)}</div>
      </div>
      ${drillable ? '<span class="chevron" aria-hidden="true">›</span>' : ''}
    `;
    li.addEventListener('click', () => onReviewStepTap(step));
    list.appendChild(li);
  });
}

// Mirrors the TUI's `action_toggle_step`: tapping a done step un-ticks it, a
// manual step ticks straight away, and anything else opens its drill-down —
// which is what ticks it, on the way out.
async function onReviewStepTap(step) {
  if (step.done) {
    await setReviewStep(step.index, false);
    return;
  }
  if (step.action === 'manual') {
    await setReviewStep(step.index, true);
    return;
  }
  state.reviewStep = step.index;
  await renderReviewStep(step);
}

async function setReviewStep(index, done) {
  try {
    state.review = await apiFetch(`/review/step/${index}`, {
      method: 'POST',
      body: JSON.stringify({ done }),
    });
  } catch (err) {
    reportError(err);
    return;
  }
  await maybeCompleteReview();
  if (state.reviewStep === null) renderReviewChecklist();
}

// Ticking the last step is what marks the review itself done for the week —
// the same habit key the TUI writes, so the terminal agrees.
async function maybeCompleteReview() {
  if (!state.review.steps.every((s) => s.done)) return;
  try {
    state.review = await apiFetch('/review/complete', { method: 'POST' });
    showToast('Weekly review complete 🎉');
  } catch (err) {
    reportError(err);
  }
}

function confirmResetReview() {
  openModal(`
    <h2>Reset review progress?</h2>
    <p class="entry-meta">Every step is un-ticked for this week.</p>
    <div class="modal-actions">
      <button class="secondary-btn" id="reset-cancel">Cancel</button>
      <button class="primary-btn danger-action" id="reset-confirm">Reset</button>
    </div>
  `);
  $('#reset-cancel').addEventListener('click', closeModal);
  $('#reset-confirm').addEventListener('click', async () => {
    try {
      state.review = await apiFetch('/review/reset', { method: 'POST' });
    } catch (err) {
      reportError(err);
      return;
    }
    closeModal();
    showToast('Review progress reset');
    renderReviewChecklist();
  });
}

async function renderReviewStep(step) {
  $('#view-title').textContent = step.label;
  if (step.action === 'areas') return renderReviewAreas(step);
  if (step.action === 'triage') return renderReviewTriage(step);
  return renderReviewEntries(step);
}

// Every drill-down is bracketed the same way: a header that gets you back
// without ticking, and an explicit "Done reviewing X" that does tick. The TUI
// separates the same two scopes onto two footer lines.
function appendReviewStepHeader(step, count) {
  const li = document.createElement('li');
  li.className = 'group-header';
  li.innerHTML = `
    <button class="group-btn" data-act="back" aria-label="Back to checklist">‹</button>
    <span class="group-label">${escapeHtml(step.label)}</span>
    <span class="group-count">${count}</span>
  `;
  li.querySelector('[data-act="back"]').addEventListener(
    'click',
    backToChecklist
  );
  $('#entry-list').appendChild(li);
}

function appendReviewStepDone(step) {
  const li = document.createElement('li');
  li.className = 'group-add';
  li.innerHTML = `<button class="group-add-btn" type="button">✓ Done reviewing ${escapeHtml(step.label)}</button>`;
  li.querySelector('button').addEventListener('click', () =>
    finishReviewStep(step)
  );
  $('#entry-list').appendChild(li);
}

function backToChecklist() {
  state.reviewStep = null;
  renderReviewChecklist();
}

async function finishReviewStep(step) {
  state.reviewStep = null;
  await setReviewStep(step.index, true);
}

async function renderReviewEntries(step) {
  const status = REVIEW_STEP_STATUS[step.action];
  try {
    state.entries = await apiFetch(
      `/entries?status=${encodeURIComponent(status)}`
    );
  } catch (err) {
    reportError(err);
    return;
  }
  const list = $('#entry-list');
  list.innerHTML = '';
  appendReviewStepHeader(step, state.entries.length);
  state.entries.forEach((entry) =>
    list.appendChild(entryRow(entry, openActionSheet))
  );
  appendReviewStepDone(step);
  setEmpty('');
}

async function renderReviewTriage(step) {
  try {
    state.entries = await apiFetch('/inbox');
  } catch (err) {
    reportError(err);
    return;
  }
  const list = $('#entry-list');
  list.innerHTML = '';
  appendReviewStepHeader(step, state.entries.length);
  state.entries.forEach((entry) =>
    list.appendChild(entryRow(entry, openTriageModal))
  );
  appendReviewStepDone(step);
  setEmpty(state.entries.length ? '' : 'Inbox is empty 🎉');
}

async function renderReviewAreas(step) {
  let areas;
  try {
    ({ areas } = await apiFetch('/areas'));
  } catch (err) {
    reportError(err);
    return;
  }
  const list = $('#entry-list');
  list.innerHTML = '';
  appendReviewStepHeader(step, areas.length);
  areas.forEach((area) => {
    const li = document.createElement('li');
    li.className = 'entry';
    li.innerHTML = `
      <div class="entry-main">
        <div class="entry-header">${escapeHtml(area)}</div>
        <div class="entry-sub">Anything falling through the cracks?</div>
      </div>
      <span class="chevron" aria-hidden="true">›</span>
    `;
    li.addEventListener('click', () => openAreaCaptureModal(area));
    list.appendChild(li);
  });
  appendReviewStepDone(step);
  setEmpty(
    areas.length ? '' : 'No horizons defined — add one from Someday/Maybe'
  );
}

function openAreaCaptureModal(area) {
  openModal(`
    <h2>${escapeHtml(area)}</h2>
    <p class="entry-meta">Anything here that isn't captured yet?</p>
    <textarea id="area-capture" placeholder="What needs attention?"></textarea>
    <div class="modal-actions">
      <button class="secondary-btn" id="area-good">All good</button>
      <button class="primary-btn" id="area-save">Capture</button>
    </div>
  `);
  $('#area-capture').focus();
  $('#area-good').addEventListener('click', closeModal);
  $('#area-save').addEventListener('click', async () => {
    const header = $('#area-capture').value.trim();
    if (!header) return;
    try {
      await apiFetch('/capture', {
        method: 'POST',
        body: JSON.stringify({ header }),
      });
    } catch (err) {
      reportError(err);
      return;
    }
    closeModal();
    showToast('Captured → Inbox');
  });
}

// The TUI's Next Steps tab always carries a Weekly Review row, done or not,
// so the review can't be forgotten. Same contract here — it is prepended to
// the list rather than folded into it, and it never disappears.
function habitLastDoneStr(iso) {
  if (!iso) return 'never';
  const days = Math.round(
    (new Date(`${todayISO()}T00:00`) - new Date(`${iso}T00:00`)) / 86400000
  );
  if (days === 0) return 'today';
  return `${formatDate(iso)} (${days}d ago)`;
}

function prependReviewHabitRow(review) {
  const li = document.createElement('li');
  li.className = 'entry habit-row';
  const done = review.done_this_week;
  const sub = done
    ? `last: ${habitLastDoneStr(review.last_done)}`
    : 'not done this week';
  li.innerHTML = `
    <div class="entry-main">
      <div class="entry-header">
        <span class="habit-dot${done ? ' done' : ''}">●</span> Weekly Review
      </div>
      <div class="entry-sub">${escapeHtml(sub)}</div>
    </div>
    <span class="chevron" aria-hidden="true">›</span>
  `;
  li.addEventListener('click', () => switchView('review'));
  $('#entry-list').prepend(li);
}

// endregion

// region Init

buildNavMenu();
if (!state.apiKey) openSettingsModal();
else loadActiveView();

// endregion
