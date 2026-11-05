'use strict';
/* Player: join -> lobby -> question / buzzer / slide -> reveal -> final. A stateless renderer over server views;
   scenes are only rebuilt when their key changes so animations never replay by accident. */

const pathParts = location.pathname.split('/').filter(Boolean);
const code = pathParts[0] === 'j' ? decodeURIComponent(pathParts[1] || '') : (new URLSearchParams(location.search).get('code') || '');
const KEY = 'quickz:' + code;
const stage = $('#stage');

let conn = null, last = null, sceneKey = null, scene = null;
const qkey = q => q.id + ':' + (q.run || 0);        // a question the host ran again counts as a new round
let submitted = null, multiSel = new Set(), jokerOn = false, shownScore = 0, fanfared = false, revealedFor = null, askedFor = null;
let ring = null, bar = null, lastSec = null;

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
};

const announce = text => { $('#live').textContent = ''; setTimeout(() => { $('#live').textContent = text; }, 40); };
const setMood = m => { document.body.dataset.mood = m || ''; };

/** Show a scene. Same key => keep the DOM (call update if the scene has one). */
function showScene(key, build) {
  if (key === sceneKey && scene) { if (scene.update) scene.update(last); return; }
  sceneKey = key;
  scene = build();
  stage.replaceChildren(scene.root);
  stage.scrollTop = 0;
}

/** append() that skips null / false (a bare append(null) would print the word "null"). */
const add = (parent, ...kids) => { parent.append(...kids.flat(Infinity).filter(k => k != null && k !== false)); return parent; };

function sceneEl(cls, ...kids) { return el('section', { class: 'scene ' + cls }, ...kids); }

/* ---------------------------------------------------------------- boot */
async function start() {
  mountBackdrop(10);
  Sound.useKey('quickz:psound', false);                  // phones start silent: the big screen is the room's sound system (taps still buzz)
  $('#snd').title = 'Sound on this phone (the big screen already plays the sound for the room)';
  $('#snd').append(icon(Sound.on ? 'volume' : 'mute'));
  $('#snd').addEventListener('click', () => {
    Sound.setOn(!Sound.on); Sound.unlock(); if (Sound.on) Sound.pop();
    $('#snd').replaceChildren(icon(Sound.on ? 'volume' : 'mute'));
  });
  document.addEventListener('pointerdown', () => Sound.unlock(), { once: true });
  enableZoom($('#stage'));                                                       // tap a picture (or its magnifier) to see it large
  document.addEventListener('pointerdown', () => PhoneMusic.retry());          // a phone may hold the music back until the first tap

  if (!code) { location.href = '/'; return; }
  let info;
  try { info = await api('/api/game/' + encodeURIComponent(code)); }
  catch (e) { return errorScene('Game not found', 'Double-check the code, or ask the host for the QR code again.'); }
  applySkin(info.skin);
  const token = store.get(KEY);
  if (token) connect(token); else joinScene(info);
}

function errorScene(title, sub, btn = true) {
  $('#top').hidden = true; sceneKey = null; scene = null;
  stage.replaceChildren(sceneEl('error-screen', el('div', { class: 'big' }, icon('help')), el('h1', {}, title), el('p', { class: 'muted' }, sub),
    btn && el('a', { class: 'btn primary lg', href: '/' }, 'Enter a different code'), siteFooter('p-foot')));
}

/* ---------------------------------------------------------------- join */
function joinScene(info) {
  $('#top').hidden = true; setMood('');
  const real = !!info.real_names;
  const nameKey = real ? 'quickz:fullname' : 'quickz:name';        // a remembered nickname must not pre-fill a real-name form
  const picker = avatarPicker(info.avatar_groups, store.get('quickz:avatar'));
  const name = el('input', { class: 'input', id: 'nick', maxLength: real ? 40 : 20, autocomplete: real ? 'name' : 'nickname', autocapitalize: 'words', spellcheck: false,
    placeholder: real ? 'Your full name' : 'Your nickname', value: store.get(nameKey) || (real ? '' : randomName()), required: true, 'aria-label': real ? 'Full name' : 'Nickname' });
  const email = info.ask_email ? el('input', { class: 'input', id: 'mail', type: 'email', autocomplete: 'email', required: true, placeholder: 'you@example.com', 'aria-label': 'Email' }) : null;
  const err = el('p', { class: 'error-text', role: 'alert' });
  const go = el('button', { class: 'btn primary xl block', type: 'submit' }, "Let's play ", icon('right'));
  const form = el('form', { class: 'scene join', novalidate: true },
    el('div', { class: 'brand' }, logo()),
    el('div', { class: 'game' }, el('small', {}, "You're joining"), el('h1', {}, info.title)),
    el('div', { class: 'card me-card' }, picker.preview,
      el('div', { class: 'name-row' }, name, real ? null : el('button', { class: 'btn icon-only', type: 'button', 'aria-label': 'Random nickname', onclick: () => { name.value = randomName(); Sound.pop(); } }, icon('dice'))),
      email, picker.tabs, picker.grid, err),
    el('div', { class: 'cta' }, go, el('p', { class: 'tiny faint' }, real
      ? 'Use your real first and last name: it is shown on the big screen. No account needed.'
      : 'No account needed. Your nickname is shown on the big screen.')),
    siteFooter('p-foot'));
  form.addEventListener('submit', async e => {
    e.preventDefault(); err.textContent = '';
    if (!name.value.trim()) { err.textContent = real ? 'Please enter your full name.' : 'Pick a nickname first.'; name.focus(); return; }
    if (real && name.value.trim().split(/\s+/).length < 2) { err.textContent = 'Please enter your first and last name.'; name.focus(); return; }
    go.disabled = true; Sound.unlock();
    try {
      const r = await api('/api/join', { method: 'POST', body: { code, nickname: name.value, email: email ? email.value : '', avatar: picker.get() } });
      store.set(KEY, r.token); store.set('quickz:avatar', r.avatar); store.set(nameKey, name.value.trim());
      Sound.start(); haptic(20);
      connect(r.token);
    } catch (ex) { err.textContent = ex.message; go.disabled = false; }
  });
  stage.replaceChildren(form);
  sceneKey = 'join'; scene = null;
  picker.redraw();
}

