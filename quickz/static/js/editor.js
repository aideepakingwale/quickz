'use strict';
/* Quiz editor: question list rail + focused editor + live phone preview, with autosave.
   The question form itself (all types, pictures, scoring) lives in qform.js. */

function quizProblems(quiz) {
  const out = [];
  if (!quiz.title.trim()) out.push({ qi: -1, msgs: ['Give the quiz a title'] });
  quiz.questions.forEach((q, qi) => { const m = questionProblems(q); if (m.length) out.push({ qi, id: q.id, msgs: m }); });
  return out;
}
const cleanForSave = quiz => ({ title: quiz.title.trim(), description: quiz.description, cover: quiz.cover || '', screen_bg: quiz.screen_bg || '', screen_bg_wash: quiz.screen_bg_wash ?? 40, lobby_music: quiz.lobby_music || '', questions: quiz.questions.map(cleanQuestion) });

/** The questions a quiz *loads* with come from the server fully formed; give them the editor-only bits. */
function hydrateQuestion(q) {
  q.rich = true; q.image = q.image || ''; q.image_effect = q.image_effect || 'none'; q.category = q.category || ''; q.explain_image = q.explain_image || '';
  q.options = (q.options || []).map(o => ({ ...o, image: o.image || '' }));
  if (q.type === 'match') q.targets = (q.targets || []).map(o => ({ ...o, image: o.image || '' }));
  if (q.type === 'text') q.fuzzy = q.fuzzy !== false;
  if (q.type === 'number') { const n = q.number || {}; q.number = { answer: n.answer ?? '', tolerance: n.tolerance ?? 0, closest: !!n.closest, spread: n.closest && n.spread != null ? n.spread : '', min: n.min ?? '', max: n.max ?? '', unit: n.unit || '' }; }
  if (q.type === 'pin') q.pin = { x: 50, y: 50, r: 10, ar: 1.5, partial: true, ...(q.pin || {}), placed: !!q.pin };
  if (q.type === 'order' || q.type === 'match') q.partial = q.partial !== false;
  if (q.type === 'jigsaw') q.jigsaw = { rows: 3, cols: 3, hint: 0, partial: false, ...(q.jigsaw || {}) };
  q.place_weights = Array.isArray(q.place_weights) ? q.place_weights : [];
  return q;
}

