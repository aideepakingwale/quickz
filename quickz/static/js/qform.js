'use strict';
/* The question form: every question type, pictures on questions AND answers, categories, effects, scoring.
   Used by the quiz editor and by the host's live "add a question now" drawer.
   Edits the question object in place. Handlers: h.change() = content changed (cheap), h.rebuild(cb) = structure changed. */

const uid = () => Math.random().toString(16).slice(2, 10);

/* ---------------------------------------------------------------- the catalogue of question types */
const TYPE_META = {
  mcq: { icon: 'mcq', title: 'Single choice', desc: 'Pick the one right answer', group: 'Choose' },
  multi: { icon: 'multi', title: 'Multiple answers', desc: 'Select all that apply', group: 'Choose' },
  tf: { icon: 'tf', title: 'True / False', desc: 'Two big buttons', group: 'Choose' },
  poll: { icon: 'poll', title: 'Poll', desc: 'No right answer, no points', group: 'Choose' },
  text: { icon: 'text', title: 'Type the answer', desc: 'Players type it in; spelling is forgiven', group: 'Type in' },
  number: { icon: 'hash', title: 'Closest number', desc: 'Estimate: the closest guess wins', group: 'Type in' },
  order: { icon: 'order', title: 'Put in order', desc: 'Sequence, timeline or ranking', group: 'Interactive' },
  match: { icon: 'match', title: 'Match pairs', desc: 'Connect each card to its partner', group: 'Interactive' },
  pin: { icon: 'pin', title: 'Pin on a picture', desc: 'Tap the right spot on a map or photo', group: 'Interactive' },
  jigsaw: { icon: 'jigsaw', title: 'Jigsaw', desc: 'Rebuild a scrambled picture: a race', group: 'Interactive' },
  buzzer: { icon: 'bell', title: 'Buzzer', desc: 'First to buzz answers aloud', group: 'Live' },
  slide: { icon: 'slide', title: 'Slide', desc: 'Round title, rules or standings', group: 'Live' },
};
const TYPE_GROUPS = ['Choose', 'Type in', 'Interactive', 'Live'];
const SPEED_LEVELS = [[0, 'Off'], [25, 'Low'], [50, 'Medium'], [75, 'High'], [100, 'Max']];
const NO_OPTION_TYPES = ['buzzer', 'text', 'number', 'pin', 'jigsaw', 'slide'];
const SCORED_KINDS = ['mcq', 'multi', 'tf', 'text', 'number', 'order', 'match', 'pin', 'jigsaw'];
/** One-tap choices for "marks by finishing place" (percent of the points for the 1st, 2nd, 3rd... correct answer). */
const PLACE_PRESETS = [['Speed-based', []], ['Podium 100 / 60 / 30', [100, 60, 30]], ['Steady drop', [100, 80, 60, 40, 20]], ['Winner takes most', [100, 50, 25, 10]]];

const isTF = q => q.type === 'mcq' && q.options.length === 2 && plainText(q.options[0].text).toLowerCase() === 'true' && plainText(q.options[1].text).toLowerCase() === 'false';
const kindOf = q => (q.type === 'mcq' && isTF(q) ? 'tf' : q.type);
const emptyOpt = () => ({ id: uid(), text: '', image: '' });

function baseQuestion() {
  return { id: uid(), rich: true, type: 'mcq', text: '', image: '', image_effect: 'none', media: null, category: '', options: [], correct: [],
    time_limit: 30, points: 1000, speed_weight: 50, winners: 0, place_weights: [], explanation: '', explain_image: '' };
}
/** Re-shape q (in place) into another kind, keeping what the kinds share: text, picture, media, category, explanation. */
function applyKind(q, kind) {
  const keep = { id: q.id, rich: true, text: q.text, image: q.image, image_effect: q.image_effect, media: q.media, category: q.category, explanation: q.explanation, explain_image: q.explain_image, _seen: q._seen };
  const fresh = Object.assign(baseQuestion(), keep);
  const opts = n => Array.from({ length: n }, emptyOpt);
  switch (kind) {
    case 'multi': fresh.type = 'multi'; fresh.options = opts(4); break;
    case 'tf': { const t = { id: uid(), text: 'True', image: '' }, f = { id: uid(), text: 'False', image: '' }; fresh.type = 'mcq'; fresh.options = [t, f]; fresh.correct = [t.id]; fresh.time_limit = 20; break; }
    case 'poll': fresh.type = 'poll'; fresh.options = opts(3); fresh.points = 0; fresh.speed_weight = 0; fresh.time_limit = 20; break;
    case 'text': fresh.type = 'text'; fresh.answers = ['']; fresh.fuzzy = true; break;
    case 'number': fresh.type = 'number'; fresh.number = { answer: '', tolerance: 0, closest: false, spread: '', min: '', max: '', unit: '' }; break;
    case 'order': fresh.type = 'order'; fresh.options = opts(3); fresh.partial = true; fresh.time_limit = 40; break;
    case 'match': fresh.type = 'match'; fresh.options = opts(3); fresh.targets = opts(3); fresh.partial = true; fresh.time_limit = 40; break;
    case 'pin': fresh.type = 'pin'; fresh.pin = { x: 50, y: 50, r: 10, ar: 1.5, partial: true, placed: false }; fresh.time_limit = 25; break;
    case 'jigsaw': fresh.type = 'jigsaw'; fresh.jigsaw = { rows: 3, cols: 3, hint: 0, partial: false }; fresh.time_limit = 90; fresh.winners = 3; fresh.place_weights = [100, 60, 30]; break;
    case 'buzzer': fresh.type = 'buzzer'; fresh.time_limit = 45; fresh.speed_weight = 0; if (fresh.media) fresh.media.timer_after = false; break;
    case 'slide': fresh.type = 'slide'; fresh.points = 0; fresh.speed_weight = 0; fresh.board = false; if (fresh.media) fresh.media.timer_after = false; break;
    default: fresh.type = 'mcq'; fresh.options = opts(4); fresh.correct = [fresh.options[0].id];
  }
  for (const k of Object.keys(q)) delete q[k];
  Object.assign(q, fresh);
  return q;
}
function newQuestion(kind = 'mcq') { return applyKind(baseQuestion(), kind); }