/* ---------------------------------------------------------------- connect */
function connect(token) {
  stage.replaceChildren(sceneEl('error-screen', el('div', { class: 'spinner' }), el('p', { class: 'muted' }, 'Connecting…')));
  sceneKey = null; scene = null;
  conn = new Conn(`/ws/player/${encodeURIComponent(code)}?token=${encodeURIComponent(token)}`, {
    reportRtt: true,
    onState: v => { last = v; render(v); },
    onStatus: ok => { $('#net').hidden = ok || !conn.everOpen; },
    onFatal: c => {
      store.del(KEY);
      if (c === 4403) errorScene('You were removed from this game', 'Ask the host if this was a mistake.', false);
      else api('/api/game/' + encodeURIComponent(code)).then(joinScene, () => errorScene('Game not found', 'This game has ended.'));
    },
    onError: m => ui.toast(m, 'error'),
  });
  runCountdown(() => (last && last.phase === 'question' ? last.q : null), () => conn.serverNow(), (ms, frac) => {
    if (ring) { ring.set(frac, ms); ring.pause(last && last.q && last.q.remaining_ms != null); }
    if (bar) { bar.firstChild.style.width = (frac * 100) + '%'; bar.classList.toggle('low', ms <= 5000); }
    fxTick(frac);
    const sec = Math.ceil(ms / 1000);
    if (sec !== lastSec) { lastSec = sec; if (sec <= 3 && sec > 0 && last && last.q && last.q.remaining_ms == null) { Sound.tick(); haptic(6); } }
  });
  document.addEventListener('visibilitychange', keepAwake);
  keepAwake();
}
async function keepAwake() {
  try { if (document.visibilityState === 'visible' && navigator.wakeLock) await navigator.wakeLock.request('screen'); } catch (e) { /* optional */ }
}

/* ---------------------------------------------------------------- render */
function header(v) {
  $('#top').hidden = false;
  $('#my-avatar').textContent = v.me.avatar || '🙂';
  $('#who').textContent = v.me.nickname;
  $('#sub').textContent = v.phase === 'lobby' ? 'In the lobby' : v.phase === 'finished' ? 'Final results'
    : (v.me.rank ? `${ordinal(v.me.rank)} of ${v.players}` : '');
  const n = $('#score-n');
  if (v.me.score !== shownScore) {
    const from = shownScore; shownScore = v.me.score;
    countUp(n, v.me.score, { from, ms: 900 });
    if (v.me.score > from) { const p = $('#score'); p.classList.remove('bump'); void p.offsetWidth; p.classList.add('bump'); }
  }
}

