/* Shelf Life sound: effects and background music, synthesised in the browser with Web Audio.
 * Everything is made from oscillators and filtered noise, except the students' murmur, a recorded loop
 * (static/audio/murmur.mp3, made by scripts/make_murmur.py) that loads from this server.
 * Only the director's projector page plays sound (see beamer.html); phones stay silent.
 *
 *   Sound.fx("bin")          one effect
 *   Sound.mood("open")       background music for a phase: calm | open | off
 *   Sound.crowd(0.5)         students talking in the room, 0 to 1
 *   Sound.set("music", false) on/off; the director switches music and effects separately
 */
const Sound = (() => {
  const state = { fx: true, music: true };
  let ctx = null, out, fxBus, musicBus, noiseBuf, mood = "off", ducked = false, timer = null;
  const last = {};  // effect name -> time, for rate limits

  function init() {
    if (ctx) return true;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    ctx = new AC();
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -18; comp.ratio.value = 4;
    out = ctx.createGain(); out.gain.value = 0.9;
    out.connect(comp).connect(ctx.destination);
    fxBus = ctx.createGain(); fxBus.gain.value = 0.55; fxBus.connect(out);
    musicBus = ctx.createGain(); musicBus.gain.value = 0; musicBus.connect(out);
    noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
    const d = noiseBuf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    loadMurmur();
    return true;
  }
  // browsers start audio only after a click or a key press on the page
  const unlock = () => { if (init() && ctx.state !== "running") ctx.resume(); musicLevel(); };
  addEventListener("pointerdown", unlock, true);
  addEventListener("keydown", unlock, true);
  const ready = () => ctx && ctx.state === "running";

  // ---------------------------------------------------------------- building blocks
  function tone(f, t, dur, o = {}) {
    const osc = ctx.createOscillator(), g = ctx.createGain();
    osc.type = o.type || "sine";
    osc.frequency.setValueAtTime(f, t);
    if (o.to) osc.frequency.exponentialRampToValueAtTime(o.to, t + (o.glide || dur));
    if (o.detune) osc.detune.value = o.detune;
    const peak = o.gain ?? 0.3, a = o.attack ?? 0.005;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(peak, t + a);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    let node = osc.connect(g);
    if (o.lowpass) { const lp = ctx.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = o.lowpass; node = node.connect(lp); }
    node.connect(o.bus || fxBus);
    osc.start(t); osc.stop(t + dur + 0.05);
  }
  function noise(t, dur, o = {}) {
    const src = ctx.createBufferSource(), f = ctx.createBiquadFilter(), g = ctx.createGain();
    src.buffer = noiseBuf; src.loop = true;
    f.type = o.filter || "bandpass";
    f.frequency.setValueAtTime(o.freq || 1000, t);
    if (o.to) f.frequency.exponentialRampToValueAtTime(o.to, t + dur);
    f.Q.value = o.q ?? 1;
    const peak = o.gain ?? 0.3, a = o.attack ?? 0.005;
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(peak, t + a);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(f).connect(g).connect(o.bus || fxBus);
    src.start(t, Math.random() * 1.5); src.stop(t + dur + 0.05);
    return g;
  }
  const bell = (f, t, gain = 0.18, dur = 1.2) => {  // a small shop bell: a few inharmonic partials
    [[1, 1], [2.76, 0.45], [5.4, 0.2], [8.9, 0.08]].forEach(([m, k]) => tone(f * m, t, dur / Math.sqrt(m), { gain: gain * k, type: "sine" }));
  };
  // students talking: a recorded loop of real voices (scripts/make_murmur.py), mixed until no word stands out.
  // One copy plays all the time at the room's level; a second copy at another spot fades in up close
  // for a moment when someone walks in, so a few voices come nearer and go again.
  let murmur = null, crowdGain = null, crowdLevel = 0;
  function loadMurmur() {
    fetch("/static/audio/murmur.mp3").then(r => r.arrayBuffer()).then(b => ctx.decodeAudioData(b)).then(buf => {
      murmur = buf;
      crowdGain = ctx.createGain(); crowdGain.gain.value = 0; crowdGain.connect(fxBus);
      loopFrom(Math.random() * buf.duration, crowdGain);
      crowdApply();
    }).catch(() => { /* no murmur, everything else still plays */ });
  }
  function loopFrom(offset, dest) {
    const src = ctx.createBufferSource();
    src.buffer = murmur; src.loop = true;
    src.loopStart = 0.06; src.loopEnd = murmur.duration - 0.06;  // skip the encoder's silence at both ends
    src.connect(dest); src.start(ctx.currentTime, Math.max(0.06, offset % (murmur.duration - 0.2)));
    return src;
  }
  function crowdApply() {
    if (crowdGain) crowdGain.gain.setTargetAtTime(state.fx ? crowdLevel * 0.35 : 0, ctx.currentTime, 0.8);
  }
  function voices(t, secs = 2.5, gain = 0.5) {  // a few voices come close for a moment
    if (!murmur) return;
    const g = ctx.createGain(), shelf = ctx.createBiquadFilter();
    shelf.type = "highshelf"; shelf.frequency.value = 2500; shelf.gain.value = 4;  // nearer voices are a little brighter
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.35);
    g.gain.setTargetAtTime(0.0001, t + secs * 0.6, secs * 0.2);
    g.connect(shelf).connect(fxBus);
    const src = loopFrom(Math.random() * murmur.duration, g);
    src.stop(t + secs + 1);
  }
  // a robot "word": quick sine glides like a little droid
  function droid(t, n = 5, gain = 0.12) {
    for (let i = 0; i < n; i++) {
      const f = 700 + Math.random() * 1400, to = f * (0.6 + Math.random() * 0.9), d = 0.05 + Math.random() * 0.08;
      tone(f, t, d, { to, gain, type: Math.random() < 0.5 ? "sine" : "triangle" });
      t += d + 0.015;
    }
    return t;
  }
  const clicks = (t, n, gap = 0.07, gain = 0.25, freq = 3000) => {
    for (let i = 0; i < n; i++) noise(t + i * gap * (0.7 + Math.random() * 0.6), 0.03, { freq, q: 2, gain });
  };

  // ---------------------------------------------------------------- the effects
  const FX = {
    wake(t) { tone(180, t, 0.5, { to: 720, type: "sawtooth", gain: 0.06, lowpass: 1400 });
              [523, 659, 784, 1046].forEach((f, i) => tone(f, t + 0.32 + i * 0.07, 0.12, { type: "square", gain: 0.05, lowpass: 3000 }));
              droid(t + 0.7, 4, 0.08); },
    look_at_shelf(t) { for (let i = 0; i < 7; i++) tone(1300 + Math.random() * 700, t + i * 0.08, 0.05, { gain: 0.08 });
                       noise(t, 0.6, { freq: 2500, to: 6000, q: 3, gain: 0.05 }); },
    read_policy(t) { noise(t, 0.18, { freq: 1500, to: 4000, q: 1.5, gain: 0.18 }); noise(t + 0.28, 0.18, { freq: 1800, to: 4500, q: 1.5, gain: 0.15 }); },
    sales_report(t) { clicks(t, 10, 0.045, 0.16, 2200); tone(880, t + 0.55, 0.12, { gain: 0.08, type: "square", lowpass: 2500 }); },
    mark_down(t) { noise(t, 0.08, { filter: "lowpass", freq: 900, gain: 0.5 }); tone(140, t, 0.12, { to: 60, gain: 0.3 });
                   tone(988, t + 0.14, 0.1, { gain: 0.1, type: "triangle" }); tone(740, t + 0.26, 0.16, { gain: 0.1, type: "triangle" }); },
    food_bank_form(t) { clicks(t, 9, 0.07, 0.2, 3500); bell(1568, t + 0.75, 0.12, 0.8); },
    send_to_food_bank(t) { noise(t, 1.3, { filter: "lowpass", freq: 160, gain: 0.35, attack: 0.25 });
                           tone(392, t + 0.9, 0.18, { type: "square", gain: 0.05, lowpass: 1500 }); tone(440, t + 1.12, 0.22, { type: "square", gain: 0.05, lowpass: 1500 }); },
    give_to_cashier(t) { [659, 784, 988].forEach((f, i) => tone(f, t + i * 0.1, 0.5, { type: "triangle", gain: 0.12 })); },
    bin(t) { noise(t, 0.25, { filter: "lowpass", freq: 250, gain: 0.7 }); tone(90, t, 0.3, { to: 45, gain: 0.35 });
             [523, 1247, 1873].forEach(f => tone(f, t + 0.02, 0.45, { gain: 0.05 })); },
    order(t) { [[697, 1209], [770, 1336], [852, 1477]].forEach(([a, b], i) => { tone(a, t + i * 0.16, 0.11, { gain: 0.07 }); tone(b, t + i * 0.16, 0.11, { gain: 0.07 }); });
               droid(t + 0.55, 3, 0.07); },
    note_for_tomorrow(t) { for (let i = 0; i < 4; i++) noise(t + i * 0.17, 0.14, { freq: 4500, to: 3000, q: 4, gain: 0.12, attack: 0.03 }); },
    done_for_now(t) { droid(t, 3, 0.08); tone(880, t + 0.3, 0.45, { to: 220, type: "square", gain: 0.04, lowpass: 1800 }); },
    think(t) { tone(600 + Math.random() * 500, t, 0.07, { gain: 0.04 }); tone(900 + Math.random() * 500, t + 0.11, 0.07, { gain: 0.03 }); },
    error(t) { tone(110, t, 0.45, { type: "square", gain: 0.08, lowpass: 900 }); tone(104, t, 0.45, { type: "square", gain: 0.08, lowpass: 900 }); },
    alert(t) { [880, 660, 880, 660].forEach((f, i) => tone(f, t + i * 0.16, 0.14, { type: "triangle", gain: 0.12 })); },
    stamp(t) { noise(t, 0.12, { filter: "lowpass", freq: 600, gain: 0.7 }); tone(110, t, 0.15, { to: 55, gain: 0.4 }); },
    open(t) { bell(1318, t, 0.2); bell(1568, t + 0.14, 0.17); voices(t + 0.3, 3.5, 0.6); },
    close(t) { bell(1318, t, 0.15); noise(t + 0.6, 0.06, { filter: "highpass", freq: 2000, gain: 0.3 }); tone(220, t + 0.62, 0.08, { gain: 0.15 }); },
    enter(t) { bell(1760, t, 0.07, 0.6); voices(t + 0.1, 2.2, 0.35); },
    join(t) { voices(t, 2.5, 0.4); tone(660, t, 0.12, { type: "triangle", gain: 0.08 }); tone(990, t + 0.09, 0.15, { type: "triangle", gain: 0.08 }); },
    till(t) { noise(t, 0.04, { freq: 3000, gain: 0.15 }); tone(2093, t + 0.03, 0.35, { gain: 0.05 }); tone(2637, t + 0.08, 0.4, { gain: 0.04 }); },
    delivery(t) { noise(t, 1.0, { filter: "lowpass", freq: 180, gain: 0.3, attack: 0.3 });
                  [0.9, 1.15, 1.35].forEach(d => { noise(t + d, 0.12, { filter: "lowpass", freq: 400, gain: 0.45 }); tone(120, t + d, 0.1, { to: 70, gain: 0.2 }); }); },
    ranking(t) { for (let i = 0; i < 22; i++) noise(t + i * 0.05, 0.05, { freq: 1800, q: 0.7, gain: 0.05 + i * 0.006 });
                 [523, 659, 784, 1046].forEach(f => tone(f, t + 1.15, 0.9, { type: "triangle", gain: 0.07 })); },
    week(t) { [523, 659, 784, 1046, 784, 1046].forEach((f, i) => tone(f, t + i * 0.13, i === 5 ? 1.0 : 0.16, { type: "square", gain: 0.05, lowpass: 2800 })); },
    pause(t) { tone(440, t, 0.4, { to: 110, type: "triangle", gain: 0.12 }); },
    resume(t) { tone(110, t, 0.3, { to: 440, type: "triangle", gain: 0.12 }); },
  };
  // the longest gap an effect waits for its next turn; buys and walk-ins come in crowds
  const LIMIT = { till: 0.3, enter: 0.9, think: 2.4, join: 1.0 };

  function fx(name) {
    if (!state.fx || !ready() || !FX[name]) return;
    const now = ctx.currentTime;
    if (LIMIT[name] && last[name] && now - last[name] < LIMIT[name]) return;
    last[name] = now;
    FX[name](now + 0.02);
  }

  // ---------------------------------------------------------------- background music
  // a slow lo-fi loop: four chords on an electric piano, bass, and, while the shop is open, soft drums
  const BPM = 76, BEAT = 60 / BPM;
  const CHORDS = [[53, 57, 60, 64], [52, 55, 59, 62], [50, 53, 57, 60], [48, 52, 55, 59]];  // Fmaj7 Em7 Dm7 Cmaj7 (MIDI)
  const PENTA = [72, 74, 76, 79, 81, 84];
  const hz = m => 440 * Math.pow(2, (m - 69) / 12);
  let nextBeat = 0, beat = 0;

  function keys(m, t, dur, gain) {  // a soft electric piano: sine plus a quiet octave, slow release
    tone(hz(m), t, dur, { gain, attack: 0.02, bus: musicBus, lowpass: 2200 });
    tone(hz(m) * 2, t, dur * 0.6, { gain: gain * 0.25, attack: 0.01, bus: musicBus });
  }
  function scheduleBeat(t, b) {
    const bar = Math.floor(b / 4) % CHORDS.length, pos = b % 4, ch = CHORDS[bar], busy = mood === "open";
    if (pos === 0) ch.forEach((m, i) => keys(m, t + i * 0.012, BEAT * 2.6, 0.05));
    if (pos === 2) ch.slice(1).forEach((m, i) => keys(m, t + BEAT * 0.5 + i * 0.012, BEAT * 1.2, 0.035));
    if (pos === 0 || pos === 2) tone(hz(ch[0] - 24), t, BEAT * 1.6, { gain: 0.11, attack: 0.02, bus: musicBus, lowpass: 400 });
    if (busy) {
      if (pos === 0 || pos === 2) tone(110, t, 0.22, { to: 45, gain: 0.18, bus: musicBus });
      if (pos === 1 || pos === 3) tone(1250, t, 0.05, { type: "triangle", gain: 0.035, bus: musicBus });  // a soft rim click, no noise
      [0, 0.5].forEach(o => noise(t + BEAT * o + (o ? BEAT * 0.06 : 0), 0.03, { filter: "highpass", freq: 8000, gain: 0.015, bus: musicBus }));
    }
    if (Math.random() < (busy ? 0.35 : 0.2)) keys(PENTA[Math.floor(Math.random() * PENTA.length)], t + BEAT * (Math.random() < 0.5 ? 0 : 0.5), BEAT * 1.5, 0.03);
  }
  function tick() {
    if (!ready()) return;
    while (nextBeat < ctx.currentTime + 0.35) {
      if (nextBeat < ctx.currentTime) nextBeat = ctx.currentTime + 0.05;
      scheduleBeat(nextBeat, beat++);
      nextBeat += BEAT;
    }
  }
  function musicLevel() {
    if (!ctx) return;
    const on = state.music && mood !== "off";
    const level = !on ? 0 : ducked ? 0.25 : mood === "open" ? 0.9 : 0.6;
    musicBus.gain.setTargetAtTime(level * 0.5, ctx.currentTime, 0.6);
    if (on && !timer) { nextBeat = 0; timer = setInterval(tick, 100); }
    if (!on && timer) setTimeout(() => { if (!(state.music && mood !== "off")) { clearInterval(timer); timer = null; } }, 2500);
  }

  return {
    fx,
    mood(m) { if (m !== mood) { mood = m; musicLevel(); } },
    duck(on) { if (on !== ducked) { ducked = on; musicLevel(); } },
    set(what, on) { if (state[what] !== on) { state[what] = on; musicLevel(); crowdApply(); } },
    crowd(level) { const l = Math.max(0, Math.min(1, level)); if (l !== crowdLevel) { crowdLevel = l; if (ctx) crowdApply(); } },  // 0 to 1
    meter() {  // loudness of everything that plays now, for tests (root mean square, 0 to 1)
      if (!ctx) return 0;
      if (!this._an) { this._an = ctx.createAnalyser(); this._an.fftSize = 2048; out.connect(this._an); }
      const a = new Float32Array(2048); this._an.getFloatTimeDomainData(a);
      return Math.sqrt(a.reduce((s, x) => s + x * x, 0) / a.length);
    },
    get on() { return { ...state, running: ready() }; },
  };
})();
