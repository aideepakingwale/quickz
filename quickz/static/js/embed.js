'use strict';
/* YouTube and Vimeo players. They behave like <audio>/<video> elements to the rest of the app: play(), pause(),
   `currentTime = 0`, and 'playing' / 'pause' / 'ended' / 'error' events, so the "timer starts when the clip ends"
   rule, replay and the projector controls all work unchanged. Only a provider + ID that the SERVER validated is
   ever turned into an address here - never a pasted URL. */

const MEDIA_LABEL = m => (m.kind === 'embed' ? (m.provider === 'vimeo' ? 'Vimeo' : 'YouTube') + (m.cover ? ' (audio only)' : '') : m.kind === 'video' ? 'Video' : 'Audio');
const mediaIcon = m => (m.kind === 'video' || (m.kind === 'embed' && !m.cover) ? 'film' : 'music');

function embedSrc(m, { controls = true, autoplay = true } = {}) {
  const on = v => (v ? '1' : '0');
  if (m.provider === 'vimeo') {
    const q = new URLSearchParams({ autoplay: on(autoplay), playsinline: '1', controls: on(controls), title: '0', byline: '0', portrait: '0', dnt: '1' });
    if (m.hash) q.set('h', m.hash);
    return `https://player.vimeo.com/video/${encodeURIComponent(m.id)}?${q}` + (m.start ? `#t=${m.start}s` : '');
  }
  const q = new URLSearchParams({ enablejsapi: '1', autoplay: on(autoplay), playsinline: '1', rel: '0', modestbranding: '1', iv_load_policy: '3', fs: '0',
    controls: on(controls), disablekb: on(!controls), origin: location.origin });
  if (m.start) q.set('start', String(m.start));
  if (m.end) q.set('end', String(m.end));
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(m.id)}?${q}`;
}

function embedPlayer(m, { controls = true, big = false, autoplay = true, onEnded = null } = {}) {
  const yt = m.provider !== 'vimeo';
  const wrap = el('div', { class: 'embed' + (big ? ' big' : '') + (m.cover ? ' covered' : ''), 'data-provider': m.provider });
  const frame = el('iframe', { src: embedSrc(m, { controls: controls && !m.cover, autoplay }), title: m.cover ? 'Audio clip' : 'Video',
    allow: 'autoplay; encrypted-media; picture-in-picture', referrerpolicy: 'strict-origin-when-cross-origin',
    sandbox: 'allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox' });
  wrap.append(frame);
  if (m.cover) wrap.append(el('div', { class: 'embed-cover' }, el('div', { class: 'eq', 'aria-hidden': 'true' }, [0, 1, 2, 3, 4, 5, 6].map(i => el('i', { style: { animationDelay: (i * 0.11) + 's' } }))), el('div', { class: 'embed-cover-title' }, 'Listen carefully…')));

  let playing = false, ended = false;
  const fire = name => {
    if (name === 'playing') { playing = true; ended = false; } else if (name === 'ended') { if (ended) return; ended = true; playing = false; } else if (name === 'pause') playing = false;
    wrap.dispatchEvent(new Event(name));
  };
  const post = obj => { try { frame.contentWindow.postMessage(JSON.stringify(obj), '*'); } catch (e) { /* not ready yet */ } };
  const command = (name, arg) => {
    if (yt) post({ event: 'command', func: { play: 'playVideo', pause: 'pauseVideo', seek: 'seekTo' }[name], args: name === 'seek' ? [arg, true] : [], id: 1, channel: 'widget' });
    else post(name === 'seek' ? { method: 'setCurrentTime', value: arg } : { method: name });
  };
  frame.addEventListener('load', () => {
    if (yt) post({ event: 'listening', id: 1, channel: 'widget' });
    else ['play', 'pause', 'finish', 'error', 'timeupdate'].forEach(value => post({ method: 'addEventListener', value }));
  });
  const onMessage = e => {
    if (!wrap.isConnected && frame.contentWindow == null) { removeEventListener('message', onMessage); return; }
    if (e.source !== frame.contentWindow) return;
    let d = e.data;
    if (typeof d === 'string') { try { d = JSON.parse(d); } catch (x) { return; } }
    if (!d || typeof d !== 'object') return;
    if (yt) {
      const state = d.event === 'onStateChange' ? d.info : (d.event === 'infoDelivery' && d.info && typeof d.info.playerState === 'number' ? d.info.playerState : null);
      if (state === 1) fire('playing'); else if (state === 0) fire('ended'); else if (state === 2) fire('pause');
      if (d.event === 'onError') fire('error');
    } else {
      if (d.event === 'play') fire('playing'); else if (d.event === 'pause') fire('pause'); else if (d.event === 'finish') fire('ended'); else if (d.event === 'error') fire('error');
      if (d.event === 'timeupdate' && m.end && d.data && d.data.seconds >= m.end) { command('pause'); fire('ended'); }   // Vimeo has no "stop at"
    }
  };
  addEventListener('message', onMessage);
  if (onEnded) wrap.addEventListener('ended', onEnded);

  wrap.play = () => new Promise((resolve, reject) => {
    if (playing) return resolve();
    command('play');
    const done = () => { clearTimeout(t); wrap.removeEventListener('playing', done); resolve(); };
    const t = setTimeout(() => { wrap.removeEventListener('playing', done); reject(new Error('blocked')); }, 3500);   // autoplay refused: the caller shows "click to play"
    wrap.addEventListener('playing', done);
  });
  wrap.pause = () => command('pause');
  Object.defineProperty(wrap, 'currentTime', { get: () => 0, set: () => { command('seek', m.start || 0); ended = false; } });
  return wrap;
}