/* ---- the show on a phone: the "final answers are in" banner and the reaction bar */
const REACT_BAR = ['👏', '😂', '🔥', '😮', '🎉'];          // always on the bar
const REACT_MORE = ['💪', '❤️', '😍', '🤩', '🥳', '🤯', '😎', '🙌', '👍', '👎', '🤣', '😅', '😬', '😱', '🥲', '😭', '🤔', '🙈', '👀', '😴', '🙏', '🤞', '🤝', '🫡', '💯', '⭐', '🏆', '🥇', '👑', '🚀', '🧠', '💡', '🎯', '⚡', '🌈', '🍀', '🍿', '☕', '🎸', '🦄'];    // in the tray behind the "more" button
const REACTS = [...REACT_BAR, ...REACT_MORE];                                  // the server only accepts these (REACTIONS in engine.py)
let reactAt = 0;
function showExtras(v) {
  let banner = $('#susp');
  if (!banner) { banner = el('div', { id: 'susp', class: 'susp-banner', role: 'status' }, icon('clock'), 'Final answers are in… revealing'); banner.hidden = true; document.body.append(banner); }
  banner.hidden = !(v.phase === 'question' && v.suspense);
  let bar = $('#react-bar');
  if (!bar) {
    const tray = el('div', { id: 'react-tray', class: 'react-tray', role: 'group', 'aria-label': 'More reactions' });
    const throwIt = (e, btn) => {
      const now = Date.now(); if (now - reactAt < 1000 || !conn) return;
      reactAt = now; conn.send({ t: 'react', e }); haptic(10); Sound.tap();
      btn.classList.remove('sent'); void btn.offsetWidth; btn.classList.add('sent');
      bar.classList.add('cool'); setTimeout(() => bar.classList.remove('cool'), 1000);
      tray.hidden = true; more.setAttribute('aria-expanded', 'false');
    };
    const pick = e => el('button', { type: 'button', 'aria-label': 'Send ' + e, onclick: ev => throwIt(e, ev.currentTarget) }, e);
    const more = el('button', { type: 'button', class: 'more', 'aria-label': 'More reactions', 'aria-expanded': 'false', onclick: () => { tray.hidden = !tray.hidden; more.setAttribute('aria-expanded', String(!tray.hidden)); } }, '＋');
    tray.append(...REACT_MORE.map(pick)); tray.hidden = true;
    bar = el('div', { id: 'react-bar', class: 'react-bar', role: 'group', 'aria-label': 'Send a reaction to the big screen' }, REACT_BAR.map(pick), more, tray);
    bar.hidden = true; document.body.append(bar);
  }
  const on = !!v.reactions && ['lobby', 'reveal', 'slide', 'finished'].includes(v.phase);
  bar.hidden = !on; document.body.classList.toggle('has-react', on);
  if (!on) { const t = $('#react-tray'); if (t) t.hidden = true; }
}

let lastView = null;
function render(v) {
  lastView = v;
  applySkin(v.skin);
  applyBackground(v.bg, v.bg_wash);                                 // the quiz's picture sits behind the whole game
  PhoneMusic.sync(v);                                    // waiting-screen music: lobby only
  preloadImages(v.preload);
  showExtras(v);
  header(v);
  if (v.phase === 'lobby') return lobbyScene(v);
  if (v.phase === 'slide') return slideScene(v);
  if (v.phase === 'question') return v.q.type === 'buzzer' ? buzzerScene(v) : questionScene(v);
  if (v.phase === 'reveal') return revealScene(v);
  return finalScene(v);
}

/* ---- lobby */
function lobbyScene(v) {
  setMood(''); ring = bar = null; revealedFor = null; askedFor = null; jokerOn = false;
  showScene('lobby', () => {
    const count = el('span', { id: 'lobby-n' }, v.players);
    const descBox = v.description ? el('div', { class: 'desc-box p-desc' }, mdNode(v.description, 'desc')) : null;      // what the host wrote for the lobby, until the quiz starts
    if (descBox) fitPhoneDescription(descBox);
    const musicBtn = v.music ? el('button', { class: 'btn sm music-btn', type: 'button', onclick: () => { PhoneMusic.setPref(!PhoneMusic.pref()); PhoneMusic.sync(lastView || v); paintMusic(); } }) : null;
    function paintMusic() { if (!musicBtn) return; const on = PhoneMusic.pref(); musicBtn.setAttribute('aria-pressed', String(on)); musicBtn.replaceChildren(icon(on ? 'volume' : 'mute'), on ? 'Music on' : 'Music off'); }
    paintMusic();
    return {
      root: sceneEl('lobby' + (v.description ? ' has-desc' : ''),
        el('div', { class: 'me-avatar', 'aria-hidden': 'true' }, v.me.avatar),
        el('div', {}, el('h1', {}, `You're in, ${v.me.nickname}!`), el('p', { class: 'muted', style: { marginTop: '6px' } }, v.title)),
        descBox,
        el('div', { class: 'dots', 'aria-hidden': 'true' }, el('span'), el('span'), el('span')),
        el('p', { class: 'muted' }, 'Waiting for the host to start…'),
        el('div', { class: 'chip brand count' }, icon('users'), count, ' ready'),
        musicBtn,
        v.jokers ? el('div', { class: 'joker-tip' }, icon('bolt'), el('div', {}, el('b', {}, 'Double up is on'), el('span', {}, 'Once in the game you can double the points of one question. Pick it wisely.'))) : null,
        el('p', { class: 'tip faint' }, 'Keep this screen open and watch the big screen. It plays the sound for everyone, so your phone stays quiet (the speaker button turns it on).'), siteFooter('p-foot')),
      update(nv) { count.textContent = nv.players; },
    };
  });
}

