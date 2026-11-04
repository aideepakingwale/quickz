'use strict';
/* Super Admin: platform settings and data clean-up.
   - Limits, upload sizes and security settings that apply the moment they are saved.
   - A clean-up list of every host's quizzes, with bulk delete.
   - Removal of uploaded pictures and music that nothing uses any more.
   The server checks everything again; nothing here is trusted. */

const fmtBytes = b => (b >= 1073741824 ? (b / 1073741824).toFixed(1) + ' GB' : b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : b >= 1024 ? Math.round(b / 1024) + ' KB' : b + ' B');

async function renderAdminSettings() {
  mount(el('div', { class: 'skeleton', style: { height: '320px' } }));
  let conf, storage, quizzes;
  try { [conf, storage, quizzes] = await Promise.all([api('/api/admin/settings'), api('/api/admin/storage'), api('/api/admin/quizzes')]); } catch (e) { return ui.toast(e.message, 'error'); }
  const DAY = 86400;
  const f = { text: '', owner: '', idle: '', edited: '', sort: 'edited_old' };
  const picked = new Set();
  const inputs = new Map();
  const cleanup = el('div', { class: 'card' }), filesBox = el('div', { class: 'card' }), formBox = el('div', {}), statsBox = el('div', { class: 'ai-stats' }), changesBox = el('div', { class: 'card' });

  /* ------------------------------------------------------------ at a glance */
  function stat(label, value, sub) { return el('div', { class: 'card flat ai-stat' }, el('div', { class: 'faint' }, label), el('div', { class: 'big' }, value), sub ? el('div', { class: 'faint' }, sub) : null); }
  function drawStats() {
    const c = storage.counts, u = storage.uploads;
    statsBox.replaceChildren(
      stat('Quizzes', String(c.quizzes), `${c.users} account${c.users === 1 ? '' : 's'}`),
      stat('Games played', String(c.games), `${c.players.toLocaleString()} players recorded`),
      stat('Database', fmtBytes(storage.db_bytes), `${c.answers.toLocaleString()} answers stored`),
      stat('Uploaded files', fmtBytes(u.bytes), `${u.files} file${u.files === 1 ? '' : 's'}${u.unused_files ? ` · ${u.unused_files} unused` : ''}`));
  }

  /* ------------------------------------------------------------ the settings form */
  function drawForm() {
    inputs.clear();
    const dirty = () => [...inputs].some(([k, i]) => String(i.value) !== String(conf.settings.find(s => s.key === k).value));
    const save = el('button', { class: 'btn primary', type: 'button', disabled: true, onclick: async () => {
      const body = {};
      inputs.forEach((i, k) => { if (String(i.value) !== String(conf.settings.find(s => s.key === k).value)) body[k] = i.value; });
      try { conf = await api('/api/admin/settings', { method: 'PUT', body }); applyLimits(); ui.toast('Settings saved. They apply now.', 'success'); drawForm(); drawChanges(); } catch (e) { ui.toast(e.message, 'error'); }
    } }, 'Save changes');
    const row = s => {
      const input = el('input', { class: 'input', type: 'number', min: s.min, max: s.max, value: s.value, id: 'conf-' + s.key, 'aria-describedby': 'conf-h-' + s.key, style: { maxWidth: '8rem' },
        oninput: () => { save.disabled = !dirty(); input.classList.toggle('changed', String(input.value) !== String(s.value)); } });
      inputs.set(s.key, input);
      return el('div', { class: 'conf-row' },
        el('div', { class: 'conf-text' }, el('label', { for: 'conf-' + s.key }, s.label, s.changed ? el('span', { class: 'chip warn', style: { marginLeft: '.5rem' } }, 'Changed') : null),
          el('div', { class: 'faint', id: 'conf-h-' + s.key, style: { fontSize: '.85rem' } }, s.help, ` Allowed: ${s.min} to ${s.max}. Default: ${s.default}.`)),
        el('div', { class: 'row nowrap', style: { gap: '.5rem' } }, input, el('span', { class: 'muted' }, s.unit),
          s.changed ? el('button', { class: 'btn ghost sm', type: 'button', title: `Back to ${s.default}`, onclick: async () => {
            try { conf = await api('/api/admin/settings/reset', { method: 'POST', body: { keys: [s.key] } }); applyLimits(); ui.toast('Back to the default', 'success'); drawForm(); drawChanges(); } catch (e) { ui.toast(e.message, 'error'); }
          } }, 'Reset') : null));
    };
    formBox.replaceChildren(el('div', { class: 'card' },
      ...conf.groups.map(g => el('section', { class: 'conf-group' }, el('h3', {}, g.label), conf.settings.filter(s => s.group === g.id).map(row))),
      el('div', { class: 'row', style: { justifyContent: 'space-between', marginTop: '1rem' } }, el('span', { class: 'faint' }, 'Changes apply straight away and are kept when the server restarts.'),
        el('div', { class: 'row', style: { gap: '.5rem' } },
          conf.settings.some(s => s.changed) ? el('button', { class: 'btn ghost', type: 'button', onclick: async () => {
            if (!(await ui.confirm({ title: 'Reset every setting?', message: 'All settings on this page go back to the values this server started with.', confirmText: 'Reset all' }))) return;
            try { conf = await api('/api/admin/settings/reset', { method: 'POST', body: { keys: 'all' } }); applyLimits(); ui.toast('All settings are back to the defaults', 'success'); drawForm(); drawChanges(); } catch (e) { ui.toast(e.message, 'error'); }
          } }, 'Reset all to defaults') : null, save))));
  }
  const applyLimits = () => {                                               // this page's own browser uses the new picture and music sizes at once
    const v = k => conf.settings.find(s => s.key === k).value;
    window.QUICKZ_MAX_IMAGE_MB = v('max_image_mb'); window.QUICKZ_MAX_MUSIC_MB = v('max_music_mb'); window.QUICKZ_MAX_MEDIA_MB = v('max_media_mb');
  };

  function drawChanges() {
    changesBox.replaceChildren(el('h3', { style: { margin: 0 } }, 'Recent changes'),
      conf.changes.length ? el('ul', { class: 'conf-changes' }, conf.changes.map(c => el('li', {}, el('b', {}, c.username || 'Someone'), ' ', c.message, ' ', el('span', { class: 'faint' }, timeAgo(c.ts)))))
        : el('p', { class: 'muted', style: { margin: '.4rem 0 0' } }, 'Nothing has been changed yet.'),
      el('div', { class: 'faint', style: { fontSize: '.82rem', marginTop: '.4rem' } }, 'Every settings change and every clean-up is also in the audit log on the AI page, under “Admin settings” and “Cleanup”.'));
  }

  /* ------------------------------------------------------------ clean up quizzes */
  const owners = () => [...new Map(quizzes.map(q => [q.owner_id, q.owner_name || 'Unassigned'])).entries()];
  function shown() {
    const now = Date.now() / 1000;
    let list = quizzes.filter(q => (!f.text || q.title.toLowerCase().includes(f.text.toLowerCase())) && (!f.owner || String(q.owner_id) === f.owner)
      && (!f.edited || now - q.updated_at > Number(f.edited) * DAY)
      && (!f.idle || (f.idle === 'never' ? q.plays === 0 : (q.plays === 0 ? now - q.created_at > Number(f.idle) * DAY : now - q.last_played > Number(f.idle) * DAY))));
    const by = { edited_old: (a, b) => a.updated_at - b.updated_at, edited_new: (a, b) => b.updated_at - a.updated_at, plays: (a, b) => b.plays - a.plays, size: (a, b) => b.bytes - a.bytes,
      idle: (a, b) => (a.last_played || a.created_at) - (b.last_played || b.created_at) };
    return list.sort(by[f.sort]);
  }
  function drawCleanup() {
    const list = shown();
    const sel = (label, key, items) => el('select', { class: 'select', 'aria-label': label, style: { maxWidth: '14rem' }, onchange: e => { f[key] = e.target.value; drawCleanup(); } },
      items.map(([v, l]) => el('option', { value: v, selected: f[key] === v }, l)));
    const rowRefs = new Map();
    const delBtn = el('button', { class: 'btn danger', type: 'button', onclick: () => deleteChosen(quizzes.filter(q => picked.has(q.id))) });
    const counter = el('span', { class: 'faint' });
    const master = el('input', { type: 'checkbox', 'aria-label': 'Select every quiz shown', onchange: e => { list.forEach(q => { e.target.checked ? picked.add(q.id) : picked.delete(q.id); }); sync(); } });
    function sync() {                                           // a tick only updates the counts, so the page keeps its place and keyboard focus
      const n = list.filter(q => picked.has(q.id)).length, all = quizzes.filter(q => picked.has(q.id)).length;
      rowRefs.forEach(({ tr, cb }, id) => { cb.checked = picked.has(id); tr.classList.toggle('picked', cb.checked); });
      master.checked = !!list.length && n === list.length; master.indeterminate = n > 0 && n < list.length;
      delBtn.disabled = !all; delBtn.replaceChildren(icon('trash'), all ? `Delete ${all} selected` : 'Delete selected');
      counter.textContent = `${list.length} of ${quizzes.length} shown · ${all} selected`;
    }
    cleanup.replaceChildren(
      el('div', { class: 'row nowrap' }, el('div', { class: 'grow' }, el('h3', { style: { margin: 0 } }, 'Clean up quizzes'),
        el('div', { class: 'faint' }, 'Every host’s quizzes in one list. Filter to the old ones, tick them and delete. Games already played keep their results.'))),
      el('div', { class: 'row', style: { gap: '.5rem', flexWrap: 'wrap', margin: '.8rem 0' } },
        el('input', { class: 'input', placeholder: 'Search by title', value: f.text, 'aria-label': 'Search quizzes', style: { maxWidth: '14rem' }, oninput: e => { f.text = e.target.value; drawCleanupKeepFocus(); } }),
        sel('Host', 'owner', [['', 'All hosts'], ...owners().map(([id, n]) => [String(id), n])]),
        sel('Played', 'idle', [['', 'Played any time'], ['never', 'Never played'], ['30', 'Not played for 30 days'], ['90', 'Not played for 90 days'], ['180', 'Not played for 6 months'], ['365', 'Not played for a year']]),
        sel('Edited', 'edited', [['', 'Edited any time'], ['30', 'Not edited for 30 days'], ['90', 'Not edited for 90 days'], ['180', 'Not edited for 6 months'], ['365', 'Not edited for a year']]),
        sel('Order', 'sort', [['edited_old', 'Oldest edit first'], ['edited_new', 'Newest edit first'], ['idle', 'Longest unplayed first'], ['plays', 'Most played first'], ['size', 'Largest first']])),
      list.length ? el('div', { class: 'tbl-wrap' }, el('table', { class: 'tbl' },
        el('thead', {}, el('tr', {}, el('th', {}, master),
          ['Quiz', 'Host', 'Questions', 'Played', 'Last edited', 'Size'].map(h => el('th', {}, h)))),
        el('tbody', {}, list.map(q => { const cb = el('input', { type: 'checkbox', 'aria-label': `Select ${q.title}`, onchange: e => { e.target.checked ? picked.add(q.id) : picked.delete(q.id); sync(); } });
          const tr = el('tr', {},
          el('td', {}, cb),
          el('td', {}, el('b', {}, q.title), q.events ? el('span', { class: 'chip warn', style: { marginLeft: '.4rem' }, title: 'An event uses this quiz' }, `${q.events} event${q.events > 1 ? 's' : ''}`) : null),
          el('td', { class: 'muted' }, q.owner_name || 'Unassigned'), el('td', { class: 'num' }, q.question_count),
          el('td', { class: 'muted' }, q.plays ? `${q.plays}× · ${timeAgo(q.last_played)}` : 'Never'), el('td', { class: 'muted' }, timeAgo(q.updated_at)), el('td', { class: 'num muted' }, fmtBytes(q.bytes)));
          rowRefs.set(q.id, { tr, cb }); return tr; })))) :
        el('p', { class: 'muted' }, quizzes.length ? 'No quiz matches these filters.' : 'There are no quizzes.'),
      el('div', { class: 'row', style: { justifyContent: 'space-between', marginTop: '.8rem' } }, counter, delBtn));
    sync();
  }
  function drawCleanupKeepFocus() {                                           // typing in the search box must not lose the cursor
    const pos = document.activeElement && document.activeElement.selectionStart;
    drawCleanup();
    const box = cleanup.querySelector('input[type=text], input:not([type])'); if (box) { box.focus(); try { box.setSelectionRange(pos, pos); } catch (x) { /* fine */ } }
  }
  async function deleteChosen(chosen) {
    const events = chosen.reduce((n, q) => n + q.events, 0), plays = chosen.reduce((n, q) => n + q.plays, 0);
    const names = chosen.slice(0, 5).map(q => '“' + q.title + '”').join(', ') + (chosen.length > 5 ? ` and ${chosen.length - 5} more` : '');
    const ok = await ui.confirm({ title: `Delete ${chosen.length} quiz${chosen.length === 1 ? '' : 'zes'}?`, danger: true, confirmText: 'Delete for good',
      message: `${names}. This cannot be undone.${plays ? ` They have been played ${plays} time${plays === 1 ? '' : 's'}; those games keep their results.` : ''}${events ? ` ${events} event${events === 1 ? ' uses' : 's use'} one of these quizzes and will be left without a quiz.` : ''}` });
    if (!ok) return;
    try {
      const r = await api('/api/admin/quizzes/delete', { method: 'POST', body: { ids: chosen.map(q => q.id) } });
      const gone = new Set(chosen.map(q => q.id)); quizzes = quizzes.filter(q => !gone.has(q.id)); picked.clear(); storage = r.storage;
      ui.toast(`Deleted ${r.deleted} quiz${r.deleted === 1 ? '' : 'zes'}`, 'success');
      drawCleanup(); drawFiles(); drawStats(); conf = await api('/api/admin/settings'); drawChanges();
    } catch (e) { ui.toast(e.message, 'error'); }
  }

  /* ------------------------------------------------------------ unused files */
  function drawFiles() {
    const u = storage.uploads;
    filesBox.replaceChildren(el('div', { class: 'row nowrap' }, el('div', { class: 'grow' }, el('h3', { style: { margin: 0 } }, 'Unused uploaded files'),
      el('div', { class: 'faint' }, 'Deleting quizzes leaves their pictures and music behind. This removes files that no quiz, game or event uses. Files uploaded in the last 24 hours are never touched, in case someone is still editing.')),
      el('button', { class: 'btn danger', type: 'button', disabled: !u.unused_files, onclick: async () => {
        if (!(await ui.confirm({ title: `Delete ${u.unused_files} unused file${u.unused_files === 1 ? '' : 's'}?`, danger: true, confirmText: 'Delete files', message: `This frees ${fmtBytes(u.unused_bytes)}. Nothing that is still in use is touched. A copy in the backup bucket, if you use one, is not removed.` }))) return;
        try { const r = await api('/api/admin/uploads/cleanup', { method: 'POST', body: { dry_run: false } }); storage = r.storage; ui.toast(`Deleted ${r.deleted} file${r.deleted === 1 ? '' : 's'}, freed ${fmtBytes(r.freed_bytes)}`, 'success'); drawFiles(); drawStats(); conf = await api('/api/admin/settings'); drawChanges(); }
        catch (e) { ui.toast(e.message, 'error'); }
      } }, icon('trash'), u.unused_files ? `Delete ${u.unused_files} unused (${fmtBytes(u.unused_bytes)})` : 'Nothing to delete')),
      el('div', { class: 'faint', style: { marginTop: '.4rem' } }, `${u.files} file${u.files === 1 ? '' : 's'} stored, ${fmtBytes(u.bytes)} in all.`));
  }

  /* ------------------------------------------------------------ the page */
  mount(el('div', { class: 'page-head' }, el('div', { class: 'grow' }, el('h1', {}, 'Settings'),
      el('div', { class: 'sub' }, 'Platform limits and security, and a clean-up of old quizzes and unused files. Only a Super Admin sees this page.')),
      el('a', { class: 'btn', href: '#/ai' }, icon('sparkle'), 'AI settings'), el('a', { class: 'btn', href: '#/team' }, icon('users'), 'Team')),
    statsBox,
    el('h2', { class: 'ai-h' }, 'Limits, uploads and security'), formBox,
    el('h2', { class: 'ai-h' }, 'Clean up'), cleanup, el('div', { style: { height: '1rem' } }), filesBox,
    el('h2', { class: 'ai-h' }, 'History'), changesBox);
  drawStats(); drawForm(); drawChanges(); drawCleanup(); drawFiles();
}
