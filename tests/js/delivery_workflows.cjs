/* Executes the real Descargas script with a small synthetic DOM/API boundary.
 * No Flask import, server, filesystem database or production data is used. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Node {
  constructor(tag, document, classes = '') {
    this.tag = tag; this.document = document; this.parent = null; this.children = [];
    this.dataset = {}; this.attributes = new Map(); this.disabled = false; this.hidden = false;
    this._text = ''; this._classes = new Set(); this.className = classes;
    this.classList = {
      add: (...values) => values.forEach(value => this._classes.add(value)),
      remove: (...values) => values.forEach(value => this._classes.delete(value)),
      contains: value => this._classes.has(value),
      toggle: (value, on) => on ? this._classes.add(value) : this._classes.delete(value),
    };
  }
  set className(value) { this._classes = new Set(value.split(/\s+/).filter(Boolean)); }
  get className() { return Array.from(this._classes).join(' '); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  matches(selector) {
    return selector.split(',').some(raw => {
      const item = raw.trim();
      if (item.startsWith('.')) return this._classes.has(item.slice(1));
      if (item === 'input[type="time"]') return this.tag === 'input' && this.type === 'time';
      return this.tag === item;
    });
  }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector) || null; }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [child, ...child.querySelectorAll('*')]).filter(child => selector === '*' || child.matches(selector));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  insertBefore(child, marker) { child.parent = this; this.children.splice(this.children.indexOf(marker), 0, child); return child; }
  remove() { this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener() {}
  focus() { if (!this.disabled) this.document.activeElement = this; }
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
const response = body => ({json: async () => body});

function harness(days = ['Ter 6'], eligible = true) {
  const ids = {}, listeners = [], requests = [], messages = [];
  const document = {
    getElementById: id => ids[id] || null,
    createElement: tag => new Node(tag, document),
    createTextNode: text => { const node = new Node('#text', document); node.textContent = text; return node; },
    addEventListener: (name, listener) => listeners.push({name, listener}),
  };
  function node(tag, classes = '', parent = null) {
    const item = new Node(tag, document, classes);
    if (parent) parent.appendChild(item);
    return item;
  }
  const context = vm.createContext({
    document, console,
    btnBusy: (button, busy) => { button.disabled = busy; button.classList.toggle('is-busy', busy); },
    toast: (message, error) => messages.push({message, error}),
    fetch: async (url, init) => { requests.push({url, body: init.body ? JSON.parse(init.body) : null}); return context.reply(url, init); },
  });
  context.reply = async url => response(url.endsWith('/horario') ? {ok: true, horario: '10:00-14:00', updated: 2} : {ok: true, id: 22});
  const template = fs.readFileSync(path.join(__dirname, '..', '..', 'templates', 'descargas.html'), 'utf8');
  const script = template.split('{% block scripts %}')[1].split('{% endblock %}')[0]
    .replace(/<\/?script>/g, '').replace('{{ semana_str|tojson }}', JSON.stringify('qa-week'));
  assert(!script.includes('{{'), 'Unexpected unrendered Jinja in Descargas script');
  vm.runInContext(script + '\nglobalThis.pendingWrites = () => deliveryWrites.size;', context);
  ids['stat-asg'] = node('span'); ids['stat-asg'].textContent = days.length;
  ids['stat-asg-label'] = node('span');
  const scopeByDay = new Map();
  const stores = days.map((dia, index) => {
    if (!scopeByDay.has(dia)) scopeByDay.set(dia, node('section', 'dz-day'));
    const day = scopeByDay.get(dia);
    const store = node('div', 'dz-store', day);
    store.dataset = {dia, loja: index === 0 ? 'carmelitas' : 'gaia'};
    const windowWrap = node('div', 'dz-window-wrap', store);
    const window = node('span', 'dz-window', windowWrap); window.textContent = '08:00-12:00';
    const edit = node('button', 'dz-hr-edit', windowWrap);
    const editor = node('div', 'dz-hr-editor', store); editor.hidden = true;
    const from = node('input', 'dz-hr-from', editor); from.type = 'time'; from.value = '';
    const to = node('input', 'dz-hr-to', editor); to.type = 'time'; to.value = '';
    const save = node('button', 'dz-hr-ok', editor);
    const cancel = node('button', 'dz-hr-cancel', editor);
    const status = node('p', 'dz-status', store); status.hidden = true;
    const assigned = node('div', 'dz-assigned', store);
    const chip = context._makeChip(index + 11, 'QA01', 'Inês Monteiro'); chip.dataset.eligible = String(eligible);
    assigned.appendChild(chip);
    const assignedEmpty = node('span', 'dz-empty-assigned', assigned); assignedEmpty.hidden = true;
    const remove = chip.querySelector('button');
    const pool = node('div', 'dz-pool', store);
    const add = context._makePill('QA02', 'Clara Matos'); pool.appendChild(add);
    const poolEmpty = node('span', 'dz-pool-empty', pool); poolEmpty.hidden = true;
    return {day, store, windowWrap, window, edit, editor, from, to, save, cancel, status, assigned, chip, remove, pool, add};
  });
  return {context, ids, stores, requests, messages, listeners, document};
}
async function test(name, run) { await run(); console.log('PASS ' + name); }
async function main() {
  for (const failure of ['rejected', 'network']) {
    const failing = async () => {
      if (failure === 'network') throw new Error('offline');
      return response({ok: false, erro: 'Sobreposição'});
    };
    await test(`delivery add ${failure} preserves eligible pill, assignments and counter`, async () => {
      const h = harness(), s = h.stores[0]; h.context.reply = failing;
      await h.context.assignPerson(s.add);
      assert.equal(s.add.parent, s.pool);
      assert.equal(s.assigned.querySelectorAll('.dz-chip').length, 1);
      assert.equal(h.ids['stat-asg'].textContent, '1');
      assert.equal(s.add.disabled, false);
      assert.equal(h.context.pendingWrites(), 0);
      assert.equal(s.status.hidden, false);
      assert.equal(s.status.classList.contains('is-error'), true);
    });
    await test(`delivery remove ${failure} preserves original chip and recovers controls`, async () => {
      const h = harness(), s = h.stores[0]; h.context.reply = failing;
      await h.context.removeDescarga(11, s.remove);
      assert.equal(s.chip.parent, s.assigned);
      assert.equal(s.pool.querySelectorAll('.dz-add').length, 1);
      assert.equal(h.ids['stat-asg'].textContent, '1');
      assert.equal(s.remove.disabled, false);
      assert.equal(h.context.pendingWrites(), 0);
      assert.equal(s.day.getAttribute('aria-busy'), undefined);
    });
    await test(`delivery time ${failure} retains saved window and editable draft`, async () => {
      const h = harness(), s = h.stores[0]; h.context.reply = failing;
      h.context.editHorario(s.edit); s.from.value = '10:00'; s.to.value = '14:00';
      await h.context.saveHorario(s.save);
      assert.equal(s.window.textContent, '08:00-12:00');
      assert.equal(s.editor.hidden, false);
      assert.equal(s.windowWrap.hidden, true);
      assert.equal(s.from.value, '10:00'); assert.equal(s.to.value, '14:00');
      assert.equal(s.from.disabled, false); assert.equal(s.save.disabled, false);
      assert.equal(h.context.pendingWrites(), 0);
      h.context.cancelHorario(s.cancel);
      assert.equal(s.windowWrap.hidden, false);
      assert.equal(s.window.textContent, '08:00-12:00');
    });
  }
  await test('delivery remove blocks duplicates and returns an eligible person once', async () => {
    const h = harness(), s = h.stores[0], gate = deferred(); h.context.reply = async () => gate.promise;
    const pending = h.context.removeDescarga(11, s.remove);
    await h.context.removeDescarga(11, s.remove);
    assert.equal(h.requests.length, 1);
    assert.equal(s.chip.parent, s.assigned);
    gate.resolve(response({ok: true})); await pending;
    assert.equal(s.chip.parent, null);
    assert.equal(s.pool.querySelectorAll('.dz-add').filter(pill => pill.dataset.sigla === 'QA01').length, 1);
    assert.equal(h.ids['stat-asg'].textContent, '0');
    assert.equal(h.ids['stat-asg-label'].textContent, 'Atribuições');
    assert.equal(h.context.pendingWrites(), 0);
  });
  await test('removing a person without an opt-in never puts them into eligible pool', async () => {
    const h = harness(['Ter 6'], false), s = h.stores[0];
    await h.context.removeDescarga(11, s.remove);
    assert.equal(s.chip.parent, null);
    assert.equal(s.pool.querySelectorAll('.dz-add').some(pill => pill.dataset.sigla === 'QA01'), false);
    assert.equal(h.document.activeElement, s.edit);
  });
  await test('same-day writes cannot race across stores or overwrite an in-flight time edit', async () => {
    const h = harness(['Ter 6', 'Ter 6']), [s, other] = h.stores, gate = deferred();
    h.context.reply = async () => gate.promise;
    h.context.editHorario(s.edit); s.from.value = '10:00'; s.to.value = '14:00';
    const pending = h.context.saveHorario(s.save);
    await h.context.saveHorario(s.save);
    await h.context.assignPerson(other.add);
    assert.equal(h.requests.length, 1);
    assert.equal(other.add.disabled, true);
    gate.resolve(response({ok: true, horario: '10:00-14:00', updated: 2})); await pending;
    assert.equal(s.window.textContent, '10:00-14:00');
    assert.equal(s.editor.hidden, true);
    assert.equal(other.add.disabled, false);
    assert.equal(s.edit.getAttribute('aria-expanded'), 'false');
    assert(h.messages.some(message => message.message === 'Horário atualizado · 2 atribuições'));
  });
  await test('other delivery days remain usable while a write is pending', async () => {
    const h = harness(['Ter 6', 'Qua 7']), [s, other] = h.stores, gate = deferred();
    h.context.reply = async url => url.endsWith('/horario') ? gate.promise : response({ok: true, id: 22});
    h.context.editHorario(s.edit); s.from.value = '10:00'; s.to.value = '14:00';
    const pending = h.context.saveHorario(s.save);
    assert.equal(other.add.disabled, false);
    await h.context.assignPerson(other.add);
    assert.equal(h.requests.length, 2);
    assert.equal(other.add.parent, null);
    gate.resolve(response({ok: false, erro: 'Sobreposição'})); await pending;
    assert.equal(s.window.textContent, '08:00-12:00');
    assert.equal(h.context.pendingWrites(), 0);
  });
  await test('assignment blocks duplicate clicks and preserves initially disabled controls', async () => {
    const h = harness(), s = h.stores[0], gate = deferred(); h.context.reply = async () => gate.promise;
    s.edit.disabled = true;
    const pending = h.context.assignPerson(s.add);
    await h.context.assignPerson(s.add);
    assert.equal(h.requests.length, 1);
    gate.resolve(response({ok: true, id: 22})); await pending;
    assert.equal(s.assigned.querySelectorAll('.dz-chip').length, 2);
    assert.equal(s.add.parent, null);
    assert.equal(h.ids['stat-asg'].textContent, '2');
    assert.equal(s.edit.disabled, true);
    assert.equal(h.context.pendingWrites(), 0);
  });
  await test('invalid equal delivery times produce no write and keep editor open', async () => {
    const h = harness(), s = h.stores[0];
    h.context.editHorario(s.edit); s.from.value = '08:00'; s.to.value = '08:00';
    await h.context.saveHorario(s.save);
    assert.equal(h.requests.length, 0);
    assert.equal(s.editor.hidden, false);
    assert.equal(s.window.textContent, '08:00-12:00');
  });
}
main().catch(error => { console.error(error); process.exitCode = 1; });
