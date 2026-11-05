/* Shared toolkit: DOM, safe markdown-lite, icons, UI primitives (toast/modal/confirm/prompt),
   WebSocket connection, sounds, haptics, confetti, timer ring, helpers. No dependencies. */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** el('div', {class:'x', onclick: fn}, 'text', childNode) - text is always inserted as text nodes. */
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') n.className = v;
    else if (k === 'style' && typeof v === 'object') for (const [sk, sv] of Object.entries(v)) { if (sk.startsWith('--')) n.style.setProperty(sk, sv); else n.style[sk] = sv; }
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (k in n && k !== 'list' && k !== 'for') n[k] = v;
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false || kid === '') continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
}
const clear = n => { while (n.firstChild) n.removeChild(n.firstChild); return n; };

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
/** Escape first, then **bold**, *italic*, `code`, newlines. Output is safe for innerHTML. */
function mdLite(text) {
  return escapeHtml(text)
    .replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\n/g, '<br>');
}
function mdNode(text, cls = '', tag = 'div') {
  const n = el(tag, { class: 'md ' + cls });
  n.innerHTML = mdLite(text || '');
  return n;
}
/* ---- rich text: question/answer text is a small sanitised HTML subset (b i u s sub sup code span[color] p ul ol li br).
   The server sanitises on save; we sanitise AGAIN before every render, so nothing unexpected can ever reach the DOM. */
