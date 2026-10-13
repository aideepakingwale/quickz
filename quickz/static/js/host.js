'use strict';
/* Host console: login, library, game start, LIVE control (one primary action), results. */

const view = $('#view');
const topbar = $('#topbar');
let leaveFns = [];
const onLeave = fn => leaveFns.push(fn);
const go = hash => { location.hash = hash; };

function mount(...nodes) {
  const list = nodes.filter(Boolean);
  if (!list.some(n => n.tagName === 'FOOTER')) list.push(siteFooter());
  view.replaceChildren(...list);
  window.scrollTo(0, 0);
}

const SAMPLE_QUIZ = `# Team Trivia: Warm-up
A friendly mix of questions to try out Quickz.

Q: What is the capital of **France**?
- Berlin
- Paris*
- Rome
- Madrid
time: 15
? Paris has been the capital of France since 987.

Q: Which of these are *prime* numbers?
- 2*
- 4
- 7*
- 9
time: 25

Q: Which planet has the most moons?
- Mercury
- Saturn*
- Mars
- Venus
time: 20

Q: True or false: the Great Wall of China is visible from space with the naked eye.
- True
- False*
points: 500
time: 20
? Astronauts have confirmed it is not visible without aid.

Q: Name the band that wrote "Bohemian Rhapsody"
type: buzzer
points: 1500
`;

/* ---------------------------------------------------------------- shell */
let me = null;
const isAdmin = () => !!me && me.role === 'admin';
const initialOf = n => (String(n || '?').trim()[0] || '?').toUpperCase();
const roleName = r => (r === 'admin' ? 'Super Admin' : 'Quiz Host');

function paintTopbar(active) {
  const dark = () => document.documentElement.dataset.theme === 'dark';
  const themeBtn = el('button', { class: 'btn ghost sm icon-only', type: 'button', 'aria-label': 'Toggle light / dark theme', title: 'Toggle theme' }, icon(dark() ? 'sun' : 'moon'));
  themeBtn.addEventListener('click', () => { Theme.toggle(); themeBtn.replaceChildren(icon(dark() ? 'sun' : 'moon')); });
  const userBtn = el('button', { class: 'btn ghost sm user-btn', type: 'button', 'aria-haspopup': 'menu', 'aria-label': `Account menu for ${me.display_name}` },
    el('span', { class: 'ava' }, initialOf(me.display_name)), el('span', { class: 'uname truncate' }, me.display_name), icon('down'));
  userBtn.addEventListener('click', () => popMenu(userBtn, [
    { info: [el('b', {}, me.display_name), el('span', { class: 'faint' }, `@${me.username} · ${roleName(me.role)}`)] }, '-',
    { label: 'Account settings', icon: 'edit', run: () => go('#/account') },
    { label: 'Sign out', icon: 'logout', run: signOut },
  ]));
  topbar.hidden = false;
  topbar.replaceChildren(logo(),
    el('nav', { class: 'nav', 'aria-label': 'Main' },
      el('a', { href: '#/', 'aria-current': active === 'quizzes' ? 'page' : null }, 'Quizzes'),
      el('a', { href: '#/games', 'aria-current': active === 'games' ? 'page' : null }, 'Games'),
      isAdmin() ? el('a', { href: '#/team', 'aria-current': active === 'team' ? 'page' : null }, 'Team') : null),
    el('span', { class: 'spacer' }), el('a', { class: 'btn ghost sm', href: '/', target: '_blank', rel: 'noopener' }, icon('external'), 'Player page'), themeBtn, userBtn);
}

async function signOut() {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch (e) { /* already signed out */ }
  me = null; runLeave(); renderLogin();
}

function popMenu(anchor, items, { up = false } = {}) {
  document.querySelectorAll('.menu').forEach(n => n.remove());
  const r = anchor.getBoundingClientRect();
  const m = el('div', { class: 'menu', role: 'menu' }, items.map(it => it === '-' ? el('hr') : it.info ? el('div', { class: 'menu-info' }, it.info)
    : el('button', { type: 'button', role: 'menuitem', class: it.danger ? 'danger' : '', onclick: () => { close(); it.run(); } }, it.icon ? icon(it.icon) : null, it.label)));
  document.body.append(m);
  const w = m.offsetWidth, h = m.offsetHeight;
  m.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.right - w)) + 'px';
  m.style.top = (up ? Math.max(8, r.top - h - 8) : Math.min(innerHeight - h - 8, r.bottom + 6)) + 'px';
  const close = () => { m.remove(); document.removeEventListener('pointerdown', away, true); document.removeEventListener('keydown', esc); };
  const away = e => { if (!m.contains(e.target)) close(); };
  const esc = e => { if (e.key === 'Escape') close(); };
  setTimeout(() => { document.addEventListener('pointerdown', away, true); document.addEventListener('keydown', esc); }, 0);
  m.querySelector('button')?.focus();
}

/* ---------------------------------------------------------------- router */
async function boot() {
  let st;
  try { st = await api('/api/auth/state'); }
  catch (e) { return mount(el('div', { class: 'login' }, el('h1', {}, 'Cannot reach Quickz'), el('p', { class: 'muted' }, e.message))); }
  window.QUICKZ_MAX_MEDIA_MB = st.max_media_mb || 50;
  if (st.setup_needed) return renderSetup();
  if (!st.user) return renderLogin();
  me = st.user;
  if (me.must_change) return renderForcedChange();
  route();
}
addEventListener('hashchange', () => { if (me && !me.must_change) route(); });
addEventListener('quickz:unauth', () => { if (me) { me = null; runLeave(); renderLogin('Your session ended. Please sign in again.'); } });
addEventListener('quickz:mustchange', () => { if (me) { me.must_change = true; runLeave(); renderForcedChange(); } });

function runLeave() { const f = leaveFns; leaveFns = []; f.forEach(fn => { try { fn(); } catch (e) { console.error(e); } }); }

