'use strict';
/* WYSIWYG editor for question and answer text. No dependencies, works offline.
   Produces the sanitised HTML subset that common.js renders (b i u s sub sup code span[color] p ul ol li br).
   The server sanitises again on save, so what you see here is exactly what players see. */

// Mid-tone colours that stay readable on both the light and the dark theme. No highlight colours on purpose:
// a pale background plus theme-coloured text can end up unreadable.
const RTE_COLORS = [['#D6405A', 'Red'], ['#E5681E', 'Orange'], ['#1E9A5F', 'Green'], ['#2D6BD6', 'Blue'], ['#7653D4', 'Purple']];
const RTE_RESET = 'rgb(1, 2, 3)'; // sentinel: "default colour" is applied as this, then un-wrapped

/** Wrap loose text/inline nodes in <p> so the editing commands (lists, Enter, formatBlock) behave consistently. */
function ensureBlocks(html) {
  const box = document.createElement('div');
  box.innerHTML = html;
  const out = document.createElement('div');
  let cur = null;
  for (const n of [...box.childNodes]) {
    const block = n.nodeType === 1 && ['P', 'UL', 'OL'].includes(n.tagName);
    if (block) { cur = null; out.append(n); continue; }
    if (!cur) { cur = document.createElement('p'); out.append(cur); }
    cur.append(n);
  }
  return out.innerHTML || '<p><br></p>';
}