/** A long lobby description gets a box that fits the phone and scrolls by itself (timers, not animation frames, so it also runs in a background tab). */
function fitPhoneDescription(box) {
  const text = box.firstElementChild;
  let tries = 0;
  const fit = () => {
    if (!box.isConnected) { if (++tries < 100) setTimeout(fit, 50); return; }
    const room = Math.round(Math.max(90, Math.min(window.innerHeight * 0.22, 200)));
    box.style.maxHeight = room + 'px';
    const over = text.scrollHeight - room;
    box.classList.toggle('scrolling', over > 4);
    if (over > 4) { box.style.setProperty('--shift', over + 'px'); box.style.setProperty('--dur', Math.max(10, 6 + over / 22).toFixed(1) + 's'); }
  };
  setTimeout(fit, 50);
  addEventListener('resize', () => { if (box.isConnected) fit(); });
}

/** The quiz's own waiting-screen music on this phone: only while players wait, stops when the quiz starts, and the player can switch it off. */
const PhoneMusic = {
  el: null, url: '',
  pref() { try { return localStorage.getItem('quickz:pmusic') !== 'off'; } catch (e) { return true; } },
  setPref(on) { try { localStorage.setItem('quickz:pmusic', on ? 'on' : 'off'); } catch (e) { /* private window */ } },
  sync(v) { if (v.phase === 'lobby' && v.music && this.pref()) this.start(v.music); else this.stop(); },
  start(url) {
    if (!this.el || this.url !== url) { this.stop(true); this.el = new Audio(url); this.el.loop = true; this.el.preload = 'auto'; this.url = url; }
    this.el.volume = 0.6;
    this.retry();
  },
  retry() { if (this.el && this.el.paused) { const p = this.el.play(); if (p && p.catch) p.catch(() => { /* waits for the next tap */ }); } },
  stop(now = false) {
    const a = this.el;
    if (!a) return;
    this.el = null; this.url = '';
    if (now || a.paused) { a.pause(); return; }
    let v = a.volume;
    const t = setInterval(() => { v -= 0.1; if (v <= 0.02) { clearInterval(t); a.pause(); a.currentTime = 0; } else a.volume = v; }, 70);
  },
};

/* ---- the clip (if this device should play it). One element per question, re-used across scene rebuilds so a tap on
   an answer never restarts the audio. */
let mediaCache = { key: null, node: null, nonce: null };
function syncMedia(q) {
  const m = q.media;
  if (!m || !m.url) { mediaCache = { key: null, node: null, nonce: null }; return; }
  const key = q.id + '|' + m.url;
  if (mediaCache.key !== key) {
    const node = mediaPlayer(m, { controls: true });
    mediaCache = { key, node, nonce: q.media_nonce };
    setTimeout(() => { const p = node.play(); if (p && p.catch) p.catch(() => { /* needs a tap: native controls are right there */ }); }, 200);
  } else if (mediaCache.nonce !== q.media_nonce) {            // host pressed "Replay"
    mediaCache.nonce = q.media_nonce;
    mediaCache.node.currentTime = 0;
    const p = mediaCache.node.play(); if (p && p.catch) p.catch(() => {});
  }
}
function mediaBlock(q) {
  const m = q.media;
  if (!m) return null;
  const wait = q.awaiting_media ? el('div', { class: 'media-note' }, icon('clock'), 'Answers open when the clip ends.') : null;
  if (!m.url) return el('div', { class: 'media-note' }, icon('speaker'), q.awaiting_media ? 'Listen to the main screen. Answers open when it ends.' : 'Listen to the main screen.');
  syncMedia(q);
  return el('div', { class: 'col', style: { gap: '.5rem' } }, mediaCache.node, wait);
}

/* ---- the top of every question: where we are, what it is worth, the clock */
function qMeta(q, v) {
  ring = makeClock(78);
  bar = el('div', { class: 'q-bar', 'aria-hidden': 'true' }, el('i'));
  const worth = !q.scored ? (q.type === 'poll' ? 'Poll · no points' : '')
    : `${fmtScore(q.points)} pts${q.speed ? ` · speed ${q.speed}%` : ''}`;
  return [
    el('div', { class: 'q-meta' }, el('div', { class: 'col', style: { gap: '4px', minWidth: 0, flex: 1 } },
      el('span', { class: 'qn' }, `Question ${q.n} of ${q.of}`),
      el('div', { class: 'row', style: { gap: '6px' } }, worth ? el('span', { class: 'pill pts' }, icon(q.scored ? 'bolt' : 'poll'), worth) : null)), ring.el),
    bar,
    (q.winners || q.only_players || placesText(q)) ? el('div', { class: 'q-flags' },
      q.only_players ? el('span', { class: 'pill tie' }, icon('bolt'), 'Tie-breaker: ' + q.only_players.map(p => `${p.avatar} ${p.nickname}`).join(' vs ')) : null,
      q.winners ? el('span', { class: 'pill win', 'data-spots': '1' }, icon('trophy'), winnersText(q)) : null,
      placesText(q) ? el('span', { class: 'pill' }, icon('bolt'), 'Marks: ' + placesText(q)) : null) : null,
  ];
}