/* ---------------------------------------------------------------- validation (mirrors the server's rules) */
function questionProblems(q) {
  const p = [], k = kindOf(q);
  if (!plainText(q.text)) p.push(k === 'slide' ? 'Add the slide text' : 'Add the question text');
  const filled = o => plainText(o.text) || o.image;
  if (['mcq', 'multi', 'tf', 'poll', 'order'].includes(k)) {
    if (q.options.length < 2) p.push('Add at least two options');
    if (q.options.some(o => !filled(o))) p.push('Give every option some text or a picture, or remove the empty ones');
    if (k === 'mcq' || k === 'tf') { if (!q.correct.length) p.push('Tick the correct answer'); }
    if (k === 'multi' && !q.correct.length) p.push('Tick at least one correct answer');
  }
  if (k === 'match') {
    if (q.options.length < 2) p.push('Add at least two pairs');
    if (q.options.some(o => !filled(o)) || q.targets.some(o => !filled(o))) p.push('Fill in both sides of every pair, or remove the empty pairs');
  }
  if (k === 'text' && !q.answers.some(a => String(a).trim())) p.push('Add at least one accepted answer');
  if (k === 'number') {
    const n = q.number, num = v => v !== '' && v != null && Number.isFinite(Number(v));
    if (!num(n.answer)) p.push('Enter the correct number');
    if ((n.min === '' || n.min == null) !== (n.max === '' || n.max == null)) p.push('Give both a lowest and a highest value for the slider, or neither');
    else if (num(n.min) && num(n.max)) {
      if (!(Number(n.min) < Number(n.max))) p.push('The slider’s lowest value must be below its highest');
      else if (num(n.answer) && !(Number(n.min) <= Number(n.answer) && Number(n.answer) <= Number(n.max))) p.push('The correct number must be within the slider range');
    }
  }
  if (k === 'pin') {
    if (!q.image) p.push('Add the picture players will point at');
    else if (!q.pin.placed) p.push('Click the picture to mark the right spot');
  }
  if (k === 'jigsaw') {
    if (!q.image) p.push('Add the picture to cut into a puzzle');
    if (q.jigsaw.rows * q.jigsaw.cols > 48) p.push('At most 48 pieces');
  }
  if (k !== 'slide') {
    if (!(q.time_limit >= 5 && q.time_limit <= 300)) p.push('Time limit must be 5-300 seconds');
    if (SCORED_KINDS.includes(k) && !(q.points >= 0 && q.points <= 10000)) p.push('Points must be 0-10,000');
  }
  return p;
}

/** What the server stores: numbers as numbers, editor-only flags removed. */
function cleanQuestion({ _seen, ...q }) {
  const out = { ...q, rich: true, media: q.media || null };
  if (q.options) out.options = q.options.map(o => ({ id: o.id, text: o.text, image: o.image || '' }));
  if (q.targets) out.targets = q.targets.map(o => ({ id: o.id, text: o.text, image: o.image || '' }));
  if (q.type === 'text') out.answers = q.answers.map(a => String(a).trim()).filter(Boolean);
  if (q.type === 'number') {
    const n = q.number, num = v => (v === '' || v == null ? null : Number(v));
    out.number = { answer: num(n.answer), tolerance: num(n.tolerance) || 0, closest: !!n.closest, spread: n.closest ? num(n.spread) : null, min: num(n.min), max: num(n.max), unit: n.unit || '' };
  }
  if (q.type === 'pin') { const { placed, ...pin } = q.pin; out.pin = pin; }
  return out;
}

/* ---------------------------------------------------------------- pictures */
async function shrinkImage(file, maxDim) {
  if (!/^image\/(png|jpe?g|webp)$/.test(file.type)) return file;            // animated GIFs go up untouched
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
    const scale = Math.min(1, maxDim / Math.max(img.naturalWidth, img.naturalHeight));
    if (scale >= 1 && file.size < 600 * 1024) return file;
    const cv = document.createElement('canvas');
    cv.width = Math.max(1, Math.round(img.naturalWidth * scale)); cv.height = Math.max(1, Math.round(img.naturalHeight * scale));
    cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
    const blob = await new Promise(res => cv.toBlob(res, 'image/webp', 0.82));
    return blob && blob.type === 'image/webp' && blob.size < file.size ? new File([blob], 'picture.webp', { type: 'image/webp' }) : file;
  } catch (e) { return file; } finally { URL.revokeObjectURL(url); }
}
async function uploadImage(file, maxDim = 1100) {
  if (!file || !file.type.startsWith('image/')) throw new Error('Choose an image file (PNG, JPEG, GIF or WebP).');
  const small = await shrinkImage(file, maxDim);              // phones on slow Wi-Fi thank you: big photos become a few hundred KB
  return (await api('/api/uploads', { method: 'POST', raw: small })).url;
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

/** A picture slot. compact = the small square used on answer options. onValue(url, img) fires after upload / removal. */
/** Ask for the address of a picture on an allowed site (Giphy, Wikimedia, Unsplash...). Resolves to a URL or null. */
async function askPictureLink() {
  const v = await ui.prompt({ title: 'Use a picture from the web', label: 'Paste the picture or GIF address (https://…). Allowed sites: Giphy, Tenor, Wikimedia, Imgur, Unsplash, Pexels, Pixabay and Flickr.', confirmText: 'Use this picture' });
  const url = (v || '').trim();
  if (!url) return null;
  const ok = await new Promise(res => { const i = new Image(); i.onload = () => res(true); i.onerror = () => res(false); i.referrerPolicy = 'no-referrer'; i.src = url; });
  if (!ok) ui.toast('That address did not load as a picture. Right-click the picture on the site and copy the image address.', 'error', 6000);
  return ok ? url : null;
}
function pictureField({ value = '', onValue, compact = false, label = 'Add a picture', max = 1100, hint = '' }) {
  const wrap = el('div', { class: 'pic ' + (compact ? 'compact' : 'full') });
  const file = el('input', { type: 'file', accept: 'image/png,image/jpeg,image/gif,image/webp', hidden: true });
  const take = async f => {
    if (!f) return;
    wrap.classList.add('busy');
    try { const url = await uploadImage(f, max); value = url; draw(); onValue(url); } catch (e) { ui.toast(e.message, 'error'); } finally { wrap.classList.remove('busy'); }
  };
  file.addEventListener('change', () => take(file.files[0]));
  function draw() {
    wrap.replaceChildren(file);
    if (value) {
      const img = el('img', { src: value, alt: 'Picture' });
      img.addEventListener('load', () => { if (!compact) onValue(value, img); });
      wrap.append(img, el('button', { class: 'pic-x', type: 'button', 'aria-label': 'Remove picture', title: 'Remove picture', onclick: () => { value = ''; draw(); onValue(''); } }, icon('x')));
      if (!compact) wrap.append(el('button', { class: 'btn sm pic-change', type: 'button', onclick: () => file.click() }, icon('upload'), 'Replace'));
      return;
    }
    const useLink = async () => { const url = await askPictureLink(); if (url) { value = url; draw(); onValue(url); } };
    const open = e => (compact ? popMenu(e.currentTarget, [{ label: 'Upload a picture', icon: 'upload', run: () => file.click() }, { label: 'Paste a link from the web', icon: 'link', run: useLink }]) : file.click());
    const dz = el('div', { class: 'dropzone', tabindex: 0, role: 'button', 'aria-label': label, title: compact ? label : '', onclick: open,
      onkeydown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(e); } } },
      icon('image'), compact ? null : el('span', {}, ` ${label}`), compact || !hint ? null : el('div', { class: 'hint', style: { fontWeight: 500, marginTop: '.2rem' } }, hint));
    dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('over'); });
    dz.addEventListener('dragleave', () => dz.classList.remove('over'));
    dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('over'); take(e.dataTransfer.files[0]); });
    dz.addEventListener('paste', e => { const f = [...(e.clipboardData ? e.clipboardData.files : [])][0]; if (f) { e.preventDefault(); take(f); } });
    wrap.append(dz);
    if (!compact) wrap.append(el('button', { class: 'linkbtn', type: 'button', onclick: useLink }, icon('link'), 'or use a picture from the web (Giphy, Wikimedia, Unsplash…)'));
  }
  draw();
  return wrap;
}

