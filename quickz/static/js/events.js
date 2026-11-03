'use strict';
/* Host console: events. An event has its own permanent link and QR code that can be published weeks ahead; people register on it,
   and when the host opens the lobby the same page lets them straight in. (Loaded before host.js; uses its mount/go/onLeave/scopeStore.) */

const EVENT_STATES = { open: ['Registration open', ''], closed: ['Registration closed', ''], live: ['Lobby open', 'live'], running: ['Quiz running', 'live'], finished: ['Finished', ''] };
const eventChip = s => el('span', { class: 'chip' + (EVENT_STATES[s][1] ? ' ' + EVENT_STATES[s][1] : '') }, EVENT_STATES[s][1] ? el('i', { class: 'dot' }) : null, EVENT_STATES[s][0]);

function fmtEventTime(ts, tz) {
  try { return new Intl.DateTimeFormat(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: tz, timeZoneName: 'short' }).format(new Date(ts * 1000)); }
  catch (e) { return new Date(ts * 1000).toLocaleString(); }
}
const localInput = ts => { const d = new Date(ts * 1000), p = n => String(n).padStart(2, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
const copyText = (text, ok) => (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(() => ui.toast(ok, 'success'), () => ui.toast('Could not copy. Select the text instead.', 'error'));

/* ---------------------------------------------------------------- list */
async function renderEvents() {
  const scope = isAdmin() ? scopeStore.get() : 'mine';
  let events;
  try { events = await api('/api/events' + (scope === 'mine' ? '?scope=mine' : '')); } catch (e) { return ui.toast(e.message, 'error'); }
  const seg = isAdmin() ? el('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Whose events' }, [['all', 'Everyone'], ['mine', 'Mine']].map(([v, l]) =>
    el('button', { type: 'button', role: 'radio', 'aria-pressed': String(scope === v), onclick: () => { scopeStore.set(v); renderEvents(); } }, l))) : null;
  const rows = events.map(e => el('a', { class: 'grow-row ev-row', href: `#/event/${e.id}` },
    el('div', { class: 'grow' }, el('b', {}, e.title),
      el('div', { class: 'muted', style: { fontSize: '.85rem' } }, `${fmtEventTime(e.starts_at, e.tz)} · ${e.registered} registered${isAdmin() && !e.mine ? ' · ' + e.owner_name : ''}`)),
    eventChip(e.state), icon('right')));
  mount(el('div', { class: 'page-head' }, el('h1', { class: 'grow' }, 'Events'), seg,
      el('button', { class: 'btn primary', type: 'button', onclick: () => eventDialog(null) }, icon('plus'), 'New event')),
    events.length ? el('div', { class: 'glist' }, rows) : el('div', { class: 'empty' }, el('div', { class: 'big' }, icon('clock')),
      el('h2', {}, 'Publish a quiz night ahead of time'),
      el('p', {}, 'An event gets its own link and QR code you can share days or weeks before. People register once with their real name, email, a PIN and an avatar. On the day, the same link lets them straight in when you open the lobby.'),
      el('button', { class: 'btn primary lg', type: 'button', onclick: () => eventDialog(null) }, icon('plus'), 'Create your first event')));
}

/* ---------------------------------------------------------------- create / edit */
async function eventDialog(ev, onSaved) {
  let quizzes = [];
  try { quizzes = await api('/api/quizzes?scope=mine'); } catch (e) { /* the quiz can be chosen later */ }
  const tomorrow = Date.now() / 1000 + 86400 - ((Date.now() / 1000 + 86400) % 3600) + 3600;
  const title = el('input', { class: 'input', maxLength: 120, value: ev ? ev.title : '', placeholder: 'e.g. Spring Quiz Night', 'aria-label': 'Title' });
  const desc = el('textarea', { class: 'textarea', rows: 3, maxLength: 1000, placeholder: 'Where, who it is for, prizes… (optional)', 'aria-label': 'Description' }, ev ? ev.description : '');
  const when = el('input', { class: 'input', type: 'datetime-local', value: localInput(ev ? ev.starts_at : tomorrow), 'aria-label': 'Start' });
  const dur = el('input', { class: 'input', type: 'number', min: 5, max: 720, value: ev ? ev.duration_min : 60, 'aria-label': 'Length in minutes' });
  const cap = el('input', { class: 'input', type: 'number', min: 1, max: 5000, value: ev ? ev.capacity : 1000, 'aria-label': 'Capacity' });
  const quiz = el('select', { class: 'select', 'aria-label': 'Quiz' }, el('option', { value: '' }, 'Choose later'),
    quizzes.map(q => el('option', { value: q.id, selected: ev && ev.quiz_id === q.id }, q.title)));
  const err = el('p', { class: 'error-text', role: 'alert' });
  const field = (label, node, hint) => el('div', { class: 'field' }, el('label', {}, label), node, hint ? el('span', { class: 'tiny faint' }, hint) : null);
  const m = ui.modal({ title: ev ? 'Edit event' : 'New event', body: el('div', { class: 'col' },
      field('Title', title), field('Description', desc),
      field('Starts', when, `Your time zone: ${Intl.DateTimeFormat().resolvedOptions().timeZone}. Everyone sees it in their own time as well.`),
      el('div', { class: 'row2' }, field('Length (minutes)', dur), field('Capacity', cap, 'Registrations allowed')),
      field('Quiz', quiz, 'You can pick or change it any time before you open the lobby.'), err),
    actions: [el('button', { class: 'btn', type: 'button', onclick: () => m.close() }, 'Cancel'), el('button', { class: 'btn primary', type: 'button', onclick: async () => {
      const startsAt = new Date(when.value).getTime() / 1000;
      if (!title.value.trim()) { err.textContent = 'Give the event a title.'; return; }
      if (!Number.isFinite(startsAt)) { err.textContent = 'Choose when the quiz starts.'; return; }
      const body = { title: title.value, description: desc.value, starts_at: startsAt, duration_min: Number(dur.value) || 60, capacity: Number(cap.value) || 1000,
        tz: Intl.DateTimeFormat().resolvedOptions().timeZone, quiz_id: quiz.value ? Number(quiz.value) : null };
      try {
        const saved = await api(ev ? `/api/events/${ev.id}` : '/api/events', { method: ev ? 'PATCH' : 'POST', body });
        m.close();
        if (onSaved) onSaved(saved); else go(`#/event/${saved.id}`);
      } catch (e) { err.textContent = e.message; }
    } }, ev ? 'Save' : 'Create event')] });
}

/* ---------------------------------------------------------------- poster */
function printPoster(ev) {
  const w = window.open('', '_blank');
  if (!w) return ui.toast('Allow pop-ups to print the poster, or use Download QR.', 'error');
  const h = escapeHtml;
  w.document.write(`<!doctype html><title>${h(ev.title)}</title><style>
    body{font-family:system-ui,sans-serif;text-align:center;margin:0;padding:40px 24px;color:#111}
    h1{font-size:44px;margin:0 0 8px}.when{font-size:24px;font-weight:600;margin:0 0 6px}.d{font-size:18px;color:#444;margin:0 auto 18px;max-width:640px;white-space:pre-line}
    img{width:420px;max-width:80vw;margin:10px 0}.go{font-size:28px;font-weight:700;margin:8px 0}.u{font:18px monospace;color:#333;word-break:break-all}.f{margin-top:28px;color:#666;font-size:14px}
    </style><h1>${h(ev.title)}</h1><p class="when">${h(fmtEventTime(ev.starts_at, ev.tz))}</p>${ev.description ? `<p class="d">${h(ev.description)}</p>` : ''}
    <img src="${location.origin}/qr/event/${encodeURIComponent(ev.slug)}.svg" alt="QR code"><p class="go">Scan to register</p><p class="u">${h(ev.url)}</p>
    <p class="f">Register with your real name and email. Pick a PIN. On the day, use the same link or QR code to join.</p>
    <script>addEventListener('load',()=>setTimeout(()=>print(),400))<\/script>`);
  w.document.close();
}

/* ---------------------------------------------------------------- detail */
async function renderEvent(id) {
  mount(el('div', { class: 'skeleton', style: { height: '300px' } }));
  let ev;
  try { ev = await api(`/api/events/${id}`); } catch (e) { ui.toast(e.message, 'error'); return go('#/events'); }
  let filter = '', topKey = '';
  const top = el('div'), regs = el('div');
  const reload = async () => { ev = await api(`/api/events/${id}`); drawTop(true); drawRegs(); };
  const act = fn => async (...args) => { try { await fn(...args); } catch (e) { ui.toast(e.message, 'error'); } };

  const openLobby = act(async () => { const r = await api(`/api/events/${id}/open-lobby`, { method: 'POST', body: {} }); go(`#/game/${r.code}`); });
  const share = () => el('div', { class: 'card ev-card' }, el('h3', {}, 'Share this'),
    el('p', { class: 'muted' }, 'Publish this link or QR code as early as you like. It stays exactly the same on the day.'),
    el('div', { class: 'ev-link' }, el('input', { class: 'input', readOnly: true, value: ev.url, 'aria-label': 'Event link', onfocus: e => e.target.select() }),
      el('button', { class: 'btn', type: 'button', onclick: () => copyText(ev.url, 'Link copied') }, icon('copy'), 'Copy')),
    el('div', { class: 'ev-qr' }, el('img', { src: `/qr/event/${encodeURIComponent(ev.slug)}.svg`, alt: `QR code for ${ev.title}`, width: 180, height: 180 }),
      el('div', { class: 'col' },
        el('a', { class: 'btn', href: `/qr/event/${encodeURIComponent(ev.slug)}.svg`, download: `${ev.slug}-qr.svg` }, icon('download'), 'Download QR'),
        el('button', { class: 'btn', type: 'button', onclick: () => printPoster(ev) }, icon('qr'), 'Print poster'),
        el('a', { class: 'btn', href: `/e/${encodeURIComponent(ev.slug)}`, target: '_blank', rel: 'noopener' }, icon('external'), 'Open the page'))));
  const control = () => {
    const running = ev.state === 'live' || ev.state === 'running';
    return el('div', { class: 'card ev-card' }, el('h3', {}, 'The quiz'),
      el('p', {}, el('b', {}, fmtEventTime(ev.starts_at, ev.tz)), el('br'), el('span', { class: 'muted' }, `${ev.duration_min} min · ${ev.quiz_title ? 'Quiz: ' + ev.quiz_title : 'No quiz chosen yet'}`)),
      running ? el('a', { class: 'btn success lg block', href: `#/game/${ev.game_code}` }, icon('play'), ev.state === 'live' ? 'Go to the lobby' : 'Go to the live game')
        : el('button', { class: 'btn primary lg block', type: 'button', disabled: !ev.quiz_id, onclick: openLobby }, icon('play'), ev.state === 'finished' ? 'Open another lobby' : 'Open the lobby'),
      !running && !ev.quiz_id ? el('p', { class: 'tiny faint' }, 'Choose a quiz with Edit before you open the lobby.') : null,
      !running ? el('p', { class: 'tiny faint' }, 'Opening the lobby lets everyone who is on the event page in automatically. Walk-ins can still register until you press Start.') : null,
      el('label', { class: 'check' }, el('input', { type: 'checkbox', checked: ev.reg_open, disabled: ev.state === 'running' || ev.state === 'finished',
        onchange: act(async e => { await api(`/api/events/${id}`, { method: 'PATCH', body: { reg_open: e.target.checked } }); await reload(); }) }),
        el('div', {}, el('div', { class: 't' }, 'Registration open'), el('div', { class: 'd' }, `Up to ${ev.capacity} people. New registrations stop automatically when the quiz starts.`))));
  };
  const drawTop = force => {
    const key = JSON.stringify([ev.state, ev.reg_open, ev.quiz_id, ev.game_code, ev.title, ev.starts_at, ev.capacity, ev.description]);
    if (!force && key === topKey) return;
    topKey = key;
    top.replaceChildren(el('div', { class: 'page-head' },
      el('a', { class: 'crumb', href: '#/events' }, icon('left'), 'All events'),
      el('h1', { class: 'grow' }, ev.title), eventChip(ev.state),
      el('button', { class: 'btn', type: 'button', onclick: () => eventDialog(ev, saved => { ev = { ...ev, ...saved }; reload(); }) }, icon('edit'), 'Edit'),
      el('button', { class: 'btn danger', type: 'button', onclick: act(async () => {
        if (!await ui.confirm({ title: 'Delete this event?', message: `This deletes the event page and all ${ev.registered} registrations (names and emails). Games already played keep their own results.`, confirmText: 'Delete event', danger: true })) return;
        await api(`/api/events/${id}`, { method: 'DELETE' }); ui.toast('Event deleted', 'success'); go('#/events');
      }) }, icon('trash'), 'Delete')),
      el('div', { class: 'ev-grid' }, share(), control()));
  };

  const search = el('input', { class: 'input', type: 'search', placeholder: 'Search name or email', 'aria-label': 'Search registrations', oninput: e => { filter = e.target.value.trim().toLowerCase(); drawRegs(); } });
  const drawRegs = () => {
    const rows = ev.registrations.filter(r => !filter || r.name.toLowerCase().includes(filter) || r.email.includes(filter));
    const hasGame = !!ev.game_code;
    regs.replaceChildren(el('div', { class: 'section-title' }, el('h2', { class: 'grow' }, `Registered: ${ev.registered}`, hasGame ? el('span', { class: 'muted' }, ` · ${ev.arrived} here`) : null),
        search,
        el('button', { class: 'btn', type: 'button', disabled: !ev.registered, onclick: () => copyText(ev.registrations.map(r => r.email).join(', '), `${ev.registered} emails copied (paste into your own email app)`) }, icon('copy'), 'Copy emails'),
        el('a', { class: 'btn', href: `/api/events/${id}/registrations.csv` }, icon('download'), 'CSV')),
      rows.length ? el('div', { class: 'glist' }, rows.map(r => el('div', { class: 'grow-row reg-row' },
        el('span', { class: 'reg-av', 'aria-hidden': 'true' }, r.avatar),
        el('div', { class: 'grow' }, el('b', {}, r.name), el('div', { class: 'muted', style: { fontSize: '.85rem' } }, `${r.email} · registered ${timeAgo(r.created_at)}`)),
        hasGame ? el('span', { class: 'chip' + (r.arrived ? ' live' : '') }, r.arrived ? 'Here' : 'Not yet') : null,
        el('button', { class: 'btn sm', type: 'button', title: 'Give them a new PIN if they forgot theirs', onclick: act(async () => {
          if (!await ui.confirm({ title: `New PIN for ${r.name}?`, message: 'Their old PIN stops working. You will see the new one once, to tell them.', confirmText: 'Make a new PIN' })) return;
          const p = await api(`/api/events/${id}/registrations/${r.id}/reset-pin`, { method: 'POST' });
          const m = ui.modal({ title: `New PIN for ${r.name}`, body: el('div', { style: { textAlign: 'center' } }, el('p', { class: 'muted' }, 'Tell them this PIN. They sign in with their email and this PIN.'), el('div', { class: 'big-pin' }, p.pin)),
            actions: [el('button', { class: 'btn primary', type: 'button', onclick: () => m.close() }, 'Done')] });
        }) }, icon('key'), 'PIN'),
        el('button', { class: 'btn sm danger', type: 'button', 'aria-label': `Remove ${r.name}`, onclick: act(async () => {
          if (!await ui.confirm({ title: `Remove ${r.name}?`, message: 'Their registration and details are deleted. They can register again while registration is open.', confirmText: 'Remove', danger: true })) return;
          await api(`/api/events/${id}/registrations/${r.id}`, { method: 'DELETE' }); await reload();
        }) }, icon('trash')))))
        : el('div', { class: 'empty' }, el('p', {}, ev.registered ? 'No one matches that search.' : 'No one has registered yet. Share the link or QR code above.')));
  };

  mount(top, regs);
  drawTop(true); drawRegs();
  const timer = setInterval(async () => {                          // new sign-ups and arrivals appear without a refresh
    if (document.hidden || document.querySelector('dialog[open]')) return;
    try { ev = await api(`/api/events/${id}`); drawTop(false); drawRegs(); } catch (e) { /* offline for a moment */ }
  }, 10000);
  onLeave(() => clearInterval(timer));
}