/* ---- question */
function questionScene(v) {
  const q = v.q;
  const mine = submitted && submitted.qid === qkey(q) ? submitted : null;
  const locked = !!v.answered || !!mine;
  const spectator = !!q.only_players && q.can_play === false;       // a tie-breaker this player isn't part of
  const awaiting = !!q.awaiting_media || spectator;
  const sameQ = scene && scene.qid === q.id;
  setMood('');
  showScene(`q:${q.id}:${locked ? 1 : 0}:${awaiting ? 1 : 0}`, () => {
    if (askedFor !== qkey(q)) { askedFor = qkey(q); jokerOn = false; multiSel = new Set(); Sound.whoosh(); }
    const pickType = q.type === 'mcq' || q.type === 'poll';
    const cardSlot = el('div', { class: 'card-slot' + (pickType || q.type === 'multi' ? ' grow' : '') }, questionCard(q, { live: true }));
    const zone = el('div', { class: 'answer-zone' });
    let submitBtn = null;

    const lockIn = value => {
      if (submitted && submitted.qid === qkey(q)) return;
      submitted = { qid: qkey(q), value, joker: jokerOn && !!v.me.joker && q.scored };
      Sound.tap(); haptic(18);
      conn.send({ t: 'answer', choice: value, joker: submitted.joker });
      announce('Answer locked in');
      render(last);
    };
    const mkSubmit = (label = 'Lock in answer', ready = false) => {
      submitBtn = el('button', { class: 'btn primary xl block', type: 'button', disabled: !ready || locked || awaiting, onclick: () => submitWidget() }, icon('check'), label);
      return el('div', { class: 'submit-bar' }, submitBtn);
    };
    let widget = null;
    const submitWidget = () => { const val = widget && widget.value(); if (val != null && val !== '') lockIn(val); };
    const ready = r => { if (submitBtn) submitBtn.disabled = !r || locked || awaiting; };

    if (pickType) {
      const sel = new Set(mine ? mine.value : []);
      zone.append(optionsGrid(q, 'phone', { selected: sel, locked, awaiting, onPick: o => lockIn([o.id]) }));
    } else if (q.type === 'multi') {
      const sel = locked ? new Set(mine ? mine.value : []) : multiSel;
      const grid = optionsGrid(q, 'phone', { selected: sel, locked, awaiting, onPick: (o, t) => {
        Sound.tap(); haptic(8);
        if (multiSel.has(o.id)) multiSel.delete(o.id); else multiSel.add(o.id);
        t.classList.toggle('sel', multiSel.has(o.id)); t.setAttribute('aria-pressed', String(multiSel.has(o.id)));
        ready(multiSel.size > 0);
      } });
      widget = { value: () => [...multiSel] };
      add(zone, !locked && !awaiting ? el('p', { class: 'q-hint' }, 'Select all that apply, then lock in.') : null, grid, locked ? null : mkSubmit('Lock in answer', multiSel.size > 0));
    } else if (q.type === 'text') {
      widget = textInput(q, { locked, initial: mine ? String(mine.value[0] || mine.value) : '', onReady: ready, onSubmit: () => submitWidget() });
      add(zone, widget.root, locked ? null : mkSubmit());
      if (!locked && !awaiting && !sameQ) setTimeout(() => widget.focus && widget.focus(), 350);
    } else if (q.type === 'number') {
      widget = numberInput(q, { locked, initial: mine ? mine.value[0] : null, onReady: ready, onSubmit: () => submitWidget() });
      add(zone, widget.root, locked ? null : mkSubmit('Lock in number', widget.ready()));
    } else if (q.type === 'order') {
      widget = orderInput(q, { locked, initial: mine ? mine.value : null });
      add(zone, widget.root, locked ? null : mkSubmit('Lock in this order', true));
    } else if (q.type === 'match') {
      widget = matchInput(q, { locked, initial: mine ? mine.value : null, onChange: v2 => ready(!!v2) });
      add(zone, widget.root, locked ? null : mkSubmit('Lock in matches', widget.ready()));
    } else if (q.type === 'pin') {
      widget = pinInput(q, { locked, initial: mine ? mine.value : null, onChange: pos => ready(!!pos) });
      add(zone, widget.root, locked ? null : mkSubmit('Lock in my pin', widget.ready()));
    } else if (q.type === 'jigsaw') {
      widget = jigsawInput(q, { locked, initial: mine ? mine.value : null, seed: v.me.id, onHint: () => { Sound.tap(); conn.send({ t: 'hint' }); } });
      add(zone, widget.root, locked ? null : mkSubmit('Submit my puzzle', true));
    }
    if (awaiting) zone.classList.add('await');

    const joker = (v.jokers && q.scored && v.me.joker && !locked && !awaiting)
      ? el('button', { class: 'joker', type: 'button', 'aria-pressed': String(jokerOn), onclick: e => {
        jokerOn = !jokerOn; e.currentTarget.setAttribute('aria-pressed', String(jokerOn)); Sound.pop(); haptic(10);
        e.currentTarget.lastChild.textContent = jokerOn ? 'Double up is ON' : 'Double up this question'; } },
        icon('bolt'), el('span', {}, jokerOn ? 'Double up is ON' : 'Double up this question'))
      : (locked && (mine ? mine.joker : v.joker_on) ? el('div', { class: 'pill joker' }, icon('bolt'), 'Doubled up') : null);

    const root = sceneEl('q-wrap qv phone', ...qMeta(q, v), cardSlot, mediaBlock(q), joker,
      spectator ? el('div', { class: 'media-note' }, icon('eye'), 'Tie-breaker: only the tied players answer this one. Watch the big screen!') : null,
      zone,
      locked ? el('div', { class: 'locked' }, el('div', { class: 'spinner', style: { width: '20px', height: '20px', borderWidth: '2px' } }),
        q.type === 'poll' ? 'Vote counted - watch the big screen.' : 'Answer locked in - waiting for the others…') : null);
    if (sameQ) root.style.animation = 'none';
    if (!locked && !sameQ) announce(`Question ${q.n}. ${plain(q.text)}`);
    return { root, qid: q.id,
      update(nv) {
        const w = $('[data-spots]', root); if (w && nv.q.winners) w.lastChild.textContent = winnersText(nv.q);
        if (widget && widget.setHint && nv.q.jigsaw) widget.setHint(!!nv.q.jigsaw.hint_used);
      } };
  });
}