function richEditor({ value = '', inline = false, placeholder = '', label = '', onChange = () => {}, onEnter = null, onBackspaceEmpty = null, id = null } = {}) {
  const ed = el('div', {
    class: 'rte-body' + (inline ? ' inline' : ''), contentEditable: 'true', role: 'textbox', id,
    'aria-multiline': String(!inline), 'aria-label': label, 'data-placeholder': placeholder, spellcheck: true,
  });
  ed.innerHTML = inline ? sanitizeRich(value, true) : ensureBlocks(sanitizeRich(value));

  /** Chrome wraps a new list in a <p> and drops the caret at the start: unwrap, and put the caret back at the line's end. */
  const tidyList = () => {
    const sel = getSelection();
    const node = sel && sel.rangeCount ? sel.anchorNode : null;
    $$('p', ed).forEach(pn => { if (pn.children.length === 1 && /^(UL|OL)$/.test(pn.firstElementChild.tagName) && !pn.textContent.replace(pn.firstElementChild.textContent, '')) pn.replaceWith(pn.firstElementChild); });
    const li = node && (node.nodeType === 1 ? node : node.parentElement) && (node.nodeType === 1 ? node : node.parentElement).closest('li');
    if (li && ed.contains(li)) { const rg = document.createRange(); rg.selectNodeContents(li); rg.collapse(false); sel.removeAllRanges(); sel.addRange(rg); }
  };
  const exec = (cmd, arg = null) => {
    ed.focus();
    // colour needs CSS mode (<span style="color">); every other command must stay semantic (<b>, <sub>, <ul>...)
    const css = cmd === 'foreColor';
    document.execCommand('styleWithCSS', false, css);
    document.execCommand(cmd, false, arg);
    if (css) document.execCommand('styleWithCSS', false, false);
    if (cmd === 'insertUnorderedList' || cmd === 'insertOrderedList') tidyList();
  };
  const buttons = {};
  const btn = (key, ic, title, run) => {
    const b = el('button', { class: 'rte-btn', type: 'button', title, 'aria-label': title, 'aria-pressed': 'false' }, icon(ic));
    b.addEventListener('mousedown', e => e.preventDefault());          // keep the text selection while clicking
    b.addEventListener('click', () => { run(); changed(); refresh(); });
    buttons[key] = b;
    return b;
  };
  const sep = () => el('span', { class: 'rte-sep', 'aria-hidden': 'true' });

  // colour popover
  const pop = el('div', { class: 'rte-colors hidden', role: 'menu' });
  const colorBtn = el('button', { class: 'rte-btn', type: 'button', title: 'Text colour', 'aria-label': 'Text colour', 'aria-haspopup': 'menu' }, el('span', { class: 'rte-A' }, 'A'));
  colorBtn.addEventListener('mousedown', e => e.preventDefault());
  colorBtn.addEventListener('click', () => pop.classList.toggle('hidden'));
  const applyColor = c => {
    exec('foreColor', c);
    if (c === RTE_RESET) $$('span', ed).forEach(s => { if (s.style.color === RTE_RESET) { while (s.firstChild) s.parentNode.insertBefore(s.firstChild, s); s.remove(); } });
    pop.classList.add('hidden'); changed();
  };
  RTE_COLORS.forEach(([c, name]) => {
    const sw = el('button', { class: 'rte-sw', type: 'button', title: name, 'aria-label': name, style: { background: c } });
    sw.addEventListener('mousedown', e => e.preventDefault()); sw.addEventListener('click', () => applyColor(c)); pop.append(sw);
  });
  const reset = el('button', { class: 'rte-sw reset', type: 'button', title: 'Default colour', 'aria-label': 'Default colour' }, icon('x'));
  reset.addEventListener('mousedown', e => e.preventDefault()); reset.addEventListener('click', () => applyColor(RTE_RESET)); pop.append(reset);

  const bar = el('div', { class: 'rte-bar', role: 'toolbar', 'aria-label': 'Text formatting' },
    btn('bold', 'bold', 'Bold (Ctrl+B)', () => exec('bold')), btn('italic', 'italic', 'Italic (Ctrl+I)', () => exec('italic')),
    btn('underline', 'underline', 'Underline (Ctrl+U)', () => exec('underline')), inline ? null : btn('strikeThrough', 'strike', 'Strikethrough', () => exec('strikeThrough')),
    sep(), btn('subscript', 'sub', 'Subscript, e.g. H₂O', () => exec('subscript')), btn('superscript', 'sup', 'Superscript, e.g. x²', () => exec('superscript')),
    inline ? null : sep(), inline ? null : btn('insertUnorderedList', 'list', 'Bulleted list', () => exec('insertUnorderedList')),
    inline ? null : btn('insertOrderedList', 'olist', 'Numbered list', () => exec('insertOrderedList')),
    inline ? null : sep(), inline ? null : el('span', { class: 'rte-colorwrap' }, colorBtn, pop),
    sep(), btn('clear', 'eraser', 'Clear formatting', () => { exec('removeFormat'); if (!inline) exec('formatBlock', 'p'); }),
    inline ? null : btn('undo', 'undo', 'Undo (Ctrl+Z)', () => exec('undo')), inline ? null : btn('redo', 'redo', 'Redo (Ctrl+Y)', () => exec('redo')));
  // 'sub'/'sup' icons are text glyphs (clearer than a drawing at this size)
  buttons.subscript.replaceChildren(el('span', { class: 'rte-glyph' }, 'x', el('sub', {}, '2')));
  buttons.superscript.replaceChildren(el('span', { class: 'rte-glyph' }, 'x', el('sup', {}, '2')));

  const root = el('div', { class: 'rte' + (inline ? ' inline' : '') }, bar, ed);

  const isEmpty = () => !plainText(ed.innerHTML);
  const getHtml = () => { const h = sanitizeRich(ed.innerHTML, inline).trim(); return plainText(h) ? h : ''; };
  const refresh = () => {
    ed.classList.toggle('is-empty', isEmpty());
    for (const k of ['bold', 'italic', 'underline', 'strikeThrough', 'subscript', 'superscript', 'insertUnorderedList', 'insertOrderedList']) {
      if (!buttons[k]) continue;
      let on = false;
      try { on = document.activeElement === ed && document.queryCommandState(k); } catch (e) { /* unsupported */ }
      buttons[k].setAttribute('aria-pressed', String(!!on));
    }
  };
  function changed() { refresh(); onChange(getHtml()); }

  ed.addEventListener('input', changed);
  ed.addEventListener('focus', () => { try { document.execCommand('defaultParagraphSeparator', false, 'p'); document.execCommand('styleWithCSS', false, false); } catch (e) { /* ignore */ } refresh(); });
  ed.addEventListener('blur', () => setTimeout(() => { if (!root.contains(document.activeElement)) pop.classList.add('hidden'); }, 0));
  ['keyup', 'mouseup'].forEach(ev => ed.addEventListener(ev, refresh));
  document.addEventListener('selectionchange', () => { if (document.activeElement === ed) refresh(); });
  ed.addEventListener('keydown', e => {
    if (inline && e.key === 'Enter') { e.preventDefault(); if (onEnter) onEnter(); return; }
    if (e.key === 'Backspace' && isEmpty() && onBackspaceEmpty) { e.preventDefault(); onBackspaceEmpty(); }
  });
  ed.addEventListener('paste', e => {
    e.preventDefault();
    const cd = e.clipboardData || window.clipboardData;
    const html = cd.getData('text/html'), text = cd.getData('text/plain');
    if (inline || !html) document.execCommand('insertText', false, inline ? text.replace(/\s*\n\s*/g, ' ') : text);
    else document.execCommand('insertHTML', false, sanitizeRich(html));   // keeps bold/italic/lists, drops everything else
    changed();
  });
  ed.addEventListener('drop', e => e.preventDefault());

  refresh();
  return {
    el: root, ed, getHtml, focus: () => { ed.focus(); const r = document.createRange(); r.selectNodeContents(ed); r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r); },
    setHtml(v) { ed.innerHTML = inline ? sanitizeRich(v, true) : ensureBlocks(sanitizeRich(v)); refresh(); },
  };
}