function route() {
  runLeave();
  document.querySelectorAll('.menu, .drawer, .drawer-scrim').forEach(n => n.remove());
  $('#net').hidden = true;
  const h = location.hash.replace(/^#\/?/, '').split('/');
  view.className = 'view';
  if (h[0] === 'quiz') { view.className = 'view bare'; paintTopbar('quizzes'); return renderEditor(h[1] || 'new'); }
  if (h[0] === 'game') { view.className = 'view wide'; paintTopbar('games'); return renderLive(h[1]); }
  if (h[0] === 'results') { paintTopbar('games'); return renderResults(h[1]); }
  if (h[0] === 'games') { paintTopbar('games'); return renderGames(); }
  if (h[0] === 'account') { paintTopbar(''); return renderAccount(); }
  if (h[0] === 'team') { if (!isAdmin()) return go('#/'); paintTopbar('team'); return renderTeam(); }
  paintTopbar('quizzes');
  return renderDashboard();
}

/* ---------------------------------------------------------------- auth screens */
function passwordField(label, { id, autocomplete = 'current-password', autofocus = false } = {}) {
  const input = el('input', { class: 'input', id, type: 'password', autocomplete, autofocus, 'aria-label': label, maxLength: 128 });
  const toggle = el('button', { class: 'btn ghost sm icon-only', type: 'button', 'aria-label': 'Show password', title: 'Show or hide password' }, icon('eye'));
  toggle.addEventListener('click', () => { const show = input.type === 'password'; input.type = show ? 'text' : 'password'; toggle.setAttribute('aria-label', show ? 'Hide password' : 'Show password'); toggle.setAttribute('aria-pressed', String(show)); });
  return { input, node: el('div', { class: 'field' }, el('label', { for: id }, label), el('div', { class: 'pw-wrap' }, input, toggle)) };
}

function strengthMeter(input) {
  const bar = el('i'), label = el('span', { class: 'meter-label' }, 'At least 10 characters. A few random words make a strong password.');
  const paint = () => {
    const pw = input.value;
    const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter(r => r.test(pw)).length;
    const score = !pw ? 0 : pw.length < 10 ? 1 : (pw.length >= 16 || (pw.length >= 12 && kinds >= 3)) ? 3 : 2;
    const [txt, w, c] = [['', 0, ''], ['Too short', 25, 'var(--danger)'], ['Okay', 62, 'var(--warn)'], ['Strong', 100, 'var(--success)']][score];
    bar.style.width = w + '%'; bar.style.background = c; label.textContent = pw ? txt : 'At least 10 characters. A few random words make a strong password.';
  };
  input.addEventListener('input', paint);
  return el('div', { class: 'col', style: { gap: '.3rem' } }, el('div', { class: 'meter' }, bar), label);
}

function authShell(title, sub, form, wide = false) {
  topbar.hidden = true;
  mount(el('div', { class: 'login' + (wide ? ' wide' : '') }, el('div', { class: 'center' }, logo()),
    el('div', { class: 'card' }, el('h1', {}, title), sub ? el('p', { class: 'muted', style: { margin: '.35rem 0 1.1rem' } }, sub) : null, form)));
}

function renderSetup() {
  const tokenFromLink = (location.hash.match(/^#\/setup\/(.+)$/) || [])[1] || '';
  const err = el('p', { class: 'error-text', role: 'alert' });
  const token = el('input', { class: 'input', id: 'su-token', value: decodeURIComponent(tokenFromLink), autocomplete: 'off', spellcheck: false, 'aria-label': 'Setup token' });
  const name = el('input', { class: 'input', id: 'su-name', autocomplete: 'name', placeholder: 'e.g. Deepak Ingwale', maxLength: 60, 'aria-label': 'Your name' });
  const user = el('input', { class: 'input', id: 'su-user', autocomplete: 'username', autocapitalize: 'none', spellcheck: false, placeholder: 'e.g. deepak', maxLength: 32, 'aria-label': 'Username' });
  let userEdited = false;
  user.addEventListener('input', () => { userEdited = true; });
  name.addEventListener('input', () => { if (!userEdited) user.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, '.').replace(/^\.+|\.+$/g, '').slice(0, 32); });
  const pw = passwordField('Password', { id: 'su-pw', autocomplete: 'new-password' });
  const pw2 = passwordField('Confirm password', { id: 'su-pw2', autocomplete: 'new-password' });
  const go2 = el('button', { class: 'btn primary lg block', type: 'submit', style: { marginTop: '.4rem' } }, 'Create Super Admin account');
  const form = el('form', { class: 'col', novalidate: true },
    el('div', { class: 'field' }, el('label', { for: 'su-token' }, 'Setup token'), token, el('span', { class: 'hint' }, 'Printed in the terminal where Quickz is running (or just use the setup link shown there).')),
    el('div', { class: 'field' }, el('label', { for: 'su-name' }, 'Your name'), name),
    el('div', { class: 'field' }, el('label', { for: 'su-user' }, 'Username'), user),
    pw.node, strengthMeter(pw.input), pw2.node, err, go2);
  form.addEventListener('submit', async e => {
    e.preventDefault(); err.textContent = '';
    if (pw.input.value !== pw2.input.value) { err.textContent = 'The two passwords do not match.'; return; }
    go2.disabled = true;
    try {
      const r = await api('/api/auth/setup', { method: 'POST', body: { token: token.value.trim(), display_name: name.value, username: user.value, password: pw.input.value } });
      me = r.user; history.replaceState(null, '', '/host'); ui.toast('Super Admin account created. Welcome!', 'success'); route();
    } catch (ex) { err.textContent = ex.message; go2.disabled = false; }
  });
  authShell('Welcome to Quickz', 'Create the Super Admin account. You will use it to add Quiz Hosts and manage the team.', form, true);
  (tokenFromLink ? name : token).focus();
}

function renderLogin(notice) {
  const err = el('p', { class: 'error-text', role: 'alert' }, notice || '');
  const user = el('input', { class: 'input', id: 'li-user', autocomplete: 'username', autocapitalize: 'none', spellcheck: false, 'aria-label': 'Username', autofocus: true });
  const pw = passwordField('Password', { id: 'li-pw' });
  const btn = el('button', { class: 'btn primary lg block', type: 'submit', style: { marginTop: '.4rem' } }, 'Sign in');
  const form = el('form', { class: 'col', novalidate: true }, el('div', { class: 'field' }, el('label', { for: 'li-user' }, 'Username'), user), pw.node, err, btn,
    el('p', { class: 'hint center' }, 'Forgot your password? Ask your Super Admin to reset it.'));
  form.addEventListener('submit', async e => {
    e.preventDefault(); err.textContent = ''; btn.disabled = true;
    try {
      const r = await api('/api/auth/login', { method: 'POST', body: { username: user.value, password: pw.input.value } });
      me = r.user;
      if (me.must_change) return renderForcedChange();
      if (!location.hash || location.hash.startsWith('#/setup')) history.replaceState(null, '', '/host');
      route();
    } catch (ex) { err.textContent = ex.message; pw.input.select(); }
    btn.disabled = false;
  });
  authShell('Host sign-in', 'Sign in to build quizzes and run games.', form);
  if (notice) user.focus();
}

function renderForcedChange() {
  const err = el('p', { class: 'error-text', role: 'alert' });
  const cur = passwordField('Temporary password', { id: 'fc-cur', autofocus: true });
  const pw = passwordField('New password', { id: 'fc-new', autocomplete: 'new-password' });
  const pw2 = passwordField('Confirm new password', { id: 'fc-new2', autocomplete: 'new-password' });
  const btn = el('button', { class: 'btn primary lg block', type: 'submit', style: { marginTop: '.4rem' } }, 'Save password and continue');
  const form = el('form', { class: 'col', novalidate: true }, cur.node, pw.node, strengthMeter(pw.input), pw2.node, err, btn,
    el('button', { class: 'btn ghost block', type: 'button', onclick: signOut }, 'Sign out'));
  form.addEventListener('submit', async e => {
    e.preventDefault(); err.textContent = '';
    if (pw.input.value !== pw2.input.value) { err.textContent = 'The two new passwords do not match.'; return; }
    btn.disabled = true;
    try { await api('/api/auth/password', { method: 'POST', body: { current: cur.input.value, new: pw.input.value } }); me.must_change = false; ui.toast('Password updated', 'success'); route(); }
    catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  });
  authShell('Choose your own password', `Hi ${me ? me.display_name : ''}. Your administrator gave you a temporary password. Pick a new one that only you know.`, form);
}

/* ---------------------------------------------------------------- account page */
function renderAccount() {
  const nameIn = el('input', { class: 'input', id: 'ac-name', value: me.display_name, maxLength: 60, 'aria-label': 'Display name' });
  const nameForm = el('form', { class: 'col', onsubmit: async e => {
    e.preventDefault();
    try { const u = await api('/api/me', { method: 'PATCH', body: { display_name: nameIn.value } }); me = { ...me, ...u }; paintTopbar(''); ui.toast('Profile saved', 'success'); } catch (x) { ui.toast(x.message, 'error'); }
  } }, el('div', { class: 'field' }, el('label', { for: 'ac-name' }, 'Display name'), nameIn, el('span', { class: 'hint' }, 'Shown to your team and next to your quizzes.')),
    el('div', {}, el('button', { class: 'btn primary', type: 'submit' }, 'Save')));

  const perr = el('p', { class: 'error-text', role: 'alert' });
  const cur = passwordField('Current password', { id: 'ac-cur' }), pw = passwordField('New password', { id: 'ac-new', autocomplete: 'new-password' }), pw2 = passwordField('Confirm new password', { id: 'ac-new2', autocomplete: 'new-password' });
  const pwForm = el('form', { class: 'col', novalidate: true }, cur.node, pw.node, strengthMeter(pw.input), pw2.node, perr,
    el('div', {}, el('button', { class: 'btn primary', type: 'submit' }, 'Change password')));
  pwForm.addEventListener('submit', async e => {
    e.preventDefault(); perr.textContent = '';
    if (pw.input.value !== pw2.input.value) { perr.textContent = 'The two new passwords do not match.'; return; }
    try { await api('/api/auth/password', { method: 'POST', body: { current: cur.input.value, new: pw.input.value } }); pwForm.reset(); ui.toast('Password changed. Other devices were signed out.', 'success'); }
    catch (x) { perr.textContent = x.message; }
  });

  mount(el('div', { class: 'page-head' }, el('div', { class: 'grow' }, el('h1', {}, 'Account settings'), el('div', { class: 'sub' }, `@${me.username} · ${roleName(me.role)}`))),
    el('div', { class: 'acct-grid' },
      el('section', { class: 'card' }, el('h2', { class: 'h-sm' }, 'Profile'), nameForm),
      el('section', { class: 'card' }, el('h2', { class: 'h-sm' }, 'Password'), pwForm),
      el('section', { class: 'card' }, el('h2', { class: 'h-sm' }, 'Devices'), el('p', { class: 'muted', style: { margin: '.3rem 0 .9rem' } }, 'Signed in somewhere you no longer use? End every session, including this one.'),
        el('button', { class: 'btn', type: 'button', onclick: async () => { if (await ui.confirm({ title: 'Sign out everywhere?', message: 'You will need to sign in again on every device.', confirmText: 'Sign out everywhere' })) { try { await api('/api/auth/logout-all', { method: 'POST' }); } catch (x) { /* ignore */ } me = null; runLeave(); renderLogin(); } } }, icon('logout'), 'Sign out of all devices'))));
}

/* ---------------------------------------------------------------- team (Super Admin) */
function credentialsDialog({ title, username, password, note }) {
  const url = `${location.origin}/host`;
  const text = `Quickz sign-in\nAddress: ${url}\nUsername: ${username}\nTemporary password: ${password}\nYou will choose your own password at first sign-in.`;
  const m = ui.modal({ title, body: el('div', { class: 'col' },
    el('p', { class: 'muted' }, note),
    el('div', { class: 'cred-box' }, el('div', {}, el('span', { class: 'faint' }, 'Username '), el('b', {}, username)), el('div', {}, el('span', { class: 'faint' }, 'Temporary password '), el('b', { class: 'mono' }, password))),
    el('p', { class: 'hint' }, 'This password is shown only once and is not stored. Share it privately. They must choose a new one when they first sign in.')),
    actions: [el('button', { class: 'btn', type: 'button', onclick: () => { (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(() => ui.toast('Sign-in details copied', 'success'), () => ui.toast('Could not copy. Select the text instead.', 'error')); } }, icon('copy'), 'Copy details'),
      el('button', { class: 'btn primary', type: 'button', onclick: () => m.close() }, 'Done')] });
}

function backupCard(b) {
  if (!b.enabled) return el('div', { class: 'card flat backup-card', style: { marginBottom: '1rem' } }, icon('warn'),
    el('div', { class: 'grow' }, el('b', {}, 'Cloud backup is off'), el('div', { class: 'muted', style: { fontSize: '.88rem' } }, 'Data is kept on this computer’s disk only. On hosts with a temporary disk (Render free tier) it would be lost on restart. See docs/DEPLOY.md.')));
  const ok = !b.last_error && b.last_ok;
  return el('div', { class: 'card backup-card', style: { marginBottom: '1rem' } }, icon(ok ? 'check' : 'warn'),
    el('div', { class: 'grow' }, el('b', {}, 'Cloud backup is on'),
      el('div', { class: 'muted', style: { fontSize: '.88rem' } }, b.last_error ? `Last attempt failed: ${b.last_error}` : b.last_ok ? `Last saved ${timeAgo(b.last_ok)} to ${b.bucket}. Accounts, quizzes, results and uploads are safe if this server restarts.` : `Connected to ${b.bucket}. The first snapshot is uploaded a few seconds after anything changes.`)),
    el('span', { class: 'chip ' + (ok ? 'good' : 'warn') }, ok ? 'Healthy' : 'Check'), b.restored ? el('span', { class: 'chip' }, 'Restored at start-up') : null);
}

async function renderTeam() {
  mount(el('div', { class: 'skeleton', style: { height: '260px' } }));
  let users;
  try { users = await api('/api/users'); } catch (e) { return ui.toast(e.message, 'error'); }
  let backup = { enabled: false };
  try { backup = await api('/api/backup/status'); } catch (e) { /* optional */ }
  const status = u => u.disabled ? el('span', { class: 'chip bad' }, 'Disabled') : u.must_change ? el('span', { class: 'chip warn' }, 'Awaiting first sign-in') : el('span', { class: 'chip good' }, 'Active');
  const act = (u, body, okMsg) => async () => { try { await api(`/api/users/${u.id}`, { method: 'PATCH', body }); ui.toast(okMsg, 'success'); renderTeam(); } catch (x) { ui.toast(x.message, 'error'); } };
  const rows = users.map(u => el('tr', {},
    el('td', {}, el('div', { class: 'row nowrap' }, el('span', { class: 'ava-sm' }, initialOf(u.display_name)), el('div', {}, el('b', {}, u.display_name, u.id === me.id ? el('span', { class: 'chip', style: { marginLeft: '.4rem' } }, 'You') : null), el('div', { class: 'faint', style: { fontSize: '.82rem' } }, '@' + u.username)))),
    el('td', {}, el('span', { class: 'chip' + (u.role === 'admin' ? ' brand' : '') }, roleName(u.role))),
    el('td', { class: 'num' }, u.quizzes), el('td', { class: 'num' }, u.games),
    el('td', { class: 'muted' }, u.last_login ? timeAgo(u.last_login) : 'Never'), el('td', {}, status(u)),
    el('td', {}, u.id === me.id ? null : el('button', { class: 'btn sm icon-only', type: 'button', 'aria-label': `Actions for ${u.display_name}`, onclick: e => popMenu(e.currentTarget, [
      { label: 'Reset password', icon: 'refresh', run: async () => { if (await ui.confirm({ title: `Reset ${u.display_name}'s password?`, message: 'They will be signed out everywhere and must choose a new password at next sign-in.', confirmText: 'Reset password' })) { try { const r = await api(`/api/users/${u.id}/reset-password`, { method: 'POST' }); credentialsDialog({ title: 'Password reset', username: u.username, password: r.temp_password, note: `${u.display_name} has been signed out. Give them this temporary password.` }); renderTeam(); } catch (x) { ui.toast(x.message, 'error'); } } } },
      u.role === 'admin' ? { label: 'Make Quiz Host', icon: 'users', run: act(u, { role: 'host' }, 'Role changed to Quiz Host') } : { label: 'Make Super Admin', icon: 'crown', run: async () => { if (await ui.confirm({ title: `Make ${u.display_name} a Super Admin?`, message: 'They will be able to manage all hosts and see every quiz and game.', confirmText: 'Make Super Admin' })) act(u, { role: 'admin' }, 'Role changed to Super Admin')(); } },
      '-',
      u.disabled ? { label: 'Re-enable account', icon: 'check', run: act(u, { disabled: false }, 'Account re-enabled') } : { label: 'Disable account', icon: 'stop', danger: true, run: async () => { if (await ui.confirm({ title: `Disable ${u.display_name}?`, message: 'They are signed out immediately and cannot sign in. Their quizzes and results are kept.', confirmText: 'Disable', danger: true })) act(u, { disabled: true }, 'Account disabled')(); } },
    ]) }, icon('dots')))));

  mount(el('div', { class: 'page-head' }, el('div', { class: 'grow' }, el('h1', {}, 'Team'), el('div', { class: 'sub' }, 'Quiz Hosts only see their own quizzes, games and results. Super Admins see everything.')),
    el('button', { class: 'btn primary', type: 'button', onclick: addHostDialog }, icon('plus'), 'Add host')),
    backupCard(backup),
    el('div', { class: 'card', style: { padding: 0 } }, el('div', { class: 'tbl-wrap' }, el('table', { class: 'tbl' },
      el('thead', {}, el('tr', {}, ['Person', 'Role', 'Quizzes', 'Games', 'Last sign-in', 'Status', ''].map(h => el('th', {}, h)))), el('tbody', {}, rows)))));

  function addHostDialog() {
    const err = el('p', { class: 'error-text', role: 'alert' });
    const name = el('input', { class: 'input', id: 'ah-name', maxLength: 60, placeholder: 'e.g. Priya Shah', autofocus: true, 'aria-label': 'Full name' });
    const user = el('input', { class: 'input', id: 'ah-user', maxLength: 32, autocapitalize: 'none', spellcheck: false, placeholder: 'e.g. priya', 'aria-label': 'Username' });
    let edited = false;
    user.addEventListener('input', () => { edited = true; });
    name.addEventListener('input', () => { if (!edited) user.value = name.value.toLowerCase().replace(/[^a-z0-9]+/g, '.').replace(/^\.+|\.+$/g, '').slice(0, 32); });
    let role = 'host';
    const seg = el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Role' });
    const paint = () => seg.replaceChildren(...[['host', 'Quiz Host'], ['admin', 'Super Admin']].map(([v, l]) => el('button', { type: 'button', role: 'radio', 'aria-pressed': String(role === v), onclick: () => { role = v; paint(); } }, l)));
    paint();
    const m = ui.modal({ title: 'Add a host', body: el('form', { class: 'col', onsubmit: e => { e.preventDefault(); submit(); } },
      el('div', { class: 'field' }, el('label', { for: 'ah-name' }, 'Full name'), name),
      el('div', { class: 'field' }, el('label', { for: 'ah-user' }, 'Username'), user, el('span', { class: 'hint' }, 'Letters, numbers, dots, dashes. They sign in with this.')),
      el('div', { class: 'field' }, el('label', {}, 'Role'), seg, el('span', { class: 'hint' }, 'A Quiz Host only sees their own quizzes and games. A Super Admin manages the team and sees everything.')), err),
      actions: [el('button', { class: 'btn', type: 'button', onclick: () => m.close() }, 'Cancel'), el('button', { class: 'btn primary', type: 'button', onclick: submit }, 'Create account')] });
    async function submit() {
      err.textContent = '';
      try {
        const r = await api('/api/users', { method: 'POST', body: { display_name: name.value, username: user.value, role } });
        m.close(); credentialsDialog({ title: 'Account created', username: r.username, password: r.temp_password, note: 'Give these sign-in details to the new host.' }); renderTeam();
      } catch (x) { err.textContent = x.message; }
    }
  }
}

/* ---------------------------------------------------------------- covers */
const COVER_COLORS = ['#1F4FD8', '#0C8C7B', '#B7791F', '#CC3045', '#6A4CC4', '#C2570F'];
function cover(title, extra) {
  let h = 0; for (const ch of title) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const fg = COVER_COLORS[h % COVER_COLORS.length];
  const mono = title.replace(/[^\p{L}\p{N} ]/gu, '').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join('') || 'Q';
  return el('div', { class: 'cover', style: { background: `color-mix(in srgb, ${fg} 13%, var(--surface))`, color: `color-mix(in srgb, ${fg} 85%, var(--text))` } },
    el('span', { class: 'mono' }, mono), extra);
}

/* ---------------------------------------------------------------- dashboard */
const scopeStore = {
  get() { try { return sessionStorage.getItem('quickz:scope') || 'all'; } catch (e) { return 'all'; } },
  set(v) { try { sessionStorage.setItem('quickz:scope', v); } catch (e) { /* ignore */ } },
};

async function renderDashboard() {
  mount(el('div', { class: 'skeleton', style: { height: '220px' } }));
  const scope = isAdmin() ? scopeStore.get() : 'mine';
  const q = scope === 'mine' ? '?scope=mine' : '';
  let quizzes, games;
  try { [quizzes, games] = await Promise.all([api('/api/quizzes' + q), api('/api/games' + q)]); } catch (e) { return ui.toast(e.message, 'error'); }
  const live = games.filter(g => g.live);
  let filter = '';
  const grid = el('div', { class: 'quiz-grid' });

  const drawGrid = () => {
    const list = quizzes.filter(z => z.title.toLowerCase().includes(filter));
    grid.replaceChildren(...list.map(z => el('article', { class: 'qcard' },
      cover(z.title, el('div', { class: 'chips' }, z.has_buzzer ? el('span', { class: 'chip' }, icon('bell'), 'Buzzer') : null)),
      el('div', { class: 'body' }, el('h3', {}, z.title),
        el('div', { class: 'meta' }, `${z.question_count} question${z.question_count === 1 ? '' : 's'} · ~${fmtDuration(z.duration)} · ${z.plays ? `played ${z.plays}×, ${timeAgo(z.last_played)}` : 'not played yet'}`),
        isAdmin() && !z.mine ? el('div', { style: { marginTop: '.45rem' } }, el('span', { class: 'chip' }, icon('users'), z.owner_name || 'Unassigned')) : null),
      el('div', { class: 'foot' },
        el('button', { class: 'btn primary grow', type: 'button', disabled: !z.question_count, onclick: () => startDialog(z) }, icon('play'), 'Host'),
        el('a', { class: 'btn', href: `#/quiz/${z.id}` }, icon('edit'), 'Edit'),
        el('button', { class: 'btn icon-only', type: 'button', 'aria-label': `More actions for ${z.title}`, onclick: e => popMenu(e.currentTarget, [
          { label: 'Duplicate', icon: 'copy', run: async () => { try { await api(`/api/quizzes/${z.id}/duplicate`, { method: 'POST' }); ui.toast('Quiz duplicated to your library', 'success'); renderDashboard(); } catch (x) { ui.toast(x.message, 'error'); } } },
          { label: 'Export JSON', icon: 'download', run: () => { location.href = `/api/quizzes/${z.id}/export`; } }, '-',
          { label: 'Delete quiz', icon: 'trash', danger: true, run: async () => { if (await ui.confirm({ title: `Delete "${z.title}"?`, message: 'Past results for this quiz are kept.', confirmText: 'Delete', danger: true })) { try { await api('/api/quizzes/' + z.id, { method: 'DELETE' }); renderDashboard(); } catch (x) { ui.toast(x.message, 'error'); } } } },
        ]) }, icon('dots'))))));
    if (!list.length && quizzes.length) grid.replaceChildren(el('p', { class: 'muted' }, 'No quizzes match your search.'));
  };

  const search = el('div', { class: 'search' }, icon('search'), el('input', { class: 'input', type: 'search', placeholder: 'Search quizzes', 'aria-label': 'Search quizzes', oninput: e => { filter = e.target.value.toLowerCase(); drawGrid(); } }));
  const scopeSeg = isAdmin() ? el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Whose quizzes' }, [['all', 'Everyone'], ['mine', 'Mine']].map(([v, l]) =>
    el('button', { type: 'button', role: 'radio', 'aria-pressed': String(scope === v), onclick: () => { scopeStore.set(v); renderDashboard(); } }, l))) : null;
  const banners = live.map(g => el('div', { class: 'live-banner' },
    el('span', { class: 'chip live' }, el('i', { class: 'dot' }), 'Live'),
    el('div', { class: 'grow' }, el('div', { class: 't' }, g.title), el('div', { class: 's' }, `Game code ${g.code} · ${g.players} player${g.players === 1 ? '' : 's'}${isAdmin() && !g.mine ? ' · hosted by ' + g.owner_name : ''}`)),
    el('a', { class: 'btn', href: `/screen/${g.code}${g.screen_key ? '?k=' + encodeURIComponent(g.screen_key) : ''}`, target: '_blank', rel: 'noopener' }, icon('external'), 'Projector'),
    el('a', { class: 'btn success', href: `#/game/${g.code}` }, 'Resume console')));

  const sampleBtn = el('button', { class: 'btn primary lg', type: 'button', onclick: async () => {
    try { const r = await api('/api/quizzes/import', { method: 'POST', body: { text: SAMPLE_QUIZ } }); go(`#/quiz/${r.id}`); } catch (e) { ui.toast(e.message, 'error'); }
  } }, icon('sparkle'), 'Try the sample quiz');

  const empty = el('div', { class: 'empty' },
    el('div', { class: 'big' }, icon('bolt')),
    el('h2', {}, scope === 'mine' && isAdmin() ? 'You have no quizzes of your own yet' : `Welcome${me ? ', ' + me.display_name.split(' ')[0] : ''}. Let’s run your first quiz`),
    el('p', {}, 'Create a quiz, show the join code on a screen, and let everyone play from their own phone. No accounts for players, no apps.'),
    el('div', { class: 'row', style: { justifyContent: 'center' } }, sampleBtn, el('a', { class: 'btn lg', href: '#/quiz/new' }, icon('plus'), 'Create from scratch'), el('button', { class: 'btn lg', type: 'button', onclick: importDialog }, icon('upload'), 'Import')),
    el('div', { class: 'steps3' },
      el('div', { class: 'st' }, el('b', {}, '1'), el('div', {}, 'Build'), el('span', {}, 'Write questions, add images, mark the right answers.')),
      el('div', { class: 'st' }, el('b', {}, '2'), el('div', {}, 'Host'), el('span', {}, 'Open the projector view and let players scan the QR code.')),
      el('div', { class: 'st' }, el('b', {}, '3'), el('div', {}, 'Play'), el('span', {}, 'Run the game from one big button. Export results after.'))));

  const recent = games.slice(0, 5);
  mount(
    el('div', { class: 'page-head' }, el('h1', { class: 'grow' }, isAdmin() && scope === 'all' ? 'All quizzes' : 'My quizzes'),
      scopeSeg, quizzes.length ? search : null,
      el('button', { class: 'btn', type: 'button', onclick: importDialog }, icon('upload'), 'Import'),
      el('a', { class: 'btn primary', href: '#/quiz/new' }, icon('plus'), 'New quiz')),
    ...banners,
    quizzes.length ? grid : empty,
    quizzes.length && recent.length ? el('div', { class: 'section-title' }, el('h2', { class: 'grow' }, 'Recent games'), el('a', { href: '#/games' }, 'View all')) : null,
    quizzes.length && recent.length ? gamesList(recent) : null);
  drawGrid();
}

function gamesList(games) {
  return el('div', { class: 'glist' }, games.map(g => el('div', { class: 'grow-row' },
    el('span', { class: 'code' }, g.code),
    el('div', { class: 'grow' }, el('b', {}, g.title), el('div', { class: 'muted', style: { fontSize: '.85rem' } }, `${g.players} player${g.players === 1 ? '' : 's'} · ${timeAgo(g.created_at)}${isAdmin() && !g.mine ? ' · ' + g.owner_name : ''}`)),
    el('span', { class: 'chip' + (g.live ? ' live' : '') }, g.live ? el('i', { class: 'dot' }) : null, g.live ? 'Live' : g.status === 'finished' ? 'Finished' : 'Ended early'),
    g.live ? el('a', { class: 'btn sm success', href: `#/game/${g.code}` }, 'Resume') : null,
    el('a', { class: 'btn sm', href: `#/results/${g.id}` }, 'Results'))));
}

async function renderGames() {
  const scope = isAdmin() ? scopeStore.get() : 'mine';
  let games;
  try { games = await api('/api/games' + (scope === 'mine' ? '?scope=mine' : '')); } catch (e) { return ui.toast(e.message, 'error'); }
  const seg = isAdmin() ? el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Whose games' }, [['all', 'Everyone'], ['mine', 'Mine']].map(([v, l]) =>
    el('button', { type: 'button', role: 'radio', 'aria-pressed': String(scope === v), onclick: () => { scopeStore.set(v); renderGames(); } }, l))) : null;
  mount(el('div', { class: 'page-head' }, el('h1', { class: 'grow' }, 'Games'), seg),
    games.length ? gamesList(games) : el('div', { class: 'empty' }, el('h2', {}, 'No games yet'), el('p', {}, 'Host a quiz from your library and it will show up here with its results.'), el('a', { class: 'btn primary', href: '#/' }, 'Go to quizzes')));
}