/* ---- buzzer */
function buzzerScene(v) {
  const q = v.q, b = v.buzz || {}, spectator = !!q.only_players && q.can_play === false, out = !!v.locked_out || spectator, mine = !!b.holder_is_me;
  setMood(b.holder ? 'buzz' : '');
  showScene(`b:${q.id}:${b.holder || 0}:${out ? 1 : 0}:${(b.order || []).length}`, () => {
    if (askedFor !== qkey(q)) { askedFor = qkey(q); Sound.whoosh(); }
    const label = b.holder ? (mine ? 'YOU!' : 'LOCKED') : out ? 'OUT' : 'BUZZ!';
    const t0 = (b.times || [])[0], secs = t0 != null ? ` (${(t0 / 1000).toFixed(3)} s)` : '';
    const note = b.holder ? (mine ? `You buzzed first${secs}. Say your answer out loud!` : `${b.holder_name} buzzed first${secs}`)
      : out ? "Wrong answer - you're out for this question" : 'Be the first to hit the buzzer';
    const btn = el('button', { class: 'buzzer' + (mine ? ' mine' : ''), type: 'button', disabled: !!b.holder || out, 'aria-label': 'Buzzer' }, label);
    btn.addEventListener('pointerdown', e => { e.preventDefault(); Sound.unlock(); Sound.buzz(); haptic([40, 30, 40]); conn.send({ t: 'buzz' }); });
    const sameQ = scene && scene.qid === q.id;
    const root = sceneEl('q-wrap qv phone', ...qMeta(q, v), el('div', { class: 'card-slot' }, questionCard(q, { live: true })), mediaBlock(q),
      el('div', { class: 'buzz-stage' }, btn, el('p', { class: 'buzz-note', role: 'status' }, note),
        (b.order || []).length > 1 ? el('div', { class: 'buzz-order' }, b.order.map((n, i) => el('span', { class: 'chip' }, `${i + 1}. ${n}`, i > 0 && b.times && b.times[i] != null ? ` +${b.times[i] - b.times[0]} ms` : ''))) : null));
    if (sameQ) root.style.animation = 'none';
    if (b.holder && !mine) haptic(30);
    return { root, qid: q.id };
  });
}

