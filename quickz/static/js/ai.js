'use strict';
/* AI-assisted question drafting.
   - aiDialog(): used inside the quiz editor. The host describes what they need, reviews the drafts, and adds the ones they want.
   - renderAISettings(): the Super Admin's page: which free services to use, their keys, limits, guardrails and the audit log.
   The server does all checking; nothing here is trusted for safety. */

const AI_STATUS = {
  ok: ['good', 'Done'], partial: ['warn', 'Partly done'], blocked: ['bad', 'Blocked'], refused: ['warn', 'Declined'],
  rate_limited: ['warn', 'Limit reached'], error: ['bad', 'Failed'],
};
const aiChip = s => { const [cls, label] = AI_STATUS[s] || ['', s]; return el('span', { class: 'chip ' + cls }, label); };

/* ---------------------------------------------------------------- host: the drafting dialog */
/** `single`: draft exactly one question (used when adding a question during a live game). `defaults`: preset form values. */
async function aiDialog({ onAdd, single = false, defaults = {} }) {
  let opts;
  try { opts = await api('/api/ai/options'); } catch (e) { return ui.toast(e.message, 'error'); }
  const admin = typeof isAdmin === 'function' && isAdmin();
  if (!opts.available) {
    const m = ui.modal({ title: 'Draft questions with AI', body: el('div', { class: 'col' },
      el('p', {}, !opts.enabled ? 'AI question drafting is switched off.' : 'No AI service is set up yet.'),
      el('p', { class: 'muted' }, admin ? 'Open AI settings to add a free service key and switch it on.' : 'Ask a Super Admin to turn it on under AI settings.')),
      actions: admin ? [el('button', { class: 'btn', type: 'button', onclick: () => m.close() }, 'Close'),
        el('button', { class: 'btn primary', type: 'button', onclick: () => { m.close(); go('#/ai'); } }, 'Open AI settings')]
        : [el('button', { class: 'btn primary', type: 'button', onclick: () => m.close() }, 'Close')] });
    return;
  }
  const pick = (id, label, map, value, hint) => {
    const entries = Array.isArray(map) ? map.map(v => [v, v[0].toUpperCase() + v.slice(1)]) : Object.entries(map);
    const sel = el('select', { class: 'select', id }, entries.map(([v, l]) => el('option', { value: v, selected: v === value }, l)));
    return { sel, field: el('div', { class: 'field' }, el('label', { for: id }, label), sel, hint ? el('span', { class: 'hint' }, hint) : null) };
  };
  const topic = el('input', { class: 'input', id: 'ai-topic', maxLength: 200, placeholder: 'e.g. The solar system, Indian festivals, Fire safety at work', 'aria-label': 'Topic', autofocus: true });
  const quizType = pick('ai-quiz', 'Quiz type', opts.quiz_types, 'general');
  const qType = pick('ai-qtype', 'Question type', opts.question_types, defaults.question_type || 'mixed');
  const diff = pick('ai-diff', 'Difficulty', opts.difficulties, 'medium');
  const level = pick('ai-level', 'Audience knowledge level', opts.levels, 'intermediate');
  const age = pick('ai-age', 'Audience age group', opts.age_groups, 'all');
  const lang = pick('ai-lang', 'Language', opts.languages, 'English');
  const counts = Array.from({ length: opts.max_questions }, (_, i) => String(i + 1));
  const count = pick('ai-count', 'How many questions', Object.fromEntries(counts.map(c => [c, c])), String(Math.min(5, opts.max_questions)));
  const err = el('p', { class: 'error-text', role: 'alert' });
  const body = el('div', { class: 'col' });
  const go1 = el('button', { class: 'btn primary', type: 'button' }, icon('sparkle'), 'Draft questions');
  const cancel = el('button', { class: 'btn', type: 'button', onclick: () => m.close() }, 'Cancel');
  const m = ui.modal({ title: 'Draft questions with AI', wide: true, body, actions: [cancel, go1] });

  function showForm() {
    err.textContent = '';
    body.replaceChildren(
      el('p', { class: 'muted' }, `AI writes drafts only. You review every question and choose which ones to add. You have ${opts.remaining_today} request${opts.remaining_today === 1 ? '' : 's'} left today.`),
      el('div', { class: 'field' }, el('label', { for: 'ai-topic' }, 'Topic'), topic, el('span', { class: 'hint' }, 'Describe the subject in words. Do not paste web addresses or instructions.')),
      el('div', { class: 'ai-grid' }, quizType.field, qType.field, diff.field, level.field, age.field, single ? null : count.field, lang.field), err);
    cancel.textContent = 'Cancel';
    go1.replaceChildren(icon('sparkle'), 'Draft questions'); go1.disabled = false; go1.onclick = generate;
    setTimeout(() => topic.focus(), 30);
  }

  async function generate() {
    err.textContent = '';
    if (topic.value.trim().length < 3) { err.textContent = 'Tell the assistant what the questions should be about.'; topic.focus(); return; }
    go1.disabled = true; go1.replaceChildren(el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Drafting…');
    try {
      const r = await api('/api/ai/generate', { method: 'POST', body: {
        topic: topic.value, quiz_type: quizType.sel.value, question_type: qType.sel.value, difficulty: diff.sel.value,
        level: level.sel.value, age_group: age.sel.value, language: lang.sel.value, count: single ? 1 : Number(count.sel.value) } });
      opts.remaining_today = Math.max(0, opts.remaining_today - 1);
      showDrafts(r);
    } catch (e) { go1.disabled = false; go1.replaceChildren(icon('sparkle'), 'Draft questions'); err.textContent = e.message; }
  }

  function draftBody(q) {
    const opt = o => plainText(o.text);
    if (q.type === 'text') return el('div', { class: 'ai-ans' }, el('b', {}, 'Accepted: '), q.answers.join(' · '));
    if (q.type === 'number') return el('div', { class: 'ai-ans' }, el('b', {}, 'Answer: '), String(q.number.answer), q.number.unit ? ' ' + q.number.unit : '', q.number.tolerance ? ` (± ${q.number.tolerance})` : '');
    if (q.type === 'match') return el('ul', { class: 'ai-opts' }, q.options.map((o, i) => el('li', {}, opt(o), ' ', el('span', { class: 'faint' }, '↔'), ' ', opt(q.targets[i]))));
    if (q.type === 'order') return el('ol', { class: 'ai-opts' }, q.options.map(o => el('li', {}, opt(o))));
    return el('ul', { class: 'ai-opts' }, q.options.map(o => el('li', { class: q.correct.includes(o.id) ? 'right' : '' }, q.correct.includes(o.id) ? icon('check') : null, opt(o))));
  }

  function showDrafts(r) {
    const boxes = r.questions.map(() => el('input', { type: 'checkbox', checked: true }));
    const refresh = () => { const n = boxes.filter(b => b.checked).length; go1.disabled = !n; go1.replaceChildren(icon('plus'), single ? 'Use this question' : n ? `Add ${n} question${n > 1 ? 's' : ''}` : 'Add questions'); };
    boxes.forEach(b => b.addEventListener('change', refresh));
    body.replaceChildren(
      el('div', { class: 'ai-note' }, icon('warn'), el('div', {}, el('b', {}, 'Drafted by AI. '), 'Check every fact and answer before you use a question. Untick any you do not want. You can edit them after adding.')),
      el('div', { class: 'faint', style: { fontSize: '.82rem' } }, `${r.provider} · ${r.questions.length} of ${r.requested} drafted${r.dropped ? ` · ${r.dropped} discarded by safety checks` : ''}`),
      el('div', { class: 'ai-drafts' }, r.questions.map((q, i) => el('label', { class: 'ai-draft' }, boxes[i],
        el('div', { class: 'grow' }, el('div', { class: 'ai-q' }, plainText(q.text)), draftBody(q),
          q.explanation ? el('div', { class: 'ai-why' }, q.explanation) : null,
          el('div', { class: 'ai-meta' }, el('span', { class: 'chip' }, questionKindLabel(q.type, q)), q.category ? el('span', { class: 'chip' }, q.category) : null))))));
    cancel.textContent = 'Start again'; cancel.onclick = showForm;
    go1.onclick = async () => {
      const chosen = r.questions.filter((_, i) => boxes[i].checked);
      if (!chosen.length) return;
      onAdd(chosen);
      api('/api/ai/accept', { method: 'POST', body: { audit_id: r.audit_id, count: chosen.length } }).catch(() => {});
      m.close(); ui.toast(single ? 'AI draft is in the form. Check it, then add it to the game.' : `Added ${chosen.length} AI-drafted question${chosen.length > 1 ? 's' : ''}. Review them before your game.`, 'success');
    };
    refresh();
  }
  showForm();
}
const questionKindLabel = (t, q) => (t === 'mcq' && q && q.options.length === 2 && q.options[0].text === 'True' ? 'True or false'
  : { mcq: 'Single choice', multi: 'Multiple answers', text: 'Typed answer', number: 'Number', order: 'Order', match: 'Match', poll: 'Poll' }[t] || t);

/* ---------------------------------------------------------------- Super Admin: AI settings */
async function renderAISettings() {
  mount(el('div', { class: 'skeleton', style: { height: '260px' } }));
  let d;
  try { d = await api('/api/admin/ai'); } catch (e) { return ui.toast(e.message, 'error'); }
  let s = d.settings;                              // refreshed on every paint, because each save returns a new copy
  const save = async (patch, ok = 'Saved') => { try { d = await api('/api/admin/ai/settings', { method: 'PUT', body: patch }); ui.toast(ok, 'success'); paint(); } catch (e) { ui.toast(e.message, 'error'); } };
  const saveProvider = async (name, patch, ok = 'Saved') => { try { d = await api(`/api/admin/ai/providers/${name}`, { method: 'PUT', body: patch }); ui.toast(ok, 'success'); paint(); return true; } catch (e) { ui.toast(e.message, 'error'); return false; } };
  let filters = { status: '', user: '', action: '', days: 30 }, logRows = [], logTotal = 0;

  function statCard(label, value, sub) { return el('div', { class: 'card flat ai-stat' }, el('div', { class: 'faint' }, label), el('div', { class: 'big' }, value), sub ? el('div', { class: 'faint' }, sub) : null); }
  function stats() {
    const day = d.stats_day, st = day.by_status, total = Object.values(st).reduce((a, b) => a + b, 0), good = (st.ok || 0) + (st.partial || 0);
    const tokens = day.by_provider.reduce((a, p) => a + (p.tin || 0) + (p.tout || 0), 0);
    return el('div', { class: 'ai-stats' },
      statCard('Requests, last 24 h', String(total), total ? `${good} worked · ${(st.blocked || 0)} blocked · ${(st.rate_limited || 0)} limited` : 'None yet'),
      statCard('Questions drafted', String(day.returned), `${day.accepted} added to quizzes`),
      statCard('Tokens used', tokens.toLocaleString(), 'in and out, last 24 h'),
      statCard('Failures', String((st.error || 0) + (st.refused || 0)), 'errors and declined requests'));
  }

  function providerCard(p) {
    const keyIn = el('input', { class: 'input', type: 'password', autocomplete: 'off', spellcheck: false, 'aria-label': `${p.label} API key`,
      placeholder: p.has_key ? `Saved (${p.key_hint}). Paste a new key to replace it` : 'Paste the API key' });
    const model = el('input', { class: 'input', value: p.model, placeholder: p.model_default || 'model name', 'aria-label': `${p.label} model`, maxLength: 100, list: `models-${p.name}`, autocomplete: 'off' });
    const modelList = el('datalist', { id: `models-${p.name}` });
    const loadBtn = el('button', { class: 'btn sm', type: 'button', disabled: !p.has_key, title: p.has_key ? '' : 'Save an API key first', onclick: async () => {
      loadBtn.disabled = true; loadBtn.textContent = 'Loading…';
      try {
        const r = await api(`/api/admin/ai/providers/${p.name}/models`);
        modelList.replaceChildren(...r.models.map(m => el('option', { value: m })));
        ui.toast(`${r.models.length} models found. Click the Model box to choose.`, 'success');
        model.focus(); model.select();
      } catch (e) { ui.toast(e.message, 'error'); }
      loadBtn.disabled = !p.has_key; loadBtn.textContent = 'Load model list';
    } }, 'Load model list');
    const acct = p.needs_account ? el('input', { class: 'input', value: p.account_id, placeholder: '32-character account ID', 'aria-label': 'Cloudflare account ID', maxLength: 64 }) : null;
    const base = p.needs_base ? el('input', { class: 'input', value: p.base_url, placeholder: 'https://example.com/v1', 'aria-label': 'Service address', maxLength: 200 }) : null;
    const prio = el('input', { class: 'input', type: 'number', min: 1, max: 99, value: p.priority, 'aria-label': 'Fallback order', style: { maxWidth: '5rem' } });
    const chosen = s.active_provider === p.name;
    const state = !p.has_key ? el('span', { class: 'chip' }, 'No key') : p.enabled ? el('span', { class: 'chip good' }, 'Ready') : el('span', { class: 'chip warn' }, 'Switched off');
    const body = () => { const b = { model: model.value, priority: Number(prio.value) || 50 }; if (acct) b.account_id = acct.value; if (base) b.base_url = base.value; if (keyIn.value.trim()) b.api_key = keyIn.value; return b; };
    const testBtn = el('button', { class: 'btn sm', type: 'button', disabled: !p.has_key, onclick: async () => {
      testBtn.disabled = true; testBtn.textContent = 'Testing…';
      try { const r = await api(`/api/admin/ai/providers/${p.name}/test`, { method: 'POST' }); ui.toast(r.ok ? 'Connection works' : r.message, r.ok ? 'success' : 'error'); loadLog(true); }
      catch (e) { ui.toast(e.message, 'error'); }
      testBtn.disabled = false; testBtn.textContent = 'Test connection';
    } }, 'Test connection');
    return el('div', { class: 'card ai-prov' + (chosen ? ' chosen' : '') },
      el('div', { class: 'row nowrap' }, el('h3', { class: 'grow' }, p.label), state, chosen ? el('span', { class: 'chip brand' }, 'In use') : null),
      el('div', { class: 'ai-grid' },
        el('div', { class: 'field' }, el('label', {}, 'API key'), keyIn, el('span', { class: 'hint' }, p.key_url ? el('span', {}, 'Free key: ', el('a', { href: p.key_url, target: '_blank', rel: 'noopener' }, p.key_url.replace('https://', ''))) : 'From your provider.')),
        el('div', { class: 'field' }, el('label', {}, 'Model'), el('div', { class: 'row nowrap' }, model, modelList, loadBtn), el('span', { class: 'hint' }, 'Free models change. Load the list from the service, or type a name. Save to apply.')),
        acct ? el('div', { class: 'field' }, el('label', {}, 'Account ID'), acct) : null, base ? el('div', { class: 'field' }, el('label', {}, 'Service address'), base) : null,
        el('div', { class: 'field' }, el('label', {}, 'Fallback order'), prio, el('span', { class: 'hint' }, 'Lower numbers are tried first.'))),
      el('div', { class: 'row', style: { gap: '.5rem', flexWrap: 'wrap' } },
        el('button', { class: 'btn primary sm', type: 'button', onclick: () => saveProvider(p.name, body(), 'Service saved') }, 'Save'),
        testBtn,
        el('button', { class: 'btn sm', type: 'button', disabled: !p.has_key || chosen, onclick: async () => { if (await saveProvider(p.name, { enabled: true }, 'Service enabled')) save({ active_provider: p.name }, `${p.label} is now in use`); } }, chosen ? 'In use' : 'Use this service'),
        el('label', { class: 'check', style: { margin: 0, padding: 0 } }, el('input', { type: 'checkbox', checked: p.enabled, disabled: !p.has_key, onchange: e => saveProvider(p.name, { enabled: e.target.checked }, e.target.checked ? 'Enabled' : 'Switched off') }), 'Enabled'),
        el('span', { class: 'grow' }),
        p.has_key ? el('button', { class: 'btn ghost sm', type: 'button', style: { color: 'var(--danger)' }, onclick: async () => { if (await ui.confirm({ title: `Remove the ${p.label} key?`, message: 'The key is deleted from this server and the service is switched off.', confirmText: 'Remove key', danger: true })) saveProvider(p.name, { clear_key: true }, 'Key removed'); } }, icon('trash'), 'Remove key') : null),
      p.updated_at ? el('div', { class: 'faint', style: { fontSize: '.8rem' } }, `Last changed ${timeAgo(p.updated_at)} by ${p.updated_by || 'unknown'}`) : null);
  }

  function limits() {
    const num = (k, label, hint) => { const i = el('input', { class: 'input', type: 'number', value: s[k], 'aria-label': label, style: { maxWidth: '8rem' } }); return { k, i, f: el('div', { class: 'field' }, el('label', {}, label), i, hint ? el('span', { class: 'hint' }, hint) : null) }; };
    const fields = [num('per_user_day', 'Requests per host, per day'), num('per_user_minute', 'Requests per host, per minute'), num('global_day', 'Requests for everyone, per day', 'Stops one busy day from using the free allowance'),
      num('max_questions', 'Most questions per request'), num('timeout_s', 'Wait for the service (seconds)'), num('retention_days', 'Keep the audit log (days)')];
    const terms = el('textarea', { class: 'textarea', rows: 4, 'aria-label': 'Extra blocked words', placeholder: 'One word or phrase per line', value: s.blocked_terms });
    const fb = el('input', { type: 'checkbox', checked: s.fallback });
    return el('div', { class: 'card' },
      el('h3', {}, 'Limits and guardrails'),
      el('div', { class: 'ai-grid' }, fields.map(x => x.f)),
      el('label', { class: 'check' }, fb, el('div', {}, el('div', {}, 'Try the other services if the chosen one fails'), el('div', { class: 'faint', style: { fontSize: '.85rem' } }, 'Uses the fallback order above. Every attempt is recorded.'))),
      el('div', { class: 'field' }, el('label', {}, 'Extra blocked words and phrases'), terms, el('span', { class: 'hint' }, `Added to the ${d.builtin_blocked} built-in phrases. Applies to topics and to what the AI writes. Younger audiences also get a stricter list.`)),
      el('div', { class: 'row' }, el('button', { class: 'btn primary', type: 'button', onclick: () => { const b = { fallback: fb.checked, blocked_terms: terms.value }; fields.forEach(x => { b[x.k] = x.i.value; }); save(b, 'Limits saved'); } }, 'Save limits')),
      el('details', { class: 'ai-always' }, el('summary', {}, 'Always on, cannot be switched off'),
        el('ul', {}, ['AI only drafts. A host reviews and adds each question; nothing is added automatically.',
          'Only the topic and the choices on the form reach the AI. No player names, emails or answers are ever sent.',
          'The topic is treated as data. Web addresses, email addresses and instruction-like text are refused.',
          'Replies must be valid questions. Anything with a link, blocked words, a duplicate or a broken answer key is discarded.',
          'API keys are encrypted on the server and never shown again after saving.',
          'Every request, refusal, limit hit and settings change is written to the audit log below.'].map(t => el('li', {}, t)))));
  }

  async function loadLog(reset) {
    const q = new URLSearchParams({ days: filters.days, limit: 25, offset: reset ? 0 : logRows.length });
    ['status', 'user', 'action'].forEach(k => filters[k] && q.set(k, filters[k]));
    try {
      const r = await api('/api/admin/ai/audit?' + q);
      logRows = reset ? r.rows : logRows.concat(r.rows); logTotal = r.total; paintLog();
    } catch (e) { ui.toast(e.message, 'error'); }
  }
  const logBox = el('div', {});
  function paintLog() {
    const sel = (k, label, items) => el('select', { class: 'select', 'aria-label': label, style: { maxWidth: '11rem' }, onchange: e => { filters[k] = e.target.value; loadLog(true); } },
      el('option', { value: '' }, label), items.map(([v, l]) => el('option', { value: v, selected: filters[k] === v }, l)));
    const userIn = el('input', { class: 'input', placeholder: 'Username', value: filters.user, 'aria-label': 'Filter by username', style: { maxWidth: '10rem' }, onchange: e => { filters.user = e.target.value.trim(); loadLog(true); } });
    const summary = r => {
      const p = r.params || {};
      if (r.action === 'generate') return [el('b', {}, p.topic || '(no topic)'), el('div', { class: 'faint' }, [p.question_type, p.difficulty, p.age_group, p.quiz_type].filter(Boolean).join(' · '))];
      return [el('span', {}, r.message || '')];
    };
    const rows = logRows.map(r => el('tr', { class: 'clickable', tabIndex: 0, onclick: () => detail(r.id), onkeydown: e => { if (e.key === 'Enter') detail(r.id); } },
      el('td', { class: 'muted nowrap' }, timeAgo(r.ts)), el('td', {}, r.username || '—'), el('td', {}, { generate: 'Drafting', test: 'Key test', models: 'Model list', config: 'Settings' }[r.action] || r.action),
      el('td', {}, aiChip(r.status)), el('td', {}, summary(r)), el('td', {}, r.provider ? `${r.provider}` : '—'),
      el('td', { class: 'num' }, r.action === 'generate' ? `${r.returned}/${r.requested} · ${r.accepted} added` : ''), el('td', { class: 'num' }, r.tokens_in + r.tokens_out ? (r.tokens_in + r.tokens_out).toLocaleString() : ''),
      el('td', { class: 'num' }, r.latency_ms ? (r.latency_ms / 1000).toFixed(1) + ' s' : '')));
    logBox.replaceChildren(...[
      el('div', { class: 'row', style: { gap: '.5rem', flexWrap: 'wrap', marginBottom: '.6rem' } },
        sel('status', 'Any result', Object.entries(AI_STATUS).map(([v, [, l]]) => [v, l])), sel('action', 'Any action', [['generate', 'Drafting'], ['test', 'Key test'], ['models', 'Model list'], ['config', 'Settings']]), userIn,
        sel('days', 'Range', []), el('span', { class: 'grow' }),
        el('a', { class: 'btn sm', href: `/api/admin/ai/audit.csv?days=${filters.days}`, download: 'ai-audit.csv' }, icon('download'), 'Export CSV')),
      logRows.length ? el('div', { class: 'tbl-wrap' }, el('table', { class: 'tbl' }, el('thead', {}, el('tr', {}, ['When', 'Who', 'What', 'Result', 'Details', 'Service', 'Questions', 'Tokens', 'Time'].map(h => el('th', {}, h)))), el('tbody', {}, rows)))
        : el('p', { class: 'muted' }, 'Nothing recorded for these filters yet.'),
      logRows.length < logTotal ? el('div', { class: 'row', style: { justifyContent: 'center', marginTop: '.6rem' } }, el('button', { class: 'btn sm', type: 'button', onclick: () => loadLog(false) }, `Show more (${logTotal - logRows.length} left)`)) : null].filter(Boolean));
    // the range picker is a plain list of choices
    const range = logBox.querySelector('select[aria-label="Range"]');
    if (range) { range.replaceChildren(...[[1, 'Last 24 hours'], [7, 'Last 7 days'], [30, 'Last 30 days'], [180, 'Last 180 days']].map(([v, l]) => el('option', { value: v, selected: filters.days === v }, l))); range.onchange = e => { filters.days = Number(e.target.value); loadLog(true); }; }
  }
  async function detail(id) {
    let r; try { r = await api('/api/admin/ai/audit/' + id); } catch (e) { return ui.toast(e.message, 'error'); }
    const kv = (k, v) => v === '' || v == null ? null : el('div', { class: 'ai-kv' }, el('span', { class: 'faint' }, k), el('span', {}, String(v)));
    const p = r.params || {}, f = r.flags || {};
    const m = ui.modal({ title: `Audit entry #${r.id}`, wide: true, body: el('div', { class: 'col' },
      kv('Time', new Date(r.ts * 1000).toLocaleString()), kv('User', r.username), kv('Action', r.action), el('div', { class: 'ai-kv' }, el('span', { class: 'faint' }, 'Result'), aiChip(r.status)),
      kv('Service', r.provider), kv('Model', r.model), kv('From address', r.ip),
      kv('Topic', p.topic), kv('Quiz type', p.quiz_type), kv('Question type', p.question_type), kv('Difficulty', p.difficulty), kv('Level', p.level), kv('Age group', p.age_group), kv('Language', p.language),
      kv('Asked for', r.requested), kv('Safe questions returned', r.returned), kv('Added by the host', r.accepted), kv('Tokens in / out', `${r.tokens_in} / ${r.tokens_out}`), kv('Time taken', r.latency_ms ? r.latency_ms + ' ms' : ''),
      kv('Note', r.message), f.dropped && f.dropped.length ? kv('Discarded by safety checks', f.dropped.join('; ')) : null, kv('Service declined because', f.refusal),
      r.response ? el('div', { class: 'field' }, el('label', {}, 'Raw reply from the service (before safety checks)'), el('pre', { class: 'ai-raw' }, r.response)) : null),
      actions: [el('button', { class: 'btn primary', type: 'button', onclick: () => m.close() }, 'Close')] });
  }

  function paint() {
    s = d.settings;
    const ready =d.providers.some(p => p.has_key && p.enabled);
    const master = el('input', { type: 'checkbox', checked: s.enabled, role: 'switch', 'aria-label': 'Allow AI question drafting', onchange: async e => {
      if (e.target.checked && !ready) { e.target.checked = false; return ui.toast('Add and enable at least one service first.', 'error'); }
      save({ enabled: e.target.checked }, e.target.checked ? 'AI drafting is on' : 'AI drafting is off'); } });
    mount(el('div', { class: 'page-head' }, el('div', { class: 'grow' }, el('h1', {}, 'AI assistant'),
        el('div', { class: 'sub' }, 'Hosts can draft quiz questions with free AI services. You choose the service, keep the keys, set the limits and see every request.')),
        el('label', { class: 'check ai-master' }, master, el('div', {}, el('b', {}, s.enabled ? 'On for all hosts' : 'Off'), el('div', { class: 'faint', style: { fontSize: '.82rem' } }, s.enabled ? 'Hosts see “Draft with AI” in the quiz editor' : 'Hosts cannot use AI drafting')))),
      !ready ? el('div', { class: 'card flat backup-card', style: { marginBottom: '1rem' } }, icon('warn'), el('div', { class: 'grow' }, el('b', {}, 'No service is ready'), el('div', { class: 'muted', style: { fontSize: '.88rem' } }, 'Paste a free API key below, save it, then choose “Use this service”.'))) : null,
      stats(),
      el('h2', { class: 'ai-h' }, 'Services'), el('div', { class: 'col' }, d.providers.map(providerCard)),
      el('h2', { class: 'ai-h' }, 'Limits'), limits(),
      el('h2', { class: 'ai-h' }, 'Assistant access'), mcpAdminCard(d, save),
      el('h2', { class: 'ai-h' }, 'Audit log'), el('div', { class: 'card' }, logBox));
    paintLog();
  }
  paint(); loadLog(true);
}

/* ---------------------------------------------------------------- assistant access (MCP): host tokens */
function tokensCard() {
  const box = el('div', { class: 'col' });
  const copy = (text, what) => (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(() => ui.toast(`${what} copied`, 'success'), () => ui.toast('Could not copy. Select the text instead.', 'error'));
  async function load() {
    let d;
    try { d = await api('/api/tokens'); } catch (e) { box.replaceChildren(el('p', { class: 'error-text' }, e.message)); return; }
    const live = d.tokens.filter(t => t.state === 'active');
    box.replaceChildren(...[
      el('p', { class: 'muted', style: { margin: 0 } }, 'Let your own AI assistant (Claude, ChatGPT, Cursor and others) write quizzes for you and read your results. It acts only as you, sees only your quizzes and games, and cannot delete anything.'),
      !d.enabled ? el('div', { class: 'ai-note' }, icon('warn'), el('div', {}, 'Assistant access is switched off. Ask a Super Admin to turn it on under AI settings.')) : null,
      d.tokens.length ? el('div', { class: 'tbl-wrap' }, el('table', { class: 'tbl' },
        el('thead', {}, el('tr', {}, ['Name', 'Access', 'Expires', 'Last used', ''].map(h => el('th', {}, h)))),
        el('tbody', {}, d.tokens.map(t => el('tr', {},
          el('td', {}, el('b', {}, t.name), el('div', { class: 'faint mono', style: { fontSize: '.8rem' } }, t.hint)),
          el('td', {}, el('span', { class: 'chip ' + (t.scope === 'write' ? 'warn' : '') }, t.scope === 'write' ? 'Read and write' : 'Read only')),
          el('td', { class: 'muted' }, t.state === 'active' ? new Date(t.expires_at * 1000).toLocaleDateString() : el('span', { class: 'chip bad' }, t.state === 'revoked' ? 'Revoked' : 'Expired')),
          el('td', { class: 'muted' }, t.last_used ? `${timeAgo(t.last_used)} · ${t.uses} calls` : 'Never'),
          el('td', {}, t.state === 'active' ? el('button', { class: 'btn ghost sm', type: 'button', style: { color: 'var(--danger)' }, onclick: async () => {
            if (!(await ui.confirm({ title: `Revoke “${t.name}”?`, message: 'The app using this token stops working immediately.', confirmText: 'Revoke', danger: true }))) return;
            try { await api(`/api/tokens/${t.id}`, { method: 'DELETE' }); ui.toast('Token revoked', 'success'); load(); } catch (x) { ui.toast(x.message, 'error'); }
          } }, 'Revoke') : null)))))) : null,
      el('div', {}, el('button', { class: 'btn primary', type: 'button', disabled: !d.enabled || live.length >= 10, onclick: () => createDialog() }, icon('key'), 'Create access token')),
    ].filter(Boolean));
  }
  function createDialog() {
    const name = el('input', { class: 'input', id: 'tk-name', maxLength: 60, placeholder: 'e.g. Claude Desktop on my laptop', 'aria-label': 'Token name', autofocus: true });
    let scope = 'read';
    const seg = el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Access' });
    const paint = () => seg.replaceChildren(...[['read', 'Read only'], ['write', 'Read and write']].map(([v, l]) => el('button', { type: 'button', role: 'radio', 'aria-pressed': String(scope === v), onclick: () => { scope = v; paint(); } }, l)));
    paint();
    const days = el('select', { class: 'select', 'aria-label': 'Expires' }, [[30, '30 days'], [90, '90 days'], [365, '1 year']].map(([v, l]) => el('option', { value: v, selected: v === 90 }, l)));
    const err = el('p', { class: 'error-text', role: 'alert' });
    const m = ui.modal({ title: 'Create an access token', body: el('div', { class: 'col' },
      el('div', { class: 'field' }, el('label', { for: 'tk-name' }, 'Name'), name, el('span', { class: 'hint' }, 'So you can recognise it later.')),
      el('div', { class: 'field' }, el('label', {}, 'What can it do?'), seg, el('span', { class: 'hint' }, 'Read only: list and read quizzes and results, and check quiz text. Read and write also lets it create quizzes, copy quizzes and open game lobbies. Neither can delete anything.')),
      el('div', { class: 'field' }, el('label', {}, 'Expires after'), days), err),
      actions: [el('button', { class: 'btn', type: 'button', onclick: () => m.close() }, 'Cancel'), el('button', { class: 'btn primary', type: 'button', onclick: async () => {
        try { const r = await api('/api/tokens', { method: 'POST', body: { name: name.value, scope, days: Number(days.value) } }); m.close(); shown(r); load(); } catch (x) { err.textContent = x.message; }
      } }, 'Create token')] });
  }
  function shown(r) {
    const cfg = JSON.stringify({ mcpServers: { diquizbuzz: { command: 'python', args: ['/path/to/dqb_mcp_bridge.py'], env: { DQB_URL: r.endpoint, DQB_TOKEN: r.token } } } }, null, 2);
    const row = (label, value, what) => el('div', { class: 'field' }, el('label', {}, label), el('div', { class: 'row nowrap' }, el('input', { class: 'input mono', readOnly: true, value, 'aria-label': label, onfocus: e => e.target.select() }),
      el('button', { class: 'btn sm', type: 'button', onclick: () => copy(value, what) }, icon('copy'), 'Copy')));
    const m = ui.modal({ title: 'Your access token', wide: true, body: el('div', { class: 'col' },
      el('div', { class: 'ai-note' }, icon('warn'), el('div', {}, el('b', {}, 'Copy it now. '), 'It is shown only once and cannot be recovered. Anyone with it can act as you, so keep it private. You can revoke it at any time.')),
      row('Token', r.token, 'Token'), row('Server address', r.endpoint, 'Address'),
      el('details', {}, el('summary', { style: { cursor: 'pointer', fontWeight: 600 } }, 'How to connect an AI app'),
        el('p', { class: 'muted' }, 'Apps that accept a web address and a header: use the server address and send the header ', el('code', {}, 'Authorization: Bearer <token>'), '.'),
        el('p', { class: 'muted' }, 'Apps that start a local program (such as Claude Desktop): download ', el('code', {}, 'tools/dqb_mcp_bridge.py'), ' from the project, then add this to the app MCP settings:'),
        el('pre', { class: 'ai-raw' }, cfg), el('button', { class: 'btn sm', type: 'button', onclick: () => copy(cfg, 'Settings') }, icon('copy'), 'Copy settings'))),
      actions: [el('button', { class: 'btn primary', type: 'button', onclick: () => m.close() }, 'I have copied it')] });
  }
  load();
  return box;
}

/* ---------------------------------------------------------------- assistant access (MCP): Super Admin controls */
function mcpAdminCard(d, save) {
  const s = d.settings, box = el('div', {});
  const on = el('input', { type: 'checkbox', checked: s.mcp_enabled, role: 'switch', 'aria-label': 'Allow assistant access', onchange: e => save({ mcp_enabled: e.target.checked }, e.target.checked ? 'Assistant access is on' : 'Assistant access is off') });
  const num = (k, label) => { const i = el('input', { class: 'input', type: 'number', value: s[k], 'aria-label': label, style: { maxWidth: '8rem' } }); return { k, i, f: el('div', { class: 'field' }, el('label', {}, label), i) }; };
  const fields = [num('mcp_per_minute', 'Calls per token, per minute'), num('mcp_quizzes_per_day', 'Quizzes per token, per day'), num('mcp_games_open', 'Open lobbies per host')];
  const log = el('div', {});
  (async () => {
    let r; try { r = await api('/api/admin/mcp/audit?limit=15'); } catch (e) { return; }
    log.replaceChildren(r.rows.length ? el('div', { class: 'tbl-wrap' }, el('table', { class: 'tbl' }, el('thead', {}, el('tr', {}, ['When', 'Who', 'Tool', 'Result', 'Details', 'Time'].map(h => el('th', {}, h)))),
      el('tbody', {}, r.rows.map(x => el('tr', {}, el('td', { class: 'muted nowrap' }, timeAgo(x.ts)), el('td', {}, x.username || '—'), el('td', { class: 'mono' }, x.tool), el('td', {}, aiChip(x.status)),
        el('td', { class: 'muted' }, x.message), el('td', { class: 'num' }, x.ms ? x.ms + ' ms' : '')))))) : el('p', { class: 'muted' }, 'No assistant activity yet.'));
  })();
  box.append(el('div', { class: 'card' },
    el('div', { class: 'row nowrap' }, el('div', { class: 'grow' }, el('h3', { style: { margin: 0 } }, 'Assistant access (MCP)'),
      el('div', { class: 'faint' }, 'Lets a host’s own AI app create quizzes, open lobbies and read results through a personal token. Off until you switch it on.')),
      el('label', { class: 'check ai-master' }, on, el('b', {}, s.mcp_enabled ? 'On' : 'Off'))),
    el('div', { class: 'ai-grid' }, fields.map(x => x.f)),
    el('div', { class: 'row' }, el('button', { class: 'btn primary', type: 'button', onclick: () => { const b = {}; fields.forEach(x => { b[x.k] = x.i.value; }); save(b, 'Limits saved'); } }, 'Save limits')),
    el('details', { class: 'ai-always' }, el('summary', {}, 'What a token can and cannot do'),
      el('ul', {}, ['It acts as one host and only reaches that host’s own quizzes and games, even if the host is a Super Admin.',
        'It can read quizzes and results, check quiz text, and (with write access) create or copy quizzes, add questions, and open a game lobby.',
        'It cannot delete anything, change accounts or settings, run a live game, or see player emails.',
        'Tokens expire, can be revoked by their owner, and stop working when the account is disabled, its password is reset, or the owner signs out everywhere.',
        'Every call is recorded below, and quizzes it creates are labelled as AI-made.'].map(t => el('li', {}, t)))),
    el('h4', { style: { margin: '1rem 0 .4rem' } }, 'Recent activity'), log));
  return box;
}
