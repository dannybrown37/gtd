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
  'filter_context',
  'filter_list',
  'move_someday',
  'move_to_list',
  'activate',
  'capture',
  'refresh',
];

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
  'someday':     { label: 'Someday/Maybe', kind: 'entries', status: 'Someday/Maybe' },
  'lists':       { label: 'Lists',         kind: 'lists' },
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
  schema: null,
  entries: [],
};

const $ = (sel) => document.querySelector(sel);
const modalBackdrop = $('#modal-backdrop');
const modal = $('#modal');
const navMenu = $('#nav-menu');

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

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function addDaysISO(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
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
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
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

function buildNavMenu() {
  navMenu.innerHTML = Object.entries(VIEWS)
    .map(([id, v]) => {
      const active = id === state.activeView ? ' active' : '';
      return `<button class="nav-item${active}" data-view="${id}">${escapeHtml(v.label)}</button>`;
    })
    .join('');
  navMenu.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      closeNav();
      switchView(btn.dataset.view);
    });
  });
}

function openNav() {
  buildNavMenu();
  navMenu.classList.remove('hidden');
}

function closeNav() {
  navMenu.classList.add('hidden');
}

$('#nav-btn').addEventListener('click', () => {
  if (navMenu.classList.contains('hidden')) openNav();
  else closeNav();
});

function switchView(view) {
  state.activeView = view;
  state.currentContext = '';
  state.currentCategory = '';
  $('#view-title').textContent = VIEWS[view].label;
  const isCapture = VIEWS[view].kind === 'capture';
  $('#view-capture').classList.toggle('hidden', !isCapture);
  $('#view-list').classList.toggle('hidden', isCapture);
  loadActiveView();
}

// endregion

// region List rendering

function setEmpty(message) {
  const empty = $('#list-empty');
  empty.textContent = message;
  empty.classList.toggle('hidden', !message);
}

function renderEntries(entries, { onTap }) {
  const list = $('#entry-list');
  list.innerHTML = '';
  entries.forEach((entry) => {
    const li = document.createElement('li');
    li.className = 'entry';
    li.dataset.pageId = entry.page_id;
    const bits = [
      entry.context,
      entry.area,
      entry.due_date && `due ${formatDate(entry.due_date)}`,
      entry.follow_up_date && `→ ${formatDate(entry.follow_up_date)}`,
    ].filter(Boolean);
    li.innerHTML = `
      <div class="entry-main">
        <div class="entry-header">${escapeHtml(entry.header)}</div>
        ${entry.next_step ? `<div class="entry-sub">${escapeHtml(entry.next_step)}</div>` : ''}
        ${bits.length ? `<div class="entry-meta">${escapeHtml(bits.join(' · '))}</div>` : ''}
      </div>
      <span class="chevron" aria-hidden="true">›</span>
    `;
    li.addEventListener('click', () => onTap(entry, li));
    list.appendChild(li);
  });
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
  return loadEntries(view);
}

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
    setEmpty(state.entries.length ? '' : 'Nothing actionable 🎉');
  } catch (err) {
    reportError(err);
  }
}

async function loadEntries(view) {
  const params = new URLSearchParams({ status: view.status });
  if (view.followUp) params.set('follow_up', view.followUp);
  try {
    state.entries = await apiFetch(`/entries?${params}`);
    renderEntries(state.entries, { onTap: openActionSheet });
    setEmpty(state.entries.length ? '' : 'Nothing here 🎉');
  } catch (err) {
    reportError(err);
  }
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

async function loadLists() {
  let categories;
  try {
    ({ list_categories: categories } = await apiFetch('/list-categories'));
  } catch (err) {
    reportError(err);
    return;
  }
  if (!categories.length) {
    setEmpty('No list categories defined');
    return;
  }
  if (!state.currentCategory) state.currentCategory = categories[0];
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
    renderEntries(state.entries, { onTap: openActionSheet });
    setEmpty(state.entries.length ? '' : 'Nothing in this list');
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
      <button class="action-btn" data-act="steps">Edit next step</button>
      <button class="action-btn" data-act="notes">Notes</button>
      <button class="action-btn" data-act="snooze">Snooze</button>
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
      if (act === 'steps') openStepsModal(entry);
      if (act === 'notes') openNotesModal(entry);
      if (act === 'snooze') openSnoozeModal(entry);
      if (act === 'activate') setStatus(entry, 'Current Project');
      if (act === 'someday') setStatus(entry, 'Someday/Maybe');
      if (act === 'list') openMoveToListModal(entry);
      if (act === 'done') isRecurring ? openRescheduleModal(entry) : markDone(entry);
      if (act === 'drop') confirmDrop(entry);
    });
  });
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
  $('#resched-complete').addEventListener('click', () => markDone(entry));
}

async function markDone(entry) {
  try {
    await apiFetch(`/done/${entry.page_id}`, { method: 'POST' });
    closeModal();
    removeEntryRow(entry.page_id);
    showToast('Done ✓');
  } catch (err) {
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
        <label>Follow-up Date (optional)</label>
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

// region Init

buildNavMenu();
if (!state.apiKey) openSettingsModal();
else loadActiveView();

// endregion
