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
const AI_TYPE_SHORT = { mixed: 'Mix of types', mcq: 'Multiple choice', truefalse: 'True or false', multi: 'Select several', text: 'Typed answer', number: 'Number', order: 'Put in order', match: 'Match pairs', poll: 'Poll' };
const AI_TOPIC_IDEAS = ['The solar system', 'World capitals', 'Indian festivals', 'Healthy eating', 'Fire safety at work', 'Famous inventions'];
const AI_STAGES = ['Planning different angles on your topic', 'Writing the questions and answer options', 'Checking the answers and the safety rules'];
const AI_REPLACE = [['easier', 'Easier'], ['harder', 'Harder'], ['different', 'Different'], ['simpler', 'Simpler words']];
const AI_MEMORY = 'dqb:ai:last';
const aiRemember = o => { try { localStorage.setItem(AI_MEMORY, JSON.stringify(o)); } catch (e) { /* private window: fine */ } };
const aiRecall = () => { try { return JSON.parse(localStorage.getItem(AI_MEMORY) || '{}') || {}; } catch (e) { return {}; } };

/** Draft questions with AI.
 *  onAdd(list)        the host chose these questions
 *  single             draft exactly one question (adding a question during a live game)
 *  defaults           preset form values, e.g. { question_type: 'mcq' }
 *  context()          { quiz_title, existing: [question texts] } so the AI avoids repeating the quiz it is writing for */
