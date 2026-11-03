'use strict';
/* Event page: register ahead of time (real name, email, 4-digit PIN, avatar) -> a "you're registered" card with calendar buttons and a
   countdown -> the same page turns into "Join now" and lets registered people straight into the game when the host opens the lobby. */

const slug = decodeURIComponent(location.pathname.split('/').filter(Boolean)[1] || '');
const stage = $('#stage');
const TICKET = 'quickz:event:' + slug;
const api_ = p => `/api/event/${encodeURIComponent(slug)}${p}`;
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
};

let info = null, me = null, skew = 0, sceneKey = null, scene = null, entering = false, pollTimer = null, enterUI = null;

const firstName = n => String(n || '').trim().split(/\s+/)[0];
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

function show(key, build) {
  if (key === sceneKey && scene) { if (scene.update) scene.update(); return; }
  sceneKey = key;
  scene = build();
  stage.replaceChildren(scene.root);
  stage.scrollTop = 0;
  if (scene.attached) scene.attached();
}

/* ---------------------------------------------------------------- when */
function fmtWhen(tz) {
  const d = new Date(info.starts_at * 1000);
  try { return new Intl.DateTimeFormat(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: tz, timeZoneName: 'short' }).format(d); }
  catch (e) { return d.toLocaleString(); }
}
function countdown() {
  const ms = info.starts_at * 1000 - (Date.now() + skew);
  if (info.state === 'live') return 'The lobby is open!';
  if (info.state === 'running') return 'The quiz is under way';
  if (info.state === 'finished') return 'This quiz has finished';
  if (ms <= 0) return 'Starting any moment: keep this page open';
  const s = Math.floor(ms / 1000), d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  if (d >= 1) return `Starts in ${plural(d, 'day')} ${h} h`;
  if (h >= 1) return `Starts in ${h} h ${m} min`;
  return `Starts in ${m}:${String(s % 60).padStart(2, '0')}`;
}
function whenBlock() {
  const mine = fmtWhen(undefined), theirs = fmtWhen(info.tz);
  const cd = el('div', { class: 'ev-count', role: 'timer' }, countdown());
  const node = el('div', { class: 'ev-when' }, el('div', { class: 'ev-date' }, mine),
    mine !== theirs ? el('div', { class: 'tiny faint' }, `Event time: ${theirs}`) : null, cd);
  return { node, tick: () => { cd.textContent = countdown(); } };
}
function head(when) {
  return [el('div', { class: 'brand' }, logo()), el('div', { class: 'game' }, el('small', {}, "You're invited to"), el('h1', {}, info.title)),
    info.description ? el('p', { class: 'ev-desc' }, info.description) : null, when.node];
}

/* ---------------------------------------------------------------- calendar + alerts */
function googleCalendar() {
  const f = t => new Date(t * 1000).toISOString().replace(/[-:]|\.\d{3}/g, '');
  const link = `${location.origin}/e/${encodeURIComponent(slug)}`;
  return 'https://calendar.google.com/calendar/render?' + new URLSearchParams({
    action: 'TEMPLATE', text: info.title, dates: `${f(info.starts_at)}/${f(info.starts_at + info.duration_min * 60)}`,
    details: (info.description ? info.description + '\n\n' : '') + 'Open this link when the quiz starts: ' + link, location: link }).toString();
}
const alertsOn = () => { try { return 'Notification' in window && Notification.permission === 'granted'; } catch (e) { return false; } };
function announceLive() {
  haptic([30, 60, 30]);
  document.title = '🔔 The quiz is live!';
  if (alertsOn()) { try { new Notification(info.title, { body: 'The quiz is live: tap to join.' }); } catch (e) { /* some phones only allow this from a service worker */ } }
}

/* ---------------------------------------------------------------- scenes */
function notice(title, sub, ...extra) {
  show('notice:' + title, () => ({ root: el('section', { class: 'scene error-screen' }, el('div', { class: 'big' }, icon('clock')),
    el('h1', {}, title), el('p', { class: 'muted' }, sub), extra, siteFooter('p-foot')) }));
}

