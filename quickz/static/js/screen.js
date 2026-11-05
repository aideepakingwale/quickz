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
  mountBackdrop(16);
  $('#s-foot').append(el('div', { class: 's-foot-line' }, `© ${new Date().getFullYear()} DiQuizBuzz · Designed and Developed by `, creditLink(), ' (Deepak Ingwale)'));
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
  sb.addEventListener('click', e => { e.stopPropagation(); if (!Sound.unlock()) { paintSound(); return; } Sound.setOn(!Sound.on); if (Sound.on) Sound.pop(); else Bed.stop(); paintSound(); atmosphere(last); });
  paintSound(); tools.classList.add('show');
  const vol = $('#vol'); vol.value = Math.round(Sound.volume * 100);
  vol.addEventListener('input', () => { Sound.setVolume(vol.value / 100); LobbyMusic.setVolume(); });
  vol.addEventListener('change', () => { Sound.unlock(); Sound.ding(); });           // a sample of how loud that is
  vol.addEventListener('pointerdown', e => e.stopPropagation());
  const tts = $('#tts-btn');
  const paintTts = () => { tts.replaceChildren(icon('volume'), Speak.on ? 'Reading aloud' : 'Read aloud'); tts.classList.toggle('on', Speak.on); tts.hidden = !Speak.supported; };
  const flipTts = () => { Speak.setOn(!Speak.on); paintTts(); if (Speak.on && last && last.phase === 'question' && last.q) Speak.say(questionScript(last.q)); };
  tts.addEventListener('click', e => { e.stopPropagation(); flipTts(); }); paintTts();
  addEventListener('keydown', e => { if (e.key === 'r' || e.key === 'R') flipTts(); });
  const fs = $('#fs-btn'); fs.append(icon('expand'));
  const toggleFs = () => { if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen().catch(() => {}); };
  fs.addEventListener('click', e => { e.stopPropagation(); toggleFs(); });
  const unlock = () => { if (Sound.unlock()) { paintSound(); atmosphere(last); setTimeout(() => tools.classList.remove('show'), 2500); } };
  addEventListener('pointerdown', unlock); addEventListener('keydown', unlock);
  addEventListener('keydown', e => { if (e.key === 'f' || e.key === 'F') toggleFs(); if (e.key === 'm' || e.key === 'M') { Sound.setOn(!Sound.on); if (!Sound.on) Bed.stop(); paintSound(); atmosphere(last); } });
  let idle;
  addEventListener('mousemove', () => { document.body.classList.remove('idle'); clearTimeout(idle); idle = setTimeout(() => document.body.classList.add('idle'), 3000); });
}

/* ---------------------------------------------------------------- lobby */
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
    const descBox = v.description ? el('div', { class: 'desc-box' }, mdNode(v.description, 'desc')) : null;
    const root = sceneEl('lobby',
      el('div', { class: 'left' },
        el('h1', {}, v.title),
        descBox,
        el('p', { class: 'how' }, 'Scan the QR code, or go to ', el('b', {}, hostText()), ' and enter this code:'),
        el('div', { class: 'code', 'aria-label': 'Game code ' + v.code }, v.code.split('').map((d, i) => el('span', { style: { animationDelay: (i * 90) + 'ms' } }, d))),
        v.jokers ? el('div', { class: 'pill joker' }, icon('bolt'), 'Double up is on: each player can double one question') : null),
      el('div', { class: 'qr-card' }, el('img', { src: `/qr/${encodeURIComponent(v.code)}.svg`, alt: `QR code to join game ${v.code}` }), el('div', { class: 'cap' }, 'Scan to join')),
      el('div', { class: 'wall' }, el('div', { class: 'wall-head' }, countN, el('span', {}, 'players ready')), empty, list));
    sync(v);
    if (descBox) { root.classList.add('has-desc'); fitDescription(root, descBox); }
    return { root, update: sync };
  });
}

/** A long lobby description must never push the join code or the player wall off the screen. The box gets only the height
 *  that is left beside the QR code; if the text is taller than that it scrolls slowly up and down by itself. */