async function aiDialog({ onAdd, single = false, defaults = {}, context = null }) {
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
  const ctx = (context && context()) || {};
  const quizTexts = (ctx.existing || []).map(t => String(t || '').trim()).filter(t => t.length >= 5);
  const mem = aiRecall();
  const pickOf = (key, list, fallback) => (list.includes(mem[key]) ? mem[key] : fallback);
  const f = {
    topic: '', notes: '',
    age_group: pickOf('age_group', Object.keys(opts.age_groups), 'all'),
    level: pickOf('level', opts.levels, 'intermediate'),
    language: pickOf('language', opts.languages, 'English'),
    question_type: defaults.question_type || pickOf('question_type', Object.keys(opts.question_types), 'mixed'),
    difficulty: pickOf('difficulty', opts.difficulties, 'medium'),
    count: Math.min(Number(mem.count) || 5, opts.max_questions),
    tone: pickOf('tone', Object.keys(opts.tones || { friendly: 1 }), 'friendly'),
    quiz_type: pickOf('quiz_type', Object.keys(opts.quiz_types), 'general'),
    explanations: mem.explanations !== false,
    use_context: quizTexts.length > 0,
  };
  let drafts = [], lastBrief = '', lastVersion = '', providerLine = '', discarded = 0, closed = false, busy = false;
  const root = el('div', { class: 'aiw' });
  const cancel = el('button', { class: 'btn', type: 'button', onclick: () => m.close() }, 'Cancel');
  const primary = el('button', { class: 'btn primary', type: 'button' });
  const m = ui.modal({ title: single ? 'Draft a question with AI' : 'Draft questions with AI', wide: true, body: root, actions: [cancel, primary], onClose: () => { closed = true; } });
  const lab = (map, v) => (map[v] || v);
  const setPrimary = (iconName, text, handler, disabled = false) => { primary.replaceChildren(icon(iconName), text); primary.onclick = handler; primary.disabled = disabled; };
  const requestBody = extra => ({
    topic: f.topic, notes: f.notes, age_group: f.age_group, level: f.level, language: f.language, question_type: f.question_type, difficulty: f.difficulty,
    tone: f.tone, quiz_type: f.quiz_type, explanations: f.explanations, count: single ? 1 : f.count, quiz_title: ctx.quiz_title || '',
    existing: [...(f.use_context ? quizTexts : []), ...drafts.map(d => plainText(d.q.text))], ...extra });

  /* ---------- 1. the brief ---------- */
  function sentence() {
    const n = single ? 1 : f.count;
    const kind = f.question_type === 'mixed' ? 'mixed' : lab(AI_TYPE_SHORT, f.question_type).toLowerCase();
    return `${n} ${f.difficulty} ${kind} question${n > 1 ? 's' : ''} ${f.topic.trim() ? `about “${f.topic.trim()}”` : 'on your topic'} for ${lab(opts.age_groups, f.age_group).toLowerCase()}, ${f.level} level, in ${f.language}.`;
  }
  function showForm(error = '') {
    const chipGroup = (label, items, key, onChange) => el('div', { class: 'chips-pick', role: 'radiogroup', 'aria-label': label },
      items.map(([v, l, tip]) => el('button', { type: 'button', role: 'radio', title: tip || '', 'aria-pressed': String(f[key] === v), onclick: e => {
        f[key] = v; e.currentTarget.parentNode.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === e.currentTarget))); if (onChange) onChange(); refreshSummary(); } }, l)));
    const seg = (label, items, key) => el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': label },
      items.map(([v, l]) => el('button', { type: 'button', role: 'radio', 'aria-pressed': String(f[key] === v), onclick: e => {
        f[key] = v; e.currentTarget.parentNode.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === e.currentTarget))); refreshSummary(); } }, l)));
    const select = (id, label, map, key) => el('div', { class: 'field' }, el('label', { for: id }, label),
      el('select', { class: 'select', id, onchange: e => { f[key] = e.target.value; refreshSummary(); } },
        (Array.isArray(map) ? map.map(v => [v, v]) : Object.entries(map)).map(([v, l]) => el('option', { value: v, selected: v === f[key] }, l))));
    const topic = el('input', { class: 'input aiw-topic', id: 'ai-topic', maxLength: 200, value: f.topic, placeholder: 'e.g. The solar system', autocomplete: 'off',
      'aria-describedby': 'ai-topic-hint', oninput: e => { f.topic = e.target.value; refreshSummary(); } });
    const notes = el('textarea', { class: 'textarea', id: 'ai-notes', rows: 3, maxLength: 500, value: f.notes, 'aria-describedby': 'ai-notes-hint',
      placeholder: 'e.g. Based on chapter 3 of our staff handbook. Skip anything about pricing. Keep it relevant to a UK audience.', oninput: e => { f.notes = e.target.value; count.textContent = `${f.notes.length}/500`; } });
    const count = el('span', { class: 'faint num' }, `${f.notes.length}/500`);
    const summary = el('div', { class: 'aiw-summary', role: 'status', 'aria-live': 'polite' });
    function refreshSummary() { summary.replaceChildren(icon('sparkle'), el('div', {}, el('b', {}, 'You are asking for '), sentence(),
      el('div', { class: 'faint', style: { fontSize: '.82rem' } }, `${opts.remaining_today} AI request${opts.remaining_today === 1 ? '' : 's'} left today. A draft is never added until you choose it.`))); }
    const num = single ? null : el('div', { class: 'aiw-step', role: 'group', 'aria-label': 'How many questions' },
      el('button', { class: 'btn sm', type: 'button', 'aria-label': 'Fewer', onclick: () => bump(-1) }, '−'), el('output', { class: 'num', 'aria-live': 'polite' }, String(f.count)),
      el('button', { class: 'btn sm', type: 'button', 'aria-label': 'More', onclick: () => bump(1) }, '+'));
    function bump(d) { f.count = Math.max(1, Math.min(opts.max_questions, f.count + d)); num.querySelector('output').textContent = String(f.count); refreshSummary(); }

    root.replaceChildren(...[
      el('p', { class: 'muted aiw-lead' }, 'Describe the quiz and the AI will draft questions for you to review. Nothing is added until you choose it, and every draft needs a quick fact-check.'),
      error ? el('div', { class: 'ai-note aiw-err', role: 'alert' }, icon('warn'), el('div', {}, error)) : null,
      el('section', { class: 'aiw-sec' }, el('h3', {}, '1. What is it about?'),
        el('div', { class: 'field' }, el('label', { for: 'ai-topic' }, 'Topic'), topic, el('span', { class: 'hint', id: 'ai-topic-hint' }, 'A subject in plain words. Be as specific as you like: “Photosynthesis for year 6” works better than “Science”.'),
          el('div', { class: 'aiw-ideas', 'aria-label': 'Topic ideas' }, el('span', { class: 'faint' }, 'Try:'), AI_TOPIC_IDEAS.map(t => el('button', { type: 'button', class: 'aiw-idea', onclick: () => { topic.value = t; f.topic = t; refreshSummary(); topic.focus(); } }, t)))),
        el('details', { class: 'aiw-more', open: !!f.notes }, el('summary', {}, 'Add focus or context (optional)'),
          el('div', { class: 'field' }, el('label', { for: 'ai-notes' }, 'Anything else the AI should know?'), notes,
            el('div', { class: 'row', style: { justifyContent: 'space-between' } }, el('span', { class: 'hint', id: 'ai-notes-hint' }, 'Sources, scope, things to avoid, local context. Describe it in words, without links.'), count)))),
      el('section', { class: 'aiw-sec' }, el('h3', {}, '2. Who is it for?'),
        el('div', { class: 'field' }, el('label', {}, 'Age group'), chipGroup('Age group', Object.entries(opts.age_groups), 'age_group')),
        el('div', { class: 'aiw-row' },
          el('div', { class: 'field' }, el('label', {}, 'Knowledge of the topic'), seg('Knowledge level', opts.levels.map(v => [v, v[0].toUpperCase() + v.slice(1)]), 'level')),
          select('ai-lang', 'Language', opts.languages, 'language'))),
      el('section', { class: 'aiw-sec' }, el('h3', {}, '3. What should it ask?'),
        el('div', { class: 'field' }, el('label', {}, 'Question type'), chipGroup('Question type', Object.entries(opts.question_types).map(([v, l]) => [v, lab(AI_TYPE_SHORT, v), l]), 'question_type')),
        el('div', { class: 'aiw-row' },
          el('div', { class: 'field' }, el('label', {}, 'Difficulty'), seg('Difficulty', opts.difficulties.map(v => [v, v[0].toUpperCase() + v.slice(1)]), 'difficulty')),
          single ? null : el('div', { class: 'field' }, el('label', {}, 'How many'), num)),
        el('details', { class: 'aiw-more' }, el('summary', {}, 'Style options'),
          el('div', { class: 'aiw-row' }, select('ai-tone', 'Tone', opts.tones || { friendly: 'Friendly and clear' }, 'tone'), select('ai-quiz', 'Quiz setting', opts.quiz_types, 'quiz_type')),
          el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: f.explanations, onchange: e => { f.explanations = e.target.checked; } }),
            el('div', {}, el('div', { class: 't' }, 'Include a one-line explanation'), el('div', { class: 'd' }, 'Shown to players after the answer is revealed.'))))),
      quizTexts.length ? el('label', { class: 'check aiw-ctx' }, el('input', { type: 'checkbox', checked: f.use_context, onchange: e => { f.use_context = e.target.checked; } }),
        el('div', {}, el('div', { class: 't' }, `Use this quiz as context (${quizTexts.length} question${quizTexts.length > 1 ? 's' : ''})`),
          el('div', { class: 'd' }, 'The AI sees your existing questions so it will not repeat them, and can match their style. They are not stored anywhere else.'))) : null,
      summary].filter(Boolean));
    refreshSummary();
    cancel.textContent = 'Cancel'; cancel.onclick = () => m.close();
    setPrimary('sparkle', single ? 'Draft a question' : 'Draft questions', generate);
    root.onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); generate(); } else if (e.key === 'Enter' && e.target === topic) { e.preventDefault(); generate(); } };
    setTimeout(() => topic.focus(), 30);
  }

  /* ---------- 2. waiting ---------- */
  function showWaiting(text) {
    const stage = el('div', { class: 'faint' }, AI_STAGES[0]), clock = el('span', { class: 'faint num' }, '0 s');
    root.replaceChildren(el('div', { class: 'aiw-wait', role: 'status', 'aria-live': 'polite' },
      el('span', { class: 'spinner', 'aria-hidden': 'true' }), el('div', { class: 'aiw-wait-t' }, text), stage,
      el('div', { class: 'faint' }, 'This usually takes 5 to 20 seconds. ', clock),
      el('div', { class: 'aiw-skel', 'aria-hidden': 'true' }, [0, 1, 2].map(() => el('div', { class: 'aiw-skel-c' })))));
    const t0 = Date.now();
    const tick = setInterval(() => {
      if (closed || !root.isConnected || !clock.isConnected) return clearInterval(tick);
      const s = Math.floor((Date.now() - t0) / 1000); clock.textContent = s + ' s';
      stage.textContent = AI_STAGES[Math.min(AI_STAGES.length - 1, Math.floor(s / 4))];
    }, 500);
    cancel.textContent = 'Cancel'; cancel.onclick = () => m.close();
    setPrimary('sparkle', 'Drafting…', null, true);
  }

  async function generate() {
    if (busy) return;
    if (f.topic.trim().length < 3) { showForm('Tell the AI what the questions should be about (at least 3 letters).'); return; }
    busy = true;
    aiRemember({ age_group: f.age_group, level: f.level, language: f.language, question_type: f.question_type, difficulty: f.difficulty, count: f.count, tone: f.tone, quiz_type: f.quiz_type, explanations: f.explanations });
    showWaiting(single ? 'Writing your question…' : `Writing ${f.count} question${f.count > 1 ? 's' : ''}…`);
    try {
      const r = await api('/api/ai/generate', { method: 'POST', body: requestBody() });
      opts.remaining_today = Math.max(0, opts.remaining_today - 1);
      drafts = r.questions.map((q, i) => ({ q, meta: (r.meta || [])[i] || { confidence: 'sure', note: '' }, audit_id: r.audit_id, on: true, busy: false }));
      lastBrief = r.brief; lastVersion = r.prompt_version; providerLine = `${r.provider} · ${r.model}`; discarded = r.dropped || 0;
      busy = false; if (!closed) showReview();
    } catch (e) { busy = false; if (!closed) showForm(e.message); }
  }

  /* ---------- 3. review ---------- */
  function draftBody(q) {
    const opt = o => plainText(o.text);
    if (q.type === 'text') return el('div', { class: 'ai-ans' }, el('b', {}, 'Accepted answers: '), q.answers.join(' · '));
    if (q.type === 'number') return el('div', { class: 'ai-ans' }, el('b', {}, 'Answer: '), String(q.number.answer), q.number.unit ? ' ' + q.number.unit : '', q.number.tolerance ? ` (± ${q.number.tolerance})` : '');
    if (q.type === 'match') return el('ul', { class: 'ai-opts' }, q.options.map((o, i) => el('li', {}, opt(o), ' ', el('span', { class: 'faint' }, '↔'), ' ', opt(q.targets[i]))));
    if (q.type === 'order') return el('ol', { class: 'ai-opts' }, q.options.map(o => el('li', {}, opt(o))));
    return el('ul', { class: 'ai-opts' }, q.options.map(o => el('li', { class: q.correct.includes(o.id) ? 'right' : '' }, q.correct.includes(o.id) ? icon('check') : null, opt(o))));
  }
  const chosen = () => drafts.filter(d => d.on);
  function refreshAction() {
    const n = chosen().length;
    setPrimary('plus', single ? 'Use this question' : n ? `Add ${n} question${n > 1 ? 's' : ''}` : 'Add questions', finish, !n || busy);
  }
  function showReview(note) {
    const flagged = drafts.filter(d => d.meta.confidence === 'check').length;
    const card = (d, i) => {
      const box = el('input', { type: 'checkbox', checked: d.on, 'aria-label': `Include question ${i + 1}`, onchange: e => { d.on = e.target.checked; card_(d, i); refreshAction(); } });
      function card_(dd, idx) { wrap.classList.toggle('off', !dd.on); }
      const wrap = el('article', { class: 'aiw-card' + (d.on ? '' : ' off') + (d.busy ? ' busy' : ''), 'aria-busy': String(d.busy) },
        el('div', { class: 'aiw-card-h' }, el('label', { class: 'aiw-pick' }, box, el('span', { class: 'num' }, single ? 'Draft' : `Question ${i + 1}`)),
          el('span', { class: 'chip' }, questionKindLabel(d.q.type, d.q)), d.q.category ? el('span', { class: 'chip' }, d.q.category) : null, el('span', { class: 'grow' })),
        el('div', { class: 'ai-q' }, plainText(d.q.text)), draftBody(d.q),
        d.q.explanation ? el('div', { class: 'ai-why' }, el('b', {}, 'Why: '), d.q.explanation) : null,
        d.meta.confidence === 'check' ? el('div', { class: 'aiw-check' }, icon('warn'), el('div', {}, el('b', {}, 'Please check this one. '), d.meta.note || 'The AI was not fully sure of this fact.')) : null,
        el('div', { class: 'aiw-card-f' }, el('span', { class: 'faint' }, 'Replace with:'), AI_REPLACE.map(([v, l]) => el('button', { class: 'btn ghost sm', type: 'button', disabled: d.busy || busy, onclick: () => replace(d, v) }, l))));
      return wrap;
    };
    root.replaceChildren(...[
      el('div', { class: 'aiw-top' },
        el('div', {}, el('h3', { style: { margin: 0 } }, single ? 'Your draft' : `${drafts.length} draft${drafts.length === 1 ? '' : 's'} ready to review`),
          el('div', { class: 'faint', style: { fontSize: '.82rem' } }, `${providerLine}${discarded ? ` · ${discarded} discarded by safety checks` : ''}`)),
        flagged ? el('span', { class: 'chip warn' }, icon('warn'), `${flagged} to double-check`) : el('span', { class: 'chip good' }, icon('check'), 'No doubts raised'),
        el('span', { class: 'grow' }),
        single ? null : el('div', { class: 'row', style: { gap: '.4rem', flexWrap: 'wrap' } },
          el('button', { class: 'btn sm', type: 'button', onclick: () => { drafts.forEach(d => { d.on = true; }); showReview(); } }, 'Select all'),
          el('button', { class: 'btn sm', type: 'button', onclick: () => { drafts.forEach(d => { d.on = false; }); showReview(); } }, 'None'))),
      note ? el('div', { class: 'ai-note aiw-err', role: 'alert' }, icon('warn'), el('div', {}, note)) : null,
      el('div', { class: 'ai-note' }, icon('warn'), el('div', {}, el('b', {}, 'Drafted by AI. '), 'Check every fact and answer before you use a question. You can edit them after adding.')),
      el('div', { class: 'aiw-cards' }, drafts.map(card)),
      el('div', { class: 'row', style: { gap: '.5rem', flexWrap: 'wrap' } },
        single ? null : el('button', { class: 'btn', type: 'button', disabled: busy || drafts.length >= opts.max_questions * 3, onclick: more }, icon('plus'), busy ? 'Drafting…' : 'Draft more like these'),
        el('button', { class: 'btn ghost', type: 'button', onclick: () => showForm() }, 'Change the brief')),
      el('details', { class: 'aiw-more' }, el('summary', {}, 'What was sent to the AI'),
        el('p', { class: 'faint', style: { margin: '.3rem 0' } }, `Prompt version ${lastVersion}. Your topic, choices and the questions already in this quiz are sent. No player names, emails or answers ever are. Admins can see this in the audit log.`),
        el('pre', { class: 'ai-raw' }, lastBrief))].filter(Boolean));
    cancel.textContent = 'Cancel'; cancel.onclick = () => m.close();
    refreshAction();
  }

  async function replace(d, change) {
    d.busy = true; busy = true; showReview();
    try {
      const r = await api('/api/ai/generate', { method: 'POST', body: { ...requestBody({ existing: [...(f.use_context ? quizTexts : []), ...drafts.filter(x => x !== d).map(x => plainText(x.q.text))] }), count: 1,
        refine: { text: plainText(d.q.text), change } } });
      opts.remaining_today = Math.max(0, opts.remaining_today - 1);
      Object.assign(d, { q: r.questions[0], meta: (r.meta || [])[0] || { confidence: 'sure', note: '' }, audit_id: r.audit_id, on: true });
      lastBrief = r.brief; busy = false; d.busy = false; if (!closed) showReview();
    } catch (e) { busy = false; d.busy = false; if (!closed) showReview(e.message); }
  }

  async function more() {
    busy = true; const keep = drafts; showReview(); refreshAction();
    try {
      const r = await api('/api/ai/generate', { method: 'POST', body: requestBody({ count: Math.min(5, opts.max_questions) }) });
      opts.remaining_today = Math.max(0, opts.remaining_today - 1);
      drafts = keep.concat(r.questions.map((q, i) => ({ q, meta: (r.meta || [])[i] || { confidence: 'sure', note: '' }, audit_id: r.audit_id, on: true, busy: false })));
      lastBrief = r.brief; busy = false; if (!closed) showReview();
    } catch (e) { busy = false; if (!closed) showReview(e.message); }
  }

  function finish() {
    const picked = chosen();
    if (!picked.length) return;
    onAdd(picked.map(d => d.q));
    const byAudit = {};
    picked.forEach(d => { byAudit[d.audit_id] = (byAudit[d.audit_id] || 0) + 1; });
    Object.entries(byAudit).forEach(([id, n]) => api('/api/ai/accept', { method: 'POST', body: { audit_id: Number(id), count: n } }).catch(() => {}));
    m.close();
    ui.toast(single ? 'AI draft is in the form. Check it, then add it to the game.' : `Added ${picked.length} AI-drafted question${picked.length > 1 ? 's' : ''}. Review them before your game.`, 'success');
  }
  showForm();
}
/* ---------------------------------------------------------------- host: one AI picture for one question */
/** Question kinds that can have a generated picture. Pin and jigsaw are left out: the picture IS the puzzle and must be real. */
const AI_PICTURE_KINDS = ['mcq', 'multi', 'tf', 'poll', 'text', 'number', 'order', 'match', 'buzzer', 'slide'];
const aiPictureOk = kind => AI_PICTURE_KINDS.includes(kind);