/* ---- a slide: a round title, a rule, a break - and sometimes the standings */
function slideScene(v) {
  const q = v.q, me = v.me;
  setMood(''); ring = bar = null;
  showScene(`s:${q.id}:${me.rank}:${me.score}`, () => {
    Sound.whoosh();
    const board = v.leaderboard ? el('ol', { class: 'mini-board' }, v.leaderboard.map(p => boardRow(p, me)),
      !v.leaderboard.some(p => p.id === me.id) && me.rank ? boardRow({ id: me.id, rank: me.rank, avatar: me.avatar, nickname: me.nickname, score: me.score }, me) : null) : null;
    const root = sceneEl('slide qv phone',
      el('div', { class: 'slide-card' }, el('div', { class: 'kicker' }, q.category || (v.leaderboard ? 'Standings' : 'Up next')), richNode(q.text, 'q-text'), qImage(q, {})),
      mediaBlock(q), board, el('p', { class: 'next-up' }, 'Look up at the big screen'), siteFooter('p-foot'));
    return { root, qid: q.id };
  });
}

/* ---- reveal */
function boardRow(p, me) {
  return el('li', { class: p.id === me.id ? 'me' : '' }, el('span', { class: 'rk' }, p.rank), el('span', {}, p.avatar), el('span', { class: 'truncate' }, p.nickname),
    p.delta ? delta(p.delta) : null, el('span', { class: 'sc num' }, fmtScore(p.score)));
}

function revealScene(v) {
  const q = v.q, r = v.reveal, res = v.result, me = v.me;
  ring = bar = null;
  showScene(`r:${q.id}:${me.score}:${me.rank}`, () => {
    const first = revealedFor !== qkey(q); revealedFor = qkey(q);
    const poll = q.type === 'poll';
    let kind, head, line;
    if (poll) {
      kind = res.answered ? 'good' : 'none'; head = res.answered ? 'Vote counted' : 'Poll closed'; line = res.answered ? 'Thanks - here is how the room voted.' : 'You did not vote on this one.';
    } else if (q.type === 'buzzer') {
      if (res.answered) { kind = res.correct ? 'good' : 'bad'; head = res.correct ? 'You got it!' : 'Not quite'; line = res.correct ? 'You buzzed first and nailed it.' : 'You buzzed, but the answer was wrong.'; }
      else { kind = 'none'; head = r.buzz_winner ? `${r.buzz_winner} got it` : 'Nobody got it'; line = 'This one was a buzzer question.'; }
    } else if (res.answered) {
      if (res.correct) { kind = 'good'; head = pick(['Correct!', 'Nailed it!', 'Boom!', 'Yes!']); line = q.speed ? 'Fast fingers earn bonus points.' : 'Well played.'; }
      else if (res.partial) { kind = 'part'; head = pick(['So close!', 'Nearly there', 'Part marks']); line = 'Not perfect, but you earned some points.'; }
      else { kind = 'bad'; head = pick(['Not quite', 'So close', 'Oof']); line = 'Shake it off - the next one is yours.'; }
    } else { kind = 'none'; head = "Time's up"; line = 'You ran out of time on this one.'; }
    const firstClaim = (r.claimed || [])[0];
    if (q.scored && r.winners) {
      if (res.correct && r.winners === 1) { head = 'You were first!'; line = 'Only one player could win this one, and you took all of the points.'; }
      else if (!res.answered && r.closed_by_limit && firstClaim) {
        kind = 'none'; head = 'Too late';
        line = `${firstClaim.avatar} ${firstClaim.nickname} had already answered correctly (${fmtSecs(firstClaim.ms)}${firstClaim.at ? ', at ' + fmtTimeOfDay(firstClaim.at) : ''}).`;
      }
    }
    setMood(kind === 'good' ? 'good' : kind === 'bad' ? 'bad' : '');
    if (first) {
      if (kind === 'good') { Sound.good(); haptic([30, 40, 30]); if (!poll) confetti({ count: res.joker ? 130 : 70, duration: 2000, origin: { x: innerWidth / 2, y: innerHeight * 0.28 } }); }
      else if (kind === 'bad') { Sound.bad(); haptic(140); }
      else if (kind === 'part') { Sound.reveal(); haptic(40); }
      announce(`${head} ${res.points ? 'Plus ' + res.points + ' points.' : ''} You are ${ordinal(me.rank || 0)}.`);
    }
    const showPts = q.scored && !(q.type === 'buzzer' && !res.answered);
    const pts = el('div', { class: 'pts num' }, '');
    if (first && res.points) countUp(pts, res.points, { fmt: n => '+' + fmtScore(n), ms: 1000 }); else pts.textContent = (res.points ? '+' : '') + fmtScore(res.points);
    const glyph = icon(kind === 'good' ? 'check' : kind === 'bad' ? 'x' : kind === 'part' ? 'target' : q.type === 'buzzer' ? 'bell' : 'clock');
    const badges = el('div', { class: 'badges' },
      me.rank && q.scored ? el('span', { class: 'chip brand' }, icon('trophy'), `${ordinal(me.rank)} place`, me.delta ? delta(me.delta) : null) : null,
      res.hint_cost ? el('span', { class: 'chip warn' }, icon('eye'), `Hint used −${fmtScore(res.hint_cost)} pts`) : null,
      res.joker ? el('span', { class: 'chip joker-chip' }, icon('bolt'), 'Doubled up ×2') : null,
      me.streak >= 2 && q.scored ? el('span', { class: 'chip warn' }, icon('flame'), `${me.streak} in a row`) : null,
      res.answered && q.scored && q.type !== 'buzzer' && res.elapsed_ms != null ? el('span', { class: 'chip' }, icon('clock'),
        `${(res.elapsed_ms / 1000).toFixed(1)}s${res.correct && res.max_points && !res.joker ? ` · ${Math.round(100 * res.points / res.max_points)}% of points` : ''}`) : null,
      r.fastest && q.scored && q.type !== 'buzzer' ? el('span', { class: 'chip' }, icon('bolt'), `${r.fastest.avatar} ${r.fastest.nickname === me.nickname ? 'You' : r.fastest.nickname} · ${(r.fastest.ms / 1000).toFixed(1)}s`) : null);
    const yours = res.answered && (res.yours || []).length && !['mcq', 'multi', 'poll'].includes(q.type) && q.type !== 'pin'
      ? el('div', { class: 'yours' }, el('small', {}, 'You answered'), el('div', { class: q.type === 'order' || q.type === 'match' ? 'lines' : 'one' }, res.yours.map(t => el('div', {}, t)))) : null;
    const answerBlock = q.type === 'buzzer' ? null : el('div', { class: 'qv phone answer-block' },
      !poll ? el('div', { class: 'subhead' }, q.type === 'multi' ? 'Correct answers' : 'Correct answer') : el('div', { class: 'subhead' }, 'How the room voted'),
      revealBody(q, r, 'phone', { mine: res.pin }));
    const board = (v.leaderboard || []).slice();
    const meInBoard = board.some(p => p.id === me.id);
    const root = sceneEl('reveal',
      el('div', { class: 'verdict ' + kind }, el('div', { class: 'badge' }, glyph), el('h1', {}, head), showPts ? pts : null, el('p', { class: 'line' }, line)),
      badges, yours, answerBlock,
      claimedList(r, me.nickname),
      (r.explanation || r.explain_image) ? el('div', { class: 'explain' }, r.explanation ? el('p', {}, r.explanation) : null, r.explain_image ? el('img', { src: r.explain_image, alt: '' }) : null) : null,
      q.scored ? el('ol', { class: 'mini-board' }, board.map(p => boardRow(p, me)),
        !meInBoard && me.rank ? boardRow({ id: me.id, rank: me.rank, avatar: me.avatar, nickname: me.nickname, score: me.score, delta: me.delta }, me) : null) : null,
      el('p', { class: 'next-up' }, 'Look up at the big screen - next question soon'), siteFooter('p-foot'));
    if (!first) root.style.animation = 'none';
    return { root };
  });
}