function guestScene() {
  const reg = info.registration;                           // open | full | closed | started | finished
  show('guest:' + reg, () => {
    const when = whenBlock();
    const open = reg === 'open';
    let mode = open ? 'register' : 'signin';
    const picker = avatarPicker(groups(), store.get('quickz:avatar'));
    const err = el('p', { class: 'error-text', role: 'alert' });
    const name = el('input', { class: 'input', id: 'ev-name', maxLength: 40, autocomplete: 'name', autocapitalize: 'words', spellcheck: false, placeholder: 'Your full name', 'aria-label': 'Full name' });
    const email = el('input', { class: 'input', id: 'ev-mail', type: 'email', autocomplete: 'email', placeholder: 'you@example.com', 'aria-label': 'Email' });
    const pin = el('input', { class: 'input pin', id: 'ev-pin', inputMode: 'numeric', maxLength: 4, pattern: '[0-9]*', autocomplete: 'off', placeholder: '4-digit PIN', 'aria-label': '4-digit PIN' });
    const consent = el('input', { type: 'checkbox', id: 'ev-consent' });
    const go = el('button', { class: 'btn primary xl block', type: 'submit' });
    const swap = el('button', { class: 'linklike', type: 'button' });
    const note = el('p', { class: 'tiny faint' });
    const blocked = { full: 'This event is full. If you already registered, sign in below.', closed: 'Registration is closed. If you already registered, sign in below.',
      started: 'The quiz has started, so registration is closed. If you registered earlier, sign in to join.', finished: 'This quiz has finished.' }[reg];
    const consentRow = el('label', { class: 'check ev-consent' }, consent, el('div', {}, el('div', { class: 'd' },
      'I agree that my name and avatar appear on the big screen, and that the organisers keep my name and email for this event only. I can delete my registration at any time.')));
    const form = el('form', { class: 'scene join', novalidate: true },
      ...head(when),
      blocked ? el('p', { class: 'ev-note' }, blocked) : null,
      el('div', { class: 'card me-card' }, picker.preview, name, email, pin, picker.tabs, picker.grid, consentRow, err),
      note, el('div', { class: 'ev-swap' }, swap), el('div', { class: 'cta' }, go), siteFooter('p-foot'));
    const label = () => go.replaceChildren(mode === 'register' ? (info.state === 'live' ? 'Register and join' : 'Register') : 'Sign in', ' ', icon('right'));
    const sync = () => {
      const reg_ = mode === 'register';
      [picker.preview, picker.tabs, picker.grid, consentRow, name].forEach(n => { n.hidden = !reg_; });
      pin.placeholder = reg_ ? 'Choose a 4-digit PIN' : 'Your 4-digit PIN';
      label();
      swap.textContent = reg_ ? 'Already registered? Sign in' : open ? 'New here? Register' : '';
      swap.hidden = !reg_ && !open;
      note.textContent = reg_ ? 'Your PIN lets you sign in again from another phone. Nothing is emailed to you: add the quiz to your calendar after registering.'
        : 'Use the email and PIN you registered with.';
      err.textContent = '';
      picker.redraw();
    };
    swap.addEventListener('click', () => { mode = mode === 'register' ? 'signin' : 'register'; sync(); });
    pin.addEventListener('input', () => { pin.value = pin.value.replace(/\D/g, '').slice(0, 4); });
    form.addEventListener('submit', async e => {
      e.preventDefault(); err.textContent = '';
      const body = { email: email.value.trim(), pin: pin.value };
      if (mode === 'register') {
        if (name.value.trim().split(/\s+/).length < 2) { err.textContent = 'Please enter your first and last name.'; name.focus(); return; }
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(body.email)) { err.textContent = 'Please enter a valid email address.'; email.focus(); return; }
        if (!/^\d{4}$/.test(pin.value)) { err.textContent = 'Choose a 4-digit PIN.'; pin.focus(); return; }
        if (!consent.checked) { err.textContent = 'Please tick the box to agree.'; return; }
        Object.assign(body, { name: name.value, avatar: picker.get(), consent: true });
      } else if (!/^\d{4}$/.test(pin.value) || !body.email) { err.textContent = 'Enter your email and 4-digit PIN.'; return; }
      go.disabled = true; Sound.unlock();
      try {
        me = await api(api_(mode === 'register' ? '/register' : '/signin'), { method: 'POST', body });
        store.set(TICKET, me.ticket); store.set('quickz:avatar', me.avatar);
        Sound.start(); haptic(20);
        info.registered += mode === 'register' ? 1 : 0;
        render();
      } catch (ex) { err.textContent = ex.message; go.disabled = false; }
    });
    return { root: form, tick: when.tick, update: () => { when.tick(); label(); }, attached: sync };
  });
}