/** aiImageDialog({ question, kind, onUse(url) }): suggest a scene, draw it, preview it. Nothing is stored until the host presses Use. */
async function aiImageDialog({ question, kind, onUse, purpose = 'question', contextText = null, title = 'Create a picture with AI' }) {
  let opts;
  try { opts = await api('/api/ai/options'); } catch (e) { return ui.toast(e.message, 'error'); }
  const img = opts.images || {};
  if (!img.available) return aiPicturesOff(img);
  const mem = (() => { try { return JSON.parse(localStorage.getItem(AI_MEMORY) || '{}') || {}; } catch (e) { return {}; } })();
  const f = { description: '', style: (() => { try { return localStorage.getItem('dqb:ai:style') || 'flat'; } catch (e) { return 'flat'; } })(), age_group: mem.age_group || 'all' };
  if (!img.styles[f.style]) f.style = Object.keys(img.styles)[0];
  const text = contextText != null ? contextText : plainText(question.text || '').trim();
  let result = null, busy = false, closed = false, left = img.remaining_today;
  const root = el('div', { class: 'aiw' });
  const cancel = el('button', { class: 'btn', type: 'button', onclick: () => m.close() }, 'Cancel');
  const primary = el('button', { class: 'btn primary', type: 'button' });
  const m = ui.modal({ title, wide: true, body: root, actions: [cancel, primary], onClose: () => { closed = true; } });
  const setPrimary = (iconName, label, handler, disabled = false) => { primary.replaceChildren(icon(iconName), label); primary.onclick = handler; primary.disabled = disabled; };
  let advice = null, thinking = false, error = '';

  function paint() {
    const desc = el('textarea', { class: 'textarea', id: 'aipic-desc', rows: 3, maxLength: 300, value: f.description, disabled: busy || thinking, 'aria-describedby': 'aipic-hint',
      placeholder: 'e.g. A friendly telescope pointing at a starry night sky', oninput: e => { f.description = e.target.value; count.textContent = `${f.description.length}/300`; update(); } });
    const count = el('span', { class: 'faint num' }, `${f.description.length}/300`);
    const styleBar = el('div', { class: 'chips-pick', role: 'radiogroup', 'aria-label': 'Picture style' }, Object.entries(img.styles).map(([v, l]) => el('button', { type: 'button', role: 'radio', 'aria-pressed': String(f.style === v), disabled: busy, onclick: e => {
      f.style = v; try { localStorage.setItem('dqb:ai:style', v); } catch (x) { /* fine */ } e.currentTarget.parentNode.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === e.currentTarget))); } }, l)));
    const age = el('select', { class: 'select', id: 'aipic-age', 'aria-label': 'Audience age', disabled: busy, onchange: e => { f.age_group = e.target.value; } },
      Object.entries(opts.age_groups).map(([v, l]) => el('option', { value: v, selected: v === f.age_group }, l)));
    const preview = el('div', { class: 'aipic-prev' + (purpose === 'cover' || purpose === 'background' ? ' wide' : '') + (result ? ' has' : ''), 'aria-live': 'polite' },
      busy ? el('div', { class: 'aipic-wait', role: 'status' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), el('b', {}, 'Drawing…'), el('span', { class: 'faint' }, 'This usually takes 5 to 30 seconds.'))
        : result ? el('img', { src: `data:${result.mime};base64,${result.image}`, alt: `AI picture: ${f.description}` })
        : el('div', { class: 'aipic-empty faint' }, icon('sparkle'), el('span', {}, 'Your picture will appear here')));
    root.replaceChildren(...[
      el('p', { class: 'muted aiw-lead' }, `${purpose === 'cover' ? 'A cover picture for this quiz.' : purpose === 'background' ? 'A calm background for the projector screen.' : purpose === 'explanation' ? 'A picture for the explanation of this question.' : 'One picture for this question.'} The AI draws what you describe; nothing is added until you press Use.`),
      error ? el('div', { class: 'ai-note aiw-err', role: 'alert' }, icon('warn'), el('div', {}, error)) : null,
      thinking ? el('div', { class: 'ai-note' }, el('span', { class: 'spinner', style: { width: '18px', height: '18px' }, 'aria-hidden': 'true' }), el('div', {}, 'Thinking of a picture for your question…')) : null,
      advice && !advice.suitable ? el('div', { class: 'ai-note' }, icon('warn'), el('div', {}, el('b', {}, 'Better to use a real picture. '), advice.reason || 'A drawn picture could show this wrongly.',
        ' AI pictures can get real things wrong, such as flags, maps and landmarks. An illustration that only sets the scene is fine.')) : null,
      el('div', { class: 'aipic-grid' },
        el('div', { class: 'col', style: { gap: '.9rem' } },
          el('div', { class: 'field' }, el('label', { for: 'aipic-desc' }, 'What should the picture show?'), desc,
            el('div', { class: 'row', style: { justifyContent: 'space-between' } }, el('span', { class: 'hint', id: 'aipic-hint' }, 'A simple scene. Do not show or name the answer, and do not ask for words or real people in it.'), count),
            text && img.can_suggest ? el('button', { class: 'btn ghost sm', type: 'button', style: { alignSelf: 'flex-start' }, disabled: busy || thinking, onclick: () => suggest(true) }, icon('sparkle'), 'Suggest an idea') : null),
          el('div', { class: 'field' }, el('label', {}, 'Style'), styleBar),
          el('div', { class: 'field' }, el('label', { for: 'aipic-age' }, 'Audience'), age)),
        preview),
      result ? el('div', { class: 'faint', style: { fontSize: '.82rem' } }, `${result.provider} · ${result.model} · ${left} picture${left === 1 ? '' : 's'} left today. Check it shows nothing wrong before you use it.`)
        : el('div', { class: 'faint', style: { fontSize: '.82rem' } }, `${left} picture${left === 1 ? '' : 's'} left today.`)].filter(Boolean));
    update();
    root.onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); draw(); } };
  }
  function update() {
    const ok = f.description.trim().length >= 5 && !busy && !thinking && left > 0;
    if (result && !busy) {
      cancel.textContent = 'Cancel'; cancel.onclick = () => m.close();
      setPrimary('check', 'Use this picture', use, false);
      if (!primary.nextSibling) primary.after(retry);
      retry.disabled = !ok;
    } else { retry.remove(); setPrimary('sparkle', busy ? 'Drawing…' : 'Create picture', draw, !ok); }
  }
  const retry = el('button', { class: 'btn', type: 'button', onclick: () => draw() }, icon('refresh'), 'Try again');

  async function suggest(manual) {
    if (!text || !img.can_suggest) return;
    thinking = true; error = ''; paint();
    try {
      const r = await api('/api/ai/image/idea', { method: 'POST', body: { question: text, question_type: kind, age_group: f.age_group, purpose } });
      advice = r; if (r.description && (manual || !f.description)) f.description = r.description;
    } catch (e) { if (!manual) error = ''; else error = e.message; }
    thinking = false; if (!closed) { paint(); document.querySelector('#aipic-desc')?.focus(); }
  }
  async function draw() {
    if (busy || f.description.trim().length < 5) return;
    busy = true; error = ''; paint();
    try {
      result = await api('/api/ai/image', { method: 'POST', body: { description: f.description, style: f.style, age_group: f.age_group, question_type: kind, purpose } });
      left = Math.max(0, left - 1);
    } catch (e) { error = e.message; result = null; }
    busy = false; if (!closed) paint();
  }
  async function use() {
    if (!result) return;
    primary.disabled = true; primary.replaceChildren(el('span', { class: 'spinner', style: { width: '16px', height: '16px' }, 'aria-hidden': 'true' }), 'Saving…');
    try {
      const bytes = Uint8Array.from(atob(result.image), c => c.charCodeAt(0));
      const url = await uploadImage(new File([bytes], 'ai-picture.' + (result.mime === 'image/jpeg' ? 'jpg' : result.mime === 'image/webp' ? 'webp' : 'png'), { type: result.mime }));
      onUse(url);
      api('/api/ai/accept', { method: 'POST', body: { audit_id: result.audit_id, count: 1 } }).catch(() => {});
      m.close(); ui.toast(purpose === 'cover' ? 'AI cover added. Check it looks right.' : purpose === 'background' ? 'AI background added. Check how it looks on the big screen.' : 'AI picture added. Check it looks right before your game.', 'success');
    } catch (e) { error = e.message; paint(); }
  }
  paint();
  if (text && img.can_suggest) suggest(false); else setTimeout(() => document.querySelector('#aipic-desc')?.focus(), 30);
}

