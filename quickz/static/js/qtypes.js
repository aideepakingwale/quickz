'use strict';
/* Question renderers shared by the phone, the projector, the host console and the editor preview.
   Everything here only builds DOM from the public question/reveal views the server sends - it never needs a secret.
   ctx = 'phone' | 'screen' | 'host' | 'mini' (the editor's preview). Sizes come from CSS (.qv.<ctx>), not from here. */

/* ---------------------------------------------------------------- shapes, categories, backdrop */
const SHAPE_SVG = [
  '<path d="M12 3.2 22 20.5H2z"/>',
  '<path d="M12 1.8 22.2 12 12 22.2 1.8 12z"/>',
  '<circle cx="12" cy="12" r="10"/>',
  '<rect x="3" y="3" width="18" height="18" rx="2.5"/>',
  '<path d="m12 2 2.9 6.6 7.1.7-5.4 4.7 1.6 7L12 17.3 5.8 21l1.6-7L2 9.3l7.1-.7z"/>',
  '<path d="M9 2h6v7h7v6h-7v7H9v-7H2V9h7z"/>',
];
function shapeIcon(i, cls = 'shp') {
  const s = document.createElement('span');
  s.className = cls; s.setAttribute('aria-hidden', 'true');
  s.innerHTML = `<svg viewBox="0 0 24 24">${SHAPE_SVG[i % 6]}</svg>`;
  return s;
}
function shapeSvg(i) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true'); s.innerHTML = SHAPE_SVG[i % 6];
  return s;
}
const SKIN_INFO = [['classic', 'Classic', '#1F4FD8', '#0C8C7B'], ['ocean', 'Ocean', '#0B7285', '#1C7ED6'], ['sunset', 'Sunset', '#D9480F', '#C2255C'],
  ['forest', 'Forest', '#2B8A3E', '#E67700'], ['royal', 'Royal', '#6741D9', '#1971C2'], ['midnight', 'Midnight', '#1B2A4A', '#38D9A9']];
const CAT_COLORS = ['#2D6BD6', '#1E9A5F', '#E5681E', '#7653D4', '#D6405A', '#0C8C7B', '#B7791F', '#C2255C'];
function catColor(name) { let h = 0; for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return CAT_COLORS[h % CAT_COLORS.length]; }
const catChip = q => (q.category ? el('span', { class: 'cat', style: { '--cc': catColor(q.category) } }, q.category) : null);

/** Slowly drifting quiz shapes behind the page: depth and life without distracting from the question. */
function mountBackdrop(n = 14) {
  if (document.querySelector('.drift')) return;
  let seed = 7; const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  const box = el('div', { class: 'drift', 'aria-hidden': 'true' });
  for (let i = 0; i < n; i++) {
    const s = 5 + rnd() * 9, node = el('i', { style: {
      '--x': (rnd() * 96) + '%', '--y': (rnd() * 92) + '%', '--s': s + 'vmin', '--c': `var(--a${Math.floor(rnd() * 6)})`, '--o': (0.05 + rnd() * 0.06).toFixed(3),
      '--d': (16 + rnd() * 20).toFixed(1) + 's', '--delay': (-rnd() * 20).toFixed(1) + 's', '--dx': ((rnd() - .5) * 14) + 'vmin', '--dy': ((rnd() - .5) * 14) + 'vmin', '--r': ((rnd() - .5) * 140) + 'deg' } });
    node.innerHTML = `<svg viewBox="0 0 24 24">${SHAPE_SVG[i % 6]}</svg>`;
    box.append(node);
  }
  document.body.prepend(box);
}

/** Skins re-colour a game for everyone watching it. "Midnight" is the dark room option. */
function applySkin(skin) {
  const root = document.documentElement;
  if (!skin || root.dataset.skin === skin) return;
  root.dataset.skin = skin;
  if (skin === 'midnight') root.dataset.theme = 'dark'; else Theme.apply();
}

/** "Guess the picture": --fx runs 1 (hidden) -> 0 (clear) over the question's time. */
function fxTick(frac) { document.querySelectorAll('.qv-img[data-fx]').forEach(n => n.style.setProperty('--fx', Math.max(0, Math.min(1, frac)).toFixed(3))); }
const fxOrigin = id => { let h = 0; for (const ch of String(id)) h = (h * 33 + ch.charCodeAt(0)) >>> 0; return [30 + (h % 40), 30 + ((h >> 8) % 40)]; };

/** Fetch the NEXT question's pictures in the background, each after a random pause, so 100 phones never ask for them
    at the same instant (a free host would feel that burst) and they are already cached when the question appears. */
const _preloaded = new Set(), _keep = [];
function preloadImages(urls) {
  (urls || []).forEach(u => {
    if (_preloaded.has(u)) return;
    _preloaded.add(u);
    setTimeout(() => { const i = new Image(); i.decoding = 'async'; if ('fetchPriority' in i) i.fetchPriority = 'low'; i.referrerPolicy = 'no-referrer'; i.src = u; _keep.push(i); if (_keep.length > 40) _keep.shift(); }, 400 + Math.random() * 7000);
  });
}