function registeredScene() {
  show('registered', () => {
    const when = whenBlock();
    const count = el('p', { class: 'tiny faint' });
    const alertBtn = el('button', { class: 'btn', type: 'button' }, icon('bell'), 'Alert me when it starts');
    const drawAlert = () => {
      const can = 'Notification' in window, state = can ? Notification.permission : 'denied';
      alertBtn.hidden = !can || state === 'denied';
      alertBtn.disabled = state === 'granted';
      alertBtn.replaceChildren(icon('bell'), state === 'granted' ? 'Alert on (keep this page open)' : 'Alert me when it starts');
    };
    alertBtn.addEventListener('click', async () => { try { await Notification.requestPermission(); } catch (e) { /* unsupported */ } drawAlert(); });
    const forget = async () => {
      if (!confirm('Cancel your registration and delete your details?')) return;
      try { await api(api_('/forget'), { method: 'POST', body: { ticket: me.ticket } }); } catch (e) { /* already gone */ }
      signOut();
    };
    const root = el('section', { class: 'scene join' },
      el('div', { class: 'brand' }, logo()),
      el('div', { class: 'game' }, el('small', {}, "You're registered for"), el('h1', {}, info.title)),
      el('div', { class: 'card me-card ev-ticket' }, el('div', { class: 'me-avatar' }, me.avatar),
        el('h2', {}, `See you there, ${firstName(me.name)}!`), el('p', { class: 'muted' }, me.name), when.node),
      el('div', { class: 'ev-actions' },
        el('a', { class: 'btn primary lg block', href: googleCalendar(), target: '_blank', rel: 'noopener' }, icon('clock'), 'Add to Google Calendar'),
        el('a', { class: 'btn lg block', href: `/e/${encodeURIComponent(slug)}.ics` }, icon('download'), 'Apple / Outlook calendar (.ics)'), alertBtn),
      el('p', { class: 'ev-note' }, 'On the day, open this page again (or scan the QR code) and you will be let straight in: no form. Keep this page open and it will take you in the moment the host opens the lobby.'),
      count,
      el('div', { class: 'ev-links' }, el('button', { class: 'linklike', type: 'button', onclick: signOut }, 'Not you? Sign out'),
        el('button', { class: 'linklike', type: 'button', onclick: forget }, 'Cancel my registration')),
      siteFooter('p-foot'));
    const update = () => { when.tick(); count.textContent = info.registered > 1 ? `${plural(info.registered, 'person')} registered so far` : ''; };
    update(); drawAlert();
    return { root, tick: when.tick, update };
  });
}

function signOut() { store.del(TICKET); me = null; sceneKey = null; scene = null; entering = false; render(); }

async function enterNow() {
  if (entering) return;
  entering = true;
  let ui_ = null;
  show('entering', () => {
    ui_ = { spinner: el('div', { class: 'spinner' }), msg: el('p', { class: 'muted' }, 'Taking you in…'),
      retry: el('button', { class: 'btn primary xl', type: 'button', hidden: true, onclick: () => { entering = false; enterNow(); } }, 'Join now') };
    return { root: el('section', { class: 'scene error-screen' }, ui_.spinner, el('h1', {}, 'The quiz is live!'), ui_.msg, ui_.retry, siteFooter('p-foot')) };
  });
  if (!ui_) ui_ = enterUI;                                         // the scene was already on screen (a retry): reuse its nodes
  enterUI = ui_;
  ui_.msg.textContent = 'Taking you in…'; ui_.retry.hidden = true; ui_.spinner.hidden = false;
  try {
    const r = await api(api_('/enter'), { method: 'POST', body: { ticket: me.ticket } });
    store.set('quickz:' + r.code, r.token);
    announceLive();
    location.replace('/j/' + encodeURIComponent(r.code));
  } catch (e) {
    if (e.status === 404) return signOut();                      // the registration was deleted
    entering = false;
    ui_.msg.textContent = e.message; ui_.retry.hidden = e.status === 403 || e.status === 410; ui_.spinner.hidden = true;
  }
}

const groups = () => info.avatar_groups;

function render() {
  if (!info) return;
  if (me && (info.state === 'live' || info.state === 'running')) return enterNow();
  if (me) return info.state === 'finished' ? notice('This quiz has finished', 'Thank you for registering. See you at the next one!') : registeredScene();
  if (info.state === 'finished') return notice('This quiz has finished', 'Thank you to everyone who took part.');
  return guestScene();
}

/* ---------------------------------------------------------------- live updates: a gentle poll, jittered so 1,000 phones do not arrive together */
function schedulePoll() { clearTimeout(pollTimer); pollTimer = setTimeout(refresh, document.hidden ? 20000 : 7000 + Math.random() * 5000); }
async function refresh() {
  try {
    const next = await api(api_(''));
    skew = next.server_now * 1000 - Date.now();
    const changed = next.state !== info.state || next.registration !== info.registration;
    const keep = info.avatar_groups;
    Object.assign(info, next, { avatar_groups: keep });
    if (changed) { if (me && next.state === 'live') announceLive(); render(); } else if (scene && scene.update) scene.update();
  } catch (e) { /* offline for a moment: try again */ }
  schedulePoll();
}

async function start() {
  mountBackdrop(10);
  try { info = await api(api_('?groups=1')); }
  catch (e) {
    stage.replaceChildren(el('section', { class: 'scene error-screen' }, el('div', { class: 'big' }, icon('help')), el('h1', {}, 'Event not found'),
      el('p', { class: 'muted' }, 'Check the link or QR code you were given.'), siteFooter('p-foot')));
    return;
  }
  skew = info.server_now * 1000 - Date.now();
  const ticket = store.get(TICKET);
  if (ticket) { try { me = await api(api_('/me'), { method: 'POST', body: { ticket } }); } catch (e) { if (e.status === 404) store.del(TICKET); } }
  render();
  setInterval(() => { if (scene && scene.tick) scene.tick(); }, 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  schedulePoll();
}
start();