function importDialog() {
  const ta = el('textarea', { class: 'textarea', rows: 12, placeholder: '# My quiz\n\nQ: Capital of France?\n- Berlin\n- Paris*\n- Rome\ntime: 20', 'aria-label': 'Quiz text or JSON' });
  const file = el('input', { type: 'file', accept: '.json,.txt,.md', 'aria-label': 'Choose a file', onchange: async e => { if (e.target.files[0]) ta.value = await e.target.files[0].text(); } });
  const err = el('p', { class: 'error-text', role: 'alert' });
  const m = ui.modal({ title: 'Import a quiz', wide: true,
    body: el('div', { class: 'col' },
      el('p', { class: 'muted' }, 'Paste text in Quickz format (a trailing * marks the correct option) or a Quickz JSON export.'),
      ta, el('div', { class: 'field' }, el('label', {}, 'Or choose a file'), file), err),
    actions: [el('button', { class: 'btn', onclick: () => m.close() }, 'Cancel'), el('button', { class: 'btn primary', onclick: async () => {
      const raw = ta.value.trim();
      try { const r = await api('/api/quizzes/import', { method: 'POST', body: raw.startsWith('{') ? { quiz: JSON.parse(raw) } : { text: raw } }); m.close(); go(`#/quiz/${r.id}`); }
      catch (e) { err.textContent = e.message; }
    } }, 'Import quiz')] });
}