/** The picture-with-AI button for the question form, enabled only for question kinds that can use one. */
function aiPictureButton(q, kind, onUse) {
  if (!aiPictureOk(kind)) {
    return el('div', { class: 'row', style: { gap: '.5rem', alignItems: 'center', marginTop: '.5rem' } },
      el('button', { class: 'btn ai-btn sm', type: 'button', disabled: true, title: 'This question type needs a real picture' }, icon('sparkle'), 'Create a picture with AI'),
      el('span', { class: 'hint' }, 'Not available here: the picture has to be real, so upload one.'));
  }
  return el('div', { class: 'row', style: { gap: '.5rem', alignItems: 'center', marginTop: '.5rem' } },
    el('button', { class: 'btn ai-btn sm', type: 'button', onclick: () => aiImageDialog({ question: q, kind, onUse }) }, icon('sparkle'), q.image ? 'Replace with an AI picture' : 'Create a picture with AI'),
    el('span', { class: 'hint' }, 'One illustration for this question. You review it before it is added.'));
}

/* ---------------------------------------------------------------- Super Admin: AI pictures */
function picturesAdminCard(d, save, saveProvider) {
  const s = d.settings;
  const capable = d.providers.filter(p => p.image_capable);
  const ready = capable.filter(p => p.has_key && p.enabled);
  const on = el('input', { type: 'checkbox', checked: s.images_enabled, role: 'switch', 'aria-label': 'Allow AI pictures', onchange: e => {
    if (e.target.checked && !ready.length) { e.target.checked = false; return ui.toast('Add and enable Google Gemini, Cloudflare or a custom service with a key first.', 'error'); }
    save({ images_enabled: e.target.checked }, e.target.checked ? 'AI pictures are on' : 'AI pictures are off'); } });
  const active = el('select', { class: 'select', 'aria-label': 'Service that draws first', style: { maxWidth: '18rem' } },
    el('option', { value: '' }, ready.length ? 'Choose a service' : 'No service ready'), ready.map(p => el('option', { value: p.name, selected: s.images_provider === p.name }, p.label)));
  const num = (k, label, hint) => { const i = el('input', { class: 'input', type: 'number', value: s[k], 'aria-label': label, style: { maxWidth: '8rem' } }); return { k, i, f: el('div', { class: 'field' }, el('label', {}, label), i, hint ? el('span', { class: 'hint' }, hint) : null) }; };
  const fields = [num('images_per_user_day', 'Pictures per host, per day'), num('images_per_minute', 'Pictures per host, per minute'), num('images_global_day', 'Pictures for everyone, per day', 'Keeps the free allowance from running out')];
  const modelRow = p => {
    const input = el('input', { class: 'input', value: p.image_model, placeholder: p.image_model_default || 'picture model name', 'aria-label': `${p.label} picture model`, maxLength: 100, list: `imgmodels-${p.name}`, autocomplete: 'off' });
    const list = el('datalist', { id: `imgmodels-${p.name}` });
    const load = el('button', { class: 'btn sm', type: 'button', disabled: !p.has_key, onclick: async () => {
      load.disabled = true; load.textContent = 'Loading…';
      try { const r = await api(`/api/admin/ai/providers/${p.name}/models?purpose=image`); list.replaceChildren(...r.models.map(x => el('option', { value: x }))); ui.toast(`${r.models.length} picture models found. Click the box to choose.`, 'success'); input.focus(); input.select(); }
      catch (e) { ui.toast(e.message, 'error'); }
      load.disabled = !p.has_key; load.textContent = 'Load model list';
    } }, 'Load model list');
    return el('div', { class: 'field' }, el('label', {}, `${p.label}: picture model`), el('div', { class: 'row nowrap' }, input, list, load,
      el('button', { class: 'btn primary sm', type: 'button', onclick: () => saveProvider(p.name, { image_model: input.value }, 'Picture model saved') }, 'Save')),
      p.name === 'custom' ? el('span', { class: 'hint' }, 'Needs the service address above and a model name. It must follow the OpenAI images API and return the picture as base64.') : null,
      !p.has_key ? el('span', { class: 'hint' }, 'Save a key for this service in the list above first.') : null);
  };
  return el('div', { class: 'card' },
    el('div', { class: 'row nowrap' }, el('div', { class: 'grow' }, el('h3', { style: { margin: 0 } }, 'AI pictures'),
      el('div', { class: 'faint' }, 'Hosts can create one illustration for a question. It is drawn only when they ask, shown as a preview, and added only when they choose. Off until you switch it on.')),
      el('label', { class: 'check ai-master' }, on, el('b', {}, s.images_enabled ? 'On' : 'Off'))),
    el('div', { class: 'ai-grid' }, el('div', { class: 'field' }, el('label', {}, 'Service that draws first'), active)),
    capable.some(p => p.has_key) ? el('div', { class: 'col', style: { gap: '.6rem' } }, capable.filter(p => p.has_key).map(modelRow)) : el('p', { class: 'muted' }, 'Pictures use the same keys as the services above. Add a key for Google Gemini or Cloudflare, or a custom service, then choose it here.'),
    el('div', { class: 'ai-grid' }, fields.map(x => x.f)),
    el('div', { class: 'row' }, el('button', { class: 'btn primary', type: 'button', onclick: () => { const b = { images_provider: active.value }; fields.forEach(x => { b[x.k] = x.i.value; }); save(b, 'Picture settings saved'); } }, 'Save picture settings')),
    el('details', { class: 'ai-always' }, el('summary', {}, 'Always on, cannot be switched off'),
      el('ul', {}, ['Pictures are not offered for pin and jigsaw questions, where the picture has to be real.',
        'Every description is checked like a topic and wrapped in fixed rules: no text, logos, real people, weapons or anything frightening. Younger audiences also get the stricter word list.',
        'Only the description is sent. No player names, emails or answers are ever sent.',
        'A host sees the picture as a preview and nothing is stored until they press Use. Every attempt is recorded in the audit log below with the exact prompt.',
        'Free services such as Google’s may use prompts and pictures to improve their products. Check each provider’s terms, including who owns the pictures.'].map(t => el('li', {}, t)))));
}

