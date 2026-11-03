'use strict';
/* Projector atmosphere: sound packs (music beds that speed up as time runs out, a suspense bed, stingers), crowd sounds,
   the host's soundboard cues, a voice for every buzzer, and read-aloud. Everything is synthesised with Web Audio:
   no audio files, nothing to license. Phones do not load this file: the big screen is the room's sound system. */

const PACK_NAMES = { gameshow: 'Game show', arcade: 'Arcade', quiet: 'Quiet' };

Object.assign(Sound, {
  _noise: null,
  noiseBuffer() {
    if (!this._noise) {
      const n = this.ctx.sampleRate, buf = this.ctx.createBuffer(1, n, this.ctx.sampleRate), d = buf.getChannelData(0);
      for (let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
      this._noise = buf;
    }
    return this._noise;
  },
  /** A short burst of filtered noise: a clap, a snare hit, a cymbal. */
  noiseAt(t0, dur, vol, { freq = 1800, q = 0.8, type = 'bandpass' } = {}) {
    if (!this.on || !this.ctx) return;
    const src = this.ctx.createBufferSource(), f = this.ctx.createBiquadFilter(), g = this.ctx.createGain();
    src.buffer = this.noiseBuffer(); f.type = type; f.frequency.value = freq; f.Q.value = q;
    g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(vol, t0 + 0.004); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(f).connect(g).connect(this.out || this.ctx.destination);
    src.start(t0, Math.random() * 0.5, dur + 0.05);
  },
  /** A room clapping: lots of tiny noise bursts that swell and fade. density 0..1.5 */
  applause(density = 1, secs = 2.6) {
    if (!this.on || !this.ctx) return;
    const t = this.ctx.currentTime + 0.05, claps = Math.round(secs * 30 * density);
    for (let i = 0; i < claps; i++) {
      const x = Math.random(), env = Math.sin(Math.PI * Math.min(1, x * 0.92 + 0.04));
      this.noiseAt(t + secs * x, 0.03 + Math.random() * 0.03, 0.1 + 0.3 * env * Math.random(), { freq: 1000 + Math.random() * 2200, q: 0.9 });
    }
  },
  /** A sympathetic "ohhh": two falling glides and a low sigh. */
  aww() { this.tone(520, 0, 0.7, 'triangle', 0.13, 340); this.tone(392, 0.28, 0.9, 'triangle', 0.12, 220); this.tone(130, 0.5, 0.6, 'sine', 0.1, 80); },
  /** A snare roll that speeds up and ends on a hit. */
  roll(secs = 2.2) {
    if (!this.on || !this.ctx) return;
    const t = this.ctx.currentTime + 0.03;
    for (let x = 0; x < secs; x += Math.max(0.028, 0.09 - 0.06 * (x / secs))) this.noiseAt(t + x, 0.06, 0.04 + 0.13 * (x / secs), { freq: 250 + 500 * (x / secs), q: 0.6 });
    this.noiseAt(t + secs, 0.5, 0.2, { freq: 5500, q: 0.4, type: 'highpass' }); this.toneAt(t + secs, 90, 0.35, 'triangle', 0.25, 40);
  },
  airhorn() { [0, 0.55].forEach(d => { this.tone(466, d, 0.45, 'sawtooth', 0.09, 440); this.tone(554, d, 0.45, 'sawtooth', 0.07, 520); this.tone(698, d, 0.45, 'square', 0.05, 660); }); },
  ding() { this.tone(1318.5, 0, 1.1, 'sine', 0.16); this.tone(2637, 0, 0.6, 'sine', 0.06); },
  wrongBuzz() { this.tone(110, 0, 0.7, 'sawtooth', 0.2, 90); this.tone(113, 0, 0.7, 'square', 0.06, 92); },
  /** Every player's buzzer has its own voice (96 combinations of pitch, tone and rhythm), so the room hears who got there first. */
  buzzFor(id) {
    const k = Math.abs(id | 0), base = [262, 294, 330, 392, 440, 523, 587, 659][k % 8], wave = ['square', 'sawtooth', 'triangle', 'sine'][(k >> 3) % 4], pat = (k >> 5) % 3;
    [[0], [0, 0.16], [0, 0.13, 0.26]][pat].forEach((d, i) => this.tone(base * (pat === 2 ? 1 + i * 0.25 : 1), d, pat === 0 ? 0.6 : 0.13, wave, 0.2, pat === 0 ? base * 0.6 : null));
    this.tone(base / 2, 0, 0.2, 'sine', 0.15);
  },
  /** One-shot musical punctuation in the chosen pack: reveal, correct, wrong. */
  stinger(kind, pack) {
    if (pack === 'quiet') { if (kind === 'correct') this.good(); else if (kind === 'wrong') this.bad(); else this.reveal(); return; }
    const arcade = pack === 'arcade';
    if (kind === 'reveal') {
      if (arcade) [523, 659, 784, 1047].forEach((f, i) => this.tone(f, i * 0.07, 0.1, 'square', 0.12));
      else { this.tone(220, 0, 0.5, 'sawtooth', 0.07, 440); this.tone(660, 0.3, 0.4, 'triangle', 0.12); this.tone(990, 0.4, 0.55, 'triangle', 0.12); }
    } else if (kind === 'correct') {
      if (arcade) { this.tone(988, 0, 0.08, 'square', 0.14); this.tone(1319, 0.08, 0.35, 'square', 0.14); }
      else { [523, 659, 784, 1047].forEach((f, i) => this.tone(f, i * 0.08, 0.9 - i * 0.1, 'triangle', 0.1)); this.tone(2093, 0.3, 0.4, 'sine', 0.04); }
    } else if (arcade) [440, 370, 311, 262].forEach((f, i) => this.tone(f, i * 0.1, 0.12, 'square', 0.12));
    else { this.tone(196, 0, 0.5, 'sawtooth', 0.1, 147); this.tone(147, 0.45, 0.8, 'sawtooth', 0.1, 98); this.toneAt(this.ctx.currentTime + 0.05, 70, 0.4, 'triangle', 0.2, 40); }
  },
});

/* ---------------------------------------------------------------- beds: music that is scheduled a few beats ahead */
const BED = {
  gameshow: {
    lobby(t, n) {
      const chords = [[220, 261.63, 329.63], [174.61, 220, 261.63], [261.63, 329.63, 392], [196, 246.94, 293.66]];
      const c = chords[Math.floor(n / 4) % 4], i = n % 4;
      if (i === 0) Sound.toneAt(t, c[0] / 2, 1.9, 'sine', 0.07);
      Sound.toneAt(t, c[[0, 1, 2, 1][i]] * 2, 0.5, 'triangle', 0.035);
      return 0.62;
    },
    thinking(t, n, b) {                                  // a heartbeat that quickens as the clock runs down
      const len = 60 / (72 + 76 * b.intensity);
      Sound.toneAt(t, 62, 0.22, 'sine', 0.2 + 0.1 * b.intensity, 42);
      Sound.toneAt(t + len / 2, 1700, 0.03, 'square', 0.015 + 0.02 * b.intensity);
      if (n % 8 === 0) Sound.toneAt(t, 110, len * 7, 'sine', 0.035);
      if (b.intensity > 0.6 && n % 2) Sound.toneAt(t + len / 4, 440 * (1 + b.intensity), 0.1, 'triangle', 0.03);
      return len;
    },
    suspense(t, n, b) {                                  // lub-dub, a rising drone, and a climbing pitch
      const p = b.dur ? Math.min(1, (t - b.t0) / b.dur) : 0;
      Sound.toneAt(t, 55, 0.2, 'sine', 0.3, 38); Sound.toneAt(t + 0.14, 50, 0.16, 'sine', 0.22, 36);
      if (n === 0) Sound.toneAt(t, 80, b.dur || 3, 'sawtooth', 0.05, 400);
      Sound.toneAt(t, 330 + 660 * p, 0.12, 'triangle', 0.03 + 0.03 * p);
      return 0.5 - 0.18 * p;
    },
  },
  arcade: {
    lobby(t, n) {
      const m = [392, 494, 587, 659, 784, 659, 587, 494];
      Sound.toneAt(t, m[n % 8], 0.12, 'square', 0.06);
      if (n % 4 === 0) Sound.toneAt(t, 196, 0.25, 'triangle', 0.1);
      return 0.25;
    },
    thinking(t, n, b) {
      const a = [330, 392, 494, 392];
      Sound.toneAt(t, a[n % 4] * (b.intensity > 0.7 ? 2 : 1), 0.06, 'square', 0.05);
      if (n % 2 === 0) Sound.toneAt(t, 82, 0.1, 'triangle', 0.16);
      return 60 / (110 + 90 * b.intensity) / 2;
    },
    suspense(t, n, b) {
      const p = b.dur ? Math.min(1, (t - b.t0) / b.dur) : 0;
      Sound.toneAt(t, 220 * Math.pow(1.06, n), 0.1, 'square', 0.08);
      return 0.18 - 0.08 * p;
    },
  },
};

const Bed = {
  timer: null, kind: null, pack: null, next: 0, n: 0, t0: 0, dur: 0, intensity: 0,
  /** Start a bed (idempotent: asking again for the one already playing does nothing). dur = how long a suspense lasts, ms. */
  start(kind, pack, { dur = 0 } = {}) {
    if (this.timer && this.kind === kind && this.pack === pack) return;
    this.stop();
    if (!Sound.on || !Sound.ctx || pack === 'quiet' || !(BED[pack] || {})[kind]) return;
    Object.assign(this, { kind, pack, n: 0, intensity: 0, dur: dur / 1000, t0: Sound.ctx.currentTime + 0.05 });
    this.next = this.t0;
    this.timer = setInterval(() => this.pump(), 50);
    this.pump();
  },
  stop() { if (this.timer) clearInterval(this.timer); this.timer = null; this.kind = null; },
  setIntensity(x) { this.intensity = Math.max(0, Math.min(1, x)); },
  pump() {
    const c = Sound.ctx;
    if (!c || !Sound.on) { this.stop(); return; }
    for (let guard = 0; this.next < c.currentTime + 0.3 && guard < 8; guard++) this.next += this.beat(this.next, this.n++);
    if (this.next < c.currentTime - 0.5) this.next = c.currentTime;          // a throttled tab: skip ahead instead of replaying old beats
  },
  beat(t, n) { const f = (BED[this.pack] || {})[this.kind]; return f ? f(t, n, this) : 1; },
};

/* ---------------------------------------------------------------- the host's soundboard */
const CUE_PLAY = {
  drumroll: () => Sound.roll(2.2),
  applause: () => Sound.applause(1, 3.2),
  aww: () => Sound.aww(),
  ding: () => Sound.ding(),
  wrong: () => Sound.wrongBuzz(),
  airhorn: () => Sound.airhorn(),
  fanfare: () => { Sound.fanfare(); confetti({ count: 160, duration: 3500 }); },
  tick: () => Sound.tick(),
};
function playCue(name) { if (CUE_PLAY[name]) CUE_PLAY[name](); }

/** What the room hears when the answer appears: a stinger, then the crowd's reaction to how many got it right. */
function revealAudio(q, r, pack) {
  Sound.stinger('reveal', pack);
  const n = r.answered || 0;
  if (q.type === 'buzzer') { setTimeout(() => Sound.stinger(r.buzz_winner ? 'correct' : 'wrong', pack), 450); return; }
  if (!q.scored || n < 3) return;
  const pct = r.correct_count / n;
  if (pct >= 0.7) setTimeout(() => Sound.applause(0.5 + pct * 0.7, 2.2), 900);
  else if (pct <= 0.25) setTimeout(() => Sound.aww(), 900);
}

/* ---------------------------------------------------------------- read-aloud */
const Speak = {
  supported: 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window,
  on: (() => { try { return localStorage.getItem('quickz:tts') === 'on'; } catch (e) { return false; } })(),
  setOn(v) { this.on = v; try { localStorage.setItem('quickz:tts', v ? 'on' : 'off'); } catch (e) { /* ignore */ } if (!v) this.stop(); },
  stop() { if (this.supported) speechSynthesis.cancel(); },
  say(text) {
    if (!this.supported || !this.on || !text) return;
    this.stop();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 0.95; u.lang = navigator.language || 'en-GB';
    speechSynthesis.speak(u);
  },
};
/** The question and, for choice questions, "A, ... B, ..." as one sentence-per-line script. */
function questionScript(q) {
  const parts = [`Question ${q.n}.`, plainText(q.text) + '.'];
  if (['mcq', 'multi', 'poll'].includes(q.type)) (q.options || []).forEach((o, i) => parts.push(`${'ABCDEF'[i] || i + 1}: ${plainText(o.text) || 'a picture'}.`));
  return parts.join(' ');
}