/* ---------------------------------------------------------------- start game dialog */
const PRESETS = [
  { id: 'party', title: 'Party', desc: 'Speed bonus and instant reveal. Great for pub quizzes and socials.', s: { speed_bonus: true, auto_reveal: true, ask_email: false } },
  { id: 'class', title: 'Classroom', desc: 'No speed pressure. You decide when to reveal each answer.', s: { speed_bonus: false, auto_reveal: false, ask_email: false } },
  { id: 'corp', title: 'Corporate', desc: 'Asks for work emails so you can export results.', s: { speed_bonus: true, auto_reveal: true, ask_email: true } },
];
function startDialog(quiz) {
  let preset = 'party';
  const opts = { ...PRESETS[0].s };
  const presetBox = el('div', { class: 'presets', role: 'radiogroup', 'aria-label': 'Game style' });
  const toggles = el('div');
  const draw = () => {
    presetBox.replaceChildren(...PRESETS.map(p => el('button', { type: 'button', class: 'preset', role: 'radio', 'aria-pressed': String(p.id === preset), onclick: () => { preset = p.id; Object.assign(opts, p.s); draw(); } },
      el('b', {}, p.title), el('span', {}, p.desc))));
    const box = (key, t, d) => el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: opts[key], onchange: e => { opts[key] = e.target.checked; preset = ''; presetBox.querySelectorAll('.preset').forEach(b => b.setAttribute('aria-pressed', 'false')); } }), el('div', {}, el('div', { class: 't' }, t), el('div', { class: 'd' }, d)));
    toggles.replaceChildren(box('speed_bonus', 'Speed bonus', 'Honour each question’s speed weighting (set in the editor). Turn off for flat points.'), box('auto_reveal', 'Reveal automatically', 'Show the answer as soon as everyone has answered.'), box('ask_email', 'Ask for email', 'Players must enter an email to join (for reporting).'));
  };
  draw();
  const m = ui.modal({ title: `Host "${quiz.title}"`, body: el('div', {}, el('p', { class: 'muted', style: { marginBottom: '1rem' } }, 'Choose a style. You can fine-tune the details below.'), presetBox, el('div', { class: 'label', style: { margin: '.4rem 0 0' } }, 'Details'), toggles),
    actions: [el('button', { class: 'btn', onclick: () => m.close() }, 'Cancel'), el('button', { class: 'btn primary', onclick: async () => {
      try { const g = await api('/api/games', { method: 'POST', body: { quiz_id: quiz.id, settings: opts } }); m.close(); go(`#/game/${g.code}`); } catch (e) { ui.toast(e.message, 'error'); }
    } }, icon('play'), 'Open lobby')] });
}