/* ---------------------------------------------------------------- the question card */
function qTextNode(q, extra = '') {
  const n = plainText(q.text).length;
  return richNode(q.text, 'q-text' + (n > 170 ? ' xlong' : n > 90 ? ' long' : '') + (extra ? ' ' + extra : ''));
}
function qImage(q, { live = false, preview = false } = {}) {
  if (!q.image || q.type === 'pin' || q.type === 'jigsaw') return null;       // those draw the picture themselves (a jigsaw must not show it whole)
  const fig = el('figure', { class: 'qv-img' }, el('img', { src: q.image, alt: '', draggable: false }));
  const fx = q.image_effect && q.image_effect !== 'none' ? q.image_effect : null;
  if (fx && (live || preview)) {
    fig.dataset.fx = fx;
    fig.style.setProperty('--fx', preview ? '.55' : '1');
    if (fx === 'zoom') { const [ox, oy] = fxOrigin(q.id); fig.style.setProperty('--ox', ox + '%'); fig.style.setProperty('--oy', oy + '%'); }
    fig.append(el('span', { class: 'fx-badge' }, icon('eye'), fx === 'blur' ? 'Sharpens as time runs out' : 'Zooms out as time runs out'));
  }
  return fig;
}
/** The white card: category, question text, picture. */
function questionCard(q, { live = false, preview = false } = {}) {
  const tag = !['mcq', 'multi', 'slide'].includes(q.type) && !isTrueFalse(q) ? el('span', { class: 'typetag' }, icon(TYPE_ICON[q.type] || 'help'), typeLabel(q)) : null;
  return el('div', { class: 'qv-card' }, (q.category || tag) ? el('div', { class: 'kick' }, catChip(q), tag) : null, qTextNode(q), qImage(q, { live, preview }));
}
const TYPE_ICON = { text: 'text', number: 'hash', order: 'order', match: 'match', pin: 'pin', jigsaw: 'jigsaw', poll: 'poll', buzzer: 'bell' };

/* ---------------------------------------------------------------- answer options (single / multiple / poll) */
function optLayout(q, ctx) {
  const n = q.options.length, img = q.options.some(o => o.image), long = q.options.some(o => plainText(o.text).length > 34);
  let cols;
  if (ctx === 'screen') cols = img ? (n <= 2 ? 2 : n === 3 ? 3 : n === 4 ? 4 : n <= 6 ? 3 : 4) : (n <= 4 ? 2 : n <= 6 ? 3 : 4);
  else cols = img ? 2 : (n > 4 || long ? 1 : 2);
  if (ctx === 'mini' && img) cols = 2;
  return { n, img, cols };
}
function optionTile(o, i, { tag = 'button', props = {} } = {}) {
  const txt = plainText(o.text), hasImg = !!o.image;
  return el(tag, { class: `opt a${i % 6}${hasImg ? ' has-img' : ''}${hasImg && !txt ? ' no-txt' : ''}`, style: { '--i': i }, type: tag === 'button' ? 'button' : null, 'data-id': o.id, ...props },
    hasImg ? el('span', { class: 'o-pic' }, el('img', { src: o.image, alt: txt || 'Picture option', draggable: false }), shapeIcon(i)) : shapeIcon(i),
    txt ? el('span', { class: 'o-txt' }, richInline(o.text)) : null);
}
/** The grid of answer tiles. `selected` is a Set of ids; `onPick(option, tile)` makes them tappable. */
function optionsGrid(q, ctx, { onPick = null, selected = null, locked = false, awaiting = false, counts = null } = {}) {
  const L = optLayout(q, ctx);
  const grid = el('div', { class: `opts n${L.n}` + (awaiting ? ' await' : ''), 'data-img': L.img ? '1' : '0', 'data-cols': L.cols, style: { '--cols': L.cols }, role: 'group', 'aria-label': 'Answer options' });
  q.options.forEach((o, i) => {
    const interactive = !!onPick && !locked && !awaiting;
    const t = optionTile(o, i, { tag: onPick ? 'button' : 'div', props: onPick ? { disabled: !interactive, 'aria-pressed': 'false' } : {} });
    if (selected) {
      if (selected.has(o.id)) { t.classList.add('sel'); t.setAttribute('aria-pressed', 'true'); }
      else if (locked && selected.size) t.classList.add('dim');
    }
    if (counts) t.append(el('span', { class: 'o-count num' }, counts[o.id] || 0));
    if (interactive) t.addEventListener('click', () => onPick(o, t));
    grid.append(t);
  });
  return grid;
}

/* ---------------------------------------------------------------- phone inputs (each returns {root, value(), ready()}) */
function textInput(q, { locked = false, initial = '', onReady = () => {}, onSubmit = () => {} } = {}) {
  const input = el('input', { class: 'big-input', type: 'text', maxLength: 120, autocomplete: 'off', autocapitalize: 'sentences', spellcheck: false,
    placeholder: 'Type your answer', value: initial, disabled: locked, enterKeyHint: 'send', 'aria-label': 'Your answer' });
  const fire = () => { if (input.value.trim()) onSubmit(input.value.trim()); };
  input.addEventListener('input', () => onReady(!!input.value.trim()));
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); fire(); } });
  return { root: el('div', { class: 'typein' }, input, el('p', { class: 'hint' }, 'Capitals and punctuation don’t matter.')), value: () => input.value.trim(), ready: () => !!input.value.trim(), focus: () => input.focus() };
}

function numberInput(q, { locked = false, initial = null, onReady = () => {}, onSubmit = () => {} } = {}) {
  const spec = q.number || {}, unit = spec.unit || '';
  const hasRange = spec.min != null && spec.max != null;
  let val = initial != null ? Number(initial) : (hasRange ? Math.round((spec.min + spec.max) / 2) : null);
  const readout = el('div', { class: 'readout' }, el('span', { class: 'num v' }, val == null ? '–' : String(val)), unit ? el('small', {}, unit) : null);
  const box = el('input', { type: 'text', inputMode: 'decimal', autocomplete: 'off', placeholder: hasRange ? 'or type a number' : 'Type a number', value: initial != null ? String(initial) : '', disabled: locked, 'aria-label': 'Your number', enterKeyHint: 'send' });
  const parts = [readout];
  let slider = null;
  const paint = () => { readout.querySelector('.v').textContent = val == null || Number.isNaN(val) ? '–' : String(val); onReady(val != null && Number.isFinite(val)); };
  if (hasRange) {
    const range = spec.max - spec.min, step = range >= 100 ? 1 : range >= 10 ? 0.5 : 0.1;
    slider = el('input', { type: 'range', min: spec.min, max: spec.max, step, value: val, disabled: locked, 'aria-label': 'Slide to your number' });
    slider.addEventListener('input', () => { val = Number(slider.value); box.value = ''; paint(); });
    parts.push(slider, el('div', { class: 'range-ends' }, el('span', {}, String(spec.min)), el('span', {}, String(spec.max))));
  }
  box.addEventListener('input', () => {
    const n = Number(box.value.replace(',', '.'));
    val = box.value.trim() === '' ? (hasRange ? Number(slider.value) : null) : (Number.isFinite(n) ? n : null);
    if (slider && val != null) slider.value = Math.min(spec.max, Math.max(spec.min, val));
    paint();
  });
  box.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); if (val != null) onSubmit(val); } });
  parts.push(box);
  if (spec.closest) parts.push(el('p', { class: 'closest-note' }, 'The closest guess wins the most.'));
  paint();
  return { root: el('div', { class: 'numin' }, ...parts), value: () => val, ready: () => val != null && Number.isFinite(val) };
}