/** What a correct answer is worth at the start, halfway and at the very end, for a given speed weight. */
function speedExample(q) {
  const w = (q.speed_weight || 0) / 100;
  return { instant: q.points, half: Math.round(q.points * (1 - w * 0.5)), last: Math.round(q.points * (1 - w)) };
}

/* ---------------------------------------------------------------- the choose-a-type grid (new question / change type) */
function typePicker(onPick, current = null) {
  return el('div', { class: 'col', style: { gap: '1rem' } }, TYPE_GROUPS.map(g => el('div', {},
    el('div', { class: 'label', style: { marginBottom: '.4rem' } }, g),
    el('div', { class: 'type-cards' }, Object.entries(TYPE_META).filter(([, m]) => m.group === g).map(([k, m]) =>
      el('button', { type: 'button', class: 'type-card', 'aria-pressed': String(k === current), onclick: () => onPick(k) }, icon(m.icon), el('b', {}, m.title), el('span', {}, m.desc)))))));
}

/* ---------------------------------------------------------------- phone preview */
function phonePreview(q) {
  const k = kindOf(q);
  const pub = previewView(q);
  const body = el('div', { class: 'qv mini pv-body' });
  if (k === 'slide') body.append(el('div', { class: 'slide-card' }, el('div', { class: 'kicker' }, q.category || (q.board ? 'Standings' : 'Up next')), plainText(q.text) ? richNode(q.text, 'q-text') : el('div', { class: 'q-text', style: { opacity: .5 } }, 'Your slide text'), qImage(pub, {})));
  else {
    body.append(el('div', { class: 'pv-card-slot' }, questionCard(pub, { preview: true })));
    if (k === 'buzzer') body.append(el('div', { class: 'pv-buzz' }, 'BUZZ!'));
    else if (k === 'text') body.append(el('div', { class: 'typein' }, el('div', { class: 'big-input' }, 'Type your answer'), el('div', { class: 'btn primary block', style: { minHeight: '2.4em' } }, 'Lock in answer')));
    else if (k === 'number') {
      const n = pub.number || {};
      body.append(el('div', { class: 'numin' }, el('div', { class: 'readout' }, n.min != null ? String(Math.round((n.min + n.max) / 2)) : '–', n.unit ? el('small', {}, n.unit) : null),
        n.min != null ? el('input', { type: 'range', min: n.min, max: n.max, value: Math.round((n.min + n.max) / 2), disabled: true }) : el('div', { class: 'big-input', style: { border: '2px solid var(--border-strong)', borderRadius: '1em', padding: '.5em', textAlign: 'center', color: 'var(--text-faint)' } }, 'Type a number')));
    } else if (k === 'pin') body.append(q.image ? pinPreview(q) : el('div', { class: 'pin-stage', style: { padding: '2.5em 1em', textAlign: 'center', color: 'var(--text-faint)', lineHeight: 1.3 } }, 'Add a picture to place the target'));
    else body.append(questionBody(pub, 'mini'));
  }
  const meta = k === 'slide' ? 'Slide' : `${q.time_limit}s · ${SCORED_KINDS.includes(k) ? fmtScore(q.points) + ' pts' : k === 'poll' ? 'no points' : ''}`;
  return el('div', { class: 'phone' }, el('div', { class: 'screen' }, el('div', { class: 'pv-meta' }, el('span', {}, TYPE_META[k].title), el('span', {}, meta)), el('div', { class: 'pv-bar' }, el('i')), body));
}
function pinPreview(q) {
  const s = el('div', { class: 'pin-stage' }, el('img', { src: q.image, alt: '' }));
  if (q.pin.placed) s.append(el('span', { class: 'pin-target', style: { left: q.pin.x + '%', top: q.pin.y + '%', width: (q.pin.r * 2) + '%', height: (q.pin.r * 2 * (q.pin.ar || 1.5)) + '%' } }));
  return s;
}
/** The question as a player's device would receive it (no secrets), for the preview. */
function previewView(q) {
  const k = kindOf(q);
  const items = a => (a || []).map((o, i) => ({ id: o.id || String(i), text: plainText(o.text) ? o.text : (o.image ? '' : `Option ${i + 1}`), image: o.image || '' }));
  const v = { id: q.id, type: q.type, text: q.text, image: q.image, image_effect: q.image_effect, category: q.category, options: items(q.options), targets: items(q.targets), scored: true };
  if (k === 'jigsaw') v.jigsaw = { rows: q.jigsaw.rows, cols: q.jigsaw.cols, hint: q.jigsaw.hint || 0 };
  if (k === 'number') { const n = q.number; const has = n.min !== '' && n.max !== '' && n.min != null && n.max != null; v.number = { min: has ? Number(n.min) : null, max: has ? Number(n.max) : null, unit: n.unit || '', closest: !!n.closest }; }
  return v;
}

