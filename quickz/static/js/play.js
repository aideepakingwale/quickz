'use strict';
/* Player: join -> lobby -> question / buzzer -> reveal -> final. A stateless renderer over server views;
   scenes are only rebuilt when their key changes so animations never replay by accident. */

const pathParts = location.pathname.split('/').filter(Boolean);
const code = pathParts[0] === 'j' ? decodeURIComponent(pathParts[1] || '') : (new URLSearchParams(location.search).get('code') || '');
const KEY = 'quickz:' + code;
const stage = $('#stage');

let conn = null, last = null, sceneKey = null, scene = null;
let submitted = null, multiSel = new Set(), shownScore = 0, fanfared = false, revealedFor = null;
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

function sceneEl(cls, ...kids) { return el('section', { class: 'scene ' + cls }, ...kids); }

/* ---------------------------------------------------------------- boot */
async function start() {
  $('#snd').append(icon(Sound.on ? 'volume' : 'mute'));
  $('#snd').addEventListener('click', () => {
    Sound.setOn(!Sound.on); Sound.unlock(); if (Sound.on) Sound.pop();
    $('#snd').replaceChildren(icon(Sound.on ? 'volume' : 'mute'));
  });
  document.addEventListener('pointerdown', () => Sound.unlock(), { once: true });

  if (!code) { location.href = '/'; return; }
  let info;
  try { info = await api('/api/game/' + encodeURIComponent(code)); }
  catch (e) { return errorScene('Game not found', 'Double-check the code, or ask the host for the QR code again.'); }
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
  let avatar = store.get('quickz:avatar');
  if (!AVATARS.includes(avatar)) avatar = pick(AVATARS);
  const name = el('input', { class: 'input', id: 'nick', maxLength: 20, autocomplete: 'nickname', autocapitalize: 'words', spellcheck: false,
    placeholder: 'Your nickname', value: store.get('quickz:name') || randomName(), required: true, 'aria-label': 'Nickname' });
  const email = info.ask_email ? el('input', { class: 'input', id: 'mail', type: 'email', autocomplete: 'email', required: true, placeholder: 'you@company.com', 'aria-label': 'Email' }) : null;
  const err = el('p', { class: 'error-text', role: 'alert' });
  const big = el('div', { class: 'me-avatar', 'aria-hidden': 'true' }, avatar);
  const grid = el('div', { class: 'avatar-grid', role: 'radiogroup', 'aria-label': 'Choose your avatar' });
  const draw = () => {
    big.textContent = avatar;
    big.classList.add('spin'); setTimeout(() => big.classList.remove('spin'), 220);
    $$('button', grid).forEach(b => b.setAttribute('aria-pressed', String(b.dataset.a === avatar)));
  };
  AVATARS.forEach(a => grid.append(el('button', { type: 'button', 'data-a': a, 'aria-label': a, 'aria-pressed': String(a === avatar), onclick: () => { avatar = a; Sound.tap(); haptic(8); draw(); } }, a)));
  const go = el('button', { class: 'btn primary xl block', type: 'submit' }, "Let's play ", icon('right'));
  const form = el('form', { class: 'scene join', novalidate: true },
    el('div', { class: 'brand' }, logo()),
    el('div', { class: 'game' }, el('small', {}, "You're joining"), el('h1', {}, info.title)),
    el('div', { class: 'card me-card' }, big,
      el('div', { class: 'name-row' }, name, el('button', { class: 'btn icon-only', type: 'button', 'aria-label': 'Random nickname', onclick: () => { name.value = randomName(); Sound.pop(); } }, icon('dice'))),
      email, grid, err),
    el('div', { class: 'cta' }, go, el('p', { class: 'tiny faint' }, 'No account needed. Your nickname is shown on the big screen.')),
    siteFooter('p-foot'));
  form.addEventListener('submit', async e => {
    e.preventDefault(); err.textContent = '';
    if (!name.value.trim()) { err.textContent = 'Pick a nickname first.'; name.focus(); return; }
    go.disabled = true; Sound.unlock();
    try {
      const r = await api('/api/join', { method: 'POST', body: { code, nickname: name.value, email: email ? email.value : '', avatar } });
      store.set(KEY, r.token); store.set('quickz:avatar', r.avatar); store.set('quickz:name', r.nickname);
      Sound.start(); haptic(20);
      connect(r.token);
    } catch (ex) { err.textContent = ex.message; go.disabled = false; }
  });
  stage.replaceChildren(form);
  sceneKey = 'join'; scene = null;
  draw();
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

function render(v) {
  header(v);
  if (v.phase === 'lobby') return lobbyScene(v);
  if (v.phase === 'question') return v.q.type === 'buzzer' ? buzzerScene(v) : questionScene(v);
  if (v.phase === 'reveal') return revealScene(v);
  return finalScene(v);
}

/* ---- lobby */
function lobbyScene(v) {
  setMood(''); ring = bar = null; revealedFor = null;
  showScene('lobby', () => {
    const count = el('span', { id: 'lobby-n' }, v.players);
    return {
      root: sceneEl('lobby',
        el('div', { class: 'me-avatar', 'aria-hidden': 'true' }, v.me.avatar),
        el('div', {}, el('h1', {}, `You're in, ${v.me.nickname}!`), el('p', { class: 'muted', style: { marginTop: '6px' } }, v.title)),
        el('div', { class: 'dots', 'aria-hidden': 'true' }, el('span'), el('span'), el('span')),
        el('p', { class: 'muted' }, 'Waiting for the host to start…'),
        el('div', { class: 'chip brand count' }, icon('users'), count, ' ready'),
        el('p', { class: 'tip faint' }, 'Keep this screen open. Turn your sound on for the buzzer rounds.'), siteFooter('p-foot')),
      update(nv) { count.textContent = nv.players; },
    };
  });
}

/* ---- question */
const tileEl = (o, i, extra = {}) => el('button', { class: `tile a${i % 6}`, type: 'button', 'data-id': o.id, ...extra },
  el('i', { class: 'shape', 'aria-hidden': 'true' }, SHAPES[i % 6]), el('span', { class: 'txt' }, richInline(o.text)));

/* The clip (if this device should play it). One element per question, re-used across scene rebuilds so a tap on
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

function questionHead(v) {
  const q = v.q;
  ring = makeClock(84);
  bar = el('div', { class: 'q-bar', 'aria-hidden': 'true' }, el('i'));
  return [
    el('div', { class: 'q-meta' }, el('span', { class: 'qn' }, `Question ${q.index + 1} of ${q.total}`),
      el('span', { class: 'chip qpts' }, icon('bolt'), `${fmtScore(q.points)}${q.speed ? ` · speed ${q.speed}%` : ''}`), ring.el),
    bar,
    (q.winners || q.only_players) ? el('div', { class: 'q-flags' },
      q.only_players ? el('span', { class: 'chip brand' }, icon('bolt'), 'Tie-breaker: ' + q.only_players.map(p => `${p.avatar} ${p.nickname}`).join(' vs ')) : null,
      q.winners ? el('span', { class: 'chip warn' }, icon('trophy'), winnersText(q)) : null) : null,
    el('div', { class: 'q-card' }, richNode(q.text, 'q-text' + (plainText(q.text).length > 110 ? ' long' : '')), q.image && el('img', { class: 'qimg', src: q.image, alt: '' })),
    mediaBlock(q),
  ];
}

function questionScene(v) {
  const q = v.q;
  const locked = v.answered || (submitted && submitted.qid === q.id);
  const spectator = !!q.only_players && q.can_play === false;       // a tie-breaker this player isn't part of
  const awaiting = !!q.awaiting_media || spectator;
  const sameQ = scene && scene.qid === q.id;
  setMood('');
  showScene(`q:${q.id}:${locked ? 1 : 0}:${awaiting ? 1 : 0}:${q.spots_left}`, () => {
    const multi = q.type === 'multi';
    const chosen = submitted && submitted.qid === q.id ? submitted.choice : null;
    const one = q.options.length > 4 || q.options.some(o => plainText(o.text).length > 34);
    const tiles = el('div', { class: 'tiles ' + (one ? 'cols-1' : 'cols-2') + (awaiting ? ' await' : ''), role: multi ? 'group' : 'radiogroup', 'aria-label': 'Answer options' });
    const submit = el('button', { class: 'btn primary xl block', type: 'button', disabled: true, onclick: () => send([...multiSel]) }, 'Lock in answer');
    q.options.forEach((o, i) => {
      const t = tileEl(o, i, { disabled: !!locked || awaiting, 'aria-pressed': 'false' });
      if (locked && chosen) t.classList.add(chosen.includes(o.id) ? 'sel' : 'dim');
      if (!locked && multi && multiSel.has(o.id)) { t.classList.add('sel'); submit.disabled = false; }
      if (!locked && !awaiting) t.addEventListener('click', () => {
        if (!multi) return send([o.id]);
        Sound.tap(); haptic(8);
        if (multiSel.has(o.id)) multiSel.delete(o.id); else multiSel.add(o.id);
        t.classList.toggle('sel', multiSel.has(o.id)); t.setAttribute('aria-pressed', String(multiSel.has(o.id)));
        submit.disabled = multiSel.size === 0;
      });
      tiles.append(t);
    });
    function send(choice) {
      if (submitted && submitted.qid === q.id) return;
      submitted = { qid: q.id, choice }; Sound.tap(); haptic(18);
      conn.send({ t: 'answer', choice }); announce('Answer locked in');
      render(last);
    }
    const root = sceneEl('q-wrap', ...questionHead(v), multi && !locked && el('p', { class: 'q-hint' }, 'Select all that apply, then lock in.'), tiles,
      spectator ? el('div', { class: 'media-note' }, icon('eye'), 'Tie-breaker: only the tied players answer this one. Watch the big screen!') : null,
      locked ? el('div', { class: 'locked' }, el('div', { class: 'spinner', style: { width: '20px', height: '20px', borderWidth: '2px' } }), 'Answer locked in - waiting for the others…')
        : multi ? el('div', { class: 'submit-bar' }, submit) : null);
    if (sameQ) root.style.animation = 'none';
    if (!locked && !sameQ) announce(`Question ${q.index + 1}. ${plain(q.text)}`);
    return { root, qid: q.id };
  });
}

/* ---- buzzer */
function buzzerScene(v) {
  const q = v.q, b = v.buzz || {}, spectator = !!q.only_players && q.can_play === false, out = !!v.locked_out || spectator, mine = !!b.holder_is_me;
  setMood(b.holder ? 'buzz' : '');
  showScene(`b:${q.id}:${b.holder || 0}:${out ? 1 : 0}:${(b.order || []).length}`, () => {
    const label = b.holder ? (mine ? 'YOU!' : 'LOCKED') : out ? 'OUT' : 'BUZZ!';
    const note = b.holder ? (mine ? 'You buzzed first. Say your answer out loud!' : `${b.holder_name} buzzed first`)
      : out ? "Wrong answer - you're out for this question" : 'Be the first to hit the buzzer';
    const btn = el('button', { class: 'buzzer' + (mine ? ' mine' : ''), type: 'button', disabled: !!b.holder || out, 'aria-label': 'Buzzer' }, label);
    btn.addEventListener('pointerdown', e => { e.preventDefault(); Sound.unlock(); Sound.buzz(); haptic([40, 30, 40]); conn.send({ t: 'buzz' }); });
    const sameQ = scene && scene.qid === q.id;
    const root = sceneEl('q-wrap', ...questionHead(v),
      el('div', { class: 'buzz-stage' }, btn, el('p', { class: 'buzz-note', role: 'status' }, note),
        (b.order || []).length > 1 ? el('div', { class: 'buzz-order' }, b.order.map((n, i) => el('span', { class: 'chip' }, `${i + 1}. ${n}`))) : null));
    if (sameQ) root.style.animation = 'none';
    if (b.holder && !mine) haptic(30);
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
  showScene(`r:${q.id}:${me.score}:${me.rank}`, () => {
    const first = revealedFor !== q.id; revealedFor = q.id;
    ring = bar = null;
    let kind, head, line;
    if (q.type === 'buzzer') {
      if (res.answered) { kind = res.correct ? 'good' : 'bad'; head = res.correct ? 'You got it!' : 'Not quite'; line = res.correct ? 'You buzzed first and nailed it.' : 'You buzzed, but the answer was wrong.'; }
      else { kind = 'none'; head = r.buzz_winner ? `${r.buzz_winner} got it` : 'Nobody got it'; line = 'This one was a buzzer question.'; }
    } else if (res.answered) { kind = res.correct ? 'good' : 'bad'; head = res.correct ? pick(['Correct!', 'Nailed it!', 'Boom!', 'Yes!']) : pick(['Not quite', 'So close', 'Oof']); line = res.correct ? 'Fast fingers earn bonus points.' : 'Shake it off - the next one is yours.'; }
    else { kind = 'none'; head = "Time's up"; line = 'You ran out of time on this one.'; }
    const firstClaim = (r.claimed || [])[0];
    if (q.type !== 'buzzer' && r.winners) {
      if (res.correct && r.winners === 1) { head = 'You were first!'; line = 'Only one player could win this one, and you took all of the points.'; }
      else if (!res.answered && r.closed_by_limit && firstClaim) {
        kind = 'none'; head = 'Too late';
        line = `${firstClaim.avatar} ${firstClaim.nickname} had already answered correctly (${fmtSecs(firstClaim.ms)}${firstClaim.at ? ', at ' + fmtTimeOfDay(firstClaim.at) : ''}).`;
      }
    }
    setMood(kind === 'good' ? 'good' : kind === 'bad' ? 'bad' : '');
    if (first) {
      if (kind === 'good') { Sound.good(); haptic([30, 40, 30]); confetti({ count: 70, duration: 2000, origin: { x: innerWidth / 2, y: innerHeight * 0.28 } }); }
      else if (kind === 'bad') { Sound.bad(); haptic(140); }
      announce(`${head} ${res.points ? 'Plus ' + res.points + ' points.' : ''} You are ${ordinal(me.rank || 0)}.`);
    }
    const showPts = !(q.type === 'buzzer' && !res.answered);
    const pts = el('div', { class: 'pts num' }, '');
    if (first && res.points) countUp(pts, res.points, { fmt: n => '+' + fmtScore(n), ms: 1000 }); else pts.textContent = (res.points ? '+' : '') + fmtScore(res.points);
    const glyph = icon(kind === 'good' ? 'check' : kind === 'bad' ? 'x' : q.type === 'buzzer' ? 'bell' : 'clock');
    const badges = el('div', { class: 'badges' },
      me.rank ? el('span', { class: 'chip brand' }, icon('trophy'), `${ordinal(me.rank)} place`, me.delta ? delta(me.delta) : null) : null,
      me.streak >= 2 ? el('span', { class: 'chip warn' }, icon('flame'), `${me.streak} in a row`) : null,
      res.answered && q.type !== 'buzzer' && res.elapsed_ms != null ? el('span', { class: 'chip' }, icon('clock'),
        `${(res.elapsed_ms / 1000).toFixed(1)}s${res.correct && res.max_points ? ` · ${Math.round(100 * res.points / res.max_points)}% of points` : ''}`) : null,
      r.fastest && q.type !== 'buzzer' ? el('span', { class: 'chip' }, icon('bolt'), `${r.fastest.avatar} ${r.fastest.nickname === me.nickname ? 'You' : r.fastest.nickname} · ${(r.fastest.ms / 1000).toFixed(1)}s`) : null);
    const correctOpts = q.options.map((o, i) => ({ o, i })).filter(x => r.correct.includes(x.o.id));
    const board = (v.leaderboard || []).slice();
    const meInBoard = board.some(p => p.id === me.id);
    const root = sceneEl('reveal',
      el('div', { class: 'verdict ' + kind }, el('div', { class: 'badge' }, glyph), el('h1', {}, head), showPts ? pts : null, el('p', { class: 'line' }, line)),
      badges,
      q.type !== 'buzzer' ? el('div', { class: 'col', style: { gap: '8px' } }, correctOpts.map(({ o, i }) =>
        el('div', { class: `answer-card a${i % 6}` }, el('i', { class: 'shape' }, SHAPES[i % 6]), el('div', {}, el('div', { class: 'tag' }, correctOpts.length > 1 ? 'Correct answers' : 'Correct answer'), el('div', { class: 't' }, richInline(o.text)))))) : null,
      claimedList(r, me.nickname),
      r.explanation ? el('p', { class: 'explain' }, r.explanation) : null,
      el('ol', { class: 'mini-board' }, board.map(p => boardRow(p, me)),
        !meInBoard && me.rank ? boardRow({ id: me.id, rank: me.rank, avatar: me.avatar, nickname: me.nickname, score: me.score, delta: me.delta }, me) : null),
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
