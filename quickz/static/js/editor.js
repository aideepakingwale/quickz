'use strict';
/* Quiz editor: question list rail + focused editor + live phone preview, with autosave.
   Question/answer text is WYSIWYG (rich.js). Also exports the question form used by the live "quick add" drawer. */

const uid = () => Math.random().toString(16).slice(2, 10);

function newQuestion(kind = 'mcq') {
  const base = { id: uid(), rich: true, image: '', media: null, time_limit: 30, points: 1000, speed_weight: 50, winners: 0, explanation: '', text: '' };
  if (kind === 'buzzer') return { ...base, type: 'buzzer', options: [], correct: [], time_limit: 45, speed_weight: 0 };
  if (kind === 'tf') {
    const t = { id: uid(), text: 'True' }, f = { id: uid(), text: 'False' };
    return { ...base, type: 'mcq', options: [t, f], correct: [t.id], time_limit: 20 };
  }
  const opts = [0, 1, 2, 3].map(() => ({ id: uid(), text: '' }));
  return { ...base, type: kind === 'multi' ? 'multi' : 'mcq', options: opts, correct: kind === 'multi' ? [] : [opts[0].id] };
}

const isTF = q => q.type === 'mcq' && q.options.length === 2 && plainText(q.options[0].text).toLowerCase() === 'true' && plainText(q.options[1].text).toLowerCase() === 'false';
const TYPE_META = {
  mcq: { icon: 'mcq', title: 'Single choice', desc: 'One correct answer' },
  tf: { icon: 'tf', title: 'True / False', desc: 'Two options' },
  multi: { icon: 'multi', title: 'Multiple answers', desc: 'Select all that apply' },
  buzzer: { icon: 'bell', title: 'Buzzer', desc: 'First to buzz answers aloud' },
};
const kindOf = q => (q.type === 'buzzer' ? 'buzzer' : q.type === 'multi' ? 'multi' : isTF(q) ? 'tf' : 'mcq');
const SPEED_LEVELS = [[0, 'Off'], [25, 'Low'], [50, 'Medium'], [75, 'High'], [100, 'Max']];

function questionProblems(q) {
  const p = [];
  if (!plainText(q.text)) p.push('Add the question text');
  if (q.type !== 'buzzer') {
    if (q.options.length < 2) p.push('Add at least two options');
    if (q.options.some(o => !plainText(o.text))) p.push('Fill in every option, or remove the empty ones');
    if (!q.correct.length) p.push(q.type === 'multi' ? 'Tick at least one correct answer' : 'Tick the correct answer');
  }
  if (!(q.time_limit >= 5 && q.time_limit <= 300)) p.push('Time limit must be 5-300 seconds');
  if (!(q.points >= 0 && q.points <= 10000)) p.push('Points must be 0-10,000');
  return p;
}
function quizProblems(quiz) {
  const out = [];
  if (!quiz.title.trim()) out.push({ qi: -1, msgs: ['Give the quiz a title'] });
  quiz.questions.forEach((q, qi) => { const m = questionProblems(q); if (m.length) out.push({ qi, id: q.id, msgs: m }); });
  return out;
}
const cleanForSave = quiz => ({ title: quiz.title.trim(), description: quiz.description, questions: quiz.questions.map(({ _seen, ...q }) => ({ ...q, rich: true, media: q.media || null, options: q.options.map(o => ({ ...o })) })) });

async function uploadImage(file) {
  if (!file || !file.type.startsWith('image/')) throw new Error('Choose an image file (PNG, JPEG, GIF or WebP).');
  return (await api('/api/uploads', { method: 'POST', raw: file })).url;
}
/** Upload with a progress callback (fetch can't report upload progress). Resolves to {url, kind}. */
function uploadWithProgress(file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api/uploads');
    x.setRequestHeader('X-Quickz', '1');
    x.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    x.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    x.onload = () => {
      let d = {}; try { d = JSON.parse(x.responseText); } catch (e) { /* not json */ }
      if (x.status >= 200 && x.status < 300) return resolve(d);
      if (x.status === 401) dispatchEvent(new Event('quickz:unauth'));
      reject(new Error(d.error || d.detail || `Upload failed (${x.status})`));
    };
    x.onerror = () => reject(new Error('Upload failed. Check your connection.'));
    x.send(file);
  });
}
const fmtClock = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;

