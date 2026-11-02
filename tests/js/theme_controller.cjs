/* Exercise the shipped theme controller (static/theme.js) and the inline
 * pre-paint bootstrap (templates/base.html) against controlled DOM, storage,
 * media-query, timer and View Transition boundaries. The transitions are
 * deferred by hand, so older callbacks can be made to run, reject or finish
 * after newer requests. No browser, network or database. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '..', '..');
const controller = fs.readFileSync(path.join(ROOT, 'static', 'theme.js'), 'utf8');
const base = fs.readFileSync(path.join(ROOT, 'templates', 'base.html'), 'utf8');
const bootOpen = base.indexOf('<script>', base.indexOf('Theme bootstrap'));
const bootstrap = base.slice(bootOpen + '<script>'.length, base.indexOf('</script>', bootOpen));
assert(bootOpen > 0 && bootstrap.includes("'amorino_theme'"), 'Theme bootstrap must be inline in base.html');
assert(base.indexOf('meta name="color-scheme"') < bootOpen && base.indexOf('meta name="theme-color"') < bootOpen,
  'Theme metas must precede the bootstrap so it can set them');
assert(base.includes("asset('theme.js')"), 'base.html must load the controller through asset()');

const unhandled = [];
process.on('unhandledRejection', reason => unhandled.push(reason));
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };

function element(attrs = {}) {
  const attributes = new Map(Object.entries(attrs)), classes = new Set();
  return {
    getAttribute: name => attributes.has(name) ? attributes.get(name) : null,
    setAttribute: (name, value) => { attributes.set(name, String(value)); },
    removeAttribute: name => { attributes.delete(name); },
    classList: {add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name)},
    get className() { return [...classes].join(' '); },
  };
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return {promise, resolve, reject};
}

function environment({stored = null, getThrows = false, setThrows = false, systemDark = false,
                      mediaThrows = false, reduced = false, api = 'deferred', hidden = false} = {}) {
  const store = new Map();
  if (stored !== null) store.set('amorino_theme', stored);
  const writes = [];
  const localStorage = {
    getItem(key) { if (getThrows) throw new Error('SecurityError'); return store.has(key) ? store.get(key) : null; },
    setItem(key, value) { if (setThrows) throw new Error('QuotaExceededError'); writes.push(value); store.set(key, String(value)); },
  };
  let dark = systemDark, reduce = reduced;
  const schemeListeners = [];
  const matchMedia = query => {
    if (mediaThrows) throw new Error('matchMedia unavailable');
    if (query === '(prefers-color-scheme: dark)')
      return {get matches() { return dark; }, addEventListener: (type, fn) => schemeListeners.push(fn)};
    if (query === '(prefers-reduced-motion: reduce)') return {get matches() { return reduce; }, addEventListener() {}};
    return {matches: false, addEventListener() {}};
  };
  let now = 0, nextId = 1;
  const timers = new Map();
  const setTimeout = (fn, ms) => { const id = nextId++; timers.set(id, {fn, at: now + (ms || 0)}); return id; };
  const clearTimeout = id => { timers.delete(id); };

  const root = element();
  const metas = {'color-scheme': element({content: 'light'}), 'theme-color': element({content: '#2c1810'})};
  const toggles = [0, 1].map(i => Object.assign(element({'aria-pressed': 'false'}), {
    getBoundingClientRect: () => ({left: 1000 + i * 100, right: 1044 + i * 100, top: 4, bottom: 48}),
  }));
  const docListeners = {}, winListeners = {}, transitions = [];
  const document = {
    documentElement: root,
    visibilityState: hidden ? 'hidden' : 'visible',
    querySelector(selector) { const m = selector.match(/^meta\[name="([^"]+)"\]$/); return m ? metas[m[1]] || null : null; },
    querySelectorAll(selector) { return selector === '.theme-toggle' ? toggles : []; },
    addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
  };
  if (api === 'deferred') document.startViewTransition = update => {
    const ready = deferred(), done = deferred(), finished = deferred();
    const vt = {
      ready: ready.promise, updateCallbackDone: done.promise, finished: finished.promise, skipped: false,
      skipTransition() { vt.skipped = true; },
      run() { update(); done.resolve(); },            // the browser applies the update
      start() { ready.resolve(); },                    // animation begins
      finish() { finished.resolve(); },
      abort() { ready.reject(new Error('AbortError: skipped')); },
      fail() { const e = new Error('update failed'); ready.reject(e); done.reject(e); finished.reject(e); },
    };
    transitions.push(vt);
    return vt;
  };
  if (api === 'throws') document.startViewTransition = () => { throw new Error('InvalidStateError'); };
  const context = {
    document, localStorage, matchMedia, setTimeout, clearTimeout,
    getComputedStyle: () => ({getPropertyValue: name => name === '--theme-fade-duration' ? ' 260ms' : ''}),
    addEventListener(type, fn) { (winListeners[type] ||= []).push(fn); },
  };
  context.window = context;
  vm.createContext(context);
  const env = {
    root, metas, toggles, store, writes, transitions, timers, context,
    boot() { vm.runInContext(bootstrap, context); return env; },
    load() { vm.runInContext(controller, context); return env; },
    start() { return env.boot().load(); },
    toggle() { context.toggleTheme({}); },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        let next = null;
        for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
        if (!next) break;
        timers.delete(next[0]); now = next[1].at; next[1].fn();
      }
      now = end;
    },
    setSystem(value) { dark = value; schemeListeners.forEach(fn => fn({matches: value})); },
    setReduced(value) { reduce = value; },
    pageshow(persisted) { (winListeners.pageshow || []).forEach(fn => fn({persisted})); },
    pointer(type, x, y, target = root) {
      const event = {target, clientX: x, clientY: y, prevented: false, preventDefault() { this.prevented = true; }};
      (docListeners[type] || []).forEach(fn => fn(event));
      return event;
    },
    clickToggle(i = 0, target = root) {
      const box = toggles[i].getBoundingClientRect(), x = box.left + 10, y = box.top + 10;
      const down = env.pointer('mousedown', x, y, target);
      env.pointer('click', x, y, target);
      return down;
    },
    shown() { return root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'; },
    requestedOnControls() { return toggles[0].getAttribute('aria-pressed') === 'true' ? 'dark' : 'light'; },
  };
  return env;
}

function assertSettled(env, theme, saved) {
  const dark = theme === 'dark', label = dark ? 'Ativar modo claro' : 'Ativar modo escuro';
  assert.equal(env.root.getAttribute('data-theme'), dark ? 'dark' : null, 'rendered theme');
  assert.equal(env.root.className, '', 'transition classes cleaned up');
  assert.equal(env.metas['color-scheme'].getAttribute('content'), theme, 'native color-scheme');
  assert.equal(env.metas['theme-color'].getAttribute('content'), dark ? '#120d09' : '#2c1810', 'theme-color meta');
  for (const toggle of env.toggles) {
    assert.equal(toggle.getAttribute('aria-pressed'), String(dark), 'pressed state');
    assert.equal(toggle.getAttribute('aria-label'), label, 'accessible label');
    assert.equal(toggle.getAttribute('title'), label, 'title');
  }
  if (saved !== undefined) assert.equal(env.store.has('amorino_theme') ? env.store.get('amorino_theme') : null, saved, 'saved choice');
  assert.equal(env.timers.size, 0, 'no timers left behind');
}
const opposite = theme => theme === 'dark' ? 'light' : 'dark';

const tests = {
  'bootstrap applies a saved choice and both metas before paint, saving nothing'() {
    const env = environment({stored: 'dark'}).boot();
    assert.equal(env.shown(), 'dark');
    assert.equal(env.metas['color-scheme'].getAttribute('content'), 'dark');
    assert.equal(env.metas['theme-color'].getAttribute('content'), '#120d09');
    assert.deepEqual(env.writes, []);
  },
  'bootstrap: explicit light outranks a dark system, native controls stay light'() {
    const env = environment({stored: 'light', systemDark: true}).boot();
    assert.equal(env.shown(), 'light');
    assert.equal(env.metas['color-scheme'].getAttribute('content'), 'light');
  },
  'bootstrap: missing or unknown saved values defer to the system'() {
    for (const stored of [null, 'sepia', '']) for (const systemDark of [true, false]) {
      const env = environment({stored, systemDark}).boot();
      assert.equal(env.shown(), systemDark ? 'dark' : 'light', `stored=${stored} system=${systemDark}`);
      assert.deepEqual(env.writes, []);
    }
  },
  'bootstrap: storage and media failures are independent; both failing means light'() {
    assert.equal(environment({getThrows: true, systemDark: true}).boot().shown(), 'dark');
    assert.equal(environment({stored: 'dark', mediaThrows: true}).boot().shown(), 'dark');
    assert.equal(environment({mediaThrows: true}).boot().shown(), 'light');
    assert.equal(environment({getThrows: true, mediaThrows: true}).boot().shown(), 'light');
  },
  'startup syncs controls and metadata without recording a choice'() {
    assertSettled(environment({stored: 'dark'}).start(), 'dark', 'dark');
    const system = environment({systemDark: true}).start();
    assertSettled(system, 'dark', null);
    assert.deepEqual(system.writes, []);
  },
  'without the API: colour fade, saved, cleaned up by its own timer'() {
    const env = environment({api: 'none'}).start();
    env.toggle();
    assert.equal(env.shown(), 'dark');
    assert.equal(env.root.className, 'theme-fade');
    env.advance(319);
    assert.equal(env.root.className, 'theme-fade', 'fade holds for the CSS duration');
    env.advance(1);
    assertSettled(env, 'dark', 'dark');
    env.toggle(); env.advance(400);
    assertSettled(env, 'light', 'light');
  },
  'colour fade: rapid requests retarget with a single timer'() {
    for (const [count, expected] of [[5, 'dark'], [6, 'light']]) {
      const env = environment({api: 'none'}).start();
      for (let i = 0; i < count; i++) { env.toggle(); assert.equal(env.timers.size, 1); env.advance(40); }
      env.advance(400);
      assertSettled(env, expected, expected);
    }
  },
  'a synchronous API error falls back to the colour fade'() {
    const env = environment({api: 'throws'}).start();
    env.toggle();
    assert.equal(env.root.className, 'theme-fade');
    env.advance(400);
    assertSettled(env, 'dark', 'dark');
  },
  async 'snapshot fade: controls answer at once, DOM follows the update, both directions'() {
    const env = environment().start();
    for (const theme of ['dark', 'light']) {
      env.toggle();
      const vt = env.transitions.at(-1);
      assert.equal(env.root.className, 'theme-switching');
      assert.equal(env.requestedOnControls(), theme, 'controls reflect the request immediately');
      assert.equal(env.store.get('amorino_theme'), theme, 'explicit choice saved at request time');
      assert.equal(env.shown(), opposite(theme), 'DOM waits for the snapshot');
      vt.run();
      assert.equal(env.shown(), theme);
      vt.start(); vt.finish(); await flush();
      assertSettled(env, theme, theme);
    }
    assert.equal(env.transitions.length, 2);
  },
  async 'rapid requests during a snapshot coalesce: newest wins, no backlog'() {
    for (const start of ['light', 'dark']) for (const [count, early] of [[5, true], [6, true], [5, false], [6, false]]) {
      const env = environment({stored: start}).start();
      env.toggle();
      const first = env.transitions[0];
      if (early) first.run();                         // update before the extra clicks
      for (let i = 1; i < count; i++) env.toggle();
      assert.equal(env.transitions.length, 1, 'requests during a fade start nothing new');
      if (!early) first.run();                        // late update applies the newest request
      first.start(); first.finish(); await flush();
      const expected = count % 2 ? opposite(start) : start;
      if (env.transitions.length === 2) {             // one follow-up, at most
        env.advance(1000);
        assert.equal(env.root.className, 'theme-switching', 'an older timer cannot end a newer fade');
        const second = env.transitions[1];
        second.run(); second.start(); second.finish(); await flush();
      }
      assert(env.transitions.length <= 2);
      assertSettled(env, expected, expected);
    }
  },
  async 'a skipped transition settles without a second visible change'() {
    const env = environment().start();
    env.toggle();
    const vt = env.transitions[0];
    vt.run(); vt.abort(); await flush();
    assert.equal(env.shown(), 'dark');
    vt.finish(); await flush();
    assertSettled(env, 'dark', 'dark');
    assert.equal(env.transitions.length, 1, 'no retry after the theme already changed');
  },
  async 'a failed transition that never applied its update renders directly, once'() {
    const env = environment().start();
    env.toggle();
    env.transitions[0].fail(); await flush();
    assertSettled(env, 'dark', 'dark');
    assert.equal(env.transitions.length, 1, 'no retry loop through the failing route');
  },
  async 'a transition that never settles is abandoned; its late callbacks change nothing'() {
    const env = environment().start();
    env.toggle();
    const stuck = env.transitions[0];
    env.advance(260 + 1500);
    assert.equal(stuck.skipped, true);
    assertSettled(env, 'dark', 'dark');
    env.toggle();                                     // later switches avoid the stuck route
    assert.equal(env.transitions.length, 1);
    assert.equal(env.root.className, 'theme-fade');
    stuck.run();                                      // stale update: must not apply anything
    assert.equal(env.shown(), 'light');
    stuck.finish(); await flush();                    // stale completion: must not clean up the newer fade
    assert.equal(env.root.className, 'theme-fade');
    env.advance(400);
    assertSettled(env, 'light', 'light');
  },
  'a hidden page switches immediately'() {
    const env = environment({hidden: true}).start();
    env.toggle();
    assert.equal(env.transitions.length, 0);
    assertSettled(env, 'dark', 'dark');
  },
  'reduced motion switches immediately and stops a running colour fade'() {
    const env = environment({reduced: true}).start();
    env.toggle();
    assert.equal(env.transitions.length, 0);
    assertSettled(env, 'dark', 'dark');
    const fading = environment({api: 'none'}).start();
    fading.toggle();
    assert.equal(fading.root.className, 'theme-fade');
    fading.setReduced(true);
    fading.toggle();
    assertSettled(fading, 'light', 'light');
  },
  'system changes are followed only until a choice is made'() {
    const env = environment().start();
    env.setSystem(true);
    assertSettled(env, 'dark', null);
    assert.deepEqual(env.writes, [], 'following the system records nothing');
    env.toggle(); env.advance(400);
    assert.equal(env.transitions.length, 1);
  },
  async 'an explicit choice outranks later system changes, with or without storage'() {
    for (const blocked of [false, true]) {
      const env = environment({getThrows: blocked, setThrows: blocked}).start();
      env.toggle();
      const vt = env.transitions[0];
      vt.run(); vt.start(); vt.finish(); await flush();
      env.setSystem(false); env.setSystem(true); env.setSystem(false);
      assertSettled(env, 'dark', blocked ? null : 'dark');
    }
  },
  'back/forward cache restore adopts a choice saved by another page'() {
    const env = environment({stored: 'light', systemDark: true}).start();
    env.store.set('amorino_theme', 'dark');
    env.pageshow(false);
    assertSettled(env, 'light');
    env.pageshow(true);
    assertSettled(env, 'dark', 'dark');
  },
  async 'clicks that land on <html> during a snapshot fade reach the toggle'() {
    const env = environment().start();
    env.toggle();                                     // fade to dark running
    const down = env.clickToggle(1);
    assert.equal(down.prevented, true, 'the press keeps focus on the toggle');
    assert.equal(env.requestedOnControls(), 'light');
    env.pointer('click', 10, 300);                    // elsewhere: ignored
    env.clickToggle(0, env.toggles[0]);               // a real target is left to its own onclick
    assert.equal(env.requestedOnControls(), 'light');
    const vt = env.transitions[0];
    vt.run(); vt.start(); vt.finish(); await flush();
    assertSettled(env, 'light', 'light');
    // Pressed during a fade, released after it ended: the press decides.
    env.toggle();
    const press = env.pointer('mousedown', 1010, 14);
    const second = env.transitions[1];
    second.run(); second.start(); second.finish(); await flush();
    env.pointer('click', 1010, 14);
    assert.equal(press.prevented, true);
    assert.equal(env.requestedOnControls(), 'light');
    env.transitions[2].run(); env.transitions[2].start(); env.transitions[2].finish(); await flush();
    assertSettled(env, 'light', 'light');
    // Outside a fade the forwarding stays out of the way.
    const idle = env.clickToggle(0);
    assert.equal(idle.prevented, false);
    assertSettled(env, 'light', 'light');
  },
  async 'five toggles end on the opposite theme and six on the start, on every route'() {
    for (const api of ['deferred', 'none', 'throws']) for (const start of ['light', 'dark']) for (const count of [5, 6]) {
      for (const outcome of api === 'deferred' ? ['finish', 'abort', 'fail'] : ['finish']) {
        const env = environment({api, stored: start}).start();
        for (let i = 0; i < count; i++) {
          env.toggle();
          env.advance(30);
          const vt = env.transitions.at(-1);
          if (vt && !vt.handled && i % 2) {         // settle some transitions mid-sequence
            vt.handled = true;
            if (outcome === 'fail') vt.fail(); else { vt.run(); if (outcome === 'abort') vt.abort(); else vt.start(); vt.finish(); }
            await flush();
          }
        }
        for (let guard = 0; guard < 4; guard++) {
          const vt = env.transitions.at(-1);
          if (!vt || vt.handled) break;
          vt.handled = true; vt.run(); vt.start(); vt.finish(); await flush();
        }
        env.advance(400);
        const expected = count % 2 ? opposite(start) : start;
        assertSettled(env, expected, expected);
      }
    }
  },
};

(async () => {
  for (const [name, test] of Object.entries(tests)) {
    await test();
    console.log('PASS ' + name);
  }
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(unhandled, [], 'no unhandled promise rejections');
  console.log('Theme controller startup, persistence, transitions, failures and races verified.');
})().catch(error => { console.error(error); process.exitCode = 1; });
