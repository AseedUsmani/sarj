/* Runtime smoke test for the voice client.
 *
 * `node --check` parses the file; it does not run it. That gap shipped a
 * render() that called itself, which blew the stack on the first state change
 * — a defect no syntax check can see. This executes the real script from
 * static/index.html against stub DOM/audio/speech APIs and drives the
 * conversation loop through its states.
 *
 * Run: node tests/test_client.js
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');
const src = html.split('<script>')[1].split('</script>')[0].replace(/__BUILD__/g, 'test');

let failures = 0;
const check = (cond, msg) => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${msg}`);
  if (!cond) failures++;
};

// ── minimal DOM ────────────────────────────────────────────────────────────
const listeners = {};
function makeEl(id){
  return {
    id, textContent: '', value: '', title: '', hidden: false, disabled: false,
    className: '', style: {}, dataset: {},
    classList: { _s:new Set(),
      add(c){this._s.add(c);}, remove(c){this._s.delete(c);},
      toggle(c){this._s.has(c)?this._s.delete(c):this._s.add(c);},
      contains(c){return this._s.has(c);} },
    children: [],
    appendChild(c){ this.children.push(c); return c; },
    remove(){},
    setAttribute(){}, getAttribute(){ return null; },
    querySelector(){ return null; },
    addEventListener(ev, fn){ (listeners[id] ||= {})[ev] = fn; },
    scrollTop: 0, scrollHeight: 0,
  };
}
const els = {};
const document = {
  getElementById: id => (els[id] ||= makeEl(id)),
  createElement: () => makeEl('created'),
};

// ── stub speech recognition ────────────────────────────────────────────────
let recogInstance = null;
class FakeRecognition {
  constructor(){ this.running = false; recogInstance = this; }
  start(){ if (this.running) throw new Error('InvalidStateError'); this.running = true;
           queueMicrotask(() => this.onstart && this.onstart()); }
  stop(){ if (!this.running) return; this.running = false;
          queueMicrotask(() => this.onend && this.onend()); }
  say(text){ this.onresult && this.onresult({ resultIndex: 0,
    results: [Object.assign([{transcript: text}], {isFinal: true})] }); }
}

// ── stub audio ─────────────────────────────────────────────────────────────
let audioPlayed = 0;
class FakeAudio {
  constructor(){ this.src = ''; }
  play(){ audioPlayed++; return Promise.resolve(); }
  pause(){}
  finish(){ this.onended && this.onended(); }
}

const sandbox = {
  document, console,
  window: { SpeechRecognition: FakeRecognition, speechSynthesis: {cancel(){}, speak(){}} },
  navigator: {},
  localStorage: { _d:{}, getItem(k){return this._d[k]??null;},
                  setItem(k,v){this._d[k]=String(v);}, removeItem(k){delete this._d[k];} },
  crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
  location: { search: '?debug=1' },
  URLSearchParams, URL: { createObjectURL: () => 'blob:x', revokeObjectURL(){} },
  Audio: FakeAudio,
  setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask,
  AbortController,
  SpeechSynthesisUtterance: function(){ return {}; },
  fetch: async (url) => {
    if (url === '/auth/me') return { ok:true, json: async () => ({signed_in:false}) };
    if (url === '/speak')   return { ok:true, status:200, blob: async () => ({}) };
    if (url === '/chat')    return { ok:true, status:200,
      json: async () => ({answer:'It is sunny.', intent:'current_weather',
                          latency_ms:12, cached:false, route:'small'}) };
    return { ok:false, status:404, json: async () => ({}) };
  },
};
sandbox.window.webkitSpeechRecognition = FakeRecognition;
// In a browser these are globals as well as window properties.
sandbox.speechSynthesis = sandbox.window.speechSynthesis;
// The markup ships with 'ready' already in the header.
els['state'] = makeEl('state'); els['state'].textContent = 'ready';
sandbox.globalThis = sandbox;

// ── run it ─────────────────────────────────────────────────────────────────
try {
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'index.html:script' });
  check(true, 'script evaluates without throwing');
} catch (e) {
  check(false, `script threw on load: ${e.message}`);
  process.exit(1);
}

const state = () => document.getElementById('state').textContent;
const tick = () => new Promise(r => setTimeout(r, 30));

(async () => {
  check(state() === 'ready', `starts in 'ready' (got '${state()}')`);

  // Open a conversation.
  listeners['mic'].click();
  await tick();
  check(state() === 'listening', `mic opens conversation -> 'listening' (got '${state()}')`);

  // The regression that motivated this file: a state change must not recurse.
  try {
    for (let i = 0; i < 50; i++) recogInstance.onend();
    check(true, 'repeated recognition restarts do not blow the stack');
  } catch (e) {
    check(false, `restart cycle threw: ${e.message}`);
  }
  await tick();
  check(state() === 'listening',
        `silent restarts keep 'listening', never flicker to 'ready' (got '${state()}')`);

  // A quiet user must not be hung up on: 'no-speech' is not a fault.
  for (let i = 0; i < 20; i++){
    recogInstance.onerror({ error: 'no-speech' });
    recogInstance.onend();
  }
  await tick();
  check(state() === 'listening',
        `20 no-speech cycles do not end the conversation (got '${state()}')`);

  // But a microphone held by something else must end it, with an explanation.
  for (let i = 0; i < 6; i++){
    recogInstance.onstart();
    recogInstance.onerror({ error: 'aborted' });
    recogInstance.onend();
  }
  await tick();
  check(state() === 'ready', `repeated aborts end the conversation (got '${state()}')`);
  check(/microphone/i.test(document.getElementById('note').textContent),
        `and explain why: "${document.getElementById('note').textContent.slice(0,60)}"`);

  // Reopen for the remaining checks.
  listeners['mic'].click();
  await tick();

  // Speak a question; it should think, then speak, then return to listening.
  recogInstance.say('what is the weather in delhi');
  check(state() === 'thinking', `sending a question -> 'thinking' (got '${state()}')`);
  await tick(); await tick();
  check(audioPlayed === 1, `answer played through server audio (played=${audioPlayed})`);
  check(state() === 'speaking', `while playing -> 'speaking' (got '${state()}')`);

  // Interrupt via the mic button mid-answer.
  listeners['mic'].click();
  await tick();
  check(state() === 'listening', `mic during answer interrupts -> 'listening' (got '${state()}')`);

  // A second click with nothing playing ends the conversation.
  listeners['mic'].click();
  await tick();
  check(state() === 'ready', `mic when idle ends conversation -> 'ready' (got '${state()}')`);

  console.log(failures ? `\n${failures} failing` : '\nall client checks pass');
  process.exit(failures ? 1 : 0);
})();