function fitDescription(root, box) {
  const text = box.firstElementChild;
  const fit = () => {
    if (!box.isConnected) { ro.disconnect(); return; }
    const left = box.parentNode, qr = root.querySelector('.qr-card');
    box.style.maxHeight = '0px'; box.classList.remove('scrolling');
    const base = left.offsetHeight, gap = parseFloat(getComputedStyle(left).rowGap) || 0;        // everything in the left column except the text
    const remPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
    const room = Math.max(remPx * 5.5, (qr ? qr.offsetHeight : 0) - base - gap);          // at least a few lines, however crowded the left column is
    box.style.maxHeight = room + 'px';
    const over = text.scrollHeight - room;
    if (over > 4) {
      box.style.setProperty('--shift', over + 'px');
      box.style.setProperty('--dur', Math.max(10, 6 + over / 22).toFixed(1) + 's');
      box.classList.add('scrolling');
    }
  };
  const ro = new ResizeObserver(() => setTimeout(fit, 30));
  let tries = 0;
  const start = () => { if (box.isConnected) { fit(); ro.observe(root); } else if (++tries < 100) setTimeout(start, 50); };      // the scene is attached a moment after it is built
  setTimeout(start, 50);
}

/** The clip on the projector: plays through the room, drives the "timer starts when it ends" rule. */
function screenMedia(q) {
  const m = q.media;
  if (!m || !m.url) return { node: null, el: null };
  const media = mediaPlayer(m, { controls: m.kind !== 'audio', big: true, onEnded: () => { if (conn) conn.send({ t: 'media_ended' }); } });
  const overlay = el('button', { class: 'play-overlay hidden', type: 'button' }, icon('play'), 'Click to play');
  const bars = m.kind === 'audio' ? el('div', { class: 'eq', 'aria-hidden': 'true' }, [0, 1, 2, 3, 4, 5, 6].map(i => el('i', { style: { animationDelay: (i * 0.11) + 's' } }))) : null;
  const card = el('div', { class: 's-media ' + (m.kind === 'embed' ? 'embedded' : m.kind) }, m.kind === 'audio' ? el('div', { class: 's-media-audio' }, bars, el('div', { class: 's-media-title' }, 'Listen carefully…')) : null, media, overlay);
  if (m.kind === 'audio') media.classList.add('hidden-audio');
  const start = () => { const p = media.play(); if (p && p.catch) p.catch(() => overlay.classList.remove('hidden')); };
  overlay.addEventListener('click', () => { Sound.unlock(); start(); });
  media.addEventListener('playing', () => { overlay.classList.add('hidden'); card.classList.add('playing'); });
  media.addEventListener('pause', () => card.classList.remove('playing'));
  media.addEventListener('ended', () => card.classList.remove('playing'));
  return { node: card, el: media, nonce: q.media_nonce, start, restart() { media.currentTime = 0; start(); } };
}

/* ---------------------------------------------------------------- the top strip of a question */
function topRow(v, live = true) {
  const q = v.q;
  if (live) { ring = makeClock(160); ring.el.style.removeProperty('--size'); }
  const worth = q.scored ? `${fmtScore(q.points)} pts` : q.type === 'poll' ? 'Poll · no points' : '';
  return el('div', { class: 'q-top' },
    q.type === 'slide' ? null : el('span', { class: 'pill big' }, `Question ${q.n} of ${q.of}`),
    worth ? el('span', { class: 'pill big pts' }, icon(q.scored ? 'bolt' : 'poll'), worth) : null,
    q.only_players ? el('span', { class: 'pill big tie' }, icon('bolt'), 'Tie-breaker: ' + q.only_players.map(p => `${p.avatar} ${p.nickname}`).join(' vs ')) : null,
    q.winners ? el('span', { class: 'pill big win', 'data-spots': '1' }, icon('trophy'), winnersText(q)) : null,
    placesText(q) ? el('span', { class: 'pill big' }, icon('bolt'), 'Marks: ' + placesText(q)) : null,
    v.jokers && q.scored && live ? el('span', { class: 'pill big joker' }, icon('bolt'), 'Double up allowed') : null,
    live ? ring.el : null);
}