const RICH_INLINE = new Set(['B', 'I', 'U', 'S', 'SUB', 'SUP', 'CODE', 'SPAN']);
const RICH_BLOCK = new Set(['P', 'UL', 'OL', 'LI']);
const RICH_DROP = new Set(['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'TEMPLATE', 'NOSCRIPT', 'SVG', 'MATH', 'TEXTAREA', 'TITLE']);
const RICH_CANON = { STRONG: 'B', EM: 'I', STRIKE: 'S', DIV: 'P' };
const COLOR_RE = /^(#[0-9a-f]{3,8}|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,\s*(0|1|0?\.\d+)\s*)?\))$/i;

function sanitizeRich(html, inline = false) {
  // DOMParser builds an inert document: no scripts run, no images load.
  const doc = new DOMParser().parseFromString('<body>' + String(html || '') + '</body>', 'text/html');
  const out = document.createElement('div');
  (function walk(src, dst) {
    for (const n of src.childNodes) {
      if (n.nodeType === 3) { dst.append(document.createTextNode(n.nodeValue)); continue; }
      if (n.nodeType !== 1) continue;
      let tag = n.tagName.toUpperCase();
      if (RICH_DROP.has(tag)) continue;
      tag = RICH_CANON[tag] || tag;
      if (tag === 'BR') { dst.append(inline ? document.createTextNode(' ') : document.createElement('br')); continue; }
      if (tag === 'FONT') {          // browsers emit <font color> for a caret colour
        const fc = (n.getAttribute('color') || '').trim();
        if (COLOR_RE.test(fc)) { const e = document.createElement('span'); e.style.color = fc; walk(n, e); dst.append(e); } else walk(n, dst);
        continue;
      }
      if (RICH_BLOCK.has(tag)) {
        if (inline) { dst.append(document.createTextNode(' ')); walk(n, dst); continue; }
        const e = document.createElement(tag.toLowerCase());
        if (tag === 'P') { const ta = (n.style && n.style.textAlign) || ''; if (['left', 'center', 'right'].includes(ta)) e.style.textAlign = ta; }
        walk(n, e); dst.append(e); continue;
      }
      if (RICH_INLINE.has(tag)) {
        if (tag === 'SPAN') {
          const c = ((n.style && n.style.color) || '').trim();
          if (c && COLOR_RE.test(c)) { const e = document.createElement('span'); e.style.color = c; walk(n, e); dst.append(e); } else walk(n, dst);
          continue;
        }
        const e = document.createElement(tag.toLowerCase()); walk(n, e); dst.append(e); continue;
      }
      walk(n, dst); // unknown tag: keep its text, drop the tag
    }
  })(doc.body, out);
  return out.innerHTML;
}
function plainText(html) {
  const doc = new DOMParser().parseFromString('<body>' + String(html || '') + '</body>', 'text/html');
  doc.body.querySelectorAll('br,p,li,div').forEach(n => n.append(' '));
  return doc.body.textContent.replace(/\s+/g, ' ').trim();
}
/** Plain text of rich HTML *or* legacy markdown-lite. */
const plain = t => (/<[a-z!/][^>]*>/i.test(String(t)) ? plainText(t) : String(t).replace(/[`*]/g, ''));
function richNode(html, cls = '', tag = 'div') {
  const n = el(tag, { class: 'rich ' + cls });
  n.innerHTML = sanitizeRich(html);
  return n;
}
function richInline(html) {
  const n = el('span', { class: 'rich-inline' });
  n.innerHTML = sanitizeRich(html, true);
  return n;
}

/* ---- "how many players can score" helpers, shared by the phone, the projector and the host ---- */
const fmtSecs = ms => `${(ms / 1000).toFixed(1)} s`;
const fmtTimeOfDay = ms => (ms ? new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '');
function winnersText(q) {
  if (!q.winners) return '';
  if (q.winners === 1) return 'First correct answer wins';
  return `First ${q.winners} correct answers score` + (q.spots_left != null ? ` · ${q.spots_left} left` : '');
}
/** Who took the limited places, in order, with how fast and the time of day. null when the question has no limit. */
function claimedList(r, myName = '', { big = false } = {}) {
  if (!r || !r.winners) return null;
  const list = r.claimed || [];
  const title = r.closed_by_limit ? (r.winners === 1 ? 'Already answered' : 'All places taken') : 'Correct answers that scored';
  if (!list.length) return el('div', { class: 'claimed' + (big ? ' big' : '') }, el('div', { class: 'claimed-head' }, title), el('div', { class: 'media-note' }, icon('clock'), 'Nobody answered correctly in time.'));
  return el('div', { class: 'claimed' + (big ? ' big' : ''), role: 'list', 'aria-label': title },
    el('div', { class: 'claimed-head' }, icon('trophy'), title),
    list.map(c => el('div', { class: 'crow' + (c.nickname === myName ? ' me' : ''), role: 'listitem' },
      el('span', { class: 'rk' }, c.rank), el('span', { class: 'av' }, c.avatar), el('span', { class: 'nm' }, c.nickname, c.nickname === myName ? el('span', { class: 'chip', style: { marginLeft: '.4rem' } }, 'You') : null),
      el('span', { class: 'tm' }, el('b', {}, fmtSecs(c.ms)), el('span', { class: 'at' }, c.at ? ` · answered at ${fmtTimeOfDay(c.at)}` : ''), el('span', { class: 'pts' }, `  +${fmtScore(c.points)}`)))));
}

/** Audio/video element for a question's media. Pages decide autoplay and what to do when it ends. */
function mediaPlayer(media, { controls = true, big = false, onEnded = null, autoplay = true } = {}) {
  if (media.kind === 'embed') return embedPlayer(media, { controls, big, onEnded, autoplay });
  const m = el(media.kind === 'video' ? 'video' : 'audio', {
    src: media.url, controls, preload: 'auto', playsInline: true, class: `media-el ${media.kind}${big ? ' big' : ''}`,
    'aria-label': media.kind === 'video' ? 'Question video' : 'Question audio',
  });
  if (onEnded) m.addEventListener('ended', onEnded);
  return m;
}

/* ---------------------------------------------------------------- icons */
const ICONS = {
  check: '<path d="M20 6 9 17l-5-5"/>', x: '<path d="M18 6 6 18M6 6l12 12"/>', minus: '<path d="M5 12h14"/>', plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v5M14 11v5"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M16 7l3 3M14 9l2 2"/>',
  jigsaw: '<path d="M10 4a2 2 0 1 1 4 0v2h4a1 1 0 0 1 1 1v4h-2a2 2 0 1 0 0 4h2v4a1 1 0 0 1-1 1h-4v-2a2 2 0 1 0-4 0v2H6a1 1 0 0 1-1-1v-4h2a2 2 0 1 0 0-4H5V7a1 1 0 0 1 1-1h4z"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>',
  play: '<path d="M7 4.5v15l13-7.5z"/>',
  edit: '<path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17z"/><path d="m14.5 6.5 3 3"/>',
  grip: '<circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/>',
  volume: '<path d="M4 9.5v5h4l5 4v-13l-5 4z"/><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11"/>',
  mute: '<path d="M4 9.5v5h4l5 4v-13l-5 4z"/><path d="m17 9.5 5 5M22 9.5l-5 5"/>',
  expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.2a6.5 6.5 0 0 1 3.5 5.8"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  trophy: '<path d="M8 4h8v6a4 4 0 0 1-8 0zM8 6H4v1a4 4 0 0 0 4 4M16 6h4v1a4 4 0 0 1-4 4M12 14v4M8 20h8"/>',
  bolt: '<path d="M13 2 4 14h6.5L10 22l9-12h-6.5z"/>',
  bell: '<path d="M6 16v-5a6 6 0 1 1 12 0v5l2 2H4zM10 21h4"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-8 8"/>',
  download: '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>', upload: '<path d="M12 16V5M7 9.5l5-5 5 5M5 20h14"/>',
  left: '<path d="m15 6-6 6 6 6"/>', right: '<path d="m9 6 6 6-6 6"/>', down: '<path d="m6 9 6 6 6-6"/>', up: '<path d="m6 15 6-6 6 6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  dots: '<circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  dice: '<rect x="4" y="4" width="16" height="16" rx="3.5"/><circle cx="9" cy="9" r="1.2"/><circle cx="15" cy="9" r="1.2"/><circle cx="9" cy="15" r="1.2"/><circle cx="15" cy="15" r="1.2"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  skip: '<path d="M5 5v14l10-7zM19 5v14"/>', stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  flame: '<path d="M12 22c4 0 7-2.8 7-7 0-3-1.8-5-3.5-7-.4 1.6-1.4 2.5-2.5 3 .3-3-1-6-3.5-8C9.7 7.5 5 10.5 5 15c0 4.2 3 7 7 7z"/>',
  mcq: '<path d="M4 6h2M4 12h2M4 18h2M10 6h10M10 12h10M10 18h10"/>',
  tf: '<rect x="2" y="7" width="20" height="10" rx="5"/><circle cx="16" cy="12" r="2.4"/>',
  multi: '<rect x="3.5" y="3.5" width="7" height="7" rx="1.8"/><path d="m5.3 7 1.5 1.5L9 5.6M14 7h7M3.5 14h7v7h-7zM14 17.5h7"/>',
  warn: '<path d="M12 3 2 20h20zM12 10v4.5M12 17.5h.01"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14-4M4 4v4h4M4 13a8 8 0 0 0 14 4M20 20v-4h-4"/>',
  qr: '<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h3v3h-3zM20 14v6h-3M14 20h.01"/>',
  arrowup: '<path d="M12 19V5M6 11l6-6 6 6"/>', arrowdown: '<path d="M12 5v14M6 13l6 6 6-6"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',
  crown: '<path d="M3 8l4.5 4L12 5l4.5 7L21 8l-2 11H5z"/>',
  home: '<path d="M4 11 12 4l8 7v9h-5v-6H9v6H4z"/>',
  music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
  film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
  speaker: '<path d="M4 9.5v5h4l5 4v-13l-5 4z"/><path d="M16.5 9a4 4 0 0 1 0 6"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  gauge: '<path d="M12 14l4-5"/><path d="M4 18a9 9 0 1 1 16 0"/>',
  bold: '<path d="M7 5h6a3.5 3.5 0 0 1 0 7H7zM7 12h7a3.5 3.5 0 0 1 0 7H7z"/>',
  italic: '<path d="M10 5h8M6 19h8M15 5 9 19"/>',
  underline: '<path d="M7 4v7a5 5 0 0 0 10 0V4M5 20h14"/>',
  strike: '<path d="M5 12h14M16 7a4 4 0 0 0-4-2c-2.5 0-4 1.2-4 3 0 4 8 2 8 6 0 1.8-1.7 3-4 3a5 5 0 0 1-4.5-2.5"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
  olist: '<path d="M10 6h10M10 12h10M10 18h10M4 5l1.5-1v4M4 11.5c0-1 2.5-1 2.5 0S4 13 4 14h2.5M4 17.5h2.5l-1.5 1.5c1 0 1.5.5 1.5 1.2 0 1-2 1-2.5.3"/>',
  eraser: '<path d="M4 15l8-8 7 7-5 5H8zM11 19h9"/>',
  undo: '<path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3"/>',
  redo: '<path d="M15 14l5-5-5-5M20 9H10a6 6 0 0 0 0 12h3"/>',
  logout: '<path d="M9 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h4M16 8l4 4-4 4M20 12H9"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.6 2.6 0 1 1 3.6 2.4c-.7.4-1.1.9-1.1 1.6M12 17h.01"/>',
  text: '<rect x="3" y="6" width="18" height="12" rx="2.5"/><path d="M7 10h.01M11 10h.01M15 10h.01M7 14h10"/>',
  hash: '<path d="M5 9h14M5 15h14M10 4 8 20M16 4l-2 16"/>',
  order: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 5.5 5.2 5v3.5M3.8 11.2h2.4l-2.4 3.2h2.6M4 17h2.2v1H4.6v1.2H6.4"/>',
  match: '<circle cx="5" cy="7" r="2"/><circle cx="19" cy="7" r="2"/><circle cx="5" cy="17" r="2"/><circle cx="19" cy="17" r="2"/><path d="m7 7.6 10 8.8M7 16.4l10-8.8"/>',
  pin: '<path d="M12 21s7-6.2 7-11.5a7 7 0 1 0-14 0C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.4"/>',
  poll: '<path d="M5 20V10M12 20V4M19 20v-7"/>',
  slide: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  palette: '<path d="M12 3a9 9 0 1 0 0 18c1.4 0 2-1 2-2 0-1.5-1-1.7-1-3 0-1 .8-1.7 2-1.7H17a4 4 0 0 0 4-4C21 6.5 17 3 12 3z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/>',
  copy2: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16V6a2 2 0 0 1 2-2h10"/>',
  tag: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".8"/>',
  shuffle: '<path d="M3 7h3c4 0 5 10 9 10h6M3 17h3c1.6 0 2.7-1.3 3.6-2.8M21 7h-6c-1.6 0-2.6 1.3-3.5 2.8M18 4l3 3-3 3M18 14l3 3-3 3"/>',
};
const FILL_ICONS = new Set(['play', 'bolt', 'grip', 'dots']);
function icon(name, cls = '') {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true');
  s.setAttribute('class', 'icon' + (FILL_ICONS.has(name) ? ' fill' : '') + (cls ? ' ' + cls : ''));
  s.innerHTML = ICONS[name] || '';
  return s;
}
/** The DiQuizBuzz logo (vector files in /static/brand, made by tools/make_logo.py). `full` adds the tagline. Both colour
 *  versions are in the page; CSS shows the one that suits the light or dark theme. */
function logo(extra = '', { full = false } = {}) {
  const f = full ? 'logo' : 'wordmark';
  return el('a', { class: 'logo ' + (full ? 'full ' : '') + extra, href: '/host', 'aria-label': 'DiQuizBuzz' },
    el('img', { class: 'lt', src: `/static/brand/diquizbuzz-${f}.svg`, alt: '', draggable: false }),
    el('img', { class: 'dk', src: `/static/brand/diquizbuzz-${f}-dark.svg`, alt: '', draggable: false }));
}

/** "Ai with DI", linking to the author's site. */
function creditLink() {
  return el('a', { class: 'credit-link', href: 'https://www.deepakingwale.com', target: '_blank', rel: 'noopener' }, el('b', {}, 'Ai with DI'));
}

/** Emoji thrown by phones rise up the screen, each with the thrower's avatar and name on a tag. Used by the projector and the
 *  host console. `items` = [{e: emoji, n: name, a: avatar}]. `small` = the host console's size. */
function reactionBurst(items, { small = false } = {}) {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  let layer = $('#react-layer');
  if (!layer) { layer = el('div', { id: 'react-layer', 'aria-hidden': 'true' }); document.body.append(layer); }
  (items || []).slice(0, 24).forEach((it, i) => {
    if (layer.childElementCount > 60 || !it || !it.e) return;
    const s = el('span', { class: 'float-emoji' + (small ? ' sm' : '') }, el('span', { class: 'fe' }, it.e),
      it.n ? el('span', { class: 'fe-name' }, it.a ? `${it.a} ${it.n}` : it.n) : null);
    s.style.left = (6 + Math.random() * 80) + '%';
    s.style.setProperty('--fs', ((small ? 1.6 : 2.6) + Math.random() * (small ? 1 : 1.8)) + 'rem');
    layer.append(s);
    const drift = (Math.random() - 0.5) * 16;
    s.animate([{ transform: 'translate(0, 0) scale(.6)', opacity: 0 }, { opacity: 1, offset: 0.12 }, { opacity: 1, offset: 0.7 }, { transform: `translate(${drift}vw, -78vh) scale(1.05)`, opacity: 0 }],
      { duration: 3000 + Math.random() * 1400, delay: i * 90, easing: 'ease-out', fill: 'backwards' }).onfinish = () => s.remove();
  });
}

/** Copyright line shown on every page. */
function siteFooter(cls = '') {
  return el('footer', { class: 'site-foot ' + cls },
    el('span', {}, `© ${new Date().getFullYear()} DiQuizBuzz`), el('span', { class: 'sep' }, ' · '),
    el('span', { class: 'credit' }, 'Designed and Developed by ', creditLink(), ' (Deepak Ingwale)'));
}

/* ---------------------------------------------------------------- shapes, avatars, names */
const SHAPES = ['▲', '◆', '●', '■', '★', '✚'];
const ADJ = ['Swift', 'Clever', 'Brave', 'Sneaky', 'Jolly', 'Mighty', 'Cosmic', 'Fuzzy', 'Lucky', 'Zesty', 'Quirky', 'Sunny', 'Turbo', 'Nimble', 'Plucky', 'Witty'];
const ANI = ['Panda', 'Fox', 'Otter', 'Koala', 'Falcon', 'Lynx', 'Walrus', 'Gecko', 'Llama', 'Badger', 'Penguin', 'Tiger', 'Owl', 'Narwhal', 'Dragon', 'Yeti'];
const pick = a => a[Math.floor(Math.random() * a.length)];
const randomName = () => `${pick(ADJ)} ${pick(ANI)}`;

/** Avatar chooser: groups is [[tab name, [emoji...]], ...]. Returns { preview, tabs, grid, get } - place the three nodes where you like. */
function avatarPicker(groups, initial) {
  const all = groups.flatMap(g => g[1]);
  let avatar = all.includes(initial) ? initial : pick(all);
  let tab = Math.max(0, groups.findIndex(g => g[1].includes(avatar)));
  const preview = el('div', { class: 'me-avatar', 'aria-hidden': 'true' }, avatar);
  const tabs = el('div', { class: 'avatar-tabs', role: 'tablist', 'aria-label': 'Avatar groups' });
  const grid = el('div', { class: 'avatar-grid', role: 'radiogroup', 'aria-label': 'Choose your avatar' });
  const draw = spin => {
    preview.textContent = avatar;
    if (spin) { preview.classList.add('spin'); setTimeout(() => preview.classList.remove('spin'), 220); }
    tabs.replaceChildren(...groups.map((g, i) => el('button', { type: 'button', class: 'avatar-tab', role: 'tab', 'aria-selected': String(i === tab), onclick: () => { tab = i; Sound.tap(); draw(); } }, g[0])));
    const sel = tabs.children[tab];                    // keep the chosen group in view (the strip scrolls sideways)
    if (sel) tabs.scrollLeft = sel.getBoundingClientRect().left - tabs.getBoundingClientRect().left + tabs.scrollLeft - (tabs.clientWidth - sel.offsetWidth) / 2;
    grid.replaceChildren(...groups[tab][1].map(a => el('button', { type: 'button', 'aria-label': a, 'aria-pressed': String(a === avatar), onclick: () => { avatar = a; Sound.tap(); haptic(8); draw(true); } }, a)));
  };
  draw();
  return { preview, tabs, grid, get: () => avatar, redraw: () => draw() };      // redraw() once the nodes are on the page (it centres the chosen tab)
}

function fmtScore(n) { return Number(n || 0).toLocaleString(); }
function ordinal(n) { const s = ['th', 'st', 'nd', 'rd'], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); }
function fmtDuration(sec) { const m = Math.round(sec / 60); return m < 1 ? '<1 min' : m + ' min'; }
function timeAgo(ts) {
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 90) return 'just now'; if (d < 3600) return Math.round(d / 60) + ' min ago';
  if (d < 86400) return Math.round(d / 3600) + ' h ago'; if (d < 86400 * 14) return Math.round(d / 86400) + ' days ago';
  return new Date(ts * 1000).toLocaleDateString();
}
function delta(n) { return n > 0 ? el('span', { class: 'delta up' }, icon('arrowup'), n) : n < 0 ? el('span', { class: 'delta down' }, icon('arrowdown'), -n) : null; }

/* ---------------------------------------------------------------- API */
async function api(path, { method = 'GET', body, raw } = {}) {
  const opts = { method, headers: {}, credentials: 'same-origin' };
  if (method !== 'GET') opts.headers['X-Quickz'] = '1'; // CSRF guard: cross-site pages cannot send custom headers
  if (raw) { opts.body = raw; opts.headers['Content-Type'] = raw.type || 'application/octet-stream'; }
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers['Content-Type'] = 'application/json'; }
  let res;
  try { res = await fetch(path, opts); } catch (e) { const err = new Error('Cannot reach the DiQuizBuzz server. Is it still running?'); err.status = 0; throw err; }
  let data = null;
  try { data = await res.json(); } catch (e) { /* not json */ }
  if (!res.ok) {
    const err = new Error((data && (data.error || data.detail)) || `Request failed (${res.status})`);
    err.status = res.status; err.code = data && data.code;
    if (res.status === 401 && !path.startsWith('/api/auth/')) dispatchEvent(new Event('quickz:unauth'));
    if (err.code === 'password_change_required') dispatchEvent(new Event('quickz:mustchange'));
    throw err;
  }
  return data;
}

/* ---------------------------------------------------------------- UI primitives */
const ui = {
  toast(msg, type = 'info', ms = type === 'error' ? Math.min(15000, Math.max(5000, String(msg).length * 75)) : 3500) {      // an error stays long enough to be read
    let box = $('#toasts');
    if (!box) { box = el('div', { id: 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.append(box); }
    const t = el('div', { class: 'toast ' + type }, type === 'success' ? icon('check') : type === 'error' ? icon('warn') : null, msg);
    box.append(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 300); }, ms);
  },
  /** modal({title, body, actions:[nodes], wide}) -> {el, close}. Esc and backdrop-click close it. */
  modal({ title, body, actions = [], wide = false, onClose } = {}) {
    const close = () => { if (dlg.open) dlg.close(); };
    const dlg = el('dialog', { class: 'modal' + (wide ? ' wide' : ''), 'aria-label': title || 'Dialog' },
      el('div', { class: 'modal-head' }, el('h2', {}, title || ''), el('button', { class: 'btn ghost sm icon-only', type: 'button', 'aria-label': 'Close', onclick: close }, icon('x'))),
      el('div', { class: 'modal-body' }, body),
      actions.length ? el('div', { class: 'modal-foot' }, actions) : null);
    dlg.addEventListener('close', () => { dlg.remove(); if (onClose) onClose(); });
    dlg.addEventListener('click', e => { if (e.target === dlg) close(); });
    document.body.append(dlg);
    dlg.showModal();
    return { el: dlg, close };
  },
  confirm({ title = 'Are you sure?', message = '', confirmText = 'Confirm', danger = false } = {}) {
    return new Promise(resolve => {
      let done = false;
      const finish = v => { if (!done) { done = true; resolve(v); } m.close(); };
      const m = ui.modal({
        title, body: el('p', { class: 'muted' }, message), onClose: () => { if (!done) { done = true; resolve(false); } },
        actions: [el('button', { class: 'btn', onclick: () => finish(false) }, 'Cancel'),
                  el('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), autofocus: true, onclick: () => finish(true) }, confirmText)],
      });
    });
  },
  prompt({ title, label, value = '', type = 'text', confirmText = 'OK', chips = [] } = {}) {
    return new Promise(resolve => {
      let done = false;
      const input = el('input', { class: 'input', type, value, 'aria-label': label || title, autofocus: true });
      const finish = v => { if (!done) { done = true; resolve(v); } m.close(); };
      const m = ui.modal({
        title, onClose: () => { if (!done) { done = true; resolve(null); } },
        body: el('form', { class: 'col', onsubmit: e => { e.preventDefault(); finish(input.value); } },
          label && el('label', { class: 'label' }, label), input,
          chips.length ? el('div', { class: 'row' }, chips.map(c => el('button', { type: 'button', class: 'chip', style: { cursor: 'pointer' }, onclick: () => { input.value = c; input.focus(); } }, c))) : null),
        actions: [el('button', { class: 'btn', onclick: () => finish(null) }, 'Cancel'), el('button', { class: 'btn primary', onclick: () => finish(input.value) }, confirmText)],
      });
      setTimeout(() => input.select(), 30);
    });
  },
};

/* ---------------------------------------------------------------- theme */
const Theme = {
  KEY: 'quickz:theme',
  get() { try { return localStorage.getItem(this.KEY) || 'auto'; } catch (e) { return 'auto'; } },
  apply() {
    const pref = this.get();
    const dark = pref === 'dark' || (pref === 'auto' && matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  },
  toggle() {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(this.KEY, next); } catch (e) { /* ignore */ }
    this.apply();
    return next;
  },
};

/* ---------------------------------------------------------------- connection */
/** Auto-reconnecting WebSocket with server clock sync and (optional) RTT reporting. */
class Conn {
  constructor(path, { onState, onStatus, onFatal, onError, onMsg, reportRtt = false }) {
    this.path = path; this.onState = onState; this.onStatus = onStatus || (() => {});
    this.onFatal = onFatal || (() => {}); this.onError = onError || (() => {}); this.onMsg = onMsg || null;   // onMsg: anything else (cues, reactions)
    this.reportRtt = reportRtt; this.offset = 0; this.samples = []; this.retry = 0; this.closed = false; this.everOpen = false;
    this.connect();
  }
  serverNow() { return Date.now() + this.offset; }
  connect() {
    const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
    const ws = this.ws = new WebSocket(proto + location.host + this.path);
    ws.onopen = () => {
      this.retry = 0; this.everOpen = true; this.onStatus(true);
      this.ping(); clearInterval(this.pingTimer);
      this.pingTimer = setInterval(() => this.ping(), 2500);
    };
    ws.onmessage = ev => {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.t === 'state') {
        if (!this.samples.length) this.offset = m.v.server_now - Date.now();
        this.onState(m.v);
      } else if (m.t === 'pong') {
        const rtt = Date.now() - m.c;
        this.samples.push({ rtt, offset: m.s + rtt / 2 - Date.now() });
        if (this.samples.length > 7) this.samples.shift();
        this.offset = this.samples.reduce((a, b) => (b.rtt < a.rtt ? b : a)).offset;
        if (this.reportRtt) this.send({ t: 'rtt', ms: rtt });
      } else if (m.t === 'error') this.onError(m.msg);
      else if (this.onMsg) this.onMsg(m);
    };
    ws.onclose = ev => {
      clearInterval(this.pingTimer);
      if (this.closed) return;
      this.onStatus(false);
      if ([4401, 4403, 4404].includes(ev.code)) { this.onFatal(ev.code); return; }
      setTimeout(() => this.connect(), Math.min(4000, 300 * 2 ** this.retry++));
    };
  }
  ping() { this.send({ t: 'ping', c: Date.now() }); }
  send(obj) { if (this.ws && this.ws.readyState === 1) { this.ws.send(JSON.stringify(obj)); return true; } return false; }
  close() { this.closed = true; clearInterval(this.pingTimer); if (this.ws) this.ws.close(); }
}

/** Calls tick(remainingMs, fraction) ~10x/s. Returns stop(). */
function runCountdown(getQ, serverNow, tick) {
  const id = setInterval(() => {
    const q = getQ();
    if (!q) return;
    const total = q.time_limit * 1000;
    const remaining = q.remaining_ms != null ? q.remaining_ms : Math.max(0, q.ends_at - serverNow());
    tick(remaining, Math.max(0, Math.min(1, remaining / total)));
  }, 100);
  return () => clearInterval(id);
}

/* ---------------------------------------------------------------- timer ring */
const RING_R = 20, RING_C = 2 * Math.PI * RING_R;
function makeRing(size = 56) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 48 48');
  svg.innerHTML = `<circle class="track" cx="24" cy="24" r="${RING_R}" stroke-width="5"/><circle class="prog" cx="24" cy="24" r="${RING_R}" stroke-width="5" stroke-dasharray="${RING_C}" stroke-dashoffset="0"/>`;
  const val = el('div', { class: 'val' }, '');
  const root = el('div', { class: 'ring', style: { '--size': size + 'px' }, role: 'timer', 'aria-label': 'Time left' }, svg, val);
  const prog = svg.querySelector('.prog');
  return {
    el: root,
    set(frac, ms) {
      prog.style.strokeDashoffset = String(RING_C * (1 - frac));
      const s = Math.ceil(ms / 1000);
      if (val.textContent !== String(s)) val.textContent = s;
      root.classList.toggle('low', ms <= 5000);
    },
  };
}

/* ---------------------------------------------------------------- countdown clock
   An analog dial that runs BACKWARDS: the wedge of remaining time shrinks and the hand ticks
   counter-clockwise once per second, with the seconds left shown in the hub. Same interface as makeRing. */
const CLOCK_TICKS = (() => {
  let s = '';
  for (let i = 0; i < 60; i++) {
    const major = i % 5 === 0, a = i * 6 * Math.PI / 180, r1 = major ? 36 : 39, r2 = 42;
    s += `<line x1="${(50 + Math.sin(a) * r1).toFixed(2)}" y1="${(50 - Math.cos(a) * r1).toFixed(2)}" x2="${(50 + Math.sin(a) * r2).toFixed(2)}" y2="${(50 - Math.cos(a) * r2).toFixed(2)}" stroke-width="${major ? 2.4 : 1}"/>`;
  }
  return s;
})();
function clockPie(frac) {
  if (frac <= 0) return '';
  if (frac >= 0.9999) return 'M50 5 A45 45 0 1 1 49.99 5 Z';
  const a = frac * 2 * Math.PI;
  return `M50 50 L50 5 A45 45 0 ${frac > 0.5 ? 1 : 0} 1 ${(50 + 45 * Math.sin(a)).toFixed(2)} ${(50 - 45 * Math.cos(a)).toFixed(2)} Z`;
}
function makeClock(size = 64) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 100 100');
  svg.innerHTML = `<circle class="face" cx="50" cy="50" r="47"/><path class="wedge" d=""/><g class="ticks">${CLOCK_TICKS}</g>` +
    `<g class="hand"><line x1="50" y1="52" x2="50" y2="13"/><circle cx="50" cy="12" r="4.5"/></g><circle class="hub" cx="50" cy="50" r="18"/>`;
  const val = el('div', { class: 'val num' }, '');
  const root = el('div', { class: 'clock', style: { '--size': size + 'px' }, role: 'timer', 'aria-label': 'Time left' }, svg, val);
  const wedge = svg.querySelector('.wedge'), hand = svg.querySelector('.hand');
  let total = 0, lastSec = null;
  return {
    el: root,
    set(frac, ms) {
      if (!total || ms > total) total = frac > 0.001 ? ms / frac : ms;
      const sec = Math.ceil(ms / 1000);
      const step = Math.min(1, (sec * 1000) / total);                // ticks once a second, like a real countdown
      if (sec !== lastSec) {
        lastSec = sec;
        wedge.setAttribute('d', clockPie(step));
        hand.style.transform = `rotate(${step * 360}deg)`;
        val.textContent = sec;
        root.classList.remove('tick'); void root.offsetWidth; root.classList.add('tick');
      }
      root.classList.toggle('low', ms <= 5000);
      root.classList.toggle('paused', false);
    },
    pause(on) { root.classList.toggle('paused', !!on); },
  };
}

/* ---------------------------------------------------------------- sound + haptics */
const Sound = {
  ctx: null, out: null, key: 'quickz:sound',
  volume: (() => { try { const v = parseFloat(localStorage.getItem('quickz:vol')); return v >= 0 && v <= 1 ? v : 0.8; } catch (e) { return 0.8; } })(),
  on: (() => { try { return localStorage.getItem('quickz:sound') !== 'off'; } catch (e) { return true; } })(),
  /** Which setting remembers on/off for this page, and what it is the first time. (Phones start silent: the big screen plays the sound.) */
  useKey(key, defaultOn) { this.key = key; try { const v = localStorage.getItem(key); this.on = v == null ? defaultOn : v === 'on'; } catch (e) { this.on = defaultOn; } },
  setOn(v) { this.on = v; try { localStorage.setItem(this.key, v ? 'on' : 'off'); } catch (e) { /* ignore */ } },
  setVolume(v) {
    this.volume = Math.max(0, Math.min(1, v));
    try { localStorage.setItem('quickz:vol', String(this.volume)); } catch (e) { /* ignore */ }
    if (this.out) this.out.gain.value = this.volume;
  },
  unlock() {
    try {
      this.ctx = this.ctx || new (window.AudioContext || window.webkitAudioContext)();
      if (!this.out) { this.out = this.ctx.createGain(); this.out.gain.value = this.volume; this.out.connect(this.ctx.destination); }
      if (this.ctx.state === 'suspended') this.ctx.resume();
    } catch (e) { /* no audio */ }
    return !!this.ctx && this.ctx.state !== 'suspended';
  },
  tone(freq, start, dur, type = 'sine', vol = 0.16, slideTo = null) {
    if (!this.on || !this.ctx) return;
    this.toneAt(this.ctx.currentTime + start, freq, dur, type, vol, slideTo);
  },
  /** One enveloped oscillator note at an absolute audio-clock time (used by the music scheduler). */
  toneAt(t0, freq, dur, type = 'sine', vol = 0.16, slideTo = null) {
    if (!this.on || !this.ctx) return;
    const osc = this.ctx.createOscillator(), g = this.ctx.createGain();
    osc.type = type; osc.frequency.setValueAtTime(freq, t0);
    if (slideTo) osc.frequency.exponentialRampToValueAtTime(slideTo, t0 + dur);
    g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(vol, t0 + 0.012); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(this.out || this.ctx.destination); osc.start(t0); osc.stop(t0 + dur + 0.03);
  },
  buzz() { this.tone(180, 0, 0.55, 'sawtooth', 0.22, 90); this.tone(185, 0, 0.55, 'square', 0.07, 92); },
  good() { this.tone(660, 0, 0.12); this.tone(880, 0.1, 0.12); this.tone(1320, 0.2, 0.28, 'sine', 0.14); },
  bad() { this.tone(240, 0, 0.22, 'triangle', 0.18, 170); this.tone(170, 0.16, 0.3, 'triangle', 0.16, 110); },
  tick() { this.tone(1000, 0, 0.04, 'square', 0.035); },
  tap() { this.tone(520, 0, 0.06, 'triangle', 0.1); },
  pop() { this.tone(500, 0, 0.1, 'sine', 0.08, 900); },
  start() { [392, 523, 659].forEach((f, i) => this.tone(f, i * 0.09, 0.2, 'triangle', 0.14)); },
  fanfare() { [523, 659, 784, 1046, 784, 1046, 1318].forEach((f, i) => this.tone(f, i * 0.13, 0.32, 'triangle', 0.17)); },
  whoosh() { this.tone(260, 0, 0.28, 'sine', 0.06, 1100); },
  reveal() { this.tone(660, 0, 0.1, 'triangle', 0.12); this.tone(990, 0.09, 0.2, 'triangle', 0.12); },
  drum() { for (let i = 0; i < 6; i++) this.tone(110 + i * 4, i * 0.12, 0.1, 'triangle', 0.16 + i * 0.01, 70); },
  thud() { this.tone(90, 0, 0.35, 'triangle', 0.25, 40); },
};
const haptic = p => { try { if (navigator.vibrate) navigator.vibrate(p); } catch (e) { /* unsupported */ } };

/* ---------------------------------------------------------------- confetti + count-up */
function confetti({ count = 120, duration = 2800, origin = null } = {}) {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const cv = el('canvas', { style: { position: 'fixed', inset: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 900 } });
  document.body.append(cv);
  const dpr = Math.min(devicePixelRatio || 1, 2);
  cv.width = innerWidth * dpr; cv.height = innerHeight * dpr;
  const ctx = cv.getContext('2d'); ctx.scale(dpr, dpr);
  const colors = ['#1F4FD8', '#0C8C7B', '#E5A400', '#D6405A', '#2D6BD6', '#E5681E'];
  const ox = origin ? origin.x : innerWidth / 2, oy = origin ? origin.y : innerHeight * 0.35;
  const parts = Array.from({ length: count }, () => {
    const a = Math.random() * Math.PI * 2, sp = 4 + Math.random() * 9;
    return { x: ox, y: oy, vx: Math.cos(a) * sp * (origin ? 1 : 1.6), vy: Math.sin(a) * sp - 6, w: 6 + Math.random() * 7, h: 4 + Math.random() * 6, r: Math.random() * 6, vr: (Math.random() - .5) * .4, c: pick(colors) };
  });
  const t0 = performance.now();
  (function frame(now) {
    const t = now - t0;
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    for (const p of parts) {
      p.vy += 0.28; p.vx *= 0.992; p.x += p.vx; p.y += p.vy; p.r += p.vr;
      ctx.save(); ctx.globalAlpha = Math.max(0, 1 - t / duration); ctx.translate(p.x, p.y); ctx.rotate(p.r);
      ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h); ctx.restore();
    }
    if (t < duration) requestAnimationFrame(frame); else cv.remove();
  })(t0);
}

function countUp(node, to, { from = 0, ms = 900, fmt = fmtScore } = {}) {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches || from === to) { node.textContent = fmt(to); return; }
  const t0 = performance.now();
  (function step(now) {
    const k = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - k, 3);
    node.textContent = fmt(Math.round(from + (to - from) * e));
    if (k < 1) requestAnimationFrame(step);
  })(t0);
}

/** The quiz's background picture fills the whole screen at any resolution (scaled to cover, centred), under a soft wash so the text stays readable. */
let bgNow = null, bgRatio = 0;
/** A wide picture fills the screen. One that would lose too much to cropping (a square or portrait picture on a wide screen, or the other way round)
 *  is shown whole instead, with a soft blurred copy of itself filling the rest. */
function fitBackground() {
  const layer = document.querySelector('.screen-bg');
  if (!layer || !bgRatio) return;
  const screen = innerWidth / Math.max(1, innerHeight);
  layer.classList.toggle('contain', 1 - Math.min(bgRatio, screen) / Math.max(bgRatio, screen) > 0.25);
}
addEventListener('resize', fitBackground);
function applyBackground(url, wash) {
  url = url || '';
  const washNow = (Number.isFinite(Number(wash)) && wash !== '' && wash != null ? Math.max(0, Math.min(85, Number(wash))) : 40) / 100 + (document.body.classList.contains('player') ? 0.1 : 0);      // a phone is held closer, so a little more wash
  let layer = document.querySelector('.screen-bg');
  if (layer) layer.style.setProperty('--wash', String(Math.min(0.9, washNow)));
  if (url === bgNow) return;
  bgNow = url;
  if (!url) { layer?.remove(); document.body.classList.remove('has-bg'); return; }
  const done = () => {
    if (!layer) { layer = el('div', { class: 'screen-bg', 'aria-hidden': 'true' }); document.body.prepend(layer); }
    const css = `url("${url.replace(/"/g, '%22')}")`;
    layer.replaceChildren(el('div', { class: 'bg-blur', style: { backgroundImage: css } }), el('div', { class: 'bg-main', style: { backgroundImage: css } }));
    bgRatio = probe.naturalWidth / Math.max(1, probe.naturalHeight);
    layer.style.setProperty('--wash', String(Math.min(0.9, washNow)));
    document.body.classList.add('has-bg');
    fitBackground();
  };
  const probe = new Image();                                // show it only once it has loaded, so there is no flash of an empty frame
  probe.onload = done; probe.onerror = () => { bgNow = null; };
  probe.src = url;
}

/* ---------------------------------------------------------------- picture viewer
   Tap or click a picture (a question's, an answer's, an explanation's, or one you uploaded) to see it large: pinch, double-tap, mouse wheel
   or the buttons to zoom, drag to move around, and a full-screen button. The big projector screen does not use this. */
function openViewer(src, alt = '') {
  if (!src || document.querySelector('.viewer')) return;
  const opener = document.activeElement;
  let scale = 1, tx = 0, ty = 0, pinch = null, drag = null, lastTap = 0, moved = false;
  const pts = new Map();
  const img = el('img', { class: 'viewer-img', src, alt: alt || 'Picture', draggable: false });
  const stage = el('div', { class: 'viewer-stage' }, img);
  const pct = el('span', { class: 'viewer-pct', 'aria-live': 'polite' }, '100%');
  const limits = () => ({ x: Math.max(0, (img.offsetWidth * scale - stage.clientWidth) / 2), y: Math.max(0, (img.offsetHeight * scale - stage.clientHeight) / 2) });
  const apply = () => {
    const L = limits(); tx = Math.max(-L.x, Math.min(L.x, tx)); ty = Math.max(-L.y, Math.min(L.y, ty));
    img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    stage.classList.toggle('zoomed', scale > 1.01); pct.textContent = Math.round(scale * 100) + '%';
    out.disabled = scale <= 1.01; fit.disabled = scale <= 1.01; into.disabled = scale >= 8;
  };
  /** Zoom to `s`, keeping the point under the finger or cursor where it is. */
  const zoomTo = (s, cx = stage.clientWidth / 2, cy = stage.clientHeight / 2) => {
    s = Math.max(1, Math.min(8, s));
    const k = s / scale, ox = cx - stage.clientWidth / 2, oy = cy - stage.clientHeight / 2;
    tx = ox - (ox - tx) * k; ty = oy - (oy - ty) * k; scale = s;
    if (scale <= 1.01) { scale = 1; tx = 0; ty = 0; }
    apply();
  };
  const at = e => { const r = stage.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  const btn = (label, ic, onclick) => el('button', { class: 'viewer-btn', type: 'button', 'aria-label': label, title: label, onclick }, icon(ic));
  const into = btn('Zoom in', 'plus', () => zoomTo(scale * 1.5)), out = btn('Zoom out', 'minus', () => zoomTo(scale / 1.5)), fit = btn('Fit to screen', 'refresh', () => zoomTo(1));
  const root = el('div', { class: 'viewer', role: 'dialog', 'aria-modal': 'true', 'aria-label': alt ? `Picture: ${alt}` : 'Picture viewer' });
  const fsSupported = !!(root.requestFullscreen || root.webkitRequestFullscreen);
  const isFull = () => !!(document.fullscreenElement || document.webkitFullscreenElement);
  const full = fsSupported ? btn('Full screen', 'expand', () => {
    if (isFull()) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    else { const p = (root.requestFullscreen || root.webkitRequestFullscreen).call(root); if (p && p.catch) p.catch(() => { /* the browser said no: the viewer already fills the page */ }); }
  }) : null;
  const close = () => {
    document.removeEventListener('keydown', onKey, true);
    if (isFull()) { try { (document.exitFullscreen || document.webkitExitFullscreen).call(document); } catch (e) { /* already out */ } }
    root.remove(); document.body.classList.remove('viewer-open');
    if (opener && opener.focus && document.contains(opener)) opener.focus();
  };
  const closeBtn = el('button', { class: 'viewer-close', type: 'button', 'aria-label': 'Close', title: 'Close (Esc)', onclick: close }, icon('x'));
  const bar = el('div', { class: 'viewer-bar', role: 'toolbar', 'aria-label': 'Picture controls' }, out, pct, into, fit, full);
  root.append(stage, bar, closeBtn, alt ? el('div', { class: 'viewer-cap' }, alt) : null);
  const onKey = e => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomTo(scale * 1.5); }
    else if (e.key === '-' || e.key === '_') { e.preventDefault(); zoomTo(scale / 1.5); }
    else if (e.key === '0') { e.preventDefault(); zoomTo(1); }
    else if (e.key.startsWith('Arrow') && scale > 1) { e.preventDefault(); const d = 60; if (e.key === 'ArrowLeft') tx += d; else if (e.key === 'ArrowRight') tx -= d; else if (e.key === 'ArrowUp') ty += d; else ty -= d; apply(); }
    else if (e.key === 'Tab') {                                                      // keep the focus inside the viewer
      const items = [...root.querySelectorAll('button:not([disabled])')]; if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  stage.addEventListener('wheel', e => { e.preventDefault(); const [x, y] = at(e); zoomTo(scale * (e.deltaY < 0 ? 1.18 : 1 / 1.18), x, y); }, { passive: false });
  stage.addEventListener('pointerdown', e => {
    stage.setPointerCapture(e.pointerId); pts.set(e.pointerId, { x: e.clientX, y: e.clientY }); moved = false;
    if (pts.size === 2) { const [a, b] = [...pts.values()]; pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, s: scale }; drag = null; }
    else drag = { x: e.clientX, y: e.clientY, tx, ty };
  });
  stage.addEventListener('pointermove', e => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2 && pinch) {
      const [a, b] = [...pts.values()], r = stage.getBoundingClientRect(); moved = true;
      zoomTo(pinch.s * Math.hypot(a.x - b.x, a.y - b.y) / pinch.d, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
    } else if (drag && scale > 1.01) {
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 4) moved = true;
      tx = drag.tx + e.clientX - drag.x; ty = drag.ty + e.clientY - drag.y; apply();
    }
  });
  const release = e => {
    const wasOne = pts.size === 1; pts.delete(e.pointerId);
    if (pts.size < 2) pinch = null;
    if (pts.size === 1) { const [p] = [...pts.values()]; drag = { x: p.x, y: p.y, tx, ty }; } else drag = null;
    if (wasOne && !moved && e.type === 'pointerup') {                                // a tap: double-tap toggles the zoom, a tap on the dark area closes
      const now = Date.now();
      if (now - lastTap < 320) { const [x, y] = at(e); zoomTo(scale > 1.01 ? 1 : 2.5, x, y); lastTap = 0; }
      else { lastTap = now; if (e.target === stage && scale <= 1.01) setTimeout(() => { if (lastTap && Date.now() - lastTap >= 300 && root.isConnected) close(); }, 330); }
    }
  };
  stage.addEventListener('pointerup', release); stage.addEventListener('pointercancel', release);
  document.addEventListener('keydown', onKey, true);
  addEventListener('resize', apply);
  document.body.classList.add('viewer-open'); document.body.append(root);
  apply(); closeBtn.focus({ preventScroll: true });
}

/** The small magnifier on a picture. A span (not a button) so it can sit inside an answer tile without nesting buttons. */
function zoomControl(src, alt = '') {
  const open = e => { e.preventDefault(); e.stopPropagation(); openViewer(src, alt); };
  const c = el('span', { class: 'zoom-btn', role: 'button', tabindex: '0', 'aria-label': alt ? `View larger: ${alt}` : 'View picture larger', title: 'View larger' }, icon('expand'));
  ['pointerdown', 'mousedown', 'touchstart'].forEach(t => c.addEventListener(t, e => e.stopPropagation()));           // never counts as picking the answer underneath
  c.addEventListener('click', open);
  c.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') open(e); });
  return c;
}

const ZOOM_PICTURES = '.qv-img img, .o-pic img, .explain img, .next-up img, .pic img, .callout img';       // question, answer, explanation and uploaded pictures
const ZOOM_SKIP = '.viewer, .pin-stage, .jig-board, .jig-stage, .jig-solved, .screen-bg';
function decorateZoom(root = document) {
  root.querySelectorAll(ZOOM_PICTURES).forEach(img => {
    if (img.dataset.zoom || img.closest(ZOOM_SKIP) || !img.getAttribute('src')) return;
    if (img.closest('.qv-img[data-fx]')) return;                         // a "guess the picture" question must not be zoomed to the clear picture mid-question
    const box = img.parentElement;
    if (!box) return;
    if (!img.complete || !img.naturalWidth) { img.addEventListener('load', () => decorateZoom(root), { once: true }); return; }       // measure it once it has loaded
    const wide = (img.offsetWidth || 0) >= 110, tile = !!img.closest('.opt, button, label, .ord-item, .mt-item, .seq-row');
    if (!wide && tile && (img.offsetWidth || 0) < 56) return;            // tiny ordering and matching cards are drag targets: leave them alone
    if (!wide && tile) { img.dataset.zoom = '1'; return; }              // an answer tile's small picture: no button to cover it, and the tile keeps meaning "pick this"
    img.dataset.zoom = '1';
    const alt = img.alt === 'Picture' ? '' : img.alt, src = () => img.currentSrc || img.src;
    if (wide) {                                                          // a magnifier in the corner; inside an answer tile it never picks the answer
      if (getComputedStyle(box).position === 'static') box.style.position = 'relative';
      box.classList.add('has-zoom'); box.append(zoomControl(src(), alt));
    }
    if (!tile) {                                                         // a free-standing picture (an answer tile keeps meaning "pick this") opens when it is clicked too
      img.classList.add('zoomable'); img.title = img.title || 'Click to view larger';
      img.addEventListener('click', () => openViewer(src(), alt));
    }
  });
}
/** Keep every picture under `root` zoomable as the page redraws itself. */
function enableZoom(root) {
  let timer = null;
  const run = () => { timer = null; decorateZoom(root); };
  new MutationObserver(() => { if (!timer) timer = setTimeout(run, 120); }).observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['src', 'data-fx'] });
  decorateZoom(root);
}