/* ---------------------------------------------------------------- host: pictures for the answer options */
const AI_OPTION_KINDS = ['mcq', 'multi', 'poll', 'order', 'match'];

function aiPicturesOff(img) {
  const admin = typeof isAdmin === 'function' && isAdmin();
  const m = ui.modal({ title: 'Create pictures with AI', body: el('div', { class: 'col' },
    el('p', {}, !img.enabled ? 'AI pictures are switched off.' : 'No picture service is set up yet.'),
    el('p', { class: 'muted' }, admin ? 'Open AI settings, add a service that can draw (Google Gemini or Cloudflare), and switch pictures on.' : 'Ask a Super Admin to turn it on under AI settings. You can upload your own pictures instead.')),
    actions: admin ? [el('button', { class: 'btn', type: 'button', onclick: () => m.close() }, 'Close'),
      el('button', { class: 'btn primary', type: 'button', onclick: () => { m.close(); go('#/ai'); } }, 'Open AI settings')]
      : [el('button', { class: 'btn primary', type: 'button', onclick: () => m.close() }, 'Close')] });
}

/** aiOptionPicturesDialog({ question, kind, onApply }): draw one picture per answer option, in one style, and keep the ones the host likes. */
async function aiOptionPicturesDialog({ question, kind, onApply }) {
  let opts;
  try { opts = await api('/api/ai/options'); } catch (e) { return ui.toast(e.message, 'error'); }
  const img = opts.images || {};
  if (!img.available) return aiPicturesOff(img);
  const rows = question.options.map(o => ({ o, desc: plainText(o.text || '').trim().slice(0, 120) })).filter(r => r.desc).slice(0, 6)
    .map(r => ({ ...r, state: 'idle', result: null, error: '', keep: true }));
  if (rows.length < 2) return ui.toast('Write at least two options first, then ask for pictures.', 'error');
  const mem = (() => { try { return JSON.parse(localStorage.getItem(AI_MEMORY) || '{}') || {}; } catch (e) { return {}; } })();
  const f = { style: (() => { try { return localStorage.getItem('dqb:ai:style') || 'flat'; } catch (e) { return 'flat'; } })(), age_group: mem.age_group || 'all' };
  if (!img.styles[f.style]) f.style = Object.keys(img.styles)[0];
  let left = img.remaining_today, running = false, stop = false, closed = false, error = '', progress = '';
  const root = el('div', { class: 'aiw' });
  const cancel = el('button', { class: 'btn', type: 'button', onclick: () => m.close() }, 'Cancel');
  const more = el('button', { class: 'btn', type: 'button', onclick: () => drawAll() }, icon('sparkle'), 'Draw the rest');
  const primary = el('button', { class: 'btn primary', type: 'button' });
  const m = ui.modal({ title: 'Create pictures for the options with AI', wide: true, body: root, actions: [cancel, primary], onClose: () => { closed = true; stop = true; } });
  const done = () => rows.filter(r => r.result);
  const todo = () => rows.filter(r => !r.result && r.desc.trim().length >= 5);

  function card(r, i) {
    const thumb = r.state === 'busy' ? el('div', { class: 'aiopt-thumb' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }))
      : r.result ? el('div', { class: 'aiopt-thumb has' }, el('img', { src: `data:${r.result.mime};base64,${r.result.image}`, alt: `AI picture for option ${i + 1}: ${r.desc}` }))
      : r.o.image ? el('div', { class: 'aiopt-thumb has' }, el('img', { src: r.o.image, alt: 'Current picture' }), el('span', { class: 'aiopt-tag' }, 'Current'))
      : el('div', { class: 'aiopt-thumb faint' }, icon('sparkle'));
    const input = el('input', { class: 'input', value: r.desc, maxLength: 120, disabled: running, 'aria-label': `What picture ${i + 1} should show`, oninput: e => { r.desc = e.target.value; refreshAction(); } });
    return el('article', { class: 'aiopt' + (r.result && !r.keep ? ' off' : '') },
      thumb, input,
      r.error ? el('div', { class: 'error-text', role: 'alert', style: { fontSize: '.8rem' } }, r.error) : null,
      el('div', { class: 'row', style: { gap: '.4rem', justifyContent: 'space-between' } },
        r.result ? el('label', { class: 'check', style: { margin: 0, padding: 0 } }, el('input', { type: 'checkbox', checked: r.keep, 'aria-label': `Use picture ${i + 1}`, onchange: e => { r.keep = e.target.checked; paint(); } }), 'Use') : el('span'),
        el('button', { class: 'btn ghost sm', type: 'button', disabled: running || left <= 0 || r.desc.trim().length < 5, onclick: () => redraw(r) }, icon('refresh'), r.result ? 'Redraw' : 'Draw')));
  }
  function paint() {
    const styleBar = el('div', { class: 'chips-pick', role: 'radiogroup', 'aria-label': 'Picture style' }, Object.entries(img.styles).map(([v, l]) => el('button', { type: 'button', role: 'radio', 'aria-pressed': String(f.style === v), disabled: running, onclick: e => {
      f.style = v; try { localStorage.setItem('dqb:ai:style', v); } catch (x) { /* fine */ } e.currentTarget.parentNode.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === e.currentTarget))); } }, l)));
    const age = el('select', { class: 'select', 'aria-label': 'Audience age', disabled: running, onchange: e => { f.age_group = e.target.value; } },
      Object.entries(opts.age_groups).map(([v, l]) => el('option', { value: v, selected: v === f.age_group }, l)));
    root.replaceChildren(...[
      el('p', { class: 'muted aiw-lead' }, 'One picture for each answer, all drawn in the same style so they look like a set. Edit what each should show, draw them, and keep the ones you like. Nothing is added until you press Use.'),
      error ? el('div', { class: 'ai-note aiw-err', role: 'alert' }, icon('warn'), el('div', {}, error)) : null,
      el('div', { class: 'aiw-row' }, el('div', { class: 'field' }, el('label', {}, 'Style (shared by all)'), styleBar), el('div', { class: 'field' }, el('label', {}, 'Audience'), age)),
      el('div', { class: 'aiopt-grid' }, rows.map(card)),
      el('div', { class: 'faint', style: { fontSize: '.82rem' }, role: 'status', 'aria-live': 'polite' }, progress || `${left} picture${left === 1 ? '' : 's'} left today. Each one counts as a request. Check them before your game: an AI can draw real things wrongly.`)].filter(Boolean));
    refreshAction();
  }
  function refreshAction() {
    more.remove();
    const n = done().filter(r => r.keep).length, rest = todo().length;
    if (running) { primary.replaceChildren(icon('sparkle'), 'Drawing…'); primary.disabled = true; primary.onclick = null; return; }
    if (n) { primary.replaceChildren(icon('check'), `Use ${n} picture${n === 1 ? '' : 's'}`); primary.disabled = false; primary.onclick = apply; if (rest && left > 0) primary.before(more); }
    else { primary.replaceChildren(icon('sparkle'), `Draw ${rest} picture${rest === 1 ? '' : 's'}`); primary.disabled = !rest || left <= 0; primary.onclick = drawAll; }
  }
  async function drawOne(r) {
    r.state = 'busy'; r.error = ''; paint();
    try {
      r.result = await api('/api/ai/image', { method: 'POST', body: { description: r.desc, purpose: 'option', question_type: kind, style: f.style, age_group: f.age_group } });
      r.state = 'done'; r.keep = true; left = Math.max(0, left - 1);
    } catch (e) { r.state = 'error'; r.error = e.message; if (/too quickly|AI pictures for today|allowance|switched off/.test(e.message)) { stop = true; error = e.message; } }
  }
  async function drawAll() {
    running = true; stop = false; error = '';
    const list = todo(); let n = 0;
    for (const r of list) {
      if (closed || stop || left <= 0) break;
      progress = `Drawing picture ${++n} of ${list.length}…`;
      await drawOne(r);
    }
    running = false; progress = ''; if (!closed) paint();
  }
  async function redraw(r) { running = true; error = ''; progress = 'Drawing…'; r.result = null; await drawOne(r); running = false; progress = ''; if (!closed) paint(); }
  async function apply() {
    const chosen = done().filter(r => r.keep);
    primary.disabled = true; primary.replaceChildren(el('span', { class: 'spinner', style: { width: '16px', height: '16px' }, 'aria-hidden': 'true' }), 'Saving…');
    let applied = 0;
    try {
      for (const r of chosen) {
        const bytes = Uint8Array.from(atob(r.result.image), c => c.charCodeAt(0));
        r.o.image = await uploadImage(new File([bytes], 'ai-option.' + (r.result.mime === 'image/jpeg' ? 'jpg' : r.result.mime === 'image/webp' ? 'webp' : 'png'), { type: r.result.mime }), 560);
        applied++;
        api('/api/ai/accept', { method: 'POST', body: { audit_id: r.result.audit_id, count: 1 } }).catch(() => {});
      }
      m.close(); ui.toast(`${applied} AI picture${applied === 1 ? '' : 's'} added. Check them before your game.`, 'success');
    } catch (e) { error = e.message; paint(); }
    finally { if (applied) onApply(); }
  }
  paint();
}