/** What a correct answer is worth at the start, halfway and at the very end, for a given speed weight. */
function speedExample(q) {
  const w = (q.speed_weight || 0) / 100;
  return { instant: q.points, half: Math.round(q.points * (1 - w * 0.5)), last: Math.round(q.points * (1 - w)) };
}

/* ---------------------------------------------------------------- phone preview */
function phonePreview(q) {
  const kind = kindOf(q);
  const one = q.options.length > 4 || q.options.some(o => plainText(o.text).length > 34);
  const m = q.media;
  const screen = el('div', { class: 'screen' },
    el('div', { class: 'pv-meta' }, el('span', {}, kind === 'buzzer' ? 'Buzzer' : 'Question'),
      el('span', {}, `${q.time_limit}s · ${fmtScore(q.points)} pts${kind !== 'buzzer' && q.winners === 1 ? ' · first correct wins' : kind !== 'buzzer' && q.winners ? ` · first ${q.winners} score` : kind !== 'buzzer' && q.speed_weight ? ` · speed ${q.speed_weight}%` : ''}`)),
    el('div', { class: 'pv-bar' }, el('i')),
    el('div', { class: 'pv-q' },
      plainText(q.text) ? richNode(q.text) : el('span', { style: { opacity: .45 } }, 'Your question appears here'),
      q.image && el('img', { src: q.image, alt: '' }),
      m && m.url ? el('div', { class: 'pv-media' }, icon(m.kind === 'video' ? 'film' : 'music'),
        m.where === 'screen' ? 'Plays on the projector' : m.where === 'players' ? 'Plays on your phone' : 'Plays on both') : null),
    kind === 'buzzer' ? el('div', { class: 'pv-buzz' }, 'BUZZ!')
      : el('div', { class: 'pv-tiles' + (one ? ' one' : '') }, q.options.map((o, i) =>
        el('div', { class: `pv-tile a${i % 6}` }, el('i', { class: 'shape' }, SHAPES[i % 6]), plainText(o.text) ? richInline(o.text) : `Option ${i + 1}`))));
  return el('div', { class: 'phone' }, screen);
}