/** Put the items in order: drag by the handle (pointer events, so it works with a finger), or use the arrows. */
function orderInput(q, { locked = false, initial = null, onChange = () => {} } = {}) {
  const byId = Object.fromEntries(q.options.map(o => [o.id, o]));
  let order = initial && initial.length === q.options.length ? initial.slice() : q.options.map(o => o.id);
  const list = el('div', { class: 'ord' + (locked ? ' locked' : ''), role: 'list', 'aria-label': 'Drag to put the items in order' });
  const rows = new Map();
  const numberRows = () => {
    [...list.children].forEach((row, k) => {
      row.querySelector('.no').textContent = k + 1;
      const [bu, bd] = row.querySelectorAll('.mv button');
      bu.disabled = k === 0; bd.disabled = k === list.children.length - 1;
    });
    order = [...list.children].map(r => r.dataset.id);
    onChange(order.slice());
  };
  const move = (row, d) => {
    const sibling = d < 0 ? row.previousElementSibling : row.nextElementSibling;
    if (!sibling) return;
    d < 0 ? list.insertBefore(row, sibling) : list.insertBefore(sibling, row);
    Sound.tap(); haptic(6); numberRows(); row.querySelector('.mv button:not(:disabled)')?.focus({ preventScroll: true });
  };
  order.forEach((id, k) => {
    const o = byId[id], txt = plainText(o.text);
    const grip = el('span', { class: 'grip', title: 'Drag to move' }, icon('grip'));
    const row = el('div', { class: 'ord-item', role: 'listitem', 'data-id': id, style: { '--i': k } }, grip, el('span', { class: 'no num' }, k + 1),
      o.image ? el('span', { class: 'o-pic' }, el('img', { src: o.image, alt: txt || 'Picture', draggable: false })) : null,
      txt ? el('span', { class: 'o-txt' }, richInline(o.text)) : null,
      el('span', { class: 'mv' },
        el('button', { type: 'button', 'aria-label': 'Move up', onclick: () => move(row, -1) }, icon('up')),
        el('button', { type: 'button', 'aria-label': 'Move down', onclick: () => move(row, 1) }, icon('down'))));
    if (!locked) grip.addEventListener('pointerdown', e => startDrag(e, row));
    rows.set(id, row); list.append(row);
  });
  function startDrag(e, row) {
    e.preventDefault();
    try { e.target.setPointerCapture && e.target.setPointerCapture(e.pointerId); } catch (x) { /* optional */ }
    const r0 = row.getBoundingClientRect(), grab = e.clientY - r0.top;
    row.classList.add('drag'); let curT = 0;
    const onMove = ev => {
      const natural = row.getBoundingClientRect().top - curT;
      curT = ev.clientY - grab - natural;
      row.style.transform = `translateY(${curT}px) scale(1.03)`;
      const mid = ev.clientY, others = [...list.children].filter(n => n !== row);
      const before = others.find(n => { const r = n.getBoundingClientRect(); return mid < r.top + r.height / 2; });
      if (before ? row.nextElementSibling !== before : row !== list.lastElementChild) {
        before ? list.insertBefore(row, before) : list.append(row);
        const nat2 = row.getBoundingClientRect().top - curT;      // the row's natural slot just moved: keep it under the finger
        curT = ev.clientY - grab - nat2; row.style.transform = `translateY(${curT}px) scale(1.03)`;
        Sound.tap(); haptic(5);
      }
    };
    const end = () => { removeEventListener('pointermove', onMove); removeEventListener('pointerup', end); removeEventListener('pointercancel', end);
      row.classList.remove('drag'); row.style.transform = ''; numberRows(); };
    addEventListener('pointermove', onMove); addEventListener('pointerup', end); addEventListener('pointercancel', end);
  }
  numberRows();
  return { root: el('div', { class: 'col', style: { gap: '.6em' } }, list, locked ? null : el('p', { class: 'mt-help' }, 'Drag the handles, or use the arrows.')), value: () => order.slice(), ready: () => true };
}

