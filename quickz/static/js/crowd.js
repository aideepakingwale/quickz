'use strict';
/* Projector visuals for the show: avatars flying into the bars, emoji thrown by the audience, "final answers locked". */

const calm = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

/** The headline under a revealed question: "Only 12% got it!" / "Everyone got it!" - or null when it would mean little. */
function crowdBadge(q, r) {
  const n = r.answered || 0;
  if (!q.scored || q.type === 'buzzer' || n < 3) return null;
  const pct = r.correct_count / n, p = Math.round(pct * 100);
  if (r.correct_count === 0) return { text: 'Nobody got that one!', tone: 'hard' };
  if (pct === 1) return { text: 'Everyone got it!', tone: 'easy' };
  if (pct <= 0.25) return { text: `Tough one: only ${p}% got it`, tone: 'hard' };
  if (pct >= 0.7) return { text: `Crowd pleaser: ${p}% got it`, tone: 'easy' };
  return null;
}

let flyLayer = null;
function clearFlights() { if (flyLayer) { flyLayer.remove(); flyLayer = null; } }

/** Each player's avatar flies from the middle of the screen into the bar of the answer they picked. `crowd` = {optionId: [avatar...]}. */
function crowdFlight(root, crowd, r) {
  clearFlights();
  if (!crowd || calm()) return;
  flyLayer = el('div', { class: 'fly-layer', 'aria-hidden': 'true' });
  document.body.append(flyLayer);
  const layer = flyLayer, cx = innerWidth / 2, cy = innerHeight * 0.36;
  let k = 0, pending = 0;
  for (const [id, list] of Object.entries(crowd)) {
    const row = root.querySelector(`.res-row[data-id="${CSS.escape(id)}"]`);
    if (!row || !list.length) continue;
    const shown = list.slice(0, 8), more = (r.distribution && r.distribution[id] || list.length) - shown.length;
    const box = el('span', { class: 'crowd' }, shown.map(a => el('i', {}, a)), more > 0 ? el('i', { class: 'more' }, '+' + more) : null);
    row.insertBefore(box, row.querySelector('.cnt'));
    requestAnimationFrame(() => [...box.children].forEach(chip => {
      const b = chip.getBoundingClientRect();
      if (!b.width) return;
      chip.style.opacity = '0';                                // the landed chip appears when its flying twin arrives
      const fly = el('i', { class: 'fly' }, chip.textContent);
      Object.assign(fly.style, { left: b.left + 'px', top: b.top + 'px', width: b.width + 'px', height: b.height + 'px', fontSize: getComputedStyle(chip).fontSize });
      layer.append(fly); pending++;
      const a = fly.animate([
        { transform: `translate(${cx - b.left - b.width / 2}px, ${cy - b.top - b.height / 2}px) scale(2.6)`, opacity: 0 },
        { opacity: 1, offset: 0.25 }, { transform: 'none', opacity: 1 }],
      { duration: 800, delay: 450 + (k++) * 16, easing: 'cubic-bezier(.2,.8,.25,1)', fill: 'backwards' });
      a.onfinish = () => { chip.style.opacity = ''; fly.remove(); if (--pending <= 0 && layer === flyLayer) clearFlights(); };
    }));
  }
}

/** "Final answers locked": dims the room and shows a pulsing banner while the server holds the reveal. */
function setSuspense(on) {
  let n = $('#suspense');
  if (!n) { n = el('div', { id: 'suspense', 'aria-hidden': 'true' }, el('div', { class: 'msg' }, 'Final answers locked', el('span', { class: 'dots' }, el('i'), el('i'), el('i')))); document.body.append(n); }
  document.body.classList.toggle('suspense', !!on);
}