/* ---------------------------------------------------------------- question form */
/** Edits q in place. h.change() = content changed (cheap), h.rebuild(cb) = structure changed (re-render the form). */
function questionForm(q, h) {
  const root = el('div', { class: 'col', style: { gap: '1.1rem' } });
  const kind = kindOf(q);

  const setKind = k => {
    if (k === kindOf(q)) return;
    if (k === 'buzzer') { q.type = 'buzzer'; q.options = []; q.correct = []; if (q.time_limit < 20) q.time_limit = 45; if (q.media) q.media.timer_after = false; }
    else if (k === 'tf') { const t = { id: uid(), text: 'True' }, f = { id: uid(), text: 'False' }; q.type = 'mcq'; q.options = [t, f]; q.correct = [t.id]; }
    else {
      if (q.options.length < 2 || isTF(q)) q.options = [0, 1, 2, 3].map(() => ({ id: uid(), text: '' }));
      q.type = k === 'multi' ? 'multi' : 'mcq';
      q.correct = k === 'multi' ? q.correct.filter(c => q.options.some(o => o.id === c)) : [q.options.find(o => q.correct.includes(o.id))?.id || q.options[0].id];
    }
    h.change(); h.rebuild();
  };
  root.append(el('div', {}, el('div', { class: 'label', style: { marginBottom: '.4rem' } }, 'Question type'),
    el('div', { class: 'type-cards', style: { gridTemplateColumns: 'repeat(4, 1fr)' }, role: 'radiogroup' },
      Object.entries(TYPE_META).map(([k, m]) => el('button', { type: 'button', class: 'type-card', role: 'radio', 'aria-pressed': String(k === kind), onclick: () => setKind(k) }, icon(m.icon), el('b', {}, m.title), el('span', {}, m.desc))))));

  // question text (WYSIWYG)
  const counter = el('span', { class: 'counter' }, `${plainText(q.text).length}/1000`);
  const qe = richEditor({ id: 'q-text', value: q.text, placeholder: 'Type your question…', label: 'Question text',
    onChange: html => { q.text = html; q._seen = true; counter.textContent = `${plainText(html).length}/1000`; h.change(); } });
  root.append(el('div', { class: 'field' }, el('label', { for: 'q-text' }, 'Question'), el('div', { class: 'qtext-wrap' }, qe.el, counter),
    el('span', { class: 'hint' }, 'Format with the toolbar: bold, italic, lists, colours, subscript for chemistry (H₂O) and superscript for maths (x²).')));

  // image
  const imgBox = el('div');
  const drawImg = () => {
    imgBox.replaceChildren();
    if (q.image) {
      imgBox.append(el('div', { class: 'imgbox' }, el('img', { src: q.image, alt: 'Question image' }),
        el('button', { class: 'btn sm', type: 'button', onclick: () => { q.image = ''; drawImg(); h.change(); } }, icon('trash'), 'Remove')));
      return;
    }
    const file = el('input', { type: 'file', accept: 'image/png,image/jpeg,image/gif,image/webp', hidden: true });
    const take = async f => { try { q.image = await uploadImage(f); drawImg(); h.change(); } catch (e) { ui.toast(e.message, 'error'); } };
    file.addEventListener('change', () => take(file.files[0]));
    const dz = el('div', { class: 'dropzone', tabindex: 0, role: 'button', onclick: () => file.click(), onkeydown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.click(); } } },
      icon('image'), ' Drop an image here, or click to browse', file);
    dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('over'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('over'));
    dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('over'); take(e.dataTransfer.files[0]); });
    imgBox.append(dz);
  };
  drawImg();
  root.append(el('div', { class: 'field' }, el('label', {}, 'Image (optional)'), imgBox));

  // audio / video
  root.append(mediaSection());

  // options
  if (kind !== 'buzzer') {
    const list = el('div', { class: 'optlist' });
    const fixed = kind === 'tf';
    const focusOpt = n => () => $$('.opt-row .rte-body', root)[n]?.focus();
    q.options.forEach((o, i) => {
      const on = q.correct.includes(o.id);
      const field = fixed ? el('div', { class: 'tf-fixed' }, plainText(o.text))
        : richEditor({ inline: true, value: o.text, placeholder: `Option ${i + 1}`, label: `Option ${i + 1}`,
          onChange: html => { o.text = html; q._seen = true; h.change(); },
          onEnter: () => { if (q.options.length < 6) { q.options.splice(i + 1, 0, { id: uid(), text: '' }); h.change(); h.rebuild(focusOpt(i + 1)); } },
          onBackspaceEmpty: () => { if (q.options.length > 2) { removeOpt(i); h.rebuild(focusOpt(Math.max(0, i - 1))); } } }).el;
      const tick = el('button', { type: 'button', class: 'tick' + (q.type === 'multi' ? ' sq' : ''), 'aria-pressed': String(on), 'aria-label': `Mark option ${i + 1} as correct`, title: 'Mark as correct' }, icon('check'));
      tick.addEventListener('click', () => {
        if (q.type === 'multi') q.correct = on ? q.correct.filter(c => c !== o.id) : [...q.correct, o.id]; else q.correct = [o.id];
        q._seen = true; h.change(); h.rebuild();
      });
      list.append(el('div', { class: `opt-row a${i % 6}` }, el('span', { class: 'shapebox', 'aria-hidden': 'true' }, SHAPES[i % 6]), field, tick,
        !fixed && q.options.length > 2 ? el('button', { class: 'btn ghost sm icon-only rm', type: 'button', 'aria-label': `Remove option ${i + 1}`, onclick: () => { removeOpt(i); h.rebuild(); } }, icon('x')) : null));
    });
    function removeOpt(i) { const [gone] = q.options.splice(i, 1); q.correct = q.correct.filter(c => c !== gone.id); if (!q.correct.length && q.type === 'mcq') q.correct = [q.options[0].id]; h.change(); }
    root.append(el('div', { class: 'field' }, el('label', {}, q.type === 'multi' ? 'Answers: tick every correct one' : 'Answers: tick the correct one'), list,
      !fixed && q.options.length < 6 ? el('div', {}, el('button', { class: 'btn sm', type: 'button', style: { marginTop: '.5rem' }, onclick: () => { q.options.push({ id: uid(), text: '' }); h.change(); h.rebuild(() => $$('.opt-row .rte-body', root).pop()?.focus()); } }, icon('plus'), 'Add option')) : null,
      !fixed ? el('span', { class: 'hint' }, 'Click an answer to format it. Press Enter for the next answer.') : null));
  } else {
    root.append(el('div', { class: 'card flat' }, el('b', {}, 'Buzzer question'), el('p', { class: 'muted', style: { fontSize: '.9rem', marginTop: '.2rem' } }, 'Players race to buzz in on their phones. The first to buzz answers out loud, and you mark them correct or wrong from the console.')));
  }

  // time + points
  const chipPick = (label, values, get, set, fmt) => {
    const box = el('div', { class: 'chips-pick' });
    const custom = el('input', { class: 'input', type: 'number', min: 0, value: values.includes(get()) ? '' : get(), placeholder: 'Custom', 'aria-label': label + ' custom' });
    const paint = () => { $$('button', box).forEach(b => b.setAttribute('aria-pressed', String(+b.dataset.v === get()))); };
    values.forEach(v => box.append(el('button', { type: 'button', 'data-v': v, 'aria-pressed': String(v === get()), onclick: () => { set(v); custom.value = ''; paint(); h.change(); } }, fmt(v))));
    custom.addEventListener('input', () => { if (custom.value !== '') { set(+custom.value); paint(); h.change(); } });
    box.append(custom);
    return el('div', { class: 'field' }, el('label', {}, label), box);
  };
  root.append(el('div', { class: 'two' },
    chipPick('Time limit (seconds)', [10, 20, 30, 45, 60, 90], () => q.time_limit, v => { q.time_limit = v; paintSpeed(); }, v => v + 's'),
    chipPick('Points', [0, 500, 1000, 2000], () => q.points, v => { q.points = v; paintSpeed(); }, v => (v === 0 ? 'None' : fmtScore(v)))));

  // who can score
  const winBox = el('div', { class: 'field' });
  function paintWinners() {
    const mode = q.winners === 0 ? 'all' : q.winners === 1 ? 'one' : 'n';
    const pick = (m, label) => el('button', { type: 'button', role: 'radio', 'aria-pressed': String(mode === m), onclick: () => {
      q.winners = m === 'all' ? 0 : m === 'one' ? 1 : (q.winners >= 2 ? q.winners : 3); h.change(); paintWinners(); paintSpeed();
    } }, label);
    const n = el('input', { class: 'input', type: 'number', min: 2, max: 50, value: Math.max(2, q.winners), 'aria-label': 'How many correct answers score', style: { width: '5.5rem' } });
    n.addEventListener('input', () => { q.winners = Math.max(2, Math.min(50, +n.value || 2)); h.change(); paintWinners(); paintSpeed(); });
    winBox.replaceChildren(el('label', {}, 'Who can score on this question'),
      el('div', { class: 'row' }, el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Who can score' }, pick('all', 'Everyone correct'), pick('one', 'Only the first correct'), pick('n', 'First few')),
        mode === 'n' ? el('div', { class: 'row nowrap', style: { gap: '.4rem' } }, 'First', n, 'correct answers') : null),
      el('span', { class: 'hint' }, mode === 'all' ? 'Every player who answers correctly earns points.'
        : mode === 'one' ? 'A race: the first correct answer takes ALL the points (100%) and the question closes straight away. Everyone else sees who answered, how fast, and at what time.'
        : `The first ${q.winners} correct answers score and the question closes when the last place is taken. Players see how many places are left.`));
  }

  // speed weighting
  const speedBox = el('div', { class: 'field' });
  function paintSpeed() {
    if (kind === 'buzzer') return;
    if (q.winners === 1) {
      speedBox.replaceChildren(el('label', {}, 'Speed weighting'),
        el('div', { class: 'speed-ex' }, icon('trophy'), `Not used here: the single winner earns 100% of the points (${fmtScore(q.points)} pts) whatever the speed.`));
      return;
    }
    const ex = speedExample(q);
    const pickers = el('div', { class: 'chips-pick' }, SPEED_LEVELS.map(([v, name]) => el('button', { type: 'button', 'aria-pressed': String(q.speed_weight === v), onclick: () => { q.speed_weight = v; h.change(); paintSpeed(); } }, `${name}${v ? ' ' + v + '%' : ''}`)));
    const slider = el('input', { type: 'range', min: 0, max: 100, step: 5, value: q.speed_weight, 'aria-label': 'Speed weighting percent', class: 'speed-slider' });
    slider.addEventListener('input', () => { q.speed_weight = +slider.value; h.change(); paintSpeed(); });
    speedBox.replaceChildren(
      el('label', {}, 'Speed weighting: how much of the points depend on answering quickly'), pickers,
      el('div', { class: 'row nowrap', style: { gap: '.8rem' } }, slider, el('b', { class: 'num', style: { minWidth: '3ch' } }, q.speed_weight + '%')),
      el('div', { class: 'speed-ex' }, icon('gauge'), q.speed_weight === 0 ? `Every correct answer earns ${fmtScore(ex.instant)} pts, however fast.`
        : [`Answer instantly: `, el('b', {}, fmtScore(ex.instant)), `  ·  halfway through: `, el('b', {}, fmtScore(ex.half)), `  ·  in the last second: `, el('b', {}, fmtScore(ex.last)), ' pts']),
      el('span', { class: 'hint' }, 'The faster the correct answer, the more of the points are earned; the speed part is added to the player’s score. Hosts can switch speed scoring off for a whole game when they start it.'));
  }
  if (kind !== 'buzzer') { paintWinners(); root.append(winBox); paintSpeed(); root.append(speedBox); }

  const ex = el('input', { class: 'input', type: 'text', maxLength: 500, value: q.explanation, placeholder: 'Shown after the answer is revealed', 'aria-label': 'Explanation' });
  ex.addEventListener('input', () => { q.explanation = ex.value; h.change(); });
  root.append(el('div', { class: 'field' }, el('label', {}, 'Explanation (optional)'), ex));

  const probs = questionProblems(q).filter(m => q._seen || m !== 'Add the question text');
  if (probs.length) root.append(el('div', { class: 'problems', role: 'status' }, el('b', {}, 'To finish this question:'), el('ul', {}, probs.map(m => el('li', {}, m)))));
  return root;

  /* ---- audio / video section ---- */
  function mediaSection() {
    const box = el('div', { class: 'field' });
    const maxMb = window.QUICKZ_MAX_MEDIA_MB || 50;
    const draw = () => {
      box.replaceChildren(el('label', {}, 'Audio or video (optional)'));
      const m = q.media;
      if (!m || !m.url) {
        const file = el('input', { type: 'file', accept: 'audio/*,video/mp4,video/webm', hidden: true });
        const prog = el('div', { class: 'upload-prog hidden' }, el('i'));
        const take = async f => {
          if (!f) return;
          if (f.type.startsWith('image/')) { try { q.image = await uploadImage(f); h.change(); h.rebuild(); } catch (e) { ui.toast(e.message, 'error'); } return; }
          if (f.size > maxMb * 1024 * 1024) { ui.toast(`That file is ${(f.size / 1048576).toFixed(0)} MB. The limit is ${maxMb} MB. Trim or compress it first.`, 'error'); return; }
          prog.classList.remove('hidden');
          try {
            const r = await uploadWithProgress(f, p => { prog.firstChild.style.width = (p * 100) + '%'; });
            if (r.kind === 'image') { q.image = r.url; } else q.media = { kind: r.kind, url: r.url, where: 'screen', timer_after: false };
            h.change(); h.rebuild();
          } catch (e) { prog.classList.add('hidden'); ui.toast(e.message, 'error'); }
        };
        file.addEventListener('change', () => take(file.files[0]));
        const dz = el('div', { class: 'dropzone', tabindex: 0, role: 'button', onclick: () => file.click(), onkeydown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.click(); } } },
          icon('music'), ' Add an audio clip or video', el('div', { class: 'hint', style: { fontWeight: 500, marginTop: '.2rem' } }, `MP3, M4A, WAV, OGG, FLAC, MP4 or WebM · up to ${maxMb} MB`), file);
        dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('over'); });
        dz.addEventListener('dragleave', () => dz.classList.remove('over'));
        dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('over'); take(e.dataTransfer.files[0]); });
        box.append(dz, prog);
        return;
      }
      const dur = el('span', { class: 'chip' }, 'loading…');
      const player = mediaPlayer(m, { controls: true });
      player.addEventListener('loadedmetadata', () => { dur.textContent = fmtClock(player.duration || 0) + ' long'; if (player.duration && player.duration > q.time_limit && !m.timer_after) dur.classList.add('warn'); });
      player.addEventListener('error', () => { dur.textContent = 'cannot play this file'; dur.classList.add('bad'); });
      const where = el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Where the media plays' }, [['screen', 'Projector'], ['players', 'Players’ phones'], ['both', 'Both']].map(([v, l]) =>
        el('button', { type: 'button', role: 'radio', 'aria-pressed': String(m.where === v), onclick: () => { m.where = v; h.change(); draw(); } }, l)));
      const timerAfter = el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: !!m.timer_after, disabled: q.type === 'buzzer',
        onchange: e => { m.timer_after = e.target.checked; h.change(); draw(); } }),
        el('div', {}, el('div', { class: 't' }, 'Start the answer timer when the media ends'),
          el('div', { class: 'd' }, q.type === 'buzzer' ? 'Buzzer questions are answered while the clip plays (think "name that tune"), so the timer always runs.'
            : 'Answers stay locked while it plays, so nobody answers early. Speed is measured from the end of the clip. Open the projector from this console, or press “Start timer” yourself.')));
      box.append(el('div', { class: 'card flat col', style: { gap: '.8rem' } },
        el('div', { class: 'row nowrap' }, el('span', { class: 'chip' }, icon(m.kind === 'video' ? 'film' : 'music'), m.kind === 'video' ? 'Video' : 'Audio'), dur, el('span', { class: 'spacer' }),
          el('button', { class: 'btn sm', type: 'button', onclick: () => { q.media = null; h.change(); h.rebuild(); } }, icon('trash'), 'Remove')),
        player,
        el('div', { class: 'field' }, el('label', {}, 'Play it on'), where,
          el('span', { class: 'hint' }, m.where === 'screen' ? 'Plays through the projector so the whole room hears it. Best for events and free hosting.'
            : m.where === 'players' ? 'Each player hears it on their own device. Good for remote players; uses more bandwidth.' : 'Plays on the projector and on every phone.')),
        timerAfter));
    };
    draw();
    return box;
  }
}