/* ---------------------------------------------------------------- question */
function questionScene(v) {
  const q = v.q;
  $('#s-join').replaceChildren(hostText() + ' · ', el('b', {}, v.code));
  showScene(`q:${q.id}`, () => {
    fanfared = false; lastSec = null; Sound.whoosh(); clearFlights();
    if (!q.awaiting_media) Speak.say(questionScript(q));
    const bar = el('i'), label = el('span', { class: 'num' }, '');
    const upd = nv => {
      root.classList.toggle('await', !!nv.q.awaiting_media);
      const wp = $('[data-spots]', root); if (wp && nv.q.winners) wp.lastChild.textContent = winnersText(nv.q);
      if (sm.el && nv.q.media_nonce !== sm.nonce) { sm.nonce = nv.q.media_nonce; sm.restart(); }
      if (nv.q.type === 'poll' && nv.live_dist && pollGrid) $$('.o-count', pollGrid).forEach((n, i) => { n.textContent = nv.live_dist[nv.q.options[i].id] || 0; });
      if (finBox && nv.finishers && nv.finishers.length !== finCount) {            // the race for the places, live
        finCount = nv.finishers.length;
        finBox.replaceChildren(finCount ? finisherList(nv.finishers) : el('p', { class: 'muted' }, placesText(nv.q) ? `Marks by place: ${placesText(nv.q)}` : 'First to finish earns the most.'));
      }
      if (nv.q.type === 'buzzer') return;
      const pct = nv.eligible ? Math.min(100, 100 * nv.answered / nv.eligible) : 0;
      bar.style.width = pct + '%'; label.textContent = `${nv.answered} of ${nv.eligible} ${q.type === 'poll' ? 'voted' : 'answered'}`;
    };
    const sm = screenMedia(q);
    let pollGrid = null, body, finBox = null, finCount = -1;
    if (q.type === 'buzzer') {
      body = el('div', { class: 'buzz-wait' }, el('div', { class: 'bell' }, icon('bell')), el('h2', {}, 'BUZZER ROUND'), el('p', {}, 'First to hit the buzzer on their phone answers out loud'));
    } else if (q.type === 'mcq' || q.type === 'multi' || q.type === 'poll') {
      body = questionBody(q, 'screen', { counts: q.type === 'poll' ? (v.live_dist || {}) : null });
      pollGrid = q.type === 'poll' ? body : null;
    } else if (q.type === 'pin') {
      body = el('div', { class: 'pin-wrap' }, questionBody(q, 'screen'), el('p', { class: 'pin-note' }, 'Players are tapping the picture on their phones…'));
    } else if (q.type === 'jigsaw') {
      finBox = el('div', { class: 'jig-fin' });
      body = el('div', { class: 'jig-split' }, el('div', { class: 'jig-main' }, questionBody(q, 'screen'), el('p', { class: 'pin-note' }, 'Put the picture back together on your phone!')),
        el('aside', { class: 'jig-side' }, el('h3', {}, icon('trophy'), 'Finished'), finBox));
    } else {
      body = el('div', { class: 'widget-wrap' }, questionBody(q, 'screen'));
    }
    const hasPic = !!q.image && q.type !== 'pin' && q.type !== 'jigsaw';
    const card = questionCard(q, { live: true });
    if (hasPic) card.classList.add('split');
    if (sm.node) card.classList.add('compact');
    const qBody = el('div', { class: 'q-body' }, card, sm.node, el('div', { class: 'await-note' }, icon('clock'), 'Answers open when the clip ends'), body);
    if (q.type !== 'jigsaw') fitColumn(qBody);       // the jigsaw sizes its board to the room it has
    const root = sceneEl('qv screen' + (q.options && q.options.some(o => o.image) ? ' pics' : ''), topRow(v), qBody,
      q.type === 'buzzer' ? null : el('div', { class: 'progress-row' }, el('div', { class: 'track' }, bar), label));
    upd(v);
    if (sm.start) setTimeout(sm.start, 300);
    return { root, update: upd };
  });
}

/* ---------------------------------------------------------------- slides */
function slideScene(v) {
  const q = v.q;
  ring = null;
  $('#s-join').replaceChildren(hostText() + ' · ', el('b', {}, v.code));
  showScene(`s:${q.id}`, () => {
    Sound.whoosh();
    const sm = screenMedia(q);
    const board = q.board ? el('aside', { class: 'board wide' }, el('h3', {}, icon('trophy'), 'Standings'), leaderRows(v.leaderboard.slice(0, 8), false)) : null;
    const card = el('div', { class: 'slide-card' }, el('div', { class: 'kicker' }, q.category || (q.board ? 'Standings' : 'Up next')), richNode(q.text, 'q-text'), qImage(q, {}));
    if (sm.start) setTimeout(sm.start, 300);
    return { root: sceneEl('qv screen slide' + (board ? ' has-board' : ''), card, sm.node, board) };
  });
}

