const KEY_STORAGE = 'gtd_api_key';

const state = {
  apiKey: localStorage.getItem(KEY_STORAGE) || '',
  activeView: 'next-steps',
  currentContext: '',
  schema: null,
  triage: null, // { entry, status, context, list_category, next_step, success_condition, due_date, follow_up_date }
};

const $ = (sel) => document.querySelector(sel);
const modalBackdrop = $('#modal-backdrop');
const modal = $('#modal');

function showToast(message, isError) {
  const el = document.createElement('div');
  el.textContent = message;
  el.style.cssText = [
    'position:fixed', 'left:50%', 'bottom:88px', 'transform:translateX(-50%)',
    'background:' + (isError ? '#f87171' : '#1e293b'),
    'color:' + (isError ? '#0f172a' : '#e2e8f0'),
    'border:1px solid ' + (isError ? '#f87171' : '#334155'),
    'padding:10px 16px', 'border-radius:10px', 'font-size:0.85rem',
    'z-index:50', 'max-width:85vw', 'text-align:center',
  ].join(';');
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
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

// region Settings / API key

function openSettingsModal() {
  const canCancel = !!state.apiKey;
  openModal(`
    <h2>GTD API Key</h2>
    <label for="api-key-input">Bearer token from your GTD_API_KEY server env var</label>
    <input id="api-key-input" type="password" autocomplete="off" placeholder="Enter API key" />
    <div class="modal-actions">
      ${canCancel ? '<button class="secondary-btn" id="settings-cancel">Cancel</button>' : ''}
      <button class="primary-btn" id="settings-save">Save</button>
    </div>
  `);
  const input = $('#api-key-input');
  input.focus();
  if (canCancel) {
    $('#settings-cancel').addEventListener('click', closeModal);
  }
  $('#settings-save').addEventListener('click', async () => {
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

// region Tabs

document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => switchView(btn.dataset.view));
});

function switchView(view) {
  state.activeView = view;
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.view === view);
  });
  document.querySelectorAll('.view').forEach((section) => {
    section.classList.toggle('hidden', section.id !== `view-${view}`);
  });
  loadActiveView();
}

function loadActiveView() {
  if (!state.apiKey) return;
  if (state.activeView === 'next-steps') loadNextSteps();
  if (state.activeView === 'inbox') loadInbox();
}

// endregion

// region Next Steps

async function loadContextChips() {
  const container = $('#context-chips');
  try {
    const { contexts } = await apiFetch('/contexts');
    const all = ['All', ...contexts];
    container.innerHTML = all
      .map((c) => {
        const value = c === 'All' ? '' : c;
        const active = value === state.currentContext ? 'active' : '';
        return `<button class="chip ${active}" data-context="${value}">${c}</button>`;
      })
      .join('');
    container.querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        state.currentContext = chip.dataset.context;
        container.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
        chip.classList.add('active');
        loadNextSteps(false);
      });
    });
  } catch (err) {
    // chips are a nicety; ignore failures here, main list fetch will surface errors
  }
}

function formatDate(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${m}/${d}`;
}

async function loadNextSteps(refreshChips = true) {
  if (refreshChips) await loadContextChips();
  const list = $('#next-steps-list');
  const empty = $('#next-steps-empty');
  try {
    const path = state.currentContext
      ? `/next-steps?context=${encodeURIComponent(state.currentContext)}`
      : '/next-steps';
    const entries = await apiFetch(path);
    list.innerHTML = '';
    empty.classList.toggle('hidden', entries.length > 0);
    entries.forEach((entry) => list.appendChild(renderNextStepItem(entry)));
  } catch (err) {
    if (err.message !== 'unauthorized') showToast(err.message, true);
  }
}

function renderNextStepItem(entry) {
  const li = document.createElement('li');
  li.className = 'entry';
  const meta = [entry.context, formatDate(entry.due_date) && `due ${formatDate(entry.due_date)}`]
    .filter(Boolean)
    .join(' · ');
  li.innerHTML = `
    <div class="entry-main">
      <div class="entry-header">${escapeHtml(entry.header)}</div>
      ${entry.next_step ? `<div class="entry-sub">${escapeHtml(entry.next_step)}</div>` : ''}
      ${meta ? `<div class="entry-meta">${escapeHtml(meta)}</div>` : ''}
    </div>
    <button class="done-btn" aria-label="Mark done">✓</button>
  `;
  li.querySelector('.done-btn').addEventListener('click', () => markDone(entry.page_id, li));
  return li;
}

async function markDone(pageId, li) {
  try {
    await apiFetch(`/done/${pageId}`, { method: 'POST' });
    li.remove();
    showToast('Done ✓');
    const list = $('#next-steps-list');
    $('#next-steps-empty').classList.toggle('hidden', list.children.length > 0);
  } catch (err) {
    if (err.message !== 'unauthorized') showToast(err.message, true);
  }
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

async function loadInbox() {
  const list = $('#inbox-list');
  const empty = $('#inbox-empty');
  try {
    const entries = await apiFetch('/inbox');
    list.innerHTML = '';
    empty.classList.toggle('hidden', entries.length > 0);
    entries.forEach((entry) => list.appendChild(renderInboxItem(entry)));
  } catch (err) {
    if (err.message !== 'unauthorized') showToast(err.message, true);
  }
}

function renderInboxItem(entry) {
  const li = document.createElement('li');
  li.className = 'entry';
  li.dataset.pageId = entry.page_id;
  li.innerHTML = `
    <div class="entry-main">
      <div class="entry-header">${escapeHtml(entry.header)}</div>
      ${entry.due_date ? `<div class="entry-meta">due ${formatDate(entry.due_date)}</div>` : ''}
    </div>
  `;
  li.addEventListener('click', () => openTriageModal(entry));
  return li;
}

async function ensureSchema() {
  if (!state.schema) {
    state.schema = await apiFetch('/triage-schema');
  }
  return state.schema;
}

async function openTriageModal(entry) {
  let schema;
  try {
    schema = await ensureSchema();
  } catch (err) {
    if (err.message !== 'unauthorized') showToast(err.message, true);
    return;
  }
  state.triage = {
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
        `<button type="button" class="option-btn ${o === selected ? 'active' : ''}" data-value="${escapeHtml(o)}">${escapeHtml(o)}</button>`
    )
    .join('')}</div>`;
}

function renderTriageModal(schema) {
  const t = state.triage;
  const isDelete = t.status === 'Delete';
  const isList = t.status === 'List';
  const showContext = t.status && !isDelete && !isList;
  const showListCategory = isList;
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
    ${showListCategory ? `
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
  const t = state.triage;
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
    const list = $('#inbox-list');
    const li = list.querySelector(`[data-page-id="${t.entry.page_id}"]`);
    if (li) li.remove();
    $('#inbox-empty').classList.toggle('hidden', list.children.length > 0);
  } catch (err) {
    if (err.message !== 'unauthorized') showToast(err.message, true);
  }
}

// endregion

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// region Init

if (!state.apiKey) {
  openSettingsModal();
} else {
  loadActiveView();
}

// endregion