/* ---------------------------------------------------------------- editor view */
async function renderEditor(id) {
  let quiz = { title: 'Untitled quiz', description: '', questions: [newQuestion('mcq')] };
  let quizId = id === 'new' ? null : id;
  if (quizId) { try { quiz = await api('/api/quizzes/' + quizId); } catch (e) { ui.toast(e.message, 'error'); return go('#/'); } }
  let sel = quiz.questions[0] && quiz.questions[0].id;
  let dirty = false, saving = false, pending = false, timer = null, dragId = null;
  const status = { kind: quizId ? 'ok' : 'busy', text: quizId ? 'All changes saved' : 'Not saved yet' };

  const statusEl = el('button', { class: 'save-status btn ghost sm', type: 'button' });
  const title = el('input', { class: 'ed-title', type: 'text', value: quiz.title, maxLength: 120, 'aria-label': 'Quiz title', placeholder: 'Quiz title' });
  const hostBtn = el('button', { class: 'btn primary', type: 'button', onclick: hostNow }, icon('play'), 'Host this quiz');
  const bar = el('div', { class: 'ed-bar' }, el('a', { class: 'crumb', href: '#/' }, icon('left'), 'Library'), title, el('span', { class: 'spacer' }), statusEl, hostBtn);
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
    startDialog({ id: +quizId, title: quiz.title.trim(), question_count: quiz.questions.length });
  }

  title.addEventListener('input', () => { quiz.title = title.value; touch(); });

  /* rail */
  const desc = el('textarea', { class: 'textarea', rows: 2, maxLength: 1000, placeholder: 'Lobby description (optional)', 'aria-label': 'Quiz description', value: quiz.description, style: { minHeight: '3.2rem', fontSize: '.9rem' } });
  desc.addEventListener('input', () => { quiz.description = desc.value; touch(); });
  function drawRail() {
    const items = quiz.questions.map((q, i) => {
      const probs = questionProblems(q);
      const li = el('div', { class: 'ritem' + (q.id === sel ? ' sel' : ''), draggable: true, role: 'button', tabindex: 0, 'aria-label': `Question ${i + 1}`,
        onclick: () => { sel = q.id; drawAll(); }, onkeydown: e => { if (e.key === 'Enter') { sel = q.id; drawAll(); } } },
        el('span', { class: 'grip', 'aria-hidden': 'true' }, icon('grip')), el('span', { class: 'num' }, i + 1),
        el('div', { style: { flex: 1, minWidth: 0 } }, el('div', { class: 'tx' + (plainText(q.text) ? '' : ' blank') }, plainText(q.text) || 'Untitled question'),
          el('div', { class: 'sub' }, el('span', {}, TYPE_META[kindOf(q)].title), el('span', {}, `${q.time_limit}s`), el('span', {}, q.points ? fmtScore(q.points) : '0 pts'),
            q.media && q.media.url ? el('span', { title: q.media.kind === 'video' ? 'Has a video' : 'Has an audio clip' }, icon(q.media.kind === 'video' ? 'film' : 'music')) : null,
            q.type !== 'buzzer' && q.winners ? el('span', { title: q.winners === 1 ? 'Only the first correct answer scores' : `Only the first ${q.winners} correct answers score` }, icon('trophy'), q.winners === 1 ? '1st only' : `first ${q.winners}`)
              : q.type !== 'buzzer' && q.speed_weight ? el('span', { title: `Speed weighting ${q.speed_weight}%` }, icon('gauge'), q.speed_weight + '%') : null)),
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
    const add = k => () => { const q = newQuestion(k); quiz.questions.push(q); sel = q.id; touch(); drawAll(); setTimeout(() => { $('#q-text')?.focus(); $('.rail .ritem.sel')?.scrollIntoView({ block: 'nearest' }); }, 30); };
    rail.replaceChildren(
      el('div', { class: 'rail-head' }, el('span', {}, `Questions (${quiz.questions.length})`), el('span', { class: 'chip' }, icon('clock'), fmtDuration(quiz.questions.reduce((a, q) => a + q.time_limit, 0)))),
      desc, ...items,
      el('div', { class: 'add-row' },
        el('button', { class: 'btn', type: 'button', onclick: add('mcq') }, icon('mcq'), 'Multiple choice'),
        el('button', { class: 'btn', type: 'button', onclick: add('tf') }, icon('tf'), 'True / False'),
        el('button', { class: 'btn', type: 'button', onclick: add('buzzer') }, icon('bell'), 'Buzzer'),
        el('button', { class: 'btn', type: 'button', onclick: pasteDialog }, icon('upload'), 'Paste questions')));
  }

  /* main editor */
  function drawMain(afterFocus) {
    const q = cur();
    if (!q) { main.replaceChildren(el('div', { class: 'empty' }, el('h2', {}, 'No questions yet'), el('p', {}, 'Add your first question from the list on the left.'))); return; }
    const i = idx();
    const form = questionForm(q, { change: touch, rebuild: cb => { drawMain(cb); } });
    main.replaceChildren(el('div', { class: 'card qeditor' },
      el('div', { class: 'head' }, el('h2', {}, `Question ${i + 1} of ${quiz.questions.length}`),
        el('button', { class: 'btn ghost sm icon-only', type: 'button', disabled: i === 0, 'aria-label': 'Move up', title: 'Move up', onclick: () => move(-1) }, icon('up')),
        el('button', { class: 'btn ghost sm icon-only', type: 'button', disabled: i === quiz.questions.length - 1, 'aria-label': 'Move down', title: 'Move down', onclick: () => move(1) }, icon('down')),
        el('button', { class: 'btn ghost sm', type: 'button', onclick: duplicate }, icon('copy'), 'Duplicate'),
        el('button', { class: 'btn ghost sm', type: 'button', style: { color: 'var(--danger)' }, onclick: remove }, icon('trash'), 'Delete')),
      form));
    drawRail(); drawPreview();
    if (typeof afterFocus === 'function') setTimeout(afterFocus, 0);
  }
  function move(d) { const i = idx(), j = i + d; if (j < 0 || j >= quiz.questions.length) return; quiz.questions.splice(j, 0, quiz.questions.splice(i, 1)[0]); touch(); drawAll(); }
  function duplicate() {
    const c = JSON.parse(JSON.stringify(cur())); c.id = uid(); const map = {};
    c.options.forEach(o => { const n = uid(); map[o.id] = n; o.id = n; }); c.correct = c.correct.map(x => map[x]);
    quiz.questions.splice(idx() + 1, 0, c); sel = c.id; touch(); drawAll();
  }
  async function remove() {
    if (!(await ui.confirm({ title: 'Delete this question?', message: 'This cannot be undone.', confirmText: 'Delete', danger: true }))) return;
    const i = idx(); quiz.questions.splice(i, 1);
    sel = (quiz.questions[Math.min(i, quiz.questions.length - 1)] || {}).id; touch(); drawAll();
  }
  function drawPreview() {
    const q = cur();
    preview.replaceChildren(el('div', { class: 'label center' }, 'What players see'), q ? phonePreview(q) : null);
  }
  function drawAll() { drawMain(); }

  function pasteDialog() {
    const ta = el('textarea', { class: 'textarea', rows: 12, placeholder: 'Q: Capital of France?\n- Berlin\n- Paris*\n- Rome\ntime: 20\n\nQ: Which are primes?\n- 2*\n- 4\n- 7*', 'aria-label': 'Questions text' });
    const err = el('p', { class: 'error-text', role: 'alert' });
    const m = ui.modal({ title: 'Paste questions', wide: true,
      body: el('div', { class: 'col' }, el('p', { class: 'muted' }, 'One question per "Q:" line. Put * after the correct option (several * makes it multi-select). Add "type: buzzer", "time: 20", "points: 500" or "? explanation" lines as needed.'), ta, err),
      actions: [el('button', { class: 'btn', onclick: () => m.close() }, 'Cancel'), el('button', { class: 'btn primary', onclick: async () => {
        try {
          const parsed = await api('/api/quizzes/parse', { method: 'POST', body: { text: ta.value } });
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