/* ---------------------------------------------------------------- reveal */
function leaderRows(rows, animate = true) {
  const rowH = 4.6;
  return rows.map((p, i) => {
    const row = el('div', { class: 'lrow', style: { animationDelay: (150 + i * 90) + 'ms' } }, el('span', { class: 'rk' }, p.rank), el('span', { class: 'av' }, p.avatar), el('span', { class: 'nm' }, p.nickname),
      p.delta ? delta(p.delta) : null, el('span', { class: 'sc num' }, fmtScore(p.score)));
    if (animate && p.delta) {                       // slide from the previous place to the new one
      row.style.setProperty('--from', (p.delta * rowH) + 'rem');
      row.classList.add('moved');
    }
    return row;
  });
}

/** A column holding more than the screen has room for (a long question, many answers, an explanation) is scaled down to
 *  fit, instead of losing its top and bottom. It re-fits whenever its contents change size (the crowd of avatars lands in
 *  the bars a moment after the reveal, making them taller). */
function fitColumn(box) {
  const fit = () => {
    if (!box.isConnected) { ro.disconnect(); return; }
    const z = parseFloat(box.style.zoom) || 1;
    const kids = [...box.children].filter(k => getComputedStyle(k).display !== 'none');
    const gap = parseFloat(getComputedStyle(box).rowGap) || 0;
    const outer = k => { const cs = getComputedStyle(k); return k.offsetHeight + parseFloat(cs.marginTop) + parseFloat(cs.marginBottom); };
    // parts that stretch to fill the room are measured at their natural size, or shrinking would only make them stretch more
    const stretchy = kids.filter(k => parseFloat(getComputedStyle(k).flexGrow) > 0);
    stretchy.forEach(k => { k.style.flexGrow = '0'; });
    const need = kids.reduce((sum, k) => sum + outer(k), 0) + gap * Math.max(0, kids.length - 1);   // layout sizes (with margins), not mid-animation ones
    stretchy.forEach(k => { k.style.flexGrow = ''; });
    const room = box.clientHeight * z;                 // the height the column really has on screen
    const next = need > room / z + 1 || z < 1 ? Math.min(1, Math.max(0.55, (room / need) * 0.97)) : 1;
    if (Math.abs(next - z) > 0.01) box.style.zoom = next >= 1 ? '' : String(next);
  };
  const ro = new ResizeObserver(() => requestAnimationFrame(fit));
  requestAnimationFrame(() => { fit(); [box, ...box.children].forEach(k => ro.observe(k)); });
}

function revealScene(v) {
  const q = v.q, r = v.reveal;
  $('#s-join').replaceChildren(hostText() + ' · ', el('b', {}, v.code));
  showScene(`r:${q.id}`, () => {
    ring = null; lastHolder = null;
    let content;
    if (q.type === 'buzzer') {
      content = el('div', { class: 'winner-card' }, el('div', { class: 'av' }, r.buzz_winner ? icon('check') : icon('x')), el('h2', {}, r.buzz_winner ? `${r.buzz_winner} got it!` : 'Nobody got it'),
        el('p', {}, r.buzz_winner ? 'Quickest on the buzzer, and right.' : 'Nobody buzzed in with the right answer.'));
    } else content = revealBody(q, r, 'screen');
    const poll = q.type === 'poll';
    const facts = el('div', { class: 'facts' },
      q.scored && q.type !== 'buzzer' ? el('span', { class: 'fact' }, icon('check'), `${r.correct_count} of ${r.answered} got it right`) : null,
      poll ? el('span', { class: 'fact' }, icon('users'), `${r.answered} vote${r.answered === 1 ? '' : 's'}`) : null,
      r.fastest ? el('span', { class: 'fact' }, icon('bolt'), `Fastest: ${r.fastest.avatar} ${r.fastest.nickname} · ${(r.fastest.ms / 1000).toFixed(1)}s`) : null);
    const board = poll ? null : el('aside', { class: 'board' }, el('h3', {}, icon('trophy'), 'Leaderboard'), leaderRows(v.leaderboard.slice(0, 5)));
    const explain = (r.explanation || r.explain_image) ? el('div', { class: 'callout' }, r.explanation ? el('div', {}, r.explanation) : null, r.explain_image ? el('img', { src: r.explain_image, alt: '' }) : null) : null;
    Speak.stop();
    revealAudio(q, r, v.pack);
    const badge = crowdBadge(q, r);
    const left = el('div', { class: 'l' }, topRow(v, false), richNode(q.text, 'q-text'), content,
      badge ? el('div', { class: 'crowd-badge ' + badge.tone, role: 'status' }, icon(badge.tone === 'easy' ? 'users' : 'target'), badge.text) : null,
      claimedList(r, '', { big: true }), explain, facts);
    const root = sceneEl('reveal qv screen' + (poll ? ' solo' : ''), left, board);
    fitColumn(left);                                   // before crowdFlight, which measures where the bars ended up
    crowdFlight(root, v.crowd, r);
    return { root };
  });
}