/* ---------------------------------------------------------------- editor view */
async function renderEditor(id) {
  let quiz = { title: 'Untitled quiz', description: '', cover: '', screen_bg: '', screen_bg_wash: 40, lobby_music: '', questions: [newQuestion('mcq')] };
  let quizId = id === 'new' ? null : id;
  if (quizId) {
    try { quiz = await api('/api/quizzes/' + quizId); quiz.questions.forEach(hydrateQuestion); }
    catch (e) { ui.toast(e.message, 'error'); return go('#/'); }
  }
  let sel = quiz.questions[0] && quiz.questions[0].id;
  let dirty = false, saving = false, pending = false, timer = null, dragId = null;
  const status = { kind: quizId ? 'ok' : 'busy', text: quizId ? 'All changes saved' : 'Not saved yet' };

  const statusEl = el('button', { class: 'save-status btn ghost sm', type: 'button' });
  const lookBtn = el('button', { class: 'btn look-btn', type: 'button', onclick: lookDialog }), lookBtn2 = el('button', { class: 'btn look-btn wide', type: 'button', onclick: lookDialog });
  const title = el('input', { class: 'ed-title', type: 'text', value: quiz.title, maxLength: 120, 'aria-label': 'Quiz title', placeholder: 'Quiz title' });
  const hostBtn = el('button', { class: 'btn primary', type: 'button', onclick: hostNow }, icon('play'), 'Host this quiz');
  const bar = el('div', { class: 'ed-bar' }, el('a', { class: 'crumb', href: '#/' }, icon('left'), 'Library'), title, el('span', { class: 'spacer' }), lookBtn, statusEl, hostBtn);
  const rail = el('aside', { class: 'rail', 'aria-label': 'Questions' });
  const main = el('section', { class: 'col', 'aria-label': 'Question editor' });
  const preview = el('aside', { class: 'preview', 'aria-label': 'Player preview' });
  const body = el('div', { class: 'ed-body' }, rail, main, preview);
  mount(bar, body, siteFooter());
  bar.style.position = 'sticky';

  const cur = () => quiz.questions.find(q => q.id === sel) || quiz.questions[0];
  const idx = () => quiz.questions.findIndex(q => q.id === sel);

  function setStatus(kind, text) {
    status.kind = kind; status.text = text;
    statusEl.className = 'save-status btn ghost sm ' + kind;
    statusEl.replaceChildren(icon(kind === 'ok' ? 'check' : kind === 'err' ? 'warn' : 'refresh'), text);
  }
  function refreshStatus() {
    const probs = quizProblems(quiz);
    hostBtn.disabled = probs.length > 0 || !quiz.questions.length;
    if (!dirty && !saving && quizId) setStatus('ok', 'All changes saved');
    else if (probs.length) setStatus('err', `${probs.length} thing${probs.length > 1 ? 's' : ''} to fix before saving`);
    else if (saving) setStatus('busy', 'Saving…');
    else setStatus('busy', 'Unsaved changes');
  }
  statusEl.addEventListener('click', () => { const p = quizProblems(quiz)[0]; if (p && p.qi >= 0) { sel = p.id; drawAll(); } else if (p) title.focus(); else save(); });

  function touch() { dirty = true; clearTimeout(timer); timer = setTimeout(save, 900); drawRail(); drawPreview(); refreshStatus(); }
  async function save() {
    clearTimeout(timer);
    if (saving) { pending = true; return false; }
    if (quizProblems(quiz).length) { refreshStatus(); return false; }
    saving = true; refreshStatus();
    try {
      const payload = cleanForSave(quiz);
      if (!quizId) { const r = await api('/api/quizzes', { method: 'POST', body: payload }); quizId = String(r.id); history.replaceState(null, '', `#/quiz/${quizId}`); }
      else await api('/api/quizzes/' + quizId, { method: 'PUT', body: payload });
      dirty = false;
    } catch (e) { saving = false; setStatus('err', e.message); return false; }
    saving = false;
    if (pending) { pending = false; if (dirty) return save(); }
    refreshStatus();
    return true;
  }
  async function hostNow() {
    if (dirty || !quizId) { if (!(await save())) return; }
    startDialog({ id: +quizId, title: quiz.title.trim(), question_count: quiz.questions.length, has_music: !!quiz.lobby_music });
  }

  title.addEventListener('input', () => { quiz.title = title.value; touch(); });

  /* rail */
  const desc = el('textarea', { class: 'textarea', rows: 2, maxLength: 1000, placeholder: 'Lobby description (optional)', 'aria-label': 'Quiz description', value: quiz.description, style: { minHeight: '3.2rem', fontSize: '.9rem' } });
  desc.addEventListener('input', () => { quiz.description = desc.value; touch(); });
  const coverBox = el('div', { class: 'cover-field look-body' });
  function drawCover() {
    const setCover = url => { if (url === (quiz.cover || '')) return; quiz.cover = url; touch(); drawCover(); };
    const setBg = url => { if (url === (quiz.screen_bg || '')) return; quiz.screen_bg = url; touch(); drawCover(); };
    refreshLookBtn();
    coverBox.replaceChildren(
      el('label', { class: 'look-h' }, 'Cover picture', el('span', { class: 'faint' }, ' · shown on the quiz card in your library')),
      pictureField({ value: quiz.cover || '', max: 1100, label: 'Drop a cover picture here, or click to browse', hint: `PNG, JPEG or WebP, up to ${window.QUICKZ_MAX_IMAGE_MB || 2} MB.`, onValue: (url, img) => { if (img === undefined) setCover(url); } }),
      typeof aiCoverButton === 'function' ? aiCoverButton(quiz, setCover) : null,
      quiz.cover && quiz.cover !== quiz.screen_bg ? el('button', { class: 'btn sm', type: 'button', style: { alignSelf: 'flex-start' }, onclick: () => { setBg(quiz.cover); ui.toast('The cover is now also the projector background', 'success'); } }, icon('image'), 'Use this as the projector background too') : null,
      el('label', { class: 'look-h' }, 'Projector background', el('span', { class: 'faint' }, ' · behind the big screen')),
      pictureField({ value: quiz.screen_bg || '', max: 1920, label: 'Drop a background picture here, or click to browse', hint: `PNG, JPEG or WebP, up to ${window.QUICKZ_MAX_IMAGE_MB || 2} MB.`, onValue: (url, img) => { if (img === undefined) setBg(url); } }),
      el('span', { class: 'hint' }, `PNG, JPEG, GIF or WebP, up to ${window.QUICKZ_MAX_IMAGE_MB || 2} MB. A wide (16:9) picture fills the whole screen. Any other shape, such as a square or a portrait, is shown whole with a soft blurred copy filling the sides, so nothing important is cut off. An animated GIF keeps moving. Use a picture with calm areas so the words stay readable.`),
      !quiz.screen_bg && quiz.cover ? el('button', { class: 'btn sm', type: 'button', style: { alignSelf: 'flex-start' }, onclick: () => { setBg(quiz.cover); ui.toast('The cover is now also the projector background', 'success'); } }, icon('image'), 'Use the cover picture here') : null,
      typeof aiBackgroundButton === 'function' ? aiBackgroundButton(quiz, setBg) : null,
      quiz.screen_bg ? washPicker() : null,
      el('label', { class: 'look-h' }, 'Waiting-screen music', el('span', { class: 'faint' }, ' · plays until the quiz starts')),
      musicField());
  }
  /** How strongly the background picture shows: a soft wash over it keeps the words readable. */
  function washPicker() {
    const levels = [[60, 'Strong', 'Words come first'], [40, 'Medium', 'A balance'], [20, 'Light', 'The picture comes first'], [0, 'None', 'The picture as it is']];
    const now = levels.reduce((best, l) => Math.abs(l[0] - (quiz.screen_bg_wash ?? 40)) < Math.abs(best[0] - (quiz.screen_bg_wash ?? 40)) ? l : best, levels[1])[0];
    return el('div', { class: 'field', style: { marginTop: '.4rem' } }, el('label', {}, 'Picture strength'),
      el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Picture strength' }, levels.map(([v, l, tip]) => el('button', { type: 'button', role: 'radio', title: tip, 'aria-pressed': String(v === now), onclick: () => { quiz.screen_bg_wash = v; touch(); drawCover(); } }, l))),
      el('span', { class: 'hint' }, 'A soft white wash over the picture keeps the words easy to read. Choose Light or None for a vivid picture, or Strong if the words are hard to read.'));
  }
  /** Music for the projector while players join. It loops on the first screen and stops the moment the quiz starts. */
  function musicField() {
    const box = el('div', { class: 'music-field' });
    const set = url => { quiz.lobby_music = url; touch(); drawCover(); };
    if (quiz.lobby_music) {
      box.append(el('audio', { controls: true, preload: 'none', src: quiz.lobby_music, 'aria-label': 'Waiting-screen music preview', style: { width: '100%' } }),
        el('div', { class: 'row', style: { gap: '.4rem' } }, el('button', { class: 'btn ghost sm', type: 'button', style: { color: 'var(--danger)' }, onclick: () => set('') }, icon('trash'), 'Remove music')));
    } else {
      const file = el('input', { type: 'file', accept: '.mp3,.m4a,.ogg,.wav,audio/mpeg,audio/mp4,audio/x-m4a,audio/ogg,audio/wav', hidden: true });
      const bar = el('i'), prog = el('div', { class: 'upl-prog', hidden: true }, bar);
      const pick = el('button', { class: 'btn sm', type: 'button', onclick: () => file.click() }, icon('upload'), 'Upload music');
      file.addEventListener('change', async () => {
        const f = file.files[0]; file.value = '';
        if (!f) return;
        if (!/\.(mp3|m4a|ogg|wav)$/i.test(f.name)) return ui.toast('Music must be an MP3, M4A, OGG or WAV file.', 'error');
        const musicMB = window.QUICKZ_MAX_MUSIC_MB || 5;
        if (f.size > musicMB * 1048576) return ui.toast(`That file is ${(f.size / 1048576).toFixed(1)} MB. Music can be at most ${musicMB} MB.`, 'error');
        pick.disabled = true; prog.hidden = false; bar.style.width = '0%';
        try {
          const r = await uploadWithProgress(f, p => { bar.style.width = (p * 100) + '%'; }, 'music');
          if (r.kind !== 'audio') throw new Error('Music must be an MP3, M4A, OGG or WAV file.');
          set(r.url);
        } catch (e) { ui.toast(e.message, 'error'); pick.disabled = false; prog.hidden = true; }
      });
      box.append(el('div', { class: 'row', style: { gap: '.5rem', flexWrap: 'wrap' } }, pick, file), prog);
    }
    box.append(el('span', { class: 'hint' }, `MP3, M4A, OGG or WAV, up to ${window.QUICKZ_MAX_MUSIC_MB || 5} MB. Plays on the big screen while players join, then stops when you start the quiz. The projector needs one click on its sound button first (browsers block sound until then). Use music you have the right to play.`));
    return box;
  }
  const lookCount = () => [quiz.cover, quiz.screen_bg, quiz.lobby_music].filter(Boolean).length;
  function refreshLookBtn() {
    const n = lookCount();
    for (const b of [lookBtn, lookBtn2]) b.replaceChildren(...[icon('image'), el('span', {}, b === lookBtn ? 'Cover, background, music' : 'Cover, background and music'), n ? el('span', { class: 'chip good' }, `${n} of 3 set`) : null].filter(Boolean));
  }
  function lookDialog() {
    drawCover();
    const m = ui.modal({ title: 'Cover, background and music', wide: true, body: coverBox, actions: [el('button', { class: 'btn primary', type: 'button', onclick: () => m.close() }, 'Done')] });
  }
  drawCover();
  const realIndex = q => quiz.questions.filter(x => x.type !== 'slide').indexOf(q) + 1;
  function drawRail() {
    const items = quiz.questions.map((q, i) => {
      const probs = questionProblems(q), k = kindOf(q), m = TYPE_META[k];
      const li = el('div', { class: 'ritem' + (q.id === sel ? ' sel' : '') + (k === 'slide' ? ' is-slide' : ''), draggable: true, role: 'button', tabindex: 0, 'aria-label': `Question ${i + 1}`,
        onclick: () => { sel = q.id; drawAll(); }, onkeydown: e => { if (e.key === 'Enter') { sel = q.id; drawAll(); } } },
        el('span', { class: 'grip', 'aria-hidden': 'true' }, icon('grip')), el('span', { class: 'num' }, k === 'slide' ? icon('slide') : realIndex(q)),
        el('div', { style: { flex: 1, minWidth: 0 } }, el('div', { class: 'tx' + (plainText(q.text) ? '' : ' blank') }, plainText(q.text) || (k === 'slide' ? 'Untitled slide' : 'Untitled question')),
          el('div', { class: 'sub' }, el('span', { title: m.desc }, icon(m.icon), m.title), k === 'slide' ? null : el('span', {}, `${q.time_limit}s`),
            SCORED_KINDS.includes(k) ? el('span', {}, q.points ? fmtScore(q.points) : '0 pts') : null,
            q.image ? el('span', { title: 'Has a picture' }, icon('image')) : null,
            q.media && q.media.url ? el('span', { title: MEDIA_LABEL(q.media) }, icon(mediaIcon(q.media))) : null,
            SCORED_KINDS.includes(k) && q.winners ? el('span', { title: q.winners === 1 ? 'Only the first correct answer scores' : `Only the first ${q.winners} correct answers score` }, icon('trophy'), q.winners === 1 ? '1st only' : `first ${q.winners}`)
              : SCORED_KINDS.includes(k) && q.speed_weight ? el('span', { title: `Speed weighting ${q.speed_weight}%` }, icon('gauge'), q.speed_weight + '%') : null)),
        q._seen && probs.length ? el('span', { class: 'bad-dot', title: probs[0] }) : null);
      li.addEventListener('dragstart', e => { dragId = q.id; li.classList.add('drag'); e.dataTransfer.effectAllowed = 'move'; });
      li.addEventListener('dragend', () => { dragId = null; li.classList.remove('drag'); $$('.ritem.over', rail).forEach(n => n.classList.remove('over')); });
      li.addEventListener('dragover', e => { e.preventDefault(); li.classList.add('over'); });
      li.addEventListener('dragleave', () => li.classList.remove('over'));
      li.addEventListener('drop', e => {
        e.preventDefault();
        if (!dragId || dragId === q.id) return;
        const from = quiz.questions.findIndex(x => x.id === dragId), to = quiz.questions.findIndex(x => x.id === q.id);
        quiz.questions.splice(to, 0, quiz.questions.splice(from, 1)[0]);
        touch(); drawRail();
      });
      return li;
    });
    const real = quiz.questions.filter(q => q.type !== 'slide');
    rail.replaceChildren(
      el('div', { class: 'rail-head' }, el('span', {}, `Questions (${real.length})`), el('span', { class: 'chip' }, icon('clock'), fmtDuration(real.reduce((a, q) => a + q.time_limit, 0)))),
      desc, lookBtn2, ...items,
      el('div', { class: 'add-row' },
        el('button', { class: 'btn primary', type: 'button', onclick: addDialog }, icon('plus'), 'Add question'),
        el('button', { class: 'btn', type: 'button', onclick: pasteDialog }, icon('upload'), 'Paste questions'),
        el('button', { class: 'btn ai-btn', type: 'button', onclick: () => aiDialog({ onAdd: addAIQuestions, context: () => ({ quiz_title: quiz.title, existing: quiz.questions.map(x => plainText(x.text)) }) }) }, icon('sparkle'), 'Draft with AI')));
  }
  function addQuestion(kind) {
    const q = newQuestion(kind); quiz.questions.push(q); sel = q.id; touch(); drawAll();
    setTimeout(() => { $('#q-text')?.focus(); $('.rail .ritem.sel')?.scrollIntoView({ block: 'nearest' }); }, 30);
  }
  function addAIQuestions(list) {
    list.forEach(hydrateQuestion);
    quiz.questions.push(...list); sel = list[0].id; touch(); drawAll();
  }
  function addDialog() {
    const m = ui.modal({ title: 'Add a question', wide: true, body: typePicker(k => { m.close(); addQuestion(k); }) });
  }

  /* main editor */
  function drawMain(afterFocus) {
    const q = cur();
    if (!q) { main.replaceChildren(el('div', { class: 'empty' }, el('h2', {}, 'No questions yet'), el('p', {}, 'Add your first question from the list on the left.'), el('button', { class: 'btn primary', type: 'button', onclick: addDialog }, icon('plus'), 'Add question'))); return; }
    const i = idx();
    const scrollTop = main.querySelector('.qeditor') ? window.scrollY : 0;
    const form = questionForm(q, { change: touch, rebuild: cb => { drawMain(cb); }, categories: () => quiz.questions.map(x => x.category) });
    main.replaceChildren(el('div', { class: 'card qeditor' },
      el('div', { class: 'head' }, el('h2', {}, kindOf(q) === 'slide' ? `Slide ${i + 1} of ${quiz.questions.length}` : `Question ${i + 1} of ${quiz.questions.length}`),
        el('button', { class: 'btn ghost sm icon-only', type: 'button', disabled: i === 0, 'aria-label': 'Move up', title: 'Move up', onclick: () => move(-1) }, icon('up')),
        el('button', { class: 'btn ghost sm icon-only', type: 'button', disabled: i === quiz.questions.length - 1, 'aria-label': 'Move down', title: 'Move down', onclick: () => move(1) }, icon('down')),
        el('button', { class: 'btn ghost sm', type: 'button', onclick: duplicate }, icon('copy'), 'Duplicate'),
        el('button', { class: 'btn ghost sm', type: 'button', style: { color: 'var(--danger)' }, onclick: remove }, icon('trash'), 'Delete')),
      form));
    if (scrollTop) window.scrollTo(0, scrollTop);
    drawRail(); drawPreview();
    if (typeof afterFocus === 'function') setTimeout(afterFocus, 0);
  }
  function move(d) { const i = idx(), j = i + d; if (j < 0 || j >= quiz.questions.length) return; quiz.questions.splice(j, 0, quiz.questions.splice(i, 1)[0]); touch(); drawAll(); }
  function duplicate() {
    const c = JSON.parse(JSON.stringify(cur())); c.id = uid(); const map = {};
    [...(c.options || []), ...(c.targets || [])].forEach(o => { const n = uid(); map[o.id] = n; o.id = n; });
    c.correct = (c.correct || []).map(x => map[x] || x);
    quiz.questions.splice(idx() + 1, 0, c); sel = c.id; touch(); drawAll();
  }
  async function remove() {
    if (!(await ui.confirm({ title: 'Delete this question?', message: 'This cannot be undone.', confirmText: 'Delete', danger: true }))) return;
    const i = idx(); quiz.questions.splice(i, 1);
    sel = (quiz.questions[Math.min(i, quiz.questions.length - 1)] || {}).id; touch(); drawAll();
  }
  function drawPreview() {
    const q = cur();
    preview.replaceChildren(...[el('div', { class: 'label center' }, 'What players see'), q ? phonePreview(q) : null].filter(Boolean));
  }
  function drawAll() { drawMain(); }

  function pasteDialog() {
    const ta = el('textarea', { class: 'textarea', rows: 12, placeholder: 'Q: Capital of France?\n- Berlin\n- Paris*\n- Rome\ntime: 20\n\nQ: Name the capital of Spain\ntype: text\n= Madrid\n\nQ: Year of the Moon landing?\ntype: number\nanswer: 1969', 'aria-label': 'Questions text' });
    const err = el('p', { class: 'error-text', role: 'alert' });
    const m = ui.modal({ title: 'Paste questions', wide: true,
      body: el('div', { class: 'col' }, el('p', { class: 'muted' }, 'One question per "Q:" line. Put * after the correct option (several * makes it multi-select). Use "type: text | number | order | match | poll | slide | buzzer", "= accepted answer", "answer: 1969", "category: Science", "time: 20", "points: 500" or "? explanation" lines as needed. The Help page lists them all.'), ta, err),
      actions: [el('button', { class: 'btn', onclick: () => m.close() }, 'Cancel'), el('button', { class: 'btn primary', onclick: async () => {
        try {
          const parsed = await api('/api/quizzes/parse', { method: 'POST', body: { text: ta.value } });
          parsed.questions.forEach(hydrateQuestion);
          quiz.questions.push(...parsed.questions); sel = parsed.questions[0].id; touch(); drawAll(); m.close();
          ui.toast(`Added ${parsed.questions.length} question${parsed.questions.length > 1 ? 's' : ''}`, 'success');
        } catch (e) { err.textContent = e.message; }
      } }, 'Add questions')] });
  }

  const warn = e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } };
  addEventListener('beforeunload', warn);
  const keys = e => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); save().then(ok => ok && ui.toast('Saved', 'success', 1500)); } };
  addEventListener('keydown', keys);
  onLeave(() => { removeEventListener('beforeunload', warn); removeEventListener('keydown', keys); clearTimeout(timer); if (dirty && quizId && !quizProblems(quiz).length) save(); });

  drawAll(); refreshStatus();
  if (!quizId) { title.focus(); title.select(); }
}