/* ---------------------------------------------------------------- buttons used by the editor */
const aiMiniButton = (label, onclick, extra = {}) => el('button', { class: 'btn ai-btn sm', type: 'button', onclick, ...extra }, icon('sparkle'), label);

/** Under the explanation: a picture for the explanation. */
function aiExplainButton(q, kind, onUse) {
  if (!aiPictureOk(kind)) return null;
  return aiMiniButton('Create with AI', () => aiImageDialog({ question: q, kind, purpose: 'explanation', title: 'Create an explanation picture with AI',
    contextText: plainText(q.explanation || '').trim() || plainText(q.text || '').trim(), onUse }));
}

/** Above the answer options: one picture per option. */
function aiOptionsButton(q, kind, onApply) {
  if (!AI_OPTION_KINDS.includes(kind)) return null;
  return aiMiniButton('Create pictures for the options with AI', () => aiOptionPicturesDialog({ question: q, kind, onApply }), { title: 'One picture for each answer, drawn in the same style' });
}

/** In the editor rail: a calm picture for the projector background. */
function aiBackgroundButton(quiz, onUse) {
  return aiMiniButton(quiz.screen_bg ? 'Replace with an AI background' : 'Create a background with AI', () => aiImageDialog({ question: {}, kind: 'background', purpose: 'background', title: 'Create a projector background with AI',
    contextText: [quiz.title, quiz.description].map(t => String(t || '').trim()).filter(Boolean).join('. '), onUse }));
}