/* ---------------------------------------------------------------- final: a staged podium */
function finalScene(v) {
  ring = null;
  $('#s-join').replaceChildren();
  showScene('final', () => {
    const top = v.leaderboard.slice(0, 3);
    const order = [1, 0, 2].filter(i => top[i]);
    const delays = { 2: 0.5, 1: 2.6, 0: 5.0 };          // third place first, the winner last
    if (!fanfared) {
      fanfared = true;
      [2, 1].forEach(i => top[i] && setTimeout(() => Sound.thud(), (delays[i] || 0) * 1000));
      if (top[0]) {
        setTimeout(() => Sound.drum(), (delays[0] - 1.2) * 1000);
        setTimeout(() => { Sound.fanfare(); confetti({ count: 240, duration: 5500 }); setTimeout(() => confetti({ count: 140, duration: 4200 }), 1400); }, delays[0] * 1000 + 450);
      }
    }
    const rest = v.leaderboard.slice(3, 7);
    return {
      root: sceneEl('final qv screen', el('h1', {}, 'Final results'), el('p', { class: 'sub' }, `${v.title} · ${v.players} players`),
        el('div', { class: 'podium' }, order.map(i => el('div', { class: `pstep p${i + 1}`, style: { '--d': delays[i] + 's' } },
          el('div', { class: 'who' }, el('div', { class: 'crown' }, i === 0 ? icon('crown') : null), el('div', { class: 'av' }, top[i].avatar), el('div', { class: 'nm' }, top[i].nickname), el('div', { class: 'pt num' }, fmtScore(top[i].score) + ' pts')),
          el('div', { class: 'block' }, String(i + 1))))),
        rest.length ? el('div', { class: 'rest' }, rest.map(p => el('div', { class: 'lrow' }, el('span', { class: 'rk' }, p.rank), el('span', { class: 'av' }, p.avatar), el('span', { class: 'nm' }, p.nickname), el('span', { class: 'sc num' }, fmtScore(p.score))))) : null),
    };
  });
}

function updateSplash(v) {
  const b = v.phase === 'question' && v.buzz && v.buzz.holder ? v.buzz : null;
  const sp = $('#splash');
  if (b) {
    if (lastHolder !== b.holder) {
      Sound.buzzFor(b.holder);                                 // this player's own buzzer voice
      const ms = (b.times || [])[0], gap = (b.times || []).length > 1 ? b.times[1] - b.times[0] : null;
      sp.replaceChildren(el('div', { class: 'siren' }, '🚨'), el('div', { class: 'av' }, b.holder_avatar || ''), el('div', { class: 'who' }, b.holder_name), el('div', { class: 'sub' }, 'buzzed in first!'),
        ms != null ? el('div', { class: 'rt num' }, `${(ms / 1000).toFixed(3)} s`) : null,
        gap != null ? el('div', { class: 'photo' }, `Photo finish! ${b.order[1]} was just ${gap} ms behind`) : null);
    }
    sp.classList.remove('hidden'); lastHolder = b.holder;
  } else { sp.classList.add('hidden'); lastHolder = null; }
}

