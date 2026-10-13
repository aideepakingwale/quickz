'use strict';
/* Projector screen: read-only. Never receives the correct answer before the reveal. */

const code = decodeURIComponent(location.pathname.split('/').filter(Boolean)[1] || '');
// opened from the host console, the projector carries a key that lets it tell the server "the clip ended"
const SCREEN_KEY = new URLSearchParams(location.search).get('k') || '';
const main = $('#main');
let conn = null, last = null, sceneKey = null, scene = null, joinBase = location.origin;
let ring = null, lastSec = null, lastHolder = null, fanfared = false;

const hostText = () => joinBase.replace(/^https?:\/\//, '');
const sceneEl = (cls, ...kids) => el('section', { class: 'scene ' + cls }, ...kids);

function showScene(key, build) {
  if (key === sceneKey && scene) { if (scene.update) scene.update(last); return; }
  sceneKey = key;
  scene = build();
  main.replaceChildren(scene.root);
}

/* ---------------------------------------------------------------- chrome */
function initChrome() {
  $('.logo .mark').append(icon('bolt'));
  $('#s-foot').append(el('div', { class: 's-foot-line' }, `© ${new Date().getFullYear()} Quickz · Designed and Developed by `, el('b', {}, 'Ai with DI'), ' (Deepak Ingwale)'));
  const tb = $('#theme-btn');
  const paintTheme = () => tb.replaceChildren(icon(document.documentElement.dataset.theme === 'dark' ? 'sun' : 'moon'));
  const flipTheme = () => { const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = next; try { localStorage.setItem('quickz:theme', next); } catch (e) { /* ignore */ } paintTheme(); };
  tb.addEventListener('click', e => { e.stopPropagation(); flipTheme(); }); paintTheme();
  addEventListener('keydown', e => { if (e.key === 'd' || e.key === 'D') flipTheme(); });
  const sb = $('#sound-btn'), tools = $('#tools');
  const paintSound = () => {
    sb.replaceChildren(icon(Sound.on && Sound.ctx && Sound.ctx.state === 'running' ? 'volume' : 'mute'),
      Sound.ctx && Sound.ctx.state === 'running' ? (Sound.on ? 'Sound on' : 'Sound off') : 'Click to enable sound');
  };
  sb.addEventListener('click', e => { e.stopPropagation(); if (!Sound.unlock()) { paintSound(); return; } Sound.setOn(!Sound.on); if (Sound.on) Sound.pop(); paintSound(); });
  paintSound(); tools.classList.add('show');
  const fs = $('#fs-btn'); fs.append(icon('expand'));
  const toggleFs = () => { if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen().catch(() => {}); };
  fs.addEventListener('click', e => { e.stopPropagation(); toggleFs(); });
  const unlock = () => { if (Sound.unlock()) { paintSound(); setTimeout(() => tools.classList.remove('show'), 2500); } };
  addEventListener('pointerdown', unlock); addEventListener('keydown', unlock);
  addEventListener('keydown', e => { if (e.key === 'f' || e.key === 'F') toggleFs(); if (e.key === 'm' || e.key === 'M') { Sound.setOn(!Sound.on); paintSound(); } });
  let idle;
  addEventListener('mousemove', () => { document.body.classList.remove('idle'); clearTimeout(idle); idle = setTimeout(() => document.body.classList.add('idle'), 3000); });
}

/* ---------------------------------------------------------------- scenes */
function lobbyScene(v) {
  ring = null;
  $('#s-join').replaceChildren();
  showScene('lobby', () => {
    const list = el('div', { class: 'wall-list', 'aria-live': 'polite' });
    const countN = el('span', { class: 'n num' }, '0');
    const empty = el('div', { class: 'wall-empty' }, 'Waiting for the first player to join…');
    const chips = new Map();
    const sync = nv => {
      const names = nv.names || [];
      const want = new Set(names.map(x => x.n));
      for (const [n, node] of chips) if (!want.has(n)) { node.remove(); chips.delete(n); }
      let added = 0;
      for (const x of names) if (!chips.has(x.n)) {
        const c = el('span', { class: 'pchip' }, el('i', {}, x.a), x.n);
        c.style.animationDelay = Math.min(added * 60, 400) + 'ms';
        chips.set(x.n, c); list.append(c); added++;
      }
      if (added && chips.size > added) Sound.pop();
      countN.textContent = nv.players;
      empty.hidden = nv.players > 0;
      list.className = 'wall-list' + (nv.players > 40 ? ' dense denser' : nv.players > 18 ? ' dense' : '');
    };
    const root = sceneEl('lobby',
      el('div', { class: 'left' },
        el('h1', {}, v.title),
        v.description ? mdNode(v.description, 'desc') : null,
        el('p', { class: 'how' }, 'Scan the QR code, or go to ', el('b', {}, hostText()), ' and enter this code:'),
        el('div', { class: 'code', 'aria-label': 'Game code ' + v.code }, v.code.split('').map(d => el('span', {}, d)))),
      el('div', { class: 'qr-card' }, el('img', { src: `/qr/${encodeURIComponent(v.code)}.svg`, alt: `QR code to join game ${v.code}` }), el('div', { class: 'cap' }, 'Scan to join')),
      el('div', { class: 'wall' }, el('div', { class: 'wall-head' }, countN, el('span', {}, 'players ready')), empty, list));
    sync(v);
    return { root, update: sync };
  });
}

/** The clip on the projector: plays through the room, drives the "timer starts when it ends" rule. */
function screenMedia(q) {
  const m = q.media;
  if (!m || !m.url) return { node: null, el: null };
  const media = mediaPlayer(m, { controls: m.kind === 'video', big: true, onEnded: () => { if (conn) conn.send({ t: 'media_ended' }); } });
  const overlay = el('button', { class: 'play-overlay hidden', type: 'button' }, icon('play'), 'Click to play');
  const bars = m.kind === 'audio' ? el('div', { class: 'eq', 'aria-hidden': 'true' }, [0, 1, 2, 3, 4, 5, 6].map(i => el('i', { style: { animationDelay: (i * 0.11) + 's' } }))) : null;
  const card = el('div', { class: 's-media ' + m.kind }, m.kind === 'audio' ? el('div', { class: 's-media-audio' }, bars, el('div', { class: 's-media-title' }, 'Listen carefully…')) : null, media, overlay);
  if (m.kind === 'audio') media.classList.add('hidden-audio');
  const start = () => { const p = media.play(); if (p && p.catch) p.catch(() => overlay.classList.remove('hidden')); };
  overlay.addEventListener('click', () => { Sound.unlock(); start(); });
  media.addEventListener('playing', () => { overlay.classList.add('hidden'); card.classList.add('playing'); });
  media.addEventListener('pause', () => card.classList.remove('playing'));
  media.addEventListener('ended', () => card.classList.remove('playing'));
  return { node: card, el: media, nonce: q.media_nonce, start, restart() { media.currentTime = 0; start(); } };
}

function qTextClass(t) { return 'q-text' + (t.length > 160 ? ' xlong' : t.length > 90 ? ' long' : ''); }

function topRow(v, live = true) {
  const q = v.q;
  if (live) { ring = makeClock(160); ring.el.style.removeProperty('--size'); }
  return el('div', { class: 'q-top' }, el('span', { class: 'pill' }, `Question ${q.index + 1} of ${q.total}`),
    el('span', { class: 'pill pts' }, icon('bolt'), `${fmtScore(q.points)} pts`),
    q.only_players ? el('span', { class: 'pill win' }, icon('bolt'), 'Tie-breaker: ' + q.only_players.map(p => `${p.avatar} ${p.nickname}`).join(' vs ')) : null,
    q.winners ? el('span', { class: 'pill win' }, icon('trophy'), winnersText(q)) : null, live ? ring.el : null);
}

function questionScene(v) {
  const q = v.q;
  $('#s-join').replaceChildren(hostText() + ' · ', el('b', {}, v.code));
  showScene(`q:${q.id}`, () => {
    fanfared = false; lastSec = null; Sound.start();
    const bar = el('i'), label = el('span', { class: 'num' }, '');
    const upd = nv => {
      root.classList.toggle('await', !!nv.q.awaiting_media);
      const wp = $$('.pill.win', root).pop(); if (wp && nv.q.winners) wp.lastChild.textContent = winnersText(nv.q);
      if (sm.el && nv.q.media_nonce !== sm.nonce) { sm.nonce = nv.q.media_nonce; sm.restart(); }
      if (nv.q.type === 'buzzer') return;
      const pct = nv.eligible ? Math.min(100, 100 * nv.answered / nv.eligible) : 0;
      bar.style.width = pct + '%'; label.textContent = `${nv.answered} of ${nv.eligible} answered`;
    };
    const sm = screenMedia(q);
    const textNode = richNode(q.text, qTextClass(plainText(q.text)) + (sm.node ? ' compact' : ''));
    let body;
    if (q.type === 'buzzer') {
      body = el('div', { class: 'buzz-wait' }, el('div', { class: 'bell' }, icon('bell')), el('h2', {}, 'BUZZER ROUND'), el('p', {}, 'First to hit the buzzer on their phone answers out loud'));
    } else {
      const long = q.options.some(o => plainText(o.text).length > 40);
      body = el('div', { class: 'answers' + (q.options.length > 4 ? ' three' : '') }, q.options.map((o, i) =>
        el('div', { class: `ans a${i % 6}` + (long ? ' long' : '') }, el('i', { class: 'shape' }, SHAPES[i % 6]), el('span', {}, richInline(o.text)))));
    }
    const root = sceneEl('', topRow(v),
      el('div', { class: 'q-body' }, q.image ? el('div', { class: 'q-main' }, textNode, el('img', { class: 'q-img', src: q.image, alt: '' })) : textNode,
        sm.node, el('div', { class: 'await-note' }, icon('clock'), 'Answers open when the clip ends'),
        q.type === 'buzzer' ? null : body),
      q.type === 'buzzer' ? body : null,
      q.type === 'buzzer' ? null : el('div', { class: 'progress-row' }, el('div', { class: 'track' }, bar), label));
    upd(v);
    if (sm.start) setTimeout(sm.start, 300);
    return { root, update: upd };
  });
}

function revealScene(v) {
  const q = v.q, r = v.reveal, correct = new Set(r.correct);
  $('#s-join').replaceChildren(hostText() + ' · ', el('b', {}, v.code));
  showScene(`r:${q.id}`, () => {
    ring = null; lastHolder = null;
    const max = Math.max(1, ...Object.values(r.distribution));
    let content;
    if (q.type === 'buzzer') {
      content = el('div', { class: 'winner-card' }, el('div', { class: 'av' }, r.buzz_winner ? icon('check') : icon('x')), el('h2', {}, r.buzz_winner ? `${r.buzz_winner} got it!` : 'Nobody got it'),
        el('p', {}, r.buzz_winner ? 'Quickest on the buzzer, and right.' : 'Nobody buzzed in with the right answer.'));
    } else {
      content = el('div', { class: 'rows' }, q.options.map((o, i) => {
        const n = r.distribution[o.id] || 0;
        const row = el('div', { class: `rrow a${i % 6} ${correct.has(o.id) ? 'right' : 'wrong'}` }, el('i', { class: 'fill' }), el('i', { class: 'shape' }, SHAPES[i % 6]),
          el('span', { class: 't' }, richInline(o.text)), el('span', { class: 'cnt num' }, n));
        requestAnimationFrame(() => requestAnimationFrame(() => { row.firstChild.style.width = (100 * n / max) + '%'; }));
        return row;
      }));
    }
    const facts = el('div', { class: 'facts' },
      q.type !== 'buzzer' ? el('span', { class: 'fact' }, icon('check'), `${r.correct_count} of ${r.answered} got it right`) : null,
      r.fastest ? el('span', { class: 'fact' }, icon('bolt'), `Fastest: ${r.fastest.avatar} ${r.fastest.nickname} · ${(r.fastest.ms / 1000).toFixed(1)}s`) : null);
    const board = el('aside', { class: 'board' }, el('h3', {}, icon('trophy'), 'Leaderboard'),
      v.leaderboard.slice(0, 5).map((p, i) => {
        const row = el('div', { class: 'lrow' }, el('span', { class: 'rk' }, p.rank), el('span', { class: 'av' }, p.avatar), el('span', { class: 'nm' }, p.nickname),
          p.delta ? delta(p.delta) : null, el('span', { class: 'sc num' }, fmtScore(p.score)));
        row.style.animationDelay = (150 + i * 90) + 'ms';
        return row;
      }));
    return { root: sceneEl('reveal', el('div', { class: 'l' }, topRow(v, false), richNode(q.text, 'q-text'), content, claimedList(r, '', { big: true }), r.explanation ? el('div', { class: 'callout' }, r.explanation) : null, facts), board) };
  });
}

function finalScene(v) {
  ring = null;
  $('#s-join').replaceChildren();
  showScene('final', () => {
    if (!fanfared) { fanfared = true; Sound.fanfare(); confetti({ count: 220, duration: 5000 }); setTimeout(() => confetti({ count: 120, duration: 4000 }), 1200); }
    const top = v.leaderboard.slice(0, 3);
    const order = [1, 0, 2].filter(i => top[i]);
    const medals = ['1', '2', '3'];
    const rest = v.leaderboard.slice(3, 9);
    return {
      root: sceneEl('final', el('h1', {}, 'Final results'), el('p', { class: 'sub' }, `${v.title} · ${v.players} players`),
        el('div', { class: 'podium' }, order.map(i => el('div', { class: `pstep p${i + 1}` },
          el('div', { class: 'who', style: { animationDelay: (i === 0 ? 1.3 : i === 1 ? 0.9 : 0.6) + 's' } }, el('div', { class: 'av' }, top[i].avatar), el('div', { class: 'nm' }, top[i].nickname), el('div', { class: 'pt num' }, fmtScore(top[i].score) + ' pts')),
          el('div', { class: 'block' }, medals[i])))),
        rest.length ? el('div', { class: 'rest' }, rest.map(p => el('div', { class: 'lrow' }, el('span', { class: 'rk' }, p.rank), el('span', { class: 'av' }, p.avatar), el('span', { class: 'nm' }, p.nickname), el('span', { class: 'sc num' }, fmtScore(p.score))))) : null),
    };
  });
}

function updateSplash(v) {
  const b = v.phase === 'question' && v.buzz && v.buzz.holder ? v.buzz : null;
  const sp = $('#splash');
  if (b) {
    if (lastHolder !== b.holder) { Sound.buzz(); sp.replaceChildren(el('div', { class: 'siren' }, '🚨'), el('div', { class: 'av' }, b.holder_avatar || ''), el('div', { class: 'who' }, b.holder_name), el('div', { class: 'sub' }, 'buzzed in first!')); }
    sp.classList.remove('hidden'); lastHolder = b.holder;
  } else { sp.classList.add('hidden'); lastHolder = null; }
}

function render(v) {
  $('#s-title').textContent = v.title;
  document.title = 'Quickz · ' + v.title;
  if (v.phase === 'lobby') lobbyScene(v);
  else if (v.phase === 'question') questionScene(v);
  else if (v.phase === 'reveal') revealScene(v);
  else finalScene(v);
  updateSplash(v);
}

function errorBox(title, sub) {
  main.replaceChildren(el('div', { class: 'error-box' }, el('h1', {}, title), el('p', { class: 'muted' }, sub)));
}

async function start() {
  initChrome();
  try { const info = await api('/api/game/' + encodeURIComponent(code)); if (info.join_url) joinBase = info.join_url; }
  catch (e) { return errorBox('Game not found', `No game with code ${code}. Start one from the host console.`); }
  conn = new Conn(`/ws/screen/${encodeURIComponent(code)}?k=${encodeURIComponent(SCREEN_KEY)}`, {
    onState: v => { last = v; render(v); },
    onFatal: () => errorBox('Game not found', 'This game has ended.'),
  });
  runCountdown(() => (last && last.phase === 'question' ? last.q : null), () => conn.serverNow(), (ms, frac) => {
    if (ring) { ring.set(frac, ms); ring.pause(last && last.q && last.q.remaining_ms != null); }
    const sec = Math.ceil(ms / 1000);
    if (sec !== lastSec) { lastSec = sec; if (sec <= 5 && sec > 0 && last && last.q.remaining_ms == null) Sound.tick(); }
  });
}

start();