/* ---------------------------------------------------------------- the form */
function questionForm(q, h) {
  const root = el('div', { class: 'col', style: { gap: '1.15rem' } });
  const kind = kindOf(q);
  const scored = SCORED_KINDS.includes(kind);

  /* -- type */
  const meta = TYPE_META[kind];
  root.append(el('div', { class: 'type-now' }, el('span', { class: 'type-chip' }, icon(meta.icon), el('b', {}, meta.title), el('span', {}, meta.desc)),
    el('button', { class: 'btn sm', type: 'button', onclick: () => {
      const m = ui.modal({ title: 'Change the question type', wide: true, body: typePicker(k => { if (k === kind) return m.close();
        applyKind(q, k); q._seen = true; m.close(); h.change(); h.rebuild(); }, kind),
        actions: [el('span', { class: 'hint', style: { marginRight: 'auto' } }, 'Your question text, picture and settings are kept; the answers start fresh.')] });
    } }, icon('refresh'), 'Change type')));

  /* -- text + category */
  const counter = el('span', { class: 'counter' }, `${plainText(q.text).length}/1000`);
  const qe = richEditor({ id: 'q-text', value: q.text, placeholder: kind === 'slide' ? 'Round title, rules, or a message for the room…' : 'Type your question…', label: 'Question text',
    onChange: html => { q.text = html; q._seen = true; counter.textContent = `${plainText(html).length}/1000`; h.change(); } });
  const cats = [...new Set((h.categories ? h.categories() : []).filter(Boolean))];
  const catId = 'cats-' + uid();
  const cat = el('input', { class: 'input', type: 'text', maxLength: 30, value: q.category || '', list: catId, placeholder: kind === 'slide' ? 'Kicker, e.g. ROUND 2' : 'e.g. Geography (shown as a coloured tag)', 'aria-label': 'Category' });
  cat.addEventListener('input', () => { q.category = cat.value; h.change(); });
  root.append(el('div', { class: 'field' }, el('label', { for: 'q-text' }, kind === 'slide' ? 'Slide text' : 'Question'), el('div', { class: 'qtext-wrap' }, qe.el, counter),
    el('span', { class: 'hint' }, 'Format with the toolbar: bold, italic, lists, colours, subscript for chemistry (H₂O) and superscript for maths (x²).')),
    el('div', { class: 'field' }, el('label', {}, kind === 'slide' ? 'Kicker (optional)' : 'Category (optional)'), cat, el('datalist', { id: catId }, cats.map(c => el('option', { value: c })))));

  /* -- picture (+ effect) */
  const picBox = el('div');
  const drawPic = () => {
    const field = pictureField({ value: q.image, label: kind === 'pin' ? 'Add the picture players will point at' : kind === 'jigsaw' ? 'Add the picture to cut into a puzzle' : 'Drop a picture here, or click to browse', hint: 'PNG, JPEG, GIF or WebP. Large photos are shrunk automatically.',
      onValue: (url, img) => {
        if (img === undefined) { q.image = url; if (!url) { q.image_effect = 'none'; if (q.pin) q.pin.placed = false; } h.change(); h.rebuild(); return; }
        if (q.pin && img.naturalWidth) { const ar = img.naturalWidth / img.naturalHeight; if (Math.abs(ar - q.pin.ar) > 0.001) { q.pin.ar = ar; h.change(); } }
      } });
    const fx = (q.image && kind !== 'pin' && kind !== 'jigsaw' && kind !== 'slide') ? el('div', { class: 'field', style: { marginTop: '.6rem' } }, el('label', {}, 'Picture effect'),
      el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Picture effect' }, [['none', 'Normal'], ['blur', 'Blur → clear'], ['zoom', 'Zoom out']].map(([v, l]) =>
        el('button', { type: 'button', role: 'radio', 'aria-pressed': String((q.image_effect || 'none') === v), onclick: () => { q.image_effect = v; h.change(); h.rebuild(); } }, l))),
      el('span', { class: 'hint' }, q.image_effect === 'blur' ? 'A “guess the picture” round: it starts blurred and sharpens as time runs out, so quick guesses earn more with speed weighting on.'
        : q.image_effect === 'zoom' ? 'Starts zoomed in on a detail and zooms out as time runs out. Great for “what is this?”.' : 'Shown clearly the whole time.')) : null;
    const ai = typeof aiPictureButton === 'function' ? aiPictureButton(q, kind, url => { q.image = url; q.image_effect = q.image_effect || 'none'; h.change(); h.rebuild(); }) : null;
    picBox.replaceChildren(...[field, ai, fx].filter(Boolean));
  };
  drawPic();
  root.append(el('div', { class: 'field' }, el('label', {}, kind === 'pin' ? 'Picture to point at' : kind === 'jigsaw' ? 'Picture to cut up' : 'Picture (optional)'), picBox));

  /* -- audio / video */
  root.append(mediaSection());

  /* -- the answers, by type */
  const answers = answerEditor();
  if (answers) root.append(answers);

  /* -- scoring */
  if (kind !== 'slide') {
    const chipPick = (label, values, get, set, fmt) => {
      const box = el('div', { class: 'chips-pick' });
      const custom = el('input', { class: 'input', type: 'number', min: 0, value: values.includes(get()) ? '' : get(), placeholder: 'Custom', 'aria-label': label + ' custom' });
      const paint = () => { $$('button', box).forEach(b => b.setAttribute('aria-pressed', String(+b.dataset.v === get()))); };
      values.forEach(v => box.append(el('button', { type: 'button', 'data-v': v, 'aria-pressed': String(v === get()), onclick: () => { set(v); custom.value = ''; paint(); h.change(); } }, fmt(v))));
      custom.addEventListener('input', () => { if (custom.value !== '') { set(+custom.value); paint(); h.change(); } });
      box.append(custom);
      return el('div', { class: 'field' }, el('label', {}, label), box);
    };
    const speedBox = el('div', { class: 'field' }), winBox = el('div', { class: 'field' }), placeBox = el('div', { class: 'field' });
    const hasPlaces = () => (q.place_weights || []).length > 0;
    const placeHint = () => {
      const w = q.place_weights || [];
      if (!w.length) return 'Marks depend on how fast each correct answer arrives (see Speed weighting below).';
      const pts = i => fmtScore(Math.round(q.points * w[Math.min(i, w.length - 1)] / 100));
      return `1st correct answer earns ${w[0]}% (${pts(0)} pts)` + (w.length > 1 ? `, 2nd ${w[1]}% (${pts(1)} pts)` : '') + (w.length > 2 ? `, 3rd ${w[2]}% (${pts(2)} pts)` : '') + (w.length > 3 ? '…' : '') +
        `. After the list the last value carries on${q.winners > 1 ? `, and only the first ${q.winners} score` : ''}.`;
    };
    function paintPlaces() {
      const input = el('input', { class: 'input', type: 'text', value: (q.place_weights || []).join(', '), placeholder: 'e.g. 100, 70, 40', 'aria-label': 'Marks for each place, as percentages' });
      const hint = el('span', { class: 'hint' }, placeHint());
      input.addEventListener('input', () => {
        q.place_weights = input.value.split(/[,\s;/]+/).map(s => parseInt(s, 10)).filter(Number.isFinite).map(n => Math.max(0, Math.min(100, n))).slice(0, 50);
        hint.textContent = placeHint(); h.change(); paintSpeed();
      });
      const same = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
      placeBox.replaceChildren(el('label', {}, 'Marks by finishing place (optional)'),
        el('div', { class: 'chips-pick' }, PLACE_PRESETS.map(([name, list]) => el('button', { type: 'button', 'aria-pressed': String(same(list, q.place_weights || [])), onclick: () => { q.place_weights = list.slice(); h.change(); paintPlaces(); paintSpeed(); } }, name))),
        input, hint);
    }
    function paintSpeed() {
      if (!scored) return;
      if (hasPlaces()) {
        speedBox.replaceChildren(el('label', {}, 'Speed weighting'), el('div', { class: 'speed-ex' }, icon('trophy'), 'Not used here: the marks follow the order people finish in (above). Choose “Speed-based” above to use speed again.'));
        return;
      }
      if (q.winners === 1) {
        speedBox.replaceChildren(el('label', {}, 'Speed weighting'), el('div', { class: 'speed-ex' }, icon('trophy'), `Not used here: the single winner earns 100% of the points (${fmtScore(q.points)} pts) whatever the speed.`));
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
        el('span', { class: 'hint' }, 'The faster the correct answer, the more of the points are earned. Hosts can switch speed scoring off for a whole game when they start it.'));
    }
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
    const rows = [chipPick('Time limit (seconds)', [10, 20, 30, 45, 60, 90], () => q.time_limit, v => { q.time_limit = v; paintSpeed(); }, v => v + 's')];
    if (scored) rows.push(chipPick('Points', [0, 500, 1000, 2000], () => q.points, v => { q.points = v; paintSpeed(); }, v => (v === 0 ? 'None' : fmtScore(v))));
    root.append(el('div', { class: 'two' }, ...rows));
    if (scored) { paintWinners(); root.append(winBox); paintPlaces(); root.append(placeBox); paintSpeed(); root.append(speedBox); }
    if (kind === 'poll') root.append(el('div', { class: 'speed-ex' }, icon('poll'), 'A poll has no right answer and no points. The room watches the votes fill up on the big screen.'));
  } else {
    root.append(el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: !!q.board, onchange: e => { q.board = e.target.checked; h.change(); } }),
      el('div', {}, el('div', { class: 't' }, 'Show the leaderboard on this slide'), el('div', { class: 'd' }, 'A standings break: the projector shows the top players and every phone shows its own rank.'))));
  }

  /* -- explanation */
  if (kind !== 'slide') {
    const ex = el('input', { class: 'input', type: 'text', maxLength: 500, value: q.explanation, placeholder: 'Shown after the answer is revealed', 'aria-label': 'Explanation' });
    ex.addEventListener('input', () => { q.explanation = ex.value; h.change(); });
    root.append(el('div', { class: 'field' }, el('label', {}, 'Explanation (optional)'), ex,
      el('div', { class: 'row', style: { marginTop: '.4rem' } }, pictureField({ value: q.explain_image, compact: true, label: 'Add a picture to the explanation', onValue: url => { q.explain_image = url; h.change(); } }),
        el('span', { class: 'hint' }, 'A picture can accompany the explanation (a map, the real photo…).'))));
  }

  const probs = questionProblems(q).filter(m => q._seen || (m !== 'Add the question text' && m !== 'Add the slide text'));
  if (probs.length) root.append(el('div', { class: 'problems', role: 'status' }, el('b', {}, 'To finish this question:'), el('ul', {}, probs.map(m => el('li', {}, m)))));
  return root;

  /* ============ the answer editors ============ */
  function optRow(list, i, { numbered = false, tickMode = null, canRemove = true, moveable = false, shapeIdx = i, placeholder = `Option ${i + 1}`, fixedText = null, min = 2 } = {}) {
    const o = list[i];
    const on = tickMode && q.correct.includes(o.id);
    const focusOpt = n => () => $$('.opt-row .rte-body', root)[n]?.focus();
    const field = fixedText ? el('div', { class: 'tf-fixed' }, fixedText)
      : richEditor({ inline: true, value: o.text, placeholder, label: placeholder,
        onChange: html => { o.text = html; q._seen = true; h.change(); },
        onEnter: () => { if (canRemove && list.length < (list === q.targets ? 6 : kind === 'match' ? 6 : 8)) addAt(list, i + 1); },
        onBackspaceEmpty: () => { if (canRemove && list.length > min && !o.image) removeAt(list, i); } }).el;
    const pic = fixedText ? null : pictureField({ value: o.image || '', compact: true, max: 560, label: 'Add a picture to this option', onValue: url => { o.image = url; q._seen = true; h.change(); h.rebuild(); } });
    const tick = tickMode ? el('button', { type: 'button', class: 'tick' + (tickMode === 'multi' ? ' sq' : ''), 'aria-pressed': String(on), 'aria-label': `Mark option ${i + 1} as correct`, title: 'Mark as correct',
      onclick: () => { if (tickMode === 'multi') q.correct = on ? q.correct.filter(c => c !== o.id) : [...q.correct, o.id]; else q.correct = [o.id]; q._seen = true; h.change(); h.rebuild(); } }, icon('check')) : null;
    return el('div', { class: `opt-row a${shapeIdx % 6}${numbered ? ' numbered' : ''}` },
      numbered ? el('span', { class: 'numbox', 'aria-hidden': 'true' }, i + 1) : el('span', { class: 'shapebox', 'aria-hidden': 'true' }, shapeIcon(shapeIdx)),
      field, pic, tick,
      moveable ? el('span', { class: 'mv' }, el('button', { class: 'btn ghost sm icon-only', type: 'button', disabled: i === 0, 'aria-label': 'Move up', onclick: () => swap(list, i, i - 1) }, icon('up')),
        el('button', { class: 'btn ghost sm icon-only', type: 'button', disabled: i === list.length - 1, 'aria-label': 'Move down', onclick: () => swap(list, i, i + 1) }, icon('down'))) : null,
      canRemove && list.length > min ? el('button', { class: 'btn ghost sm icon-only rm', type: 'button', 'aria-label': `Remove option ${i + 1}`, onclick: () => removeAt(list, i) }, icon('x')) : null);
    function focusLater(n) { h.rebuild(focusOpt(n)); }
    function addAt(l, at) { if (kind === 'match') { q.options.splice(at, 0, emptyOpt()); q.targets.splice(at, 0, emptyOpt()); } else l.splice(at, 0, emptyOpt()); h.change(); focusLater(at); }
    function removeAt(l, at) {
      const [gone] = l.splice(at, 1);
      if (kind === 'match') { (l === q.options ? q.targets : q.options).splice(at, 1); }
      q.correct = q.correct.filter(c => c !== gone.id);
      if (!q.correct.length && (kind === 'mcq') && q.options.length) q.correct = [q.options[0].id];
      h.change(); focusLater(Math.max(0, at - 1));
    }
    function swap(l, a, b) { [l[a], l[b]] = [l[b], l[a]]; h.change(); h.rebuild(); }
  }

  function answerEditor() {
    if (kind === 'buzzer') return el('div', { class: 'card flat' }, el('b', {}, 'Buzzer question'), el('p', { class: 'muted', style: { fontSize: '.9rem', marginTop: '.2rem' } }, 'Players race to buzz in on their phones. The first to buzz answers out loud, and you mark them correct or wrong from the console.'));
    if (kind === 'slide') return el('div', { class: 'card flat' }, el('b', {}, 'A slide, not a question'), el('p', { class: 'muted', style: { fontSize: '.9rem', marginTop: '.2rem' } }, 'Use slides for round titles, rules, breaks or “halfway” standings. Nothing is answered and there is no timer: press the big button when you are ready to move on.'));
    if (kind === 'text') {
      const list = el('div', { class: 'optlist' });
      const draw = () => {
        list.replaceChildren(...q.answers.map((a, i) => {
          const inp = el('input', { class: 'input', type: 'text', maxLength: 100, value: a, placeholder: i === 0 ? 'e.g. Paris' : 'Another accepted answer', 'aria-label': `Accepted answer ${i + 1}` });
          inp.addEventListener('input', () => { q.answers[i] = inp.value; q._seen = true; h.change(); });
          inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); if (q.answers.length < 20) { q.answers.splice(i + 1, 0, ''); draw(); list.children[i + 1].querySelector('input').focus(); h.change(); } } });
          return el('div', { class: 'opt-row' }, el('span', { class: 'numbox ok', 'aria-hidden': 'true' }, icon('check')), inp,
            q.answers.length > 1 ? el('button', { class: 'btn ghost sm icon-only rm', type: 'button', 'aria-label': 'Remove this answer', onclick: () => { q.answers.splice(i, 1); draw(); h.change(); } }, icon('x')) : null);
        }));
      };
      draw();
      return el('div', { class: 'field' }, el('label', {}, 'Accepted answers'), list,
        el('div', {}, el('button', { class: 'btn sm', type: 'button', style: { marginTop: '.5rem' }, onclick: () => { q.answers.push(''); draw(); list.lastChild.querySelector('input').focus(); h.change(); } }, icon('plus'), 'Add another accepted answer')),
        el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: q.fuzzy !== false, onchange: e => { q.fuzzy = e.target.checked; h.change(); } }),
          el('div', {}, el('div', { class: 't' }, 'Forgive small typos'), el('div', { class: 'd' }, 'Capitals, punctuation, accents and “the/a/an” never matter. This also lets “Eifel Tower” match “Eiffel Tower”. Very short answers are always exact.'))),
        el('span', { class: 'hint' }, 'During the game you can also accept an answer you did not think of, right after the reveal.'));
    }
    if (kind === 'number') {
      const n = q.number;
      const numField = (label, key, ph, help, w) => { const i = el('input', { class: 'input', type: 'number', step: 'any', value: n[key], placeholder: ph, 'aria-label': label, style: w ? { maxWidth: w } : null });
        i.addEventListener('input', () => { n[key] = i.value; q._seen = true; h.change(); }); return el('div', { class: 'field' }, el('label', {}, label), i, help ? el('span', { class: 'hint' }, help) : null); };
      const closest = el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: !!n.closest, onchange: e => { n.closest = e.target.checked; h.change(); h.rebuild(); } }),
        el('div', {}, el('div', { class: 't' }, 'Give partial points for close guesses'), el('div', { class: 'd' }, 'Without this only a guess within the tolerance scores. With it, the nearer the guess, the more points - a proper “closest wins”.')));
      return el('div', { class: 'field' }, el('label', {}, 'The numeric answer'),
        el('div', { class: 'two' }, numField('Correct number', 'answer', 'e.g. 1969'), numField('Tolerance (±)', 'tolerance', '0', 'Guesses this close count as fully correct.')),
        closest, n.closest ? numField('Partial credit fades to zero this far away', 'spread', 'auto', 'Leave empty for 10% of the answer.', '14rem') : null,
        el('div', { class: 'two' }, numField('Slider from (optional)', 'min', 'e.g. 1900'), numField('Slider to', 'max', 'e.g. 2020')),
        numField('Unit (optional)', 'unit', 'e.g. km, °C, years', 'Shown next to the number.', '14rem'),
        el('span', { class: 'hint' }, 'With a slider range players drag to their guess (and can still type one); without it they type a number.'));
    }
    if (kind === 'pin') {
      if (!q.image) return el('div', { class: 'card flat' }, el('b', {}, 'Add the picture first'), el('p', { class: 'muted', style: { fontSize: '.9rem', marginTop: '.2rem' } }, 'Upload a map, a photo or a diagram above. Then click it to mark the right spot.'));
      const stage = el('div', { class: 'pin-stage editable' }, el('img', { src: q.image, alt: 'Click to place the target' }));
      const ring = el('span', { class: 'pin-target' });
      const place = () => { ring.style.display = q.pin.placed ? '' : 'none'; ring.style.left = q.pin.x + '%'; ring.style.top = q.pin.y + '%'; ring.style.width = (q.pin.r * 2) + '%'; ring.style.height = (q.pin.r * 2 * (q.pin.ar || 1.5)) + '%'; };
      stage.append(ring); place();
      stage.addEventListener('pointerdown', e => {
        const r = stage.querySelector('img').getBoundingClientRect();
        q.pin.x = Math.round(Math.max(0, Math.min(100, (e.clientX - r.left) / r.width * 100)) * 10) / 10; q.pin.y = Math.round(Math.max(0, Math.min(100, (e.clientY - r.top) / r.height * 100)) * 10) / 10;
        q.pin.placed = true; q._seen = true; place(); h.change();
      });
      const slider = el('input', { type: 'range', min: 2, max: 40, step: 1, value: q.pin.r, class: 'speed-slider', 'aria-label': 'Size of the target' });
      slider.addEventListener('input', () => { q.pin.r = +slider.value; place(); h.change(); });
      return el('div', { class: 'field' }, el('label', {}, 'The right spot'), el('p', { class: 'hint' }, q.pin.placed ? 'Click again to move the target.' : 'Click the picture where the answer is.'), stage,
        el('div', { class: 'row nowrap', style: { gap: '.8rem', marginTop: '.5rem' } }, el('span', { class: 'hint', style: { whiteSpace: 'nowrap' } }, 'Target size'), slider),
        el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: q.pin.partial !== false, onchange: e => { q.pin.partial = e.target.checked; h.change(); } }),
          el('div', {}, el('div', { class: 't' }, 'Partial points near the target'), el('div', { class: 'd' }, 'Players inside the circle earn full points; just outside it, a fading share.'))));
    }
    if (kind === 'jigsaw') {
      if (!q.image) return el('div', { class: 'card flat' }, el('b', {}, 'Add the picture first'), el('p', { class: 'muted', style: { fontSize: '.9rem', marginTop: '.2rem' } }, 'Upload the picture above. DiQuizBuzz cuts it into a grid and scrambles it differently on every phone.'));
      const j = q.jigsaw, preview = el('div'), label = el('span', { class: 'hint' });
      const drawPreview = () => { preview.replaceChildren(jigsawStage({ id: q.id, image: q.image, jigsaw: j }, { lines: true })); label.textContent = `${j.cols} across × ${j.rows} down = ${j.rows * j.cols} pieces. Players get it scrambled; the finished picture is shown here for you.`; };
      const SIZES = [[2, 2, 'Tiny'], [3, 2, 'Easy'], [3, 3, 'Classic'], [4, 3, 'Medium'], [4, 4, 'Hard'], [5, 4, 'Harder'], [6, 4, 'Expert'], [6, 5, 'Brutal']];
      const chips = el('div', { class: 'chips-pick' });
      const drawChips = () => chips.replaceChildren(...SIZES.map(([c, r, name]) => el('button', { type: 'button', 'aria-pressed': String(j.cols === c && j.rows === r), onclick: () => { j.cols = c; j.rows = r; h.change(); drawChips(); drawPreview(); } }, `${c}×${r} ${name}`)));
      drawChips(); drawPreview();
      const hintField = () => {
        const costs = [0, 100, 200, 300, 500], box = el('div', { class: 'chips-pick' });
        const custom = el('input', { class: 'input', type: 'number', min: 0, max: 10000, value: costs.includes(j.hint) ? '' : j.hint, placeholder: 'Custom', 'aria-label': 'Hint cost, custom' });
        const paint = () => $$('button', box).forEach(b => b.setAttribute('aria-pressed', String(+b.dataset.v === j.hint)));
        costs.forEach(c => box.append(el('button', { type: 'button', 'data-v': c, 'aria-pressed': String(c === j.hint), onclick: () => { j.hint = c; custom.value = ''; paint(); h.change(); } }, c ? `−${fmtScore(c)} pts` : 'No hint')));
        custom.addEventListener('input', () => { if (custom.value !== '') { j.hint = Math.max(0, Math.min(10000, +custom.value || 0)); paint(); h.change(); } });
        box.append(custom);
        return el('div', { class: 'field' }, el('label', {}, 'Hint button'), box,
          el('span', { class: 'hint' }, 'Phones never show the finished picture. With a hint button, a player can tap it to see the picture, and the cost is taken off the marks they earn (never below zero).'));
      };
      return el('div', { class: 'field' }, el('label', {}, 'How many pieces'), chips, label, preview,
        hintField(),
        el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: !!j.partial, onchange: e => { j.partial = e.target.checked; h.change(); } }),
          el('div', {}, el('div', { class: 't' }, 'Part marks for an unfinished puzzle'), el('div', { class: 'd' }, 'Pieces in the right place earn up to half the points. Only a finished puzzle counts as correct and takes a place.'))));
    }
    if (kind === 'match') {
      const wrap = el('div', { class: 'optlist' });
      q.options.forEach((o, i) => {
        const left = optRow(q.options, i, { placeholder: `Left card ${i + 1}`, min: 2 });
        const right = optRow(q.targets, i, { placeholder: `Its partner ${i + 1}`, canRemove: false, shapeIdx: i });
        left.classList.add('pair-l'); right.classList.add('pair-r');
        wrap.append(el('div', { class: 'pair-edit' }, left, el('span', { class: 'pair-arrow', 'aria-hidden': 'true' }, icon('right')), right));
      });
      return el('div', { class: 'field' }, el('label', {}, 'Pairs: each left card with its partner'), wrap,
        q.options.length < 6 ? el('div', {}, el('button', { class: 'btn sm', type: 'button', style: { marginTop: '.5rem' }, onclick: () => { q.options.push(emptyOpt()); q.targets.push(emptyOpt()); h.change(); h.rebuild(() => $$('.pair-l .rte-body', root).pop()?.focus()); } }, icon('plus'), 'Add a pair')) : null,
        el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: q.partial !== false, onchange: e => { q.partial = e.target.checked; h.change(); } }),
          el('div', {}, el('div', { class: 't' }, 'Partial points for some correct pairs'), el('div', { class: 'd' }, 'Otherwise all pairs must be right to score.'))),
        el('span', { class: 'hint' }, 'Players see the left cards in this order and the partners shuffled.'));
    }
    // choice kinds + order
    const list = el('div', { class: 'optlist' });
    const fixed = kind === 'tf', max = 8;
    q.options.forEach((o, i) => list.append(optRow(q.options, i, {
      numbered: kind === 'order', moveable: kind === 'order', tickMode: kind === 'poll' || kind === 'order' ? null : (kind === 'multi' ? 'multi' : 'single'),
      canRemove: !fixed, fixedText: fixed ? plainText(o.text) : null, placeholder: kind === 'order' ? `Item ${i + 1}` : `Option ${i + 1}` })));
    const label = kind === 'multi' ? 'Answers: tick every correct one' : kind === 'poll' ? 'Options to vote on' : kind === 'order' ? 'Items: list them in the CORRECT order' : 'Answers: tick the correct one';
    return el('div', { class: 'field' }, el('label', {}, label), list,
      !fixed && q.options.length < max ? el('div', {}, el('button', { class: 'btn sm', type: 'button', style: { marginTop: '.5rem' }, onclick: () => { q.options.push(emptyOpt()); h.change(); h.rebuild(() => $$('.opt-row .rte-body', root).pop()?.focus()); } }, icon('plus'), kind === 'order' ? 'Add an item' : 'Add option')) : null,
      kind === 'order' ? el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: q.partial !== false, onchange: e => { q.partial = e.target.checked; h.change(); } }),
        el('div', {}, el('div', { class: 't' }, 'Partial points for nearly-right orders'), el('div', { class: 'd' }, 'Otherwise the whole sequence must be right to score.'))) : null,
      !fixed ? el('span', { class: 'hint' }, kind === 'order' ? 'Players see the items shuffled and drag them into order. Add a small picture to any item with the picture button.'
        : 'Click an answer to format it. Add a picture to any answer with the picture button: image-only answers work too. Press Enter for the next answer.') : null);
  }

  /* ---- audio / video section: an uploaded file, or a YouTube / Vimeo link ---- */
  function mediaSection() {
    const box = el('div', { class: 'field' });
    const maxMb = window.QUICKZ_MAX_MEDIA_MB || 50;
    const clock = sec => (sec ? fmtClock(sec) : '');
    const toSecs = v => { const t = String(v || '').trim(); if (!t) return 0; if (/^\d+$/.test(t)) return +t; const p = t.split(':').map(Number); return p.some(Number.isNaN) ? 0 : p.reduce((a, b) => a * 60 + b, 0); };
    const wherePicker = (m, redraw) => el('div', { class: 'field' }, el('label', {}, 'Play it on'),
      el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Where the media plays' }, [['screen', 'Projector'], ['players', 'Players’ phones'], ['both', 'Both']].map(([v, l]) =>
        el('button', { type: 'button', role: 'radio', 'aria-pressed': String(m.where === v), onclick: () => { m.where = v; h.change(); redraw(); } }, l))),
      el('span', { class: 'hint' }, m.where === 'screen' ? 'Plays through the projector so the whole room hears it. Best for events and free hosting.'
        : m.where === 'players' ? 'Each player hears it on their own device. Good for remote players; uses more bandwidth.' : 'Plays on the projector and on every phone.'));
    const noWait = kind === 'buzzer' || kind === 'slide';
    const timerAfter = (m, redraw) => noWait ? null : el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: !!m.timer_after, onchange: e => { m.timer_after = e.target.checked; h.change(); redraw(); } }),
      el('div', {}, el('div', { class: 't' }, 'Start the answer timer when the media ends'),
        el('div', { class: 'd' }, 'Answers stay locked while it plays, so nobody answers early. Speed is measured from the end of the clip. Open the projector from this console, or press “Start timer” yourself.')));

    const draw = () => {
      box.replaceChildren(el('label', {}, 'Audio, video or a YouTube / Vimeo link (optional)'));
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
          icon('music'), ' Upload an audio clip or video', el('div', { class: 'hint', style: { fontWeight: 500, marginTop: '.2rem' } }, `MP3, M4A, WAV, OGG, FLAC, MP4 or WebM · up to ${maxMb} MB`), file);
        dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('over'); });
        dz.addEventListener('dragleave', () => dz.classList.remove('over'));
        dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('over'); take(e.dataTransfer.files[0]); });
        const link = el('input', { class: 'input', type: 'url', placeholder: 'https://www.youtube.com/watch?v=…  or  https://vimeo.com/…', 'aria-label': 'YouTube or Vimeo link' });
        const err = el('p', { class: 'error-text', role: 'alert' });
        const add = el('button', { class: 'btn', type: 'button' }, icon('link'), 'Add video');
        const useLink = async () => {
          err.textContent = ''; add.disabled = true;
          try { const info = await api('/api/embed/parse', { method: 'POST', body: { link: link.value } }); q.media = { kind: 'embed', ...info, url: info.link, end: 0, cover: false, where: 'screen', timer_after: false }; h.change(); h.rebuild(); }
          catch (e) { err.textContent = e.message; add.disabled = false; }
        };
        add.addEventListener('click', useLink);
        link.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); useLink(); } });
        box.append(dz, prog, el('div', { class: 'or-line' }, el('span', {}, 'or play a video from the web')), el('div', { class: 'row nowrap' }, link, add), err,
          el('span', { class: 'hint' }, 'YouTube and Vimeo links only. These need an internet connection on the projector (and on phones if you play it there).'));
        return;
      }

      if (m.kind === 'embed') {
        const redraw = () => draw();
        const preview = mediaPlayer({ ...m, cover: false }, { controls: true, autoplay: false });
        const bad = el('span', { class: 'chip' }, m.provider === 'vimeo' ? 'Vimeo' : 'YouTube');
        preview.addEventListener('error', () => { bad.textContent = 'cannot be embedded'; bad.classList.add('bad'); });
        const time = (label, key, help) => { const i = el('input', { class: 'input', type: 'text', value: clock(m[key]), placeholder: 'm:ss', 'aria-label': label, style: { maxWidth: '7rem' } });
          i.addEventListener('change', () => { m[key] = toSecs(i.value); i.value = clock(m[key]); h.change(); }); return el('div', { class: 'field' }, el('label', {}, label), i, help ? el('span', { class: 'hint' }, help) : null); };
        box.append(el('div', { class: 'card flat col', style: { gap: '.8rem' } },
          el('div', { class: 'row nowrap' }, bad, el('a', { class: 'hint', href: m.link, target: '_blank', rel: 'noopener', style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, m.link), el('span', { class: 'spacer' }),
            el('button', { class: 'btn sm', type: 'button', onclick: () => { q.media = null; h.change(); h.rebuild(); } }, icon('trash'), 'Remove')),
          preview,
          el('div', { class: 'two' }, time('Start at', 'start', 'Leave empty to start from the beginning.'), time('Stop at', 'end', 'Leave empty to play to the end.')),
          el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: !!m.cover, onchange: e => { m.cover = e.target.checked; h.change(); redraw(); } }),
            el('div', {}, el('div', { class: 't' }, 'Audio only: hide the picture'), el('div', { class: 'd' }, 'For “name that tune”. Players hear it but the video, its title and thumbnails stay hidden, so the screen cannot give the answer away.'))),
          wherePicker(m, redraw), timerAfter(m, redraw),
          el('span', { class: 'hint' }, 'Some videos refuse to be embedded: press play above to check yours. Needs internet on every device that plays it.')));
        return;
      }

      const dur = el('span', { class: 'chip' }, 'loading…');
      const player = mediaPlayer(m, { controls: true });
      player.addEventListener('loadedmetadata', () => { dur.textContent = fmtClock(player.duration || 0) + ' long'; if (player.duration && player.duration > q.time_limit && !m.timer_after) dur.classList.add('warn'); });
      player.addEventListener('error', () => { dur.textContent = 'cannot play this file'; dur.classList.add('bad'); });
      box.append(el('div', { class: 'card flat col', style: { gap: '.8rem' } },
        el('div', { class: 'row nowrap' }, el('span', { class: 'chip' }, icon(mediaIcon(m)), MEDIA_LABEL(m)), dur, el('span', { class: 'spacer' }),
          el('button', { class: 'btn sm', type: 'button', onclick: () => { q.media = null; h.change(); h.rebuild(); } }, icon('trash'), 'Remove')),
        player, wherePicker(m, draw), timerAfter(m, draw)));
    };
    draw();
    return box;
  }
}