/** Music, suspense and read-aloud follow the game's state. Safe to call often: starting what is already playing does nothing. */
function atmosphere(v) {
  if (!v) return;
  const pack = v.pack || 'gameshow', held = v.phase === 'question' && v.suspense;
  setSuspense(held);
  if (v.phase === 'lobby' && v.music) { LobbyMusic.start(v.music); Bed.stop(); }          // the host's own music replaces the built-in waiting tune
  else {
    LobbyMusic.stop();                                                                      // the quiz has started (or there is none): fade it out
    if (v.phase === 'lobby') Bed.start('lobby', pack);
    else if (held) Bed.start('suspense', pack, { dur: Math.max(500, v.suspense.until - (conn ? conn.serverNow() : Date.now())) });
    else if (v.phase === 'question' && v.q && !v.q.awaiting_media && v.q.remaining_ms == null) Bed.start('thinking', pack);
    else Bed.stop();
  }
  if (held) Speak.stop();
}

/** The quiz's own waiting-screen music: loops while players join, fades out when the quiz starts. Browsers only allow sound after a click,
 *  so if it is blocked the unlock handler asks again on the first click. It follows the screen's sound switch and volume. */
const LobbyMusic = {
  el: null, url: '',
  level() { return Math.max(0, Math.min(1, Sound.volume * 0.7)); },
  start(url) {
    if (!url || !Sound.on) { this.stop(); return; }
    if (!this.el || this.url !== url) {
      this.stop(true);
      this.el = new Audio(url); this.el.loop = true; this.el.preload = 'auto'; this.url = url;
    }
    this.el.volume = this.level();
    const p = this.el.play();
    if (p && p.catch) p.catch(() => { /* waiting for the first click */ });
  },
  setVolume() { if (this.el) this.el.volume = this.level(); },
  stop(now = false) {
    const a = this.el;
    if (!a) return;
    this.el = null; this.url = '';
    if (now || a.paused) { a.pause(); return; }
    let v = a.volume;
    const t = setInterval(() => { v -= 0.1; if (v <= 0.02) { clearInterval(t); a.pause(); a.currentTime = 0; } else a.volume = v; }, 70);
  },
};

function render(v) {
  applySkin(v.skin);
  applyBackground(v.bg, v.bg_wash);
  preloadImages(v.preload);
  $('#s-title').textContent = v.title;
  document.title = 'DiQuizBuzz · ' + v.title;
  if (v.phase === 'lobby') lobbyScene(v);
  else if (v.phase === 'slide') slideScene(v);
  else if (v.phase === 'question') questionScene(v);
  else if (v.phase === 'reveal') revealScene(v);
  else finalScene(v);
  updateSplash(v);
  atmosphere(v);
}

function errorBox(title, sub) {
  main.replaceChildren(el('div', { class: 'error-box' }, el('h1', {}, title), el('p', { class: 'muted' }, sub)));
}

async function start() {
  initChrome();
  try { const info = await api('/api/game/' + encodeURIComponent(code)); if (info.join_url) joinBase = info.join_url; applySkin(info.skin); }
  catch (e) { return errorBox('Game not found', `No game with code ${code}. Start one from the host console.`); }
  conn = new Conn(`/ws/screen/${encodeURIComponent(code)}?k=${encodeURIComponent(SCREEN_KEY)}`, {
    onState: v => { last = v; render(v); },
    onFatal: () => errorBox('Game not found', 'This game has ended.'),
    onMsg: m => { if (m.t === 'cue') { Sound.unlock(); playCue(m.name); } else if (m.t === 'react') reactionBurst(m.items); },
  });
  runCountdown(() => (last && last.phase === 'question' ? last.q : null), () => conn.serverNow(), (ms, frac) => {
    if (ring) { ring.set(frac, ms); ring.pause(last && last.q && last.q.remaining_ms != null); }
    fxTick(frac);
    Bed.setIntensity(1 - frac);
    if (last && last.phase === 'question' && !Bed.timer) atmosphere(last);       // audio was unlocked after the question began
    const sec = Math.ceil(ms / 1000);
    if (sec !== lastSec) { lastSec = sec; if (sec <= 5 && sec > 0 && last && last.q.remaining_ms == null) Sound.tick(); }
  });
}

start();