/** Match each left card to a right card: tap one, tap its partner. Pairs are colour-coded. */
function matchInput(q, { locked = false, initial = null, onChange = () => {} } = {}) {
  const lefts = q.options, rights = q.targets || [];
  const pairs = {};
  (initial || []).forEach(([l, r]) => { pairs[l] = r; });
  let active = null;
  const col = (title, items, side) => el('div', { class: 'mt-col' }, el('h4', {}, title), ...items.map((o, k) => {
    const txt = plainText(o.text);
    return el('button', { class: 'mt-item', type: 'button', 'data-side': side, 'data-id': o.id, style: { '--i': k }, disabled: locked },
      o.image ? el('span', { class: 'o-pic' }, el('img', { src: o.image, alt: txt || 'Picture', draggable: false })) : null,
      txt ? el('span', { class: 'o-txt' }, richInline(o.text)) : null, el('span', { class: 'tag' }));
  }));
  const root = el('div', { class: 'col', style: { gap: '.6em' } });
  const grid = el('div', { class: 'mt' }, col('Match these…', lefts, 'l'), col('…with these', rights, 'r'));
  const help = el('p', { class: 'mt-help' });
  const paint = () => {
    const rightOwner = Object.fromEntries(Object.entries(pairs).map(([l, r]) => [r, l]));
    $$('.mt-item', grid).forEach(b => {
      const id = b.dataset.id, isLeft = b.dataset.side === 'l';
      const lid = isLeft ? (pairs[id] ? id : null) : rightOwner[id];
      const idx = lid ? lefts.findIndex(o => o.id === lid) : -1;
      b.className = 'mt-item' + (idx >= 0 ? ` paired pc${idx % 6}` : '') + (isLeft && active === id ? ' active' : '');
      b.querySelector('.tag').replaceChildren(...(idx >= 0 ? [shapeSvg(idx)] : []));
    });
    const done = lefts.every(o => pairs[o.id]);
    help.textContent = locked ? '' : done ? 'All matched. Tap a card to change it.' : active ? 'Now tap its partner on the right.' : 'Tap a card on the left, then its partner on the right.';
    onChange(done ? lefts.map(o => [o.id, pairs[o.id]]) : null);
  };
  grid.addEventListener('click', e => {
    const b = e.target.closest('.mt-item'); if (!b || locked) return;
    const id = b.dataset.id; Sound.tap(); haptic(6);
    if (b.dataset.side === 'l') {
      delete pairs[id];                                       // tapping a card (again) always means "choose its partner now"
      active = id;
    } else {
      const owner = Object.keys(pairs).find(l => pairs[l] === id);
      if (active) {
        if (owner) delete pairs[owner];
        pairs[active] = id;
        active = (lefts.find(o => !pairs[o.id]) || {}).id || null;     // jump to the next card that still needs a partner
      } else if (owner) { delete pairs[owner]; active = owner; }
    }
    paint();
  });
  root.append(grid, help); paint();
  return { root, value: () => (lefts.every(o => pairs[o.id]) ? lefts.map(o => [o.id, pairs[o.id]]) : null), ready: () => lefts.every(o => pairs[o.id]) };
}

/** Tap the picture where the answer is. Coordinates are percentages of the picture, so every screen agrees. */
function pinInput(q, { locked = false, initial = null, onChange = () => {} } = {}) {
  const img = el('img', { src: q.image, alt: 'Tap the right spot on this picture', draggable: false });
  const stage = el('div', { class: 'pin-stage', role: 'application', 'aria-label': 'Tap the right spot on the picture' }, img);
  let pos = initial ? initial.slice() : null, mark = null;
  const place = () => {
    if (!pos) return;
    if (mark) mark.remove();
    mark = el('span', { class: 'pin-mark', style: { left: pos[0] + '%', top: pos[1] + '%' } });
    stage.append(mark);
  };
  if (!locked) {
    const set = ev => {
      const r = img.getBoundingClientRect();
      pos = [Math.max(0, Math.min(100, (ev.clientX - r.left) / r.width * 100)), Math.max(0, Math.min(100, (ev.clientY - r.top) / r.height * 100))];
      Sound.tap(); haptic(8); place(); onChange(pos.slice());
    };
    stage.addEventListener('pointerdown', e => { e.preventDefault(); set(e); });
  }
  place();
  return { root: el('div', { class: 'col', style: { gap: '.5em' } }, stage, el('p', { class: 'pin-note' }, locked ? 'Your pin is in.' : pos ? 'Tap again to move your pin.' : 'Tap the picture where you think it is.')),
    value: () => (pos ? pos.map(v => Math.round(v * 100) / 100) : null), ready: () => !!pos };
}

/* ---------------------------------------------------------------- jigsaw
   One picture, cut into rows x cols. Every piece has real jigsaw tabs and sockets: neighbouring pieces interlock, and a tab
   shows the picture from the NEXT cell over (as on a cardboard puzzle). Each piece is the same picture, cropped with CSS
   (background-position) and shaped with an SVG clip-path, so a puzzle costs no more to send than a normal picture.
   An answer is the list of piece numbers by slot, left to right and top to bottom; solved = 0,1,2,... */
