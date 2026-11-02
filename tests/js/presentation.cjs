// Exercise the shipped presentation controller with a small DOM boundary.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/management.js'), 'utf8');
function harness(url = 'https://app.example/supervisor/visao-geral?semana=chosen') {
  const ids = ['start','toolbar','pager','prev','next','close','position'].map(s => 'mg-present-' + s)
    .concat(['mg-week-summary','mg-week-metrics','mg-present-message','mg-layout','mg-coverage','mg-priorities','mg-hours']);
  const nodes = Object.fromEntries(ids.map(id => [id, {
    hidden: false, disabled: false, dataset: {}, attrs: {}, handlers: {},
    addEventListener(name, fn) { this.handlers[name] = fn; },
    setAttribute(name, value) { this.attrs[name] = value; },
    focus() { nodes.focus = this; },
  }]));
  const steps = [0,1,2].map(i => ({dataset: {presentationStep: String(i)}, attrs: {}, handlers: {},
    addEventListener(name, fn) { this.handlers[name] = fn; },
    setAttribute(name, value) { this.attrs[name] = value; },
  }));
  const keys = {}, events = {}, classes = new Set();
  const location = {href: url};
  const body = {dataset: {}, classList: {add: s => classes.add(s), remove: s => classes.delete(s)}};
  const context = {URL, Map, Number, Array, location, document: {
    body, getElementById: id => nodes[id], querySelectorAll: () => steps,
    addEventListener: (event, handler) => { keys[event] = handler; },
  }, history: {replaceState: (_s, _t, next) => { location.href = next.href; }},
  window: {scrollTo() {}, addEventListener: (event, handler) => { events[event] = handler; }}};
  vm.createContext(context); vm.runInContext(source, context);
  return {nodes, steps, keys, events, location, classes, body,
    click(id) { nodes[id].handlers.click(); },
    key(key, tagName = 'BUTTON') { keys.keydown({key, target: {tagName}, preventDefault() {}}); }};
}
const h = harness();
h.click('mg-present-start');
assert(h.classes.has('mg-presenting'));
assert.equal(h.nodes['mg-layout'].hidden, true);
assert.equal(new URL(h.location.href).searchParams.get('semana'), 'chosen');
h.click('mg-present-next');
assert.equal(h.nodes['mg-coverage'].hidden, false);
assert.equal(h.nodes['mg-week-metrics'].hidden, true);
h.key('ArrowRight', 'SELECT'); // Editing a control must not change chapter.
assert.equal(h.body.dataset.presentationStep, '1');
h.key('ArrowRight');
assert.equal(h.nodes['mg-hours'].hidden, false);
assert.equal(h.nodes['mg-priorities'].hidden, false);
assert.equal(h.nodes['mg-present-next'].disabled, true);
h.events.beforeprint(); h.events.beforeprint();
assert.equal(h.nodes['mg-week-metrics'].hidden, false);
h.events.afterprint();
assert.equal(h.nodes['mg-week-metrics'].hidden, true);
assert.equal(h.nodes['mg-hours'].hidden, false);
h.key('Escape');
assert.equal(h.classes.has('mg-presenting'), false);
assert.equal(h.nodes['mg-coverage'].hidden, false);
assert.equal(h.nodes['mg-week-metrics'].hidden, false);
assert.equal(h.nodes.focus, h.nodes['mg-present-start']);
assert.equal(new URL(h.location.href).searchParams.has('apresentacao'), false);
const restored = harness('https://app.example/supervisor/visao-geral?semana=chosen&apresentacao=1&capitulo=2');
assert.equal(restored.body.dataset.presentationStep, '2');
assert.equal(restored.nodes['mg-hours'].hidden, false);
console.log('Presentation navigation, focus, refresh, keyboard and print restoration verified.');