/* ---------------------------------------------------------------- results */
async function renderResults(gameId) {
  mount(el('div', { class: 'skeleton', style: { height: '300px' } }));
  let data;
  try { data = await api(`/api/games/${gameId}/results`); } catch (e) { ui.toast(e.message, 'error'); return go('#/'); }
  const lb = data.leaderboard, an = data.analytics;
  const answeredQs = an.filter(a => a.answered);
  const totalAns = an.reduce((s, a) => s + a.answered, 0), totalOk = an.reduce((s, a) => s + a.correct, 0);
  const hardest = answeredQs.filter(a => a.pct_correct != null).sort((a, b) => a.pct_correct - b.pct_correct)[0];
  const avg = lb.length ? Math.round(lb.reduce((s, r) => s + r.score, 0) / lb.length) : 0;
  const when = new Date(data.game.created_at * 1000).toLocaleString();
  const top = lb.slice(0, 3);

  const podium = top.length ? el('div', { class: 'mini-podium' }, [1, 0, 2].filter(i => top[i]).map(i => el('div', { class: `mp p${i + 1}`, style: { cursor: 'pointer' }, tabindex: 0, role: 'button', 'aria-label': `Show ${top[i].nickname}'s answers`, onclick: () => openPlayerDetail(gameId, top[i]), onkeydown: e => { if (e.key === 'Enter') openPlayerDetail(gameId, top[i]); } },
    el('div', { class: 'av' }, top[i].avatar), el('div', { class: 'nm' }, top[i].nickname), el('div', { class: 'pt' }, fmtScore(top[i].score) + ' pts'), el('div', { class: 'bl' }, i + 1)))) : null;
  const diff = p => (p == null ? '' : p >= 70 ? 'diff-easy' : p >= 40 ? 'diff-med' : 'diff-hard');

  mount(
    el('a', { class: 'crumb', href: '#/games' }, icon('left'), 'All games'),
    el('div', { class: 'page-head', style: { marginTop: '.4rem' } }, el('div', { class: 'grow' }, el('h1', {}, data.game.title), el('div', { class: 'sub' }, `Game ${data.game.code} · ${when}`)),
      data.game.quiz_id ? el('button', { class: 'btn', type: 'button', onclick: () => startDialog({ id: data.game.quiz_id, title: data.game.title }) }, icon('refresh'), 'Play again') : null,
      el('a', { class: 'btn primary', href: `/api/games/${gameId}/results.csv` }, icon('download'), 'Download CSV')),
    el('div', { class: 'stat-cards' },
      el('div', { class: 'card stat-card' }, el('small', {}, 'Players'), el('b', { class: 'num' }, lb.length)),
      el('div', { class: 'card stat-card' }, el('small', {}, 'Average score'), el('b', { class: 'num' }, fmtScore(avg))),
      el('div', { class: 'card stat-card' }, el('small', {}, 'Overall accuracy'), el('b', { class: 'num' }, totalAns ? Math.round(100 * totalOk / totalAns) + '%' : '–')),
      el('div', { class: 'card stat-card' }, el('small', {}, 'Toughest question'), el('b', { class: 'num' }, hardest ? hardest.pct_correct + '%' : '–'), el('span', {}, hardest ? `Q${hardest.index + 1} got it right` : 'No data yet'))),
    el('div', { class: 'res-grid' },
      el('section', { class: 'card' }, el('h2', { style: { fontSize: '1.15rem', marginBottom: '.2rem' } }, 'Leaderboard'), el('p', { class: 'muted', style: { fontSize: '.85rem' } }, 'Click a player to see every answer, the time it was given and how the points were earned.'), podium,
        el('div', { class: 'tbl-wrap', style: { marginTop: '1rem' } }, lb.length ? el('table', { class: 'tbl' },
          el('thead', {}, el('tr', {}, ['#', 'Player', 'Email', 'Score', 'Correct', ''].map(h => el('th', {}, h)))),
          el('tbody', {}, lb.map(r => el('tr', { class: 'clickable', tabindex: 0, role: 'button', 'aria-label': `Show ${r.nickname}'s answers`, onclick: () => openPlayerDetail(gameId, r), onkeydown: e => { if (e.key === 'Enter') openPlayerDetail(gameId, r); } }, el('td', { class: 'num' }, r.rank), el('td', {}, `${r.avatar || ''} ${r.nickname}`, r.is_bot ? el('span', { class: 'chip', style: { marginLeft: '.4rem' } }, 'bot') : null),
            el('td', { class: 'muted' }, r.email || '–'), el('td', { class: 'num' }, fmtScore(r.score)), el('td', { class: 'num' }, `${r.correct}/${r.answered}`), el('td', { class: 'faint' }, icon('right')))))) : el('p', { class: 'muted' }, 'No players joined this game.'))),
      el('section', { class: 'card' }, el('h2', { style: { fontSize: '1.15rem', marginBottom: '.4rem' } }, 'Question breakdown'), el('p', { class: 'muted', style: { fontSize: '.85rem', marginBottom: '.6rem' } }, 'Share of players who answered correctly.'),
        an.map(a => el('div', { class: 'arow ' + diff(a.pct_correct) }, el('span', { class: 'faint num' }, a.index + 1), el('span', { class: 'q', title: plain(a.text) }, plain(a.text)),
          el('div', { class: 'bar' }, el('i', { style: { width: (a.pct_correct || 0) + '%' } })), el('b', { class: 'num' }, a.pct_correct == null ? '–' : a.pct_correct + '%'))))));
}

/* ---------------------------------------------------------------- one player's report */
async function openPlayerDetail(gameId, p) {
  const body = el('div', { class: 'skeleton', style: { height: '260px' } });
  const m = ui.modal({ title: `${p.avatar || ''} ${p.nickname}`, wide: true, body });
  try {
    const d = await api(`/api/games/${gameId}/players/${p.id}`);
    m.el.querySelector('.modal-body').replaceChildren(playerReport(d));
  } catch (e) { m.close(); ui.toast(e.message, 'error'); }
}

function playerReport(d) {
  const s = d.summary, pl = d.player;
  const tile = (label, value, sub) => el('div', { class: 'pd-tile' }, el('small', {}, label), el('b', { class: 'num' }, value), sub ? el('span', {}, sub) : null);
  const head = el('div', { class: 'pd-head' }, el('span', { class: 'avatar lg' }, pl.avatar),
    el('div', { class: 'grow' }, el('h3', {}, pl.nickname, pl.is_bot ? el('span', { class: 'chip', style: { marginLeft: '.5rem' } }, 'bot') : null),
      el('div', { class: 'muted' }, `${pl.email || 'No email given'} · ${ordinal(s.rank)} of ${s.players}`)),
    el('div', { class: 'pd-score' }, el('b', { class: 'num' }, fmtScore(s.score)), el('span', {}, 'points')));
  const tiles = el('div', { class: 'pd-tiles' },
    tile('Correct', `${s.correct}/${s.asked}`, s.accuracy == null ? '' : `${s.accuracy}% accuracy`), tile('Answered', `${s.answered}/${s.asked}`),
    tile('Average time', s.avg_correct_ms == null ? '–' : fmtSecs(s.avg_correct_ms), 'on correct answers'), tile('Fastest', s.fastest_ms == null ? '–' : fmtSecs(s.fastest_ms)));
  const line = (label, value, strong) => el('div', { class: 'pd-line' + (strong ? ' strong' : '') }, el('span', {}, label), el('b', { class: 'num' }, value));
  const totals = el('div', { class: 'pd-total' }, line('Points from answers', fmtScore(s.earned)),
    s.adjustment ? line('Host adjustment', (s.adjustment > 0 ? '+' : '') + fmtScore(s.adjustment)) : null, line('Final score', fmtScore(s.score), true));
  return el('div', { class: 'col', style: { gap: '1rem' } }, head, tiles, d.questions.length ? el('div', { class: 'col', style: { gap: '.8rem' } }, d.questions.map(reportCard)) : el('p', { class: 'muted' }, 'No questions were played.'), totals);
}

function reportCard(r) {
  let kind, status;
  if (r.not_in_round) { kind = 'none'; status = 'Not part of this round'; }
  else if (!r.answered) { kind = 'none'; status = r.winners ? 'No answer (or the places were taken)' : 'No answer'; }
  else if (r.buzzed) { kind = r.correct ? 'ok' : 'bad'; status = r.correct ? 'Buzzed in · judged correct' : 'Buzzed in · judged wrong'; }
  else { kind = r.correct ? 'ok' : 'bad'; status = r.correct ? 'Correct' : 'Wrong'; }
  const pct = r.max_points ? Math.round(100 * r.earned / r.max_points) : 0;
  const facts = [
    r.answered && !r.buzzed && r.elapsed_ms != null ? el('span', { class: 'chip' }, icon('clock'), `${fmtSecs(r.elapsed_ms)} after the timer started`) : null,
    r.submitted_at ? el('span', { class: 'chip' }, icon('eye'), `answered at ${fmtTimeOfDay(r.submitted_at)}`) : null,
    r.correct && r.winners === 1 ? el('span', { class: 'chip good' }, icon('trophy'), 'First correct answer: 100% of the points') : null,
    r.correct && r.winners > 1 && r.claim_rank ? el('span', { class: 'chip good' }, icon('trophy'), `Place ${r.claim_rank} of ${r.winners} scoring answers`) : null,
    r.correct && !r.winners && r.speed_weight > 0 && r.type !== 'buzzer' ? el('span', { class: 'chip' }, icon('gauge'), `Speed weighting ${r.speed_weight}%`) : null,
    r.tie_breaker ? el('span', { class: 'chip warn' }, icon('bolt'), 'Tie-breaker') : null,
  ];
  const opts = r.options.length ? el('div', { class: 'pd-opts' }, r.options.map(o => el('div', { class: `pd-opt a${o.index % 6}` + (o.chosen ? ' chosen' : '') + (o.correct ? ' right' : '') },
    el('span', { class: 'shapebox', 'aria-hidden': 'true' }, SHAPES[o.index % 6]), el('span', { class: 't' }, richInline(o.text)),
    o.chosen ? el('span', { class: 'tag' }, 'Their answer') : null, o.correct ? el('span', { class: 'ok', 'aria-label': 'correct answer' }, icon('check')) : null))) : null;
  return el('article', { class: 'pd-q ' + kind },
    el('div', { class: 'pd-qhead' }, el('span', { class: 'pd-n' }, r.index + 1), el('span', { class: 'chip ' + (kind === 'ok' ? 'good' : kind === 'bad' ? 'bad' : '') }, status), el('span', { class: 'spacer' }),
      el('div', { class: 'pd-pts' }, el('b', { class: 'num' }, `${r.earned ? '+' : ''}${fmtScore(r.earned)}`), el('span', {}, `of ${fmtScore(r.max_points)}${r.answered && r.earned ? ` · ${pct}%` : ''}`))),
    richNode(r.text, 'pd-qtext'), opts,
    el('div', { class: 'pd-foot' }, el('div', { class: 'row', style: { gap: '.4rem' } }, facts), el('span', { class: 'muted num' }, `Score so far ${fmtScore(r.running)}`)));
}