const NS_SVG = 'http://www.w3.org/2000/svg';
const _jigAr = new Map(), _jigPromise = new Map();
/** Width / height of a picture. Resolves once; jigArNow() answers instantly with a guess until then. */
function jigAspect(src) {
  if (!_jigPromise.has(src)) _jigPromise.set(src, new Promise(res => {
    const i = new Image();
    i.onload = () => { const ar = i.naturalWidth && i.naturalHeight ? i.naturalWidth / i.naturalHeight : 1.5; _jigAr.set(src, ar); res(ar); };
    i.onerror = () => { _jigAr.set(src, 1.5); res(1.5); };
    i.src = src;
  }));
  return _jigPromise.get(src);
}
const jigArNow = src => _jigAr.get(src) || 1.5;
/** How far a tab sticks out of its cell, as a fraction of the cell's width (mx) and height (my): the same distance both ways. */
function jigGeom(rows, cols, ar) {
  const k = (ar * rows) / cols;                                  // cell width / cell height
  return k >= 1 ? { mx: 0.22 / k, my: 0.22 } : { mx: 0.22, my: 0.22 * k };
}
function rngFrom(seed) {
  let s = 0; for (const ch of String(seed)) s = (s * 31 + ch.charCodeAt(0)) >>> 0;
  return () => { s = (s + 0x6D2B79F5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
/** A stable scramble from a seed (a reconnecting phone gets the same puzzle back), never already solved. */
function seededShuffle(n, seed) {
  const rnd = rngFrom(seed), a = Array.from({ length: n }, (_, i) => i);
  for (let tries = 0; tries < 8; tries++) {
    for (let i = n - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
    if (a.some((v, i) => v !== i)) break;
  }
  return a;
}
/** Which way every seam bulges (+1 = the left/upper piece has the tab). Seeded by the question id, so every screen draws the same puzzle. */
const _jigSeams = new Map();
function jigSeams(q) {
  const { rows, cols } = q.jigsaw, key = `${q.id}:${rows}x${cols}`;
  if (!_jigSeams.has(key)) {
    const rnd = rngFrom('seams:' + key), h = [], v = [];
    for (let r = 0; r < rows; r++) { h.push([]); v.push([]); for (let c = 0; c < cols; c++) { h[r].push(rnd() < 0.5 ? 1 : -1); v[r].push(rnd() < 0.5 ? 1 : -1); } }
    _jigSeams.set(key, { h, v });
  }
  return _jigSeams.get(key);
}
/** A piece's four edges, clockwise from the top: o = tab, i = socket, f = flat (the edge of the picture). */
function jigSig(q, p) {
  const { rows, cols } = q.jigsaw, r = Math.floor(p / cols), c = p % cols, { h, v } = jigSeams(q);
  const ch = n => (n > 0 ? 'o' : n < 0 ? 'i' : 'f');
  return ch(r > 0 ? -v[r - 1][c] : 0) + ch(c < cols - 1 ? h[r][c] : 0) + ch(r < rows - 1 ? v[r][c] : 0) + ch(c > 0 ? -h[r][c - 1] : 0);
}
/** The knob along one edge, as SVG path text. u runs along the edge (0..1), w out from it (1 = full depth), s = +1 tab / -1 socket. */
function jigEdge(x0, y0, x1, y1, nx, ny, s) {
  if (!s) return `L${x1.toFixed(4)} ${y1.toFixed(4)}`;
  const pt = (u, w) => `${(x0 + u * (x1 - x0) + w * s * nx).toFixed(4)} ${(y0 + u * (y1 - y0) + w * s * ny).toFixed(4)}`;
  return `L${pt(0.4, 0)} C${pt(0.4, 0.35)} ${pt(0.26, 0.32)} ${pt(0.29, 0.62)} C${pt(0.31, 1.12)} ${pt(0.69, 1.12)} ${pt(0.71, 0.62)} C${pt(0.74, 0.32)} ${pt(0.6, 0.35)} ${pt(0.6, 0)} L${x1.toFixed(4)} ${y1.toFixed(4)}`;
}
/** The id of a clip-path with this outline (created once, kept in a hidden <svg>). Units are fractions of the piece's own box. */
const _jigClips = new Set();
function jigClip(mx, my, sig) {
  const id = `jc-${Math.round(mx * 1000)}-${Math.round(my * 1000)}-${sig}`;
  if (_jigClips.has(id)) return id;
  _jigClips.add(id);
  let svg = document.getElementById('jig-defs');
  if (!svg) {
    svg = document.createElementNS(NS_SVG, 'svg');
    svg.id = 'jig-defs'; svg.setAttribute('width', '0'); svg.setAttribute('height', '0'); svg.setAttribute('aria-hidden', 'true'); svg.style.position = 'absolute';
    svg.append(document.createElementNS(NS_SVG, 'defs')); document.body.append(svg);
  }
  const a = mx / (1 + 2 * mx), b = my / (1 + 2 * my), sg = k => (k === 'o' ? 1 : k === 'i' ? -1 : 0);
  const d = `M${a.toFixed(4)} ${b.toFixed(4)} ${jigEdge(a, b, 1 - a, b, 0, -b, sg(sig[0]))} ${jigEdge(1 - a, b, 1 - a, 1 - b, a, 0, sg(sig[1]))} ` +
    `${jigEdge(1 - a, 1 - b, a, 1 - b, 0, b, sg(sig[2]))} ${jigEdge(a, 1 - b, a, b, -a, 0, sg(sig[3]))} Z`;
  const cp = document.createElementNS(NS_SVG, 'clipPath'), path = document.createElementNS(NS_SVG, 'path');
  cp.setAttribute('id', id); cp.setAttribute('clipPathUnits', 'objectBoundingBox'); path.setAttribute('d', d); cp.append(path);
  svg.firstChild.append(cp);
  return id;
}
/** Style for piece `idx`: a box a little bigger than its cell (room for the tabs), showing the right part of the picture, cut to its outline. */
function jigPieceStyle(q, idx, geom) {
  const { rows, cols } = q.jigsaw, { mx, my } = geom, r = Math.floor(idx / cols), c = idx % cols;
  const pos = (m, i, n) => ((m - i) / (1 + 2 * m - n)) * 100;
  return { backgroundImage: `url("${String(q.image).replace(/"/g, '%22')}")`, backgroundSize: `${cols / (1 + 2 * mx) * 100}% ${rows / (1 + 2 * my) * 100}%`,
    backgroundPosition: `${pos(mx, c, cols)}% ${pos(my, r, rows)}%`, clipPath: `url(#${jigClip(mx, my, jigSig(q, idx))})`,
    left: `${-mx * 100}%`, top: `${-my * 100}%`, width: `${(1 + 2 * mx) * 100}%`, height: `${(1 + 2 * my) * 100}%` };
}
/** A board of shaped pieces in the given order (order[slot] = piece number). Re-styles itself once the picture's shape is known. */
function jigBoard(q, order, { cls = '' } = {}) {
  const { rows, cols } = q.jigsaw;
  const board = el('div', { class: 'jig-board ' + cls, style: { '--rows': rows, '--cols': cols } });
  const pieces = order.map((piece, slot) => { const p = el('div', { class: 'jig-piece' }); board.append(el('div', { class: 'jig-cell', 'data-slot': slot }, p)); return { p, piece }; });
  const paint = () => { const g = jigGeom(rows, cols, jigArNow(q.image)); pieces.forEach(x => Object.assign(x.p.style, jigPieceStyle(q, x.piece, g))); };
  board.style.setProperty('--ar', String(jigArNow(q.image))); paint();
  jigAspect(q.image).then(ar => { board.style.setProperty('--ar', String(ar)); paint(); });
  return board;
}
/** The picture as the room sees it: assembled from its jigsaw pieces (seams showing) or plain, or scrambled. */
function jigsawStage(q, { scrambled = false, lines = true } = {}) {
  const n = q.jigsaw.rows * q.jigsaw.cols;
  if (scrambled) return el('div', { class: 'jig-stage' }, jigBoard(q, seededShuffle(n, q.id + ':room'), { cls: 'static' }));
  if (lines) return el('div', { class: 'jig-stage' }, jigBoard(q, Array.from({ length: n }, (_, i) => i), { cls: 'static solved' }));
  return el('div', { class: 'jig-stage' }, el('div', { class: 'jig-solved' }, el('img', { src: q.image, alt: 'The finished picture', draggable: false })));
}
function placesText(q) {
  const p = q.places || [];
  return p.length ? p.slice(0, 5).map((w, i) => `${ordinal(i + 1)} ${w}%`).join(' · ') + (p.length > 5 ? ' …' : '') : '';
}
/** Who finished, in order, with the time and the marks they earned. */
function finisherList(list) {
  const medal = i => ['🥇', '🥈', '🥉'][i] || String(i + 1);
  return el('ol', { class: 'fin' }, list.map(f => el('li', { class: 'fin-row' }, el('span', { class: 'fin-rk' }, medal(f.place - 1)), el('span', { class: 'fin-av' }, f.avatar),
    el('span', { class: 'fin-who' }, el('span', { class: 'fin-nm' }, f.nickname), el('span', { class: 'fin-tm num' }, (f.ms / 1000).toFixed(1) + ' s')),
    f.points != null ? el('span', { class: 'fin-pt num' }, '+' + fmtScore(f.points)) : null)));
}

/** The phone puzzle: tap a piece then another to swap them, or drag one onto another. Nothing tells you if it is right. */
function jigsawInput(q, { locked = false, initial = null, seed = '', onChange = () => {}, onHint = () => {} } = {}) {
  const { rows, cols } = q.jigsaw, n = rows * cols;
  let arr = initial && initial.length === n ? initial.slice() : seededShuffle(n, q.id + ':' + seed), picked = -1;
  let geom = jigGeom(rows, cols, jigArNow(q.image));
  const board = el('div', { class: 'jig-board' + (locked ? ' locked' : ''), role: 'application', style: { '--rows': rows, '--cols': cols, '--ar': String(jigArNow(q.image)) },
    'aria-label': `Jigsaw with ${n} pieces. Tap a piece, then tap another to swap them, or drag a piece onto another.` });
  const cells = Array.from({ length: n }, (_, s) => { const piece = el('div', { class: 'jig-piece' }), cell = el('div', { class: 'jig-cell', 'data-slot': s }, piece); board.append(cell); return { cell, piece }; });
  const paint = () => cells.forEach((c, s) => { Object.assign(c.piece.style, jigPieceStyle(q, arr[s], geom)); c.cell.classList.toggle('picked', s === picked); });
  jigAspect(q.image).then(ar => { board.style.setProperty('--ar', String(ar)); geom = jigGeom(rows, cols, ar); paint(); });
  const swap = (a, b) => {
    if (a === b) return;
    [arr[a], arr[b]] = [arr[b], arr[a]]; picked = -1; Sound.tap(); haptic(8); paint();
    const c = cells[b].cell; c.classList.remove('landed'); void c.offsetWidth; c.classList.add('landed');
    onChange(arr.slice());
  };
  const tap = s => { if (picked < 0) { picked = s; Sound.tap(); paint(); } else if (picked === s) { picked = -1; paint(); } else swap(picked, s); };
  if (!locked) board.addEventListener('pointerdown', e => {
    const cell = e.target.closest('.jig-cell');
    if (!cell || e.button > 0) return;
    const from = Number(cell.dataset.slot), x0 = e.clientX, y0 = e.clientY;
    let ghost = null, over = null, moved = false;
    const move = ev => {
      if (!moved) {
        if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 8) return;
        moved = true; picked = -1; paint(); cell.classList.add('lifted');
        const r = cell.getBoundingClientRect();
        ghost = el('div', { class: 'jig-ghost', style: { width: r.width * (1 + 2 * geom.mx) + 'px', height: r.height * (1 + 2 * geom.my) + 'px' } },
          el('div', { class: 'jig-piece', style: { ...jigPieceStyle(q, arr[from], geom), left: '0', top: '0', width: '100%', height: '100%' } }));
        document.body.append(ghost);
      }
      ghost.style.transform = `translate(${ev.clientX - ghost.offsetWidth / 2}px, ${ev.clientY - ghost.offsetHeight / 2}px) scale(1.08)`;
      const t = document.elementFromPoint(ev.clientX, ev.clientY), c = t && t.closest ? t.closest('.jig-cell') : null;
      const next = c && board.contains(c) && c !== cell ? c : null;
      if (next !== over) { if (over) over.classList.remove('over'); if (next) next.classList.add('over'); over = next; }
    };
    const end = ev => {
      removeEventListener('pointermove', move); removeEventListener('pointerup', end); removeEventListener('pointercancel', end);
      if (ghost) ghost.remove();
      cell.classList.remove('lifted'); if (over) over.classList.remove('over');
      if (!moved) tap(from);
      else if (over && ev.type === 'pointerup') swap(from, Number(over.dataset.slot));
    };
    addEventListener('pointermove', move); addEventListener('pointerup', end); addEventListener('pointercancel', end);
  });
  paint();
  // The finished picture is never shown unasked. A hint (if the host allows one) reveals it and costs marks, charged by the server.
  const slot = el('div', { class: 'jig-hintslot' }), cost = q.jigsaw.hint || 0;
  let armed = 0;
  const setHint = used => {
    if (used) slot.replaceChildren(el('button', { class: 'jig-ref', type: 'button', 'aria-label': 'Show the finished picture bigger', onclick: e => e.currentTarget.classList.toggle('open') },
      el('img', { src: q.image, alt: 'The finished picture', draggable: false }), el('span', {}, 'Your hint')));
    else if (cost && !locked) {
      const b = el('button', { class: 'btn sm jig-hint', type: 'button', onclick: () => {
        if (!armed) { armed = setTimeout(() => { armed = 0; b.lastChild.textContent = label; }, 5000); b.lastChild.textContent = `Tap again to pay ${fmtScore(cost)} pts`; return; }
        clearTimeout(armed); onHint();
      } }, icon('eye'), el('span', {}, label));
      slot.replaceChildren(b);
    } else slot.replaceChildren();
  };
  const label = `Need a hint? −${fmtScore(cost)} pts`;
  setHint(!!q.jigsaw.hint_used);
  return { root: el('div', { class: 'col jig-wrap', style: { gap: '.6em' } }, slot, board,
      el('p', { class: 'pin-note' }, locked ? 'Your puzzle is in.' : `Tap a piece, then tap another to swap them. ${n} pieces.`)),
    value: () => arr.slice(), ready: () => true, setHint };
}

/* ---------------------------------------------------------------- static question bodies (projector, host, editor preview) */
function typeLabel(q) {
  return ({ mcq: 'Single choice', multi: 'Multiple answers', buzzer: 'Buzzer', text: 'Type the answer', number: 'Closest number', order: 'Put in order', match: 'Match the pairs', pin: 'Pin the spot', jigsaw: 'Jigsaw', poll: 'Poll', slide: 'Slide' })[q.type] || q.type;
}
function isTrueFalse(q) {
  return q.type === 'mcq' && q.options.length === 2 && plainText(q.options[0].text).toLowerCase() === 'true' && plainText(q.options[1].text).toLowerCase() === 'false';
}
function pinStage(q, { reveal = null, mine = null } = {}) {
  const stage = el('div', { class: 'pin-stage' }, el('img', { src: q.image, alt: 'The picture', draggable: false }));
  if (reveal) {
    const t = reveal.target, ar = t.ar || 1.5;
    stage.append(el('span', { class: 'pin-target', style: { left: t.x + '%', top: t.y + '%', width: (t.r * 2) + '%', height: (t.r * 2 * ar) + '%' } }));
    (reveal.points || []).forEach((p, i) => stage.append(el('span', { class: 'pin-dot' + (p[2] ? ' ok' : ''), style: { left: p[0] + '%', top: p[1] + '%', animationDelay: (i * 25) + 'ms' } })));
    if (mine) stage.append(el('span', { class: 'pin-dot me', style: { left: mine[0] + '%', top: mine[1] + '%' } }));
  }
  return stage;
}
/** What a question looks like while it is being answered, for places where nobody taps it. */
function questionBody(q, ctx, { counts = null, awaiting = false } = {}) {
  switch (q.type) {
    case 'mcq': case 'multi': case 'poll': return optionsGrid(q, ctx, { awaiting, counts: q.type === 'poll' ? counts : null });
    case 'text': return el('div', { class: 'typein' }, el('div', { class: 'big-input', style: { opacity: .7 } }, icon('edit'), ' Type your answer on your phone'));
    case 'number': return el('div', { class: 'numin' }, el('div', { class: 'readout' }, '?', q.number && q.number.unit ? el('small', {}, q.number.unit) : null), el('p', { class: 'closest-note' }, q.number && q.number.closest ? 'The closest guess wins the most points.' : 'Enter your number on your phone.'));
    case 'order': return el('div', { class: 'ord' }, q.options.map((o, k) => { const t = plainText(o.text);
      return el('div', { class: 'ord-item', style: { '--i': k } }, el('span', { class: 'no' }, '?'), o.image ? el('span', { class: 'o-pic' }, el('img', { src: o.image, alt: '' })) : null, t ? el('span', { class: 'o-txt' }, richInline(o.text)) : null); }));
    case 'match': return el('div', { class: 'mt' }, [['Left', q.options], ['Right', q.targets || []]].map(([title, items]) => el('div', { class: 'mt-col' }, items.map((o, k) => { const t = plainText(o.text);
      return el('div', { class: 'mt-item', style: { '--i': k } }, o.image ? el('span', { class: 'o-pic' }, el('img', { src: o.image, alt: '' })) : null, t ? el('span', { class: 'o-txt' }, richInline(o.text)) : null); }))));
    case 'pin': return pinStage(q);
    case 'jigsaw': return q.jigsaw && q.image ? jigsawStage(q, { scrambled: ctx === 'mini' || ctx === 'screen' }) : null;     // the room sees the scrambled puzzle; the finished picture waits for the reveal
    default: return null;
  }
}

/* ---------------------------------------------------------------- reveals */
function checkSvg() { const s = document.createElement('span'); s.className = 'okmark'; s.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>'; return s; }
function fmtNum(v, unit = '') { const n = Number(v); const s = Number.isInteger(n) ? n.toLocaleString() : String(Math.round(n * 1000) / 1000); return s + (unit ? ' ' + unit : ''); }

function choiceReveal(q, r, ctx, { poll = false } = {}) {
  const correct = new Set(r.correct || []), max = Math.max(1, ...Object.values(r.distribution || {}), 1);
  return el('div', { class: 'res' }, q.options.map((o, i) => {
    const n = (r.distribution || {})[o.id] || 0, txt = plainText(o.text);
    const row = el('div', { class: `res-row a${i % 6} ${poll ? '' : correct.has(o.id) ? 'right' : 'wrong'}`, 'data-id': o.id, style: { '--i': i } }, el('i', { class: 'fill' }), shapeIcon(i),
      o.image ? el('span', { class: 'o-pic' }, el('img', { src: o.image, alt: txt || 'Picture' })) : null,
      el('span', { class: 't' }, txt ? richInline(o.text) : ''), el('span', { class: 'cnt num' }, n), checkSvg());
    requestAnimationFrame(() => requestAnimationFrame(() => { row.firstChild.style.width = (100 * n / max) + '%'; }));
    return row;
  }));
}

function numberLine(d) {
  const vals = d.guesses.map(g => g.value).concat([d.answer]);
  let lo = d.min != null ? d.min : Math.min(...vals), hi = d.max != null ? d.max : Math.max(...vals);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const pad = d.min != null ? 0 : (hi - lo) * 0.08; lo -= pad; hi += pad;
  const pos = v => (100 * (v - lo) / (hi - lo)).toFixed(2) + '%';
  const line = el('div', { class: 'numline' }, el('div', { class: 'axis' }));
  if (d.tolerance) line.append(el('div', { class: 'band', style: { left: pos(d.answer - d.tolerance), width: (100 * 2 * d.tolerance / (hi - lo)).toFixed(2) + '%' } }));
  line.append(el('span', { class: 'tick', style: { left: '0%' } }, fmtNum(lo + pad)), el('span', { class: 'tick', style: { left: '100%' } }, fmtNum(hi - pad)));
  const groups = new Map();                                     // identical guesses share one marker: "×3"
  d.guesses.forEach(g => { const k = String(g.value); const e = groups.get(k) || { value: g.value, avatar: g.avatar, n: 0 }; e.n++; groups.set(k, e); });
  [...groups.values()].forEach((g, i) => line.append(el('div', { class: 'mk' + (i % 2 ? ' lo' : ''), style: { left: pos(g.value), '--i': i } }, el('span', { class: 'dot' }),
    el('span', { class: 'lab' }, g.n > 1 ? `${fmtNum(g.value)} ×${g.n}` : `${g.avatar} ${fmtNum(g.value)}`))));
  line.append(el('div', { class: 'mk ans', style: { left: pos(d.answer), '--i': groups.size + 1 } }, el('span', { class: 'dot' }), el('span', { class: 'lab' }, fmtNum(d.answer, d.unit))));
  return line;
}

/** The answer picture for a finished question. `judge(key, accept)` (host only) adds accept / reject buttons to typed answers. */
function revealBody(q, r, ctx, { mine = null, judge = null } = {}) {
  const d = r.detail || {};
  switch (q.type) {
    case 'mcq': case 'multi': return choiceReveal(q, r, ctx);
    case 'poll': return choiceReveal(q, r, ctx, { poll: true });
    case 'text': {
      const top = d.top || [], max = Math.max(1, ...top.map(t => t.count));
      return el('div', { class: 'col', style: { gap: '.8em' } },
        el('div', { class: 'chips-row' }, (d.answers || []).slice(0, 6).map((a, i) => el('span', { class: 'ans-chip', style: { '--i': i } }, icon('check'), a))),
        top.length ? el('div', { class: 'subhead' }, 'What players typed') : null,
        top.length ? el('div', { class: 'typed' }, top.map((t, i) => {
          const row = el('div', { class: 'typed-row' + (t.correct ? ' ok' : ''), style: { '--i': i } }, el('i', { class: 'fill' }), el('span', { class: 't' }, t.text), el('span', { class: 'n num' }, t.count),
            judge ? el('span', { class: 'judge' }, t.correct ? el('button', { class: 'no', type: 'button', onclick: () => judge(t.key, false) }, 'Not correct') : el('button', { class: 'yes', type: 'button', onclick: () => judge(t.key, true) }, 'Accept')) : (t.correct ? icon('check') : null));
          requestAnimationFrame(() => requestAnimationFrame(() => { row.firstChild.style.width = (100 * t.count / max) + '%'; }));
          return row;
        })) : null);
    }
    case 'number': return el('div', { class: 'col', style: { gap: '.8em' } },
      el('div', { class: 'big-answer' }, el('div', {}, el('div', { class: 'lbl' }, 'The answer'), el('div', { class: 'val num' }, fmtNum(d.answer, d.unit), d.tolerance ? el('small', { style: { fontSize: '.45em', marginLeft: '.5em', color: 'var(--text-muted)' } }, `± ${fmtNum(d.tolerance)}`) : null))),
      d.guesses && d.guesses.length ? numberLine(d) : null,
      d.guesses && d.guesses.length && ctx !== 'mini' ? el('div', { class: 'guess-list' }, d.guesses.slice(0, ctx === 'screen' ? 4 : 6).map((g, i) => el('div', { class: 'guess-row' + (i === 0 ? ' first' : ''), style: { '--i': i } },
        el('span', { class: 'rk' }, i + 1), el('span', {}, `${g.avatar} ${g.nickname}`), el('span', { class: 'v num' }, fmtNum(g.value, d.unit)), el('span', { class: 'off num' }, g.err === 0 ? 'exact' : `off by ${fmtNum(g.err)}`)))) : null);
    case 'order': return el('div', { class: 'seq' }, (d.order || []).map((o, i) => { const t = plainText(o.text);
      return el('div', { class: 'seq-row', style: { '--i': i } }, el('span', { class: 'no num' }, i + 1), o.image ? el('span', { class: 'o-pic' }, el('img', { src: o.image, alt: '' })) : null, t ? el('span', { class: 'o-txt' }, richInline(o.text)) : null); }));
    case 'match': return el('div', { class: 'pairs' }, (d.pairs || []).map((p, i) => {
      const side = o => el('div', { class: 'side' }, o.image ? el('span', { class: 'o-pic' }, el('img', { src: o.image, alt: '' })) : null, plainText(o.text) ? richInline(o.text) : null);
      const link = el('span', { class: 'link' }); link.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';
      return el('div', { class: `pair-row pc${i % 6}`, style: { '--i': i } }, side(p.left), link, side(p.right));
    }));
    case 'pin': return el('div', {}, pinStage(q, { reveal: d, mine }), el('p', { class: 'pin-note' }, `${(d.points || []).filter(p => p[2]).length} of ${(d.points || []).length} pinned the right spot`));
    case 'jigsaw': return el('div', { class: 'col', style: { gap: '.7em' } }, q.jigsaw ? jigsawStage(q, { lines: false }) : null,
      el('p', { class: 'pin-note' }, d.attempted ? `${d.solved} of ${d.attempted} solved it` : 'Nobody handed in a puzzle'),
      d.finishers && d.finishers.length && !r.winners ? finisherList(d.finishers.slice(0, ctx === 'screen' ? 6 : 5)) : null);     // with limited places the "scored" list already says it
    default: return null;
  }
}