/* ---- final */
function finalScene(v) {
  setMood(''); ring = bar = null;
  showScene(`f:${v.me.score}:${v.me.rank}`, () => {
    const me = v.me, st = v.stats || {};
    const top = me.rank && me.rank <= 3;
    if (!fanfared) { fanfared = true; Sound.fanfare(); if (top) { confetti({ count: 180, duration: 3600 }); haptic([60, 40, 60, 40, 120]); } announce(`Game over. You finished ${ordinal(me.rank || 0)} with ${me.score} points.`); }
    const board = v.leaderboard.slice(0, 5);
    const meIn = board.some(p => p.id === me.id);
    return {
      root: sceneEl('final',
        el('div', { class: 'hero' }, el('div', { class: 'medal' + (top ? ' m' + me.rank : '') }, me.rank || icon('trophy')),
          el('h1', {}, me.rank ? (top ? `You finished ${ordinal(me.rank)}!` : `You finished ${ordinal(me.rank)}`) : 'Game over'),
          el('p', { class: 'muted' }, `out of ${v.players} players`)),
        el('div', { class: 'stat-grid' },
          el('div', { class: 'stat' }, el('b', { class: 'num' }, fmtScore(me.score)), el('span', {}, 'Points')),
          el('div', { class: 'stat' }, el('b', { class: 'num' }, `${st.correct ?? 0}/${st.questions ?? 0}`), el('span', {}, 'Correct')),
          el('div', { class: 'stat' }, el('b', { class: 'num' }, st.fastest_ms != null ? (st.fastest_ms / 1000).toFixed(1) + 's' : '–'), el('span', {}, 'Fastest'))),
        el('ol', { class: 'mini-board' }, board.map(p => boardRow({ ...p, delta: 0 }, me)),
          !meIn && me.rank ? boardRow({ id: me.id, rank: me.rank, avatar: me.avatar, nickname: me.nickname, score: me.score }, me) : null),
        el('p', { class: 'foot' }, 'Thanks for playing.'), siteFooter('p-foot')),
    };
  });
}

start();