/* ---------------------------------------------------------------- live console */
async function renderLive(code) {
  mount(el('div', { class: 'skeleton', style: { height: '320px' } }));
  let joinUrl = '';
  try { joinUrl = (await api('/api/game/' + encodeURIComponent(code))).join_url; }
  catch (e) { ui.toast('That game is no longer running.', 'error'); return go('#/'); }

  let last = null, tab = 'board', stageKey = null, stagePatch = null, ring = null, lastHolder = null, tabScroll = 0;
  const hostText = joinUrl.replace(/^https?:\/\//, '');
  const stage = el('section', { class: 'card stage-card', 'aria-live': 'polite' });
  const side = el('aside', { class: 'card side-card' });
  const deck = el('div', { class: 'deck', role: 'toolbar', 'aria-label': 'Game controls' });
  const top = el('div', { class: 'live-top' });
  mount(top, el('div', { class: 'live-grid' }, stage, side), deck);
  deck.addEventListener('click', () => document.activeElement && document.activeElement.blur());

  const conn = new Conn(`/ws/host/${encodeURIComponent(code)}`, {
    onState: v => { last = v; draw(v); },
    onStatus: ok => { $('#net').hidden = ok || !conn.everOpen; },
    onFatal: code => { if (code === 4403) { ui.toast('That game belongs to another host.', 'error'); go('#/'); } else window.dispatchEvent(new Event('quickz:unauth')); },
    onError: msg => ui.toast(msg, 'error'),
  });
  const stopCountdown = runCountdown(() => (last && last.phase === 'question' ? last.q : null), () => conn.serverNow(), (ms, frac) => { if (ring) { ring.set(frac, ms); ring.pause(last && last.q && last.q.remaining_ms != null); } const t = $('#deck-time'); if (t) t.textContent = Math.ceil(ms / 1000) + 's left'; });
  const send = o => conn.send(o);

  const keyHandler = e => {
    if (!last || e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.querySelector('dialog[open], .drawer') || /^(INPUT|TEXTAREA|SELECT|BUTTON|A)$/.test(e.target.tagName)) return;
    if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); primary(); }
    else if ((e.key === 'e' || e.key === 'E') && last.phase === 'question') send({ t: 'end_question' });
  };
  addEventListener('keydown', keyHandler);
  onLeave(() => { conn.close(); stopCountdown(); removeEventListener('keydown', keyHandler); });

  function primary() {
    const v = last; if (!v) return;
    if (v.phase === 'lobby' && !v.outline.length) return;
    if (v.phase === 'lobby' || v.phase === 'reveal') send({ t: 'next' });
    else if (v.phase === 'question' && v.q.awaiting_media) send({ t: 'start_timer' });
    else if (v.phase === 'question' && !(v.buzz && v.buzz.holder)) send({ t: 'end_question' });
    else if (v.phase === 'finished') go(`#/results/${v.game_id}`);
  }

  /* ---- pieces */
  const shapeBox = i => el('span', { class: 'shapebox', 'aria-hidden': 'true' }, SHAPES[i % 6]);
  const pchip = p => el('span', { class: 'pchip' + (p.connected ? '' : ' off'), title: p.connected ? '' : 'Disconnected' }, el('span', { class: 'avatar' }, p.avatar), p.nickname,
    el('button', { class: 'x', type: 'button', 'aria-label': `Remove ${p.nickname}`, onclick: () => kick(p) }, icon('x')));
  async function kick(p) { if (await ui.confirm({ title: `Remove ${p.nickname}?`, message: 'They will be disconnected and can’t rejoin this game.', confirmText: 'Remove', danger: true })) send({ t: 'kick', player_id: p.id }); }
  async function adjust(p) {
    const val = await ui.prompt({ title: `Adjust score for ${p.nickname}`, label: 'Points to add (use a minus sign to remove)', value: '100', type: 'number', confirmText: 'Apply', chips: ['+100', '+500', '-100', '-500'] });
    const n = Number(String(val).replace('+', ''));
    if (val !== null && Number.isFinite(n) && n !== 0) send({ t: 'adjust_score', player_id: p.id, delta: n });
  }

  function qHead(v, withRing) {
    const q = v.q;
    ring = withRing ? makeClock(72) : null;
    const kind = q.type === 'mcq' && isTF({ type: 'mcq', options: q.options }) ? 'tf' : q.type;
    return el('div', { class: 'stage-head' }, el('span', { class: 'chip brand' }, `Question ${q.index + 1} of ${q.total}`), el('span', { class: 'chip' }, TYPE_META[kind].title),
      el('span', { class: 'chip' }, icon('bolt'), `${fmtScore(q.points)} pts`),
      q.only_players ? el('span', { class: 'chip warn' }, icon('bolt'), 'Tie-breaker: ' + q.only_players.map(p => p.nickname).join(' vs ')) : null,
      q.winners ? el('span', { class: 'chip warn' }, icon('trophy'), winnersText(q)) : null,
      q.speed ? el('span', { class: 'chip', title: 'How much of the points depend on answering quickly' }, icon('gauge'), `Speed ${q.speed}%`) : null,
      q.media ? el('span', { class: 'chip' }, icon(q.media.kind === 'video' ? 'film' : 'music'), q.media.kind === 'video' ? 'Video' : 'Audio') : null, ring ? ring.el : null);
  }

  function lobbyStage(v) {
    const grid = el('div', { class: 'pgrid' });
    const count = el('h2', {}, '');
    const empty = el('p', { class: 'muted' }, 'Waiting for players. Share the code or QR on the projector.');
    const patch = nv => {
      count.textContent = `${nv.players} player${nv.players === 1 ? '' : 's'} joined`;
      grid.replaceChildren(...nv.players_list.map(pchip));
      empty.hidden = nv.players > 0;
    };
    const s = v.settings;
    const settings = el('div', { class: 'row', style: { gap: '.4rem' } },
      el('span', { class: 'chip' }, icon('bolt'), s.speed_bonus ? 'Speed bonus on' : 'No speed bonus'),
      el('span', { class: 'chip' }, icon('eye'), s.auto_reveal ? 'Auto reveal' : 'Manual reveal'),
      s.ask_email ? el('span', { class: 'chip' }, 'Collecting emails') : null,
      el('button', { class: 'btn ghost sm', type: 'button', onclick: settingsDialog }, icon('edit'), 'Change'));
    const root = [
      el('div', { class: 'lobby-grid' },
        el('div', { class: 'joinbox' }, el('div', { class: 'qr' }, el('img', { src: `/qr/${encodeURIComponent(v.code)}.svg`, alt: `QR code for game ${v.code}` })), el('div', { class: 'big-code' }, v.code), el('div', { class: 'addr' }, hostText)),
        el('div', { class: 'col' }, count, empty, grid, el('hr', { class: 'divider', style: { margin: '.4rem 0' } }), settings))];
    patch(v);
    return { nodes: root, patch };
  }

  function mediaPanel(v) {
    const q = v.q, m = q.media;
    if (!m) return null;
    const where = m.where === 'screen' ? 'plays on the projector' : m.where === 'players' ? 'plays on players’ phones' : 'plays on the projector and phones';
    return el('div', { class: 'card flat col', style: { gap: '.7rem' } },
      el('div', { class: 'row nowrap' }, el('span', { class: 'chip' }, icon(m.kind === 'video' ? 'film' : 'music'), `${m.kind === 'video' ? 'Video' : 'Audio'} · ${where}`), el('span', { class: 'spacer' }),
        el('button', { class: 'btn sm', type: 'button', onclick: () => send({ t: 'replay_media' }) }, icon('refresh'), 'Replay on screens')),
      q.awaiting_media ? el('div', { class: 'await-banner' }, icon('clock'),
        el('div', { class: 'grow' }, el('b', {}, 'Media is playing'), el('div', { class: 'muted', style: { fontSize: '.88rem' } }, 'Players can’t answer yet. The timer starts when the clip ends on the projector.')),
        el('button', { class: 'btn primary', type: 'button', onclick: () => send({ t: 'start_timer' }) }, icon('clock'), 'Start timer now')) : null,
      m.url ? mediaPlayer(m, { controls: true }) : null);
  }

  function questionStage(v) {
    const q = v.q, correct = new Set(v.q_correct || []);
    const patchers = [];
    let body = [];
    if (q.type === 'buzzer') {
      const slot = el('div');
      patchers.push(nv => {
        const b = nv.buzz;
        slot.replaceChildren(b && b.holder ? el('div', { class: 'buzzpanel' }, el('span', { class: 'av' }, b.holder_avatar || ''),
          el('div', { class: 'grow' }, el('div', { class: 'lbl' }, 'Buzzed in first'), el('div', { class: 'who' }, b.holder_name), (b.order || []).length > 1 ? el('div', { class: 'faint', style: { fontSize: '.85rem' } }, 'Then: ' + b.order.slice(1).join(', ')) : null),
          el('button', { class: 'btn success lg', type: 'button', onclick: () => send({ t: 'resolve_buzz', correct: true }) }, icon('check'), `Correct +${fmtScore(q.points)}`),
          el('button', { class: 'btn danger lg', type: 'button', onclick: () => send({ t: 'resolve_buzz', correct: false }) }, icon('x'), 'Wrong, reopen'))
          : el('div', { class: 'waitbuzz' }, icon('bell', 'bell'), 'Waiting for someone to buzz in…'));
      });
      body = [slot];
    } else {
      const rows = q.options.map((o, i) => {
        const fill = el('i', { class: 'fill' }), n = el('span', { class: 'n num' }, '0');
        const row = el('div', { class: `drow a${i % 6} ${correct.has(o.id) ? 'right' : ''}` }, fill, shapeBox(i), el('span', { class: 't' }, richInline(o.text)), el('span', { class: 'ok' }, icon('check')), n);
        return { o, row, fill, n };
      });
      const bar = el('i'), label = el('span', { class: 'num' });
      patchers.push(nv => {
        const d = nv.live_dist || {}; const max = Math.max(1, ...Object.values(d));
        rows.forEach(r => { const c = d[r.o.id] || 0; r.n.textContent = c; r.fill.style.width = (100 * c / max) + '%'; });
        const pct = nv.eligible ? Math.min(100, 100 * nv.answered / nv.eligible) : 0;
        bar.style.width = pct + '%';
        label.textContent = `${nv.answered} of ${nv.eligible} answered` + (nv.q.winners ? ` · ${nv.q.spots_left} winning place${nv.q.spots_left === 1 ? '' : 's'} left` : '');
      });
      body = [el('div', { class: 'dist', 'aria-label': 'Live answers' }, rows.map(r => r.row)), el('div', { class: 'prog' }, el('div', { class: 'track' }, bar), label),
        el('p', { class: 'hint' }, 'The green tick marks the correct answer. Players cannot see it until you reveal.')];
    }
    const nodes = [qHead(v, true), richNode(q.text, 'q-big'), q.image ? el('img', { class: 'q-thumb', src: q.image, alt: '' }) : null, mediaPanel(v), ...body];
    const patch = nv => patchers.forEach(p => p(nv));
    patch(v);
    return { nodes, patch };
  }

  function revealStage(v) {
    const q = v.q, r = v.reveal, correct = new Set(r.correct);
    const max = Math.max(1, ...Object.values(r.distribution));
    const next = v.outline[v.index + 1];
    let body;
    if (q.type === 'buzzer') body = el('div', { class: 'waitbuzz' }, r.buzz_winner ? `${r.buzz_winner} got it right.` : 'Nobody got it this time.');
    else body = el('div', { class: 'dist' }, q.options.map((o, i) => {
      const c = r.distribution[o.id] || 0, ok = correct.has(o.id);
      const fill = el('i', { class: 'fill' });
      requestAnimationFrame(() => requestAnimationFrame(() => { fill.style.width = (100 * c / max) + '%'; }));
      return el('div', { class: `drow a${i % 6} ${ok ? 'right' : 'dim'}` }, fill, shapeBox(i), el('span', { class: 't' }, richInline(o.text)), el('span', { class: 'ok' }, icon('check')), el('span', { class: 'n num' }, c));
    }));
    return { nodes: [qHead(v, false), richNode(q.text, 'q-big'), body,
      el('div', { class: 'facts' }, q.type !== 'buzzer' ? el('span', { class: 'chip good' }, icon('check'), `${r.correct_count} of ${r.answered} correct`) : null,
        r.fastest ? el('span', { class: 'chip' }, icon('bolt'), `Fastest: ${r.fastest.avatar} ${r.fastest.nickname} · ${(r.fastest.ms / 1000).toFixed(1)}s`) : null),
      tieAlert(v),
      claimedList(r, ''),
      r.explanation ? el('div', { class: 'next-up' }, el('small', {}, 'Explanation'), r.explanation) : null,
      el('div', { class: 'next-up' }, el('small', {}, next ? 'Up next' : 'That was the last question'), next ? `Q${v.index + 2}: ${plain(next.text)}` : 'Show the final results when you are ready.')] };
  }

  function finishedStage(v) {
    const top3 = v.leaderboard.slice(0, 3);
    return { nodes: [el('div', { class: 'center', style: { padding: '1rem 0' } }, el('h2', { style: { fontSize: '1.8rem' } }, 'Game finished'), el('p', { class: 'muted', style: { marginTop: '.3rem' } }, `${v.players} player${v.players === 1 ? '' : 's'} took part.`)),
      top3.length ? el('div', { class: 'mini-podium' }, [1, 0, 2].filter(i => top3[i]).map(i => el('div', { class: `mp p${i + 1}` }, el('div', { class: 'av' }, top3[i].avatar), el('div', { class: 'nm' }, top3[i].nickname), el('div', { class: 'pt' }, fmtScore(top3[i].score) + ' pts'), el('div', { class: 'bl' }, i + 1)))) : null,
      tieAlert(v),
      el('div', { class: 'row', style: { justifyContent: 'center', marginTop: '.6rem' } }, el('a', { class: 'btn primary lg', href: `#/results/${v.game_id}` }, 'View results & export'), el('button', { class: 'btn lg', type: 'button', onclick: () => openQuickAdd() }, icon('plus'), 'Add a tie-breaker or question'), el('a', { class: 'btn lg', href: '#/' }, 'Back to library'))] };
  }

  function drawStage(v) {
    const key = v.phase === 'lobby' ? 'lobby' : v.phase === 'question' ? `q:${v.q.id}:${(v.buzz && v.buzz.holder) || 0}:${(v.q.remaining_ms == null) ? 'run' : 'hold'}` : v.phase === 'reveal' ? `r:${v.q.id}` : 'f';
    if (key === stageKey && stagePatch) { stagePatch(v); return; }
    stageKey = key;
    const built = v.phase === 'lobby' ? lobbyStage(v) : v.phase === 'question' ? questionStage(v) : v.phase === 'reveal' ? revealStage(v) : finishedStage(v);
    stagePatch = built.patch || null;
    stage.replaceChildren(...built.nodes.filter(Boolean));
  }

  function drawTop(v) {
    const dots = el('div', { class: 'stepper', role: 'img', 'aria-label': `Question ${Math.max(v.index + 1, 0)} of ${v.outline.length}` },
      v.outline.map((_, i) => el('i', { class: i < v.index || v.phase === 'finished' || (i === v.index && v.phase === 'reveal') ? 'done' : i === v.index ? 'cur' : '' })));
    top.replaceChildren(
      el('div', { class: 'code-chip' }, el('small', {}, 'Game code'), el('b', {}, v.code)),
      el('span', { class: 'chip' }, icon('users'), `${v.players} player${v.players === 1 ? '' : 's'}`),
      dots, el('span', { class: 'spacer' }),
      el('button', { class: 'btn', type: 'button', onclick: () => shareDialog(v) }, icon('qr'), 'Share join info'),
      el('a', { class: 'btn', href: `/screen/${v.code}?k=${encodeURIComponent(v.screen_key || '')}`, target: '_blank', rel: 'noopener' }, icon('external'), 'Open projector'));
  }

  function drawSide(v) {
    const scroller = side.querySelector('.tabpane'); if (scroller) tabScroll = scroller.scrollTop;
    const tabs = [['board', 'Leaderboard'], ['players', `Players (${v.players})`], ['questions', 'Questions']];
    const av = p => el('span', { class: 'avatar', style: { width: '1.9rem', height: '1.9rem', fontSize: '1.05rem' } }, p.avatar);
    let pane;
    if (tab === 'board') pane = v.leaderboard.length ? v.leaderboard.map(p => el('div', { class: 'lb-row' }, el('span', { class: 'rk' }, p.rank), av(p), el('span', { class: 'nm' }, p.nickname), p.delta ? delta(p.delta) : null, el('span', { class: 'sc num' }, fmtScore(p.score)))) : [el('p', { class: 'muted', style: { padding: '1rem' } }, 'Scores will appear here once the game starts.')];
    else if (tab === 'players') pane = v.players_list.length ? v.players_list.map(p => el('div', { class: 'lb-row' + (p.connected ? '' : ' off') }, el('span', { class: 'rk' }, p.rank), av(p),
      el('span', { class: 'nm' }, p.nickname, p.is_bot ? el('span', { class: 'chip', style: { marginLeft: '.35rem', padding: '0 .4em' } }, 'bot') : null, p.streak >= 2 ? el('span', { class: 'chip warn', style: { marginLeft: '.35rem', padding: '0 .45em' } }, icon('flame'), p.streak) : null),
      el('span', { class: 'sc num' }, fmtScore(p.score)),
      el('span', { class: 'acts' }, el('button', { class: 'btn sm icon-only', type: 'button', title: 'Adjust score', 'aria-label': `Adjust score for ${p.nickname}`, onclick: () => adjust(p) }, icon('edit')), el('button', { class: 'btn sm icon-only', type: 'button', title: 'Remove player', 'aria-label': `Remove ${p.nickname}`, onclick: () => kick(p) }, icon('x'))))) : [el('p', { class: 'muted', style: { padding: '1rem' } }, 'Nobody has joined yet.')];
    else pane = [el('ol', { class: 'q-out' }, v.outline.map((q, i) => el('li', { class: (i === v.index && v.phase !== 'finished' ? 'cur' : '') + (i < v.index || v.phase === 'finished' ? ' done' : '') }, el('span', { class: 'n' }, i + 1), el('span', {}, (q.tie ? 'Tie-breaker: ' : q.type === 'buzzer' ? 'Buzzer: ' : '') + plain(q.text)))))];
    side.replaceChildren(el('div', { class: 'tabs', role: 'tablist' }, tabs.map(([k, label]) => el('button', { type: 'button', role: 'tab', 'aria-selected': String(tab === k), onclick: () => { tab = k; tabScroll = 0; drawSide(last); } }, label))),
      el('div', { class: 'tabpane', role: 'tabpanel' }, pane));
    const np = side.querySelector('.tabpane'); if (np) np.scrollTop = tabScroll;
  }

  function drawDeck(v) {
    const isLast = v.index + 1 >= v.outline.length;
    const more = el('button', { class: 'btn icon-only', type: 'button', 'aria-label': 'More actions', title: 'More actions', onclick: e => popMenu(e.currentTarget, [
      { label: 'Add a question now', icon: 'plus', run: () => openQuickAdd() },
      { label: 'Share join info', icon: 'qr', run: () => shareDialog(last) },
      v.phase === 'lobby' ? { label: 'Add 10 test bots', icon: 'users', run: () => send({ t: 'add_bots', n: 10 }) } : null,
      { label: Sound.on ? 'Mute console sounds' : 'Unmute console sounds', icon: Sound.on ? 'mute' : 'volume', run: () => Sound.setOn(!Sound.on) },
      v.phase !== 'finished' ? '-' : null,
      v.phase !== 'finished' ? { label: 'End game now', icon: 'stop', danger: true, run: async () => { if (await ui.confirm({ title: 'End the game now?', message: 'Players will see the final standings.', confirmText: 'End game', danger: true })) send({ t: 'finish' }); } } : null,
    ].filter(Boolean), { up: true }) }, icon('dots'));
    let status, sub, secondary = [], act;
    if (v.phase === 'lobby') {
      status = `${v.players} player${v.players === 1 ? '' : 's'} ready`; sub = v.players ? 'Start whenever everyone has joined.' : 'Show the QR code on the projector so people can join.';
      act = el('button', { class: 'btn primary xl primary-act', type: 'button', disabled: !v.outline.length, onclick: primary }, icon('play'), 'Start quiz', el('span', { class: 'kbd' }, 'Space'));
    } else if (v.phase === 'question') {
      const holder = v.buzz && v.buzz.holder;
      status = v.q.awaiting_media ? 'Media is playing' : v.q.type === 'buzzer' ? (holder ? `${v.buzz.holder_name} buzzed in` : 'Waiting for a buzz') : `${v.answered} of ${v.eligible} answered`;
      sub = v.q.awaiting_media ? 'The timer starts when the clip ends' : holder ? 'Judge their answer above.' : el('span', { id: 'deck-time' }, '');
      secondary = [el('button', { class: 'btn', type: 'button', onclick: () => send({ t: 'add_time', seconds: 10 }) }, icon('clock'), '+10 s')];
      act = el('button', { class: 'btn primary xl primary-act', type: 'button', disabled: !!holder, onclick: primary }, v.q.awaiting_media ? icon('clock') : icon('eye'), v.q.awaiting_media ? 'Start timer now' : 'Reveal answer', el('span', { class: 'kbd' }, 'Space'));
    } else if (v.phase === 'reveal') {
      status = isLast ? 'That was the last question' : `Question ${v.index + 1} complete`; sub = isLast ? 'Time for the final results.' : `Next: Q${v.index + 2}`;
      act = el('button', { class: 'btn primary xl primary-act', type: 'button', onclick: primary }, isLast ? icon('trophy') : icon('right'), isLast ? 'Show final results' : 'Next question', el('span', { class: 'kbd' }, 'Space'));
    } else {
      status = 'Game finished'; sub = 'Results are saved and ready to export.';
      act = el('a', { class: 'btn primary xl primary-act', href: `#/results/${v.game_id}` }, 'View results');
    }
    deck.replaceChildren(el('div', { class: 'status' }, status, el('small', {}, sub)), ...secondary, v.phase !== 'finished' ? el('button', { class: 'btn quick', type: 'button', onclick: () => openQuickAdd() }, icon('plus'), 'Add question') : null, more, act);
  }

  function draw(v) {
    if (v.buzz && v.buzz.holder && v.buzz.holder !== lastHolder) Sound.buzz();
    lastHolder = v.buzz ? v.buzz.holder : null;
    drawTop(v); drawStage(v); drawSide(v); drawDeck(v);
  }

  /* ---- dialogs + drawer */
  function shareDialog(v) {
    ui.modal({ title: 'Join this game', body: el('div', { class: 'joinbox' },
      el('div', { class: 'qr' }, el('img', { src: `/qr/${encodeURIComponent(v.code)}.svg`, alt: `QR code for game ${v.code}`, style: { maxWidth: '280px' } })),
      el('div', { class: 'big-code' }, v.code), el('div', { class: 'addr' }, `${hostText} (same Wi-Fi as this computer)`),
      el('div', { class: 'row', style: { justifyContent: 'center', marginTop: '1rem' } },
        el('button', { class: 'btn', type: 'button', onclick: () => { (navigator.clipboard ? navigator.clipboard.writeText(`${joinUrl}/j/${v.code}`) : Promise.reject()).then(() => ui.toast('Join link copied', 'success'), () => ui.toast(`${joinUrl}/j/${v.code}`)); } }, icon('link'), 'Copy join link'))) });
  }
  function settingsDialog() {
    const v = last;
    const box = (key, t, d, disabled) => el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: v.settings[key], disabled, onchange: e => send({ t: 'set', key, value: e.target.checked }) }), el('div', {}, el('div', { class: 't' }, t), el('div', { class: 'd' }, d)));
    ui.modal({ title: 'Game settings', body: el('div', {}, box('speed_bonus', 'Speed bonus', 'Honour each question’s speed weighting (set in the editor). Turn off for flat points.'), box('auto_reveal', 'Reveal automatically', 'Show the answer as soon as everyone has answered.'), box('ask_email', 'Ask for email', 'Set when the game is created and can’t change now.', true)) });
  }
  /** Add a question while the game runs. `arg` may be a tie group ({players, rank, score}) to start a tie-breaker. */
  function openQuickAdd(arg) {
    if (document.querySelector('.drawer')) return;
    const tie = arg && arg.players ? arg : null;
    const roster = (last && last.players_list) || [];
    const q = newQuestion('mcq');
    if (tie) { q.winners = 1; q.time_limit = 20; q.points = 1000; q.speed_weight = 0; q.options = q.options.slice(0, 2); q.correct = [q.options[0].id]; }   // a quick two-way decider   // a race: first correct answer takes all
    const st = { where: 'next', everyone: !tie, ids: new Set(tie ? tie.players.map(p => p.id) : []), save: false };
    const formBox = el('div'), optBox = el('div', { class: 'col', style: { gap: '1.1rem', marginTop: '1.2rem' } });
    const insert = el('button', { class: 'btn primary', type: 'button' }, icon('plus'), tie ? 'Start the tie-breaker' : 'Add question');
    const closeDrawer = () => { drawer.remove(); scrim.remove(); removeEventListener('keydown', esc); };
    const check = () => { insert.disabled = questionProblems(q).length > 0 || (!st.everyone && st.ids.size === 0); };

    const drawOptions = () => {
      const seg = (items, get, set) => el('div', { class: 'segmented', role: 'radiogroup' }, items.map(([v, l]) => el('button', { type: 'button', role: 'radio', 'aria-pressed': String(get() === v), onclick: () => { set(v); drawOptions(); check(); } }, l)));
      const players = el('div', { class: 'pick-players' }, roster.map(p => {
        const cb = el('input', { type: 'checkbox', checked: st.ids.has(p.id), onchange: e => { if (e.target.checked) st.ids.add(p.id); else st.ids.delete(p.id); check(); } });
        return el('label', { class: 'pick' + (tie && tie.players.some(t => t.id === p.id) ? ' tied' : '') }, cb, el('span', { class: 'avatar' }, p.avatar), el('span', { class: 'truncate' }, p.nickname), el('span', { class: 'faint num' }, fmtScore(p.score)));
      }));
      optBox.replaceChildren(
        el('div', { class: 'field' }, el('label', {}, 'When'), seg([['next', 'Play it next'], ['end', 'Add to the end']], () => st.where, v => { st.where = v; }),
          el('span', { class: 'hint' }, st.where === 'next' ? (last && last.phase === 'finished' ? 'The game reopens and this becomes the next question.' : 'It is played right after the current question.') : 'It is played after all the other questions.')),
        el('div', { class: 'field' }, el('label', {}, 'Who can answer'),
          seg([['all', 'Everyone'], ['some', 'Only selected players']], () => (st.everyone ? 'all' : 'some'), v => { st.everyone = v === 'all'; }),
          st.everyone ? null : el('div', { class: 'col', style: { gap: '.5rem', marginTop: '.4rem' } },
            tie ? el('span', { class: 'hint' }, `The ${tie.players.length} players tied for ${ordinal(tie.rank)} place are selected. Everyone else watches.`) : el('span', { class: 'hint' }, 'Everyone else sees the question but cannot answer, which makes it ideal for a tie-breaker.'),
            players)),
        el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: st.save, onchange: e => { st.save = e.target.checked; } }),
          el('div', {}, el('div', { class: 't' }, 'Also add it to the quiz'), el('div', { class: 'd' }, 'Keeps the question in your library for next time. Leave off for a one-off tie-breaker.'))));
    };

    const redraw = cb => { formBox.replaceChildren(questionForm(q, { change: check, rebuild: redraw })); check(); if (typeof cb === 'function') setTimeout(cb, 0); };
    insert.addEventListener('click', () => {
      const { _seen, ...clean } = q;
      send({ t: 'add_question', question: clean, only: st.everyone ? undefined : [...st.ids], where: st.where, save_to_quiz: st.save });
      ui.toast(tie ? 'Tie-breaker added. Press the big button to play it.' : (st.where === 'next' ? 'Added as the next question' : 'Added to the end'), 'success');
      closeDrawer();
    });
    const drawer = el('div', { class: 'drawer wide-drawer', role: 'dialog', 'aria-label': tie ? 'Tie-breaker' : 'Add a question' },
      el('div', { class: 'dh' }, el('h2', {}, tie ? 'Break the tie' : 'Add a question now'), el('button', { class: 'btn ghost sm icon-only', type: 'button', 'aria-label': 'Close', onclick: closeDrawer }, icon('x'))),
      el('div', { class: 'db' },
        tie ? el('div', { class: 'tie-alert', style: { marginBottom: '1rem' } }, icon('trophy'), el('div', {}, el('b', {}, `${tie.players.map(p => p.avatar + ' ' + p.nickname).join(' vs ')}`), el('div', { class: 'muted', style: { fontSize: '.88rem' } }, `tied for ${ordinal(tie.rank)} place on ${fmtScore(tie.score)} points`)))
          : el('p', { class: 'muted', style: { marginBottom: '1rem', fontSize: '.9rem' } }, 'Write a question and it joins the game immediately. Perfect for a tie-breaker or a surprise bonus round.'),
        formBox, optBox),
      el('div', { class: 'df' }, el('button', { class: 'btn', type: 'button', onclick: closeDrawer }, 'Cancel'), insert));
    const scrim = el('div', { class: 'drawer-scrim', onclick: closeDrawer });
    const esc = e => { if (e.key === 'Escape') closeDrawer(); };
    addEventListener('keydown', esc);
    document.body.append(scrim, drawer);
    redraw(() => $('#q-text')?.focus()); drawOptions();
  }

  function tieAlert(v) {
    const groups = v.tie_groups || [];
    if (!groups.length) return null;
    return el('div', { class: 'col', style: { gap: '.6rem' } }, groups.map(g => el('div', { class: 'tie-alert' }, icon('trophy'),
      el('div', { class: 'grow' }, el('b', {}, `Tie for ${ordinal(g.rank)} place`),
        el('div', { class: 'muted', style: { fontSize: '.88rem' } }, `${g.players.map(p => p.avatar + ' ' + p.nickname).join(', ')} · ${fmtScore(g.score)} pts each`)),
      el('button', { class: 'btn primary', type: 'button', onclick: () => openQuickAdd(g) }, icon('bolt'), 'Add a tie-breaker'))));
  }
}

boot();