/** In the editor rail: a cover picture for the whole quiz. */
function aiCoverButton(quiz, onUse) {
  return aiMiniButton(quiz.cover ? 'Replace with an AI cover' : 'Create a cover with AI', () => aiImageDialog({ question: {}, kind: 'cover', purpose: 'cover', title: 'Create a cover picture with AI',
    contextText: [quiz.title, quiz.description].map(t => String(t || '').trim()).filter(Boolean).join('. '), onUse }));
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
      if (r.action === 'image') return [el('b', {}, p.description || '(no description)'), el('div', { class: 'faint' }, [p.purpose, p.question_type, p.style, p.age_group].filter(Boolean).join(' · '))];
      if (r.action === 'image_idea') return [el('b', {}, p.question || '(no question)'), el('div', { class: 'faint' }, r.message || '')];
      return [el('span', {}, r.message || '')];
    };
    const rows = logRows.map(r => el('tr', { class: 'clickable', tabIndex: 0, onclick: () => detail(r.id), onkeydown: e => { if (e.key === 'Enter') detail(r.id); } },
      el('td', { class: 'muted nowrap' }, timeAgo(r.ts)), el('td', {}, r.username || '—'), el('td', {}, { generate: 'Drafting', image: 'Picture', image_idea: 'Picture idea', test: 'Key test', models: 'Model list', config: 'Settings' }[r.action] || r.action),
      el('td', {}, aiChip(r.status)), el('td', {}, summary(r)), el('td', {}, r.provider ? `${r.provider}` : '—'),
      el('td', { class: 'num' }, r.action === 'generate' ? `${r.returned}/${r.requested} · ${r.accepted} added` : r.action === 'image' ? (r.returned ? (r.accepted ? 'drawn · used' : 'drawn') : '') : ''), el('td', { class: 'num' }, r.tokens_in + r.tokens_out ? (r.tokens_in + r.tokens_out).toLocaleString() : ''),
      el('td', { class: 'num' }, r.latency_ms ? (r.latency_ms / 1000).toFixed(1) + ' s' : '')));
    logBox.replaceChildren(...[
      el('div', { class: 'row', style: { gap: '.5rem', flexWrap: 'wrap', marginBottom: '.6rem' } },
        sel('status', 'Any result', Object.entries(AI_STATUS).map(([v, [, l]]) => [v, l])), sel('action', 'Any action', [['generate', 'Drafting'], ['image', 'Picture'], ['image_idea', 'Picture idea'], ['test', 'Key test'], ['models', 'Model list'], ['config', 'Settings']]), userIn,
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
    const p = r.params || {}, f = r.flags || {}, q = r.request || {}, c = q.context || {};
    const copy = (text, what) => (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(() => ui.toast(`${what} copied`, 'success'), () => ui.toast('Could not copy. Select the text instead.', 'error'));
    const block = (title, text, what) => el('div', { class: 'field' }, el('div', { class: 'row nowrap' }, el('label', { class: 'grow' }, title), el('button', { class: 'btn sm', type: 'button', onclick: () => copy(text, what) }, icon('copy'), 'Copy')), el('pre', { class: 'ai-raw tall' }, text));
    const tabs = {
      Overview: () => [
        kv('Time', new Date(r.ts * 1000).toLocaleString()), kv('User', r.username), kv('Action', r.action), el('div', { class: 'ai-kv' }, el('span', { class: 'faint' }, 'Result'), aiChip(r.status)),
        kv('Service', r.provider), kv('Model', r.model), kv('From address', r.ip), kv('Prompt version', q.prompt_version || q.template_version),
        kv('Topic', p.topic), kv('Picture description', p.description), kv('Picture for', p.purpose), kv('Picture style', p.style), kv('Question asked about', p.question), kv('Question kind', r.action.startsWith('image') ? p.question_type : ''), kv('Focus text', p.notes), kv('Quiz type', p.quiz_type), kv('Question type', p.question_type), kv('Difficulty', p.difficulty), kv('Level', p.level), kv('Age group', p.age_group), kv('Tone', p.tone), kv('Language', p.language),
        kv('Explanations', p.explanations === false ? 'Off' : p.explanations ? 'On' : ''), kv('Replacing a draft', p.refine ? `${p.refine.change}: “${p.refine.text}”` : ''),
        kv('Quiz title used', c.quiz_title), kv('Existing questions sent', c.existing_sent),
        r.action.startsWith('image') ? null : kv('Asked for', r.requested), r.action.startsWith('image') ? kv('Picture size', f.bytes ? `${Math.round(f.bytes / 1024)} KB ${f.mime || ''}` : '') : kv('Safe questions returned', r.returned), r.action === 'image' ? kv('Fingerprint (SHA-256)', f.sha256) : null, r.action === 'image' ? kv('Used by the host', r.accepted ? 'Yes' : 'No') : kv('Added by the host', r.accepted), kv('Tokens in / out', `${r.tokens_in} / ${r.tokens_out}`), kv('Time taken', r.latency_ms ? r.latency_ms + ' ms' : ''),
        kv('Note', r.message), f.dropped && f.dropped.length ? kv('Discarded by safety checks', f.dropped.join('; ')) : null, kv('Service declined because', f.refusal)],
      Prompt: () => q.user ? [
        el('p', { class: 'muted', style: { margin: 0 } }, `Exactly what was sent. Version ${q.prompt_version || q.template_version}${q.temperature != null ? `, temperature ${q.temperature}, up to ${q.max_tokens} tokens back` : ''}.`),
        block(r.action === 'image' ? 'Prompt sent to the picture service (description inside the fixed rules)' : 'Brief (the message that changes with every request)', q.user, 'Brief'),
        q.system ? block('System prompt (fixed instructions, stored once)', q.system, 'System prompt') : null].filter(Boolean)
        : [el('p', { class: 'muted' }, r.action === 'generate' ? 'No prompt was sent for this entry (it was stopped by a check before reaching the AI, or it was recorded before prompts were kept).' : 'This action does not send a prompt.')],
      Reply: () => r.response ? [block('Raw reply from the service (before safety checks)', r.response, 'Reply')] : [el('p', { class: 'muted' }, 'No reply was received.')],
      Drafts: () => (f.kept_questions && f.kept_questions.length) || (f.dropped && f.dropped.length) ? [
        ...(f.kept_questions || []).map((k, i) => el('div', { class: 'ai-kv' }, el('span', { class: 'faint' }, `Kept ${i + 1}`), el('span', {}, k.text, ' ', el('span', { class: 'chip' }, k.type), ' ', k.confidence === 'check' ? el('span', { class: 'chip warn' }, 'Marked “check”') : null))),
        ...(f.dropped || []).map((d, i) => el('div', { class: 'ai-kv' }, el('span', { class: 'faint' }, `Discarded ${i + 1}`), el('span', {}, d)))] : [el('p', { class: 'muted' }, 'No drafts were kept or discarded.')],
      Attempts: () => (q.attempts && q.attempts.length) ? [el('div', { class: 'tbl-wrap' }, el('table', { class: 'tbl' }, el('thead', {}, el('tr', {}, ['Service', 'Model', 'Result', 'Time', 'Tokens in/out', 'Detail'].map(h => el('th', {}, h)))),
        el('tbody', {}, q.attempts.map(a => el('tr', {}, el('td', {}, a.provider), el('td', { class: 'mono' }, a.model), el('td', {}, aiChip(a.status)), el('td', { class: 'num' }, a.ms + ' ms'), el('td', { class: 'num' }, `${a.tokens_in} / ${a.tokens_out}`), el('td', { class: 'muted' }, a.message || '')))))),
        el('p', { class: 'faint' }, 'With fallback on, each service is tried in order until one answers.')] : [el('p', { class: 'muted' }, 'No service was called.')],
    };
    const names = r.action === 'generate' ? Object.keys(tabs) : r.action.startsWith('image') ? ['Overview', 'Prompt', 'Attempts'] : ['Overview'];
    const panel = el('div', { class: 'col', role: 'tabpanel' });
    const bar = el('div', { class: 'segmented', role: 'tablist', 'aria-label': 'Audit sections', style: { alignSelf: 'flex-start' } });
    let cur = 'Overview';
    const draw = () => {
      bar.replaceChildren(...names.map(n => el('button', { type: 'button', role: 'tab', 'aria-selected': String(n === cur), 'aria-pressed': String(n === cur), onclick: () => { cur = n; draw(); } }, n)));
      panel.replaceChildren(...tabs[cur]().filter(Boolean));
    };
    draw();
    const m = ui.modal({ title: `Audit entry #${r.id}`, wide: true, body: el('div', { class: 'col' }, names.length > 1 ? bar : null, panel),
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
      el('h2', { class: 'ai-h' }, 'AI pictures'), picturesAdminCard(d, save, saveProvider),
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
