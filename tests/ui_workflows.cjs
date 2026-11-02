/* Run with node tests/ui_workflows.cjs. No app import or database access.
 * Execute the actual template functions; stub only browser/API boundaries. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function section(file, start, end) {
  const source = fs.readFileSync(path.join(__dirname, '..', 'templates', file), 'utf8');
  const first = source.indexOf(start), last = source.indexOf(end, first);
  assert(first >= 0 && last > first, `Missing template section: ${start}`);
  return source.slice(first, last);
}
function element() {
  const classes = new Set(), attributes = new Map();
  return {
    disabled: false, textContent: '', dataset: {}, hidden: false, style: {},
    classList: {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      contains: name => classes.has(name),
      toggle: (name, force) => {
        const on = force === undefined ? !classes.has(name) : force;
        on ? classes.add(name) : classes.delete(name);
        return on;
      },
    },
    setAttribute: (name, value) => attributes.set(name, value),
    removeAttribute: name => attributes.delete(name),
    getAttribute: name => attributes.get(name),
  };
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
const response = data => ({json: async () => data});

function scheduleHarness() {
  const chips = new Map(), additions = [], hourDeltas = [], requests = [], messages = [];
  function chip(id, sigla, nota, tipo) {
    const node = element();
    node.dataset = {id: String(id), sigla, tipo};
    node.querySelector = selector => selector === '.chip-note' && nota ? {textContent: nota} : null;
    node.remove = () => { if (chips.get(String(id)) === node) chips.delete(String(id)); };
    chips.set(String(id), node);
    return node;
  }
  const context = vm.createContext({
    console, CSS: {escape: String},
    document: {querySelector: selector => chips.get(selector.match(/data-id="([^"]+)"/)?.[1]) || null},
    addChipToGrid: (loja, horario, dia, sigla, nota, id, tipo) => {
      additions.push({loja, horario, dia, sigla, nota, id: String(id), tipo});
      chip(id, sigla, nota, tipo);
    },
    updateSummary: () => {}, adjustHours: (sigla, horario, delta) => hourDeltas.push({sigla, horario, delta}),
    renderPanel: () => {}, refreshEmpties: () => {}, fetchEmpties: async () => {},
    highlightChips: () => {}, showResultDrawer: () => {}, setFilter: () => {},
    toast: (message, error) => messages.push({message, error}), avisoToast: () => {},
    fetch: async (url, init) => { requests.push({url, init}); return context.reply(url, init); },
  });
  context.reply = async () => response({ok: true, id: 71, tipo_contrato: 'contrato'});
  vm.runInContext(`
    const FUNC_INFO = {QA01: {tipo_contrato: 'contrato'}}, SEMANA = 'qa-week';
    const assigned = {}, movesInFlight = new Set();
    let panelOpen = false, activeSearch = null, activeFilter = 'todos';
    let _lastAct = null, _undoInFlight = false, _assignInFlight = false, _drag = {};
    function aKey(loja, horario, dia) { return loja + '|||' + horario + '|||' + dia; }
    ${section('escala.html', 'function applyMovedChip(', '// Fold a server-side soft warning')}
    ${section('escala.html', 'async function onDrop(', '// ── Click an empty cell')}
    globalThis.state = {assigned, movesInFlight, setDrag: value => {_drag = value}, action: () => _lastAct};
  `, context);
  const from = {loja: 'carmelitas', horario: '08:00-16:30', dia: 'Seg 5'};
  const to = {loja: 'gaia', horario: '10:00-14:00', dia: 'Ter 6'};
  const key = place => `${place.loja}|||${place.horario}|||${place.dia}`;
  const drag = {id: '71', sigla: 'QA01', fromL: from.loja, fromH: from.horario, fromD: from.dia, fromPanel: false};
  const original = chip(71, 'QA01', 'Troca autorizada', 'contrato');
  context.state.assigned[key(from)] = new Set(['QA01']);
  const drop = () => {
    context.state.setDrag({...drag});
    return context.onDrop({preventDefault() {}, currentTarget: {querySelector: () => element()}}, to.loja, to.horario, to.dia);
  };
  return {context, chips, original, additions, hourDeltas, requests, messages, from, to, key, drop};
}

function teamHarness() {
  const ids = Object.fromEntries(['btn-save-all', 'savebar', 'sb-txt', 'h-QA01'].map(id => [id, element()]));
  ids['h-QA01'].checkValidity = () => true;
  const stickySave = element(), card = element();
  card.classList.add('card-modified');
  const requests = [], messages = [];
  const controls = [ids['btn-save-all'], ids['h-QA01'], stickySave];
  const context = vm.createContext({
    console, CSS: {escape: String},
    document: {
      getElementById: id => ids[id] || null,
      querySelector: () => card,
      querySelectorAll: () => controls,
    },
    toast: (message, error) => messages.push({message, error}),
    setTimeout: () => {}, location: {reload() {}},
    fetch: async (url, init) => { requests.push({url, init}); return context.reply(url, init); },
  });
  context.reply = async () => response({ok: true});
  vm.runInContext(`
    ${section('equipa.html', 'const pendingChanges =', '// Leaving the page with edits')}
    globalThis.state = {pendingChanges, saving: () => teamSaveInProgress};
  `, context);
  return {context, ids, controls, requests, messages, card};
}

function searchHarness() {
  const chips = ['QA01', 'QA01', 'QA02'].map(sigla => {
    const node = element();
    node.dataset = {sigla, tipo: sigla === 'QA02' ? 'turnos' : 'contrato'};
    return node;
  });
  const ids = Object.fromEntries(['chip-search', 'search-count', 'search-bar', 'search-clear', 'result-drawer', 'tag-todos', 'tag-contrato', 'tag-turnos'].map(id => {
    const node = element(); node.id = id; node.value = '';
    return [id, node];
  }));
  const people = {QA01: {nome: 'Clára Matos'}, QA02: {nome: 'Inês Monteiro'}};
  const context = vm.createContext({
    document: {
      getElementById: id => ids[id],
      querySelectorAll: selector => selector === '.chip' ? chips : selector === '.chip.match' ? chips.filter(chip => chip.classList.contains('match')) : Object.values(ids).filter(node => node.id.startsWith('tag-')),
    },
    FUNC_INFO: people, ALL_EMPS: Object.entries(people).map(([sigla, info]) => ({sigla, ...info})),
    buildAcList() {}, clearSearch() {}, showResultDrawer() {},
  });
  vm.runInContext(`
    let activeFilter = 'todos', activeSearch = null;
    ${section('escala.html', 'const normalizeScheduleSearch =', '// Search / filter state')}
    ${section('escala.html', 'function onSearchInput(', 'function onSearchKey(')}
    ${section('escala.html', 'function highlightChips(', 'function showResultDrawer(')}
    ${section('escala.html', 'function setFilter(', '\n{% endif %}')}
    globalThis.state = {selected: () => activeSearch, select: value => {activeSearch = value}};
  `, context);
  return {context, chips, ids};
}

async function test(name, body) {
  await body();
  console.log(`PASS ${name}`);
}
async function main() {
  for (const failure of ['rejected', 'network']) {
    await test(`atomic move ${failure} preserves original chip, note, hours and assignments`, async () => {
      const h = scheduleHarness();
      h.context.reply = async () => {
        if (failure === 'network') throw new Error('offline');
        return response({ok: false, erro: 'Sobreposição'});
      };
      await h.drop();
      assert.equal(h.chips.get('71'), h.original);
      assert.equal(h.original.querySelector('.chip-note').textContent, 'Troca autorizada');
      assert(h.context.state.assigned[h.key(h.from)].has('QA01'));
      assert.equal(h.context.state.assigned[h.key(h.to)], undefined);
      assert.equal(h.additions.length, 0);
      assert.equal(h.hourDeltas.length, 0);
      assert.equal(h.context.state.movesInFlight.size, 0);
      assert.equal(h.original.classList.contains('is-moving'), false);
      assert.equal(h.requests[0].url, '/api/turno/move/71');
      assert.equal(h.requests.length, 1);
    });
  }
  await test('move captures drag state before await and blocks duplicate moves', async () => {
    const h = scheduleHarness(), gate = deferred();
    h.context.reply = async () => gate.promise;
    const pending = h.drop();
    h.context.state.setDrag({sigla: ''}); // browser dragend fires during request
    await h.drop();
    assert.equal(h.requests.length, 1);
    assert.equal(h.chips.get('71'), h.original);
    h.context.state.setDrag({sigla: ''});
    gate.resolve(response({ok: true, id: 71, tipo_contrato: 'contrato'}));
    await pending;
    assert.equal(h.context.state.action().sigla, 'QA01');
    assert(h.context.state.assigned[h.key(h.to)].has('QA01'));
    assert.equal(h.additions[0].id, '71');
    assert.equal(h.additions[0].nota, 'Troca autorizada');
    assert.equal(h.context.state.movesInFlight.size, 0);
  });
  await test('failed undo remains retryable; move and successful undo use only atomic route', async () => {
    const h = scheduleHarness();
    await h.drop();
    const moved = h.chips.get('71'), action = h.context.state.action();
    h.context.reply = async () => response({ok: false, erro: 'Conflito'});
    await h.context.undoLast();
    assert.equal(h.chips.get('71'), moved);
    assert.equal(h.context.state.action(), action);
    assert(h.context.state.assigned[h.key(h.to)].has('QA01'));
    h.context.reply = async () => { throw new Error('offline'); };
    await h.context.undoLast();
    assert.equal(h.chips.get('71'), moved);
    assert.equal(h.context.state.action(), action);
    h.context.reply = async () => response({ok: true, id: 71, tipo_contrato: 'contrato'});
    await h.context.undoLast();
    assert(h.context.state.assigned[h.key(h.from)].has('QA01'));
    assert.equal(h.context.state.assigned[h.key(h.to)].has('QA01'), false);
    assert.equal(h.additions[1].id, '71');
    assert.equal(h.additions[1].nota, 'Troca autorizada');
    assert.equal(h.context.state.action(), null);
    assert(h.requests.every(request => request.url === '/api/turno/move/71'));
    assert.deepEqual(h.hourDeltas.map(({horario, delta}) => [horario, delta]), [
      [h.from.horario, -1], [h.to.horario, 1], [h.to.horario, -1], [h.from.horario, 1],
    ]);
  });
  await test('panel drop retains employee/type after dragend and prevents duplicate add', async () => {
    const h = scheduleHarness(), gate = deferred();
    h.context.reply = async () => gate.promise;
    h.context.state.setDrag({sigla: 'QA02', fromPanel: true, tipo: 'turnos'});
    const pending = h.context.onDrop({preventDefault() {}, currentTarget: {querySelector: () => element()}}, 'gaia', '10:00-14:00', 'Ter 6');
    h.context.state.setDrag({sigla: 'QA02', fromPanel: true, tipo: 'turnos'});
    await h.context.onDrop({preventDefault() {}, currentTarget: {querySelector: () => element()}}, 'gaia', '10:00-14:00', 'Ter 6');
    h.context.state.setDrag({sigla: ''});
    assert.equal(h.requests.length, 1);
    gate.resolve(response({ok: true, id: 72}));
    await pending;
    assert.equal(h.additions[0].sigla, 'QA02');
    assert.equal(h.additions[0].tipo, 'turnos');
  });
  await test('team partial failure unlocks controls and retries only failed update', async () => {
    const h = teamHarness();
    Object.assign(h.context.state.pendingChanges, {QA01: {horas: 32, rank: 'staff', loja_gerida: ''}});
    h.context.reply = async url => {
      if (url.endsWith('update-rank')) throw new Error('offline');
      return response({ok: true});
    };
    await h.context.commitAllChanges();
    assert.equal(h.context.state.saving(), false);
    assert.equal(h.ids['btn-save-all'].disabled, false);
    assert(h.controls.every(control => !control.disabled));
    assert.equal(h.context.state.pendingChanges.QA01.horas, undefined);
    assert.equal(h.context.state.pendingChanges.QA01.rank, 'staff');
    h.context.reply = async () => response({ok: true});
    await h.context.commitAllChanges();
    assert.deepEqual(h.requests.map(request => request.url), [
      '/api/funcionario/update-horas', '/api/funcionario/update-rank', '/api/funcionario/update-rank',
    ]);
    assert.equal(Object.keys(h.context.state.pendingChanges).length, 0);
    assert.equal(h.card.classList.contains('card-modified'), false);
  });
  await test('team rejected update preserves pending value and blocks duplicate save', async () => {
    const h = teamHarness(), gate = deferred();
    h.context.state.pendingChanges.QA01 = {horas: 32};
    h.context.reply = async () => gate.promise;
    const pending = h.context.commitAllChanges();
    assert(h.controls.every(control => control.disabled));
    await h.context.commitAllChanges();
    assert.equal(h.requests.length, 1);
    gate.resolve(response({ok: false, erro: 'Horas inválidas'}));
    await pending;
    assert.equal(h.context.state.pendingChanges.QA01.horas, 32);
    assert.equal(h.context.state.saving(), false);
    assert.equal(h.ids['btn-save-all'].disabled, false);
  });
  await test('schedule search matches accented names and combines with contract filter', async () => {
    const h = searchHarness();
    h.ids['chip-search'].value = 'ines';
    h.context.onSearchInput();
    assert.equal(h.chips[2].classList.contains('match'), true);
    assert.equal(h.ids['search-count'].textContent, '1 turno nesta semana');
    h.context.setFilter('contrato');
    assert.equal(h.chips[2].style.display, 'none');
    assert.equal(h.ids['search-count'].textContent, 'Sem turnos correspondentes');
    assert.equal(h.ids['tag-contrato'].getAttribute('aria-pressed'), 'true');
    h.context.setFilter('todos');
    assert.equal(h.chips[2].style.display, '');
    assert.equal(h.ids['search-count'].textContent, '1 turno nesta semana');
  });
  await test('typing a new name clears stale selected-person drawer and distinguishes no matches', async () => {
    const h = searchHarness();
    h.context.state.select('QA02');
    h.ids['result-drawer'].classList.add('open');
    h.ids['chip-search'].value = 'clara';
    h.context.onSearchInput();
    assert.equal(h.context.state.selected(), null);
    assert.equal(h.ids['result-drawer'].classList.contains('open'), false);
    assert.equal(h.ids['search-count'].textContent, '2 turnos nesta semana');
    h.ids['chip-search'].value = 'inexistente';
    h.context.onSearchInput();
    assert.equal(h.ids['search-count'].textContent, 'Nenhuma pessoa encontrada');
    assert.equal(h.chips.filter(chip => chip.classList.contains('match')).length, 0);
  });
}
main().catch(error => { console.error(error); process.exitCode = 1; });
