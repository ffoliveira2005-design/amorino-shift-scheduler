/* Execute the dashboard's actual functions. No network, app import or DB. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', '..', 'templates', 'supervisor_dashboard.html'), 'utf8');

function section(start, end) {
  const first = source.indexOf(start), last = source.indexOf(end, first);
  assert(first >= 0 && last > first, `Missing dashboard section: ${start}`);
  return source.slice(first, last);
}
function element(value = '') {
  const classes = new Set(), attributes = new Map();
  return {
    value, textContent: '', disabled: false, checked: false, hidden: true, style: {}, dataset: {},
    get selectedOptions() { return [{textContent: this.value}]; },
    classList: {
      add: (...names) => names.forEach(name => classes.add(name)),
      contains: name => classes.has(name),
      toggle(name) { classes.has(name) ? classes.delete(name) : classes.add(name); return classes.has(name); },
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
const response = (data, ok = true) => ({ok, json: async () => data});

function harness(readReply) {
  const ids = Object.fromEntries(['cp-src', 'cp-dst', 'cp-loja', 'cp-overwrite', 'cp-save', 'cp-status', 'dcg-save-btn', 'dcg-status', 'dcg-state-pill', 'weeks-status'].map(id => [id, element()]));
  ids['cp-src'].value = 'old-week'; ids['cp-dst'].value = 'destination-week'; ids['cp-loja'].value = 'gaia';
  ids['cp-save'].textContent = 'Copiar'; ids['dcg-save-btn'].textContent = 'Guardar configuração';
  ids['dcg-save-btn'].disabled = true;
  ids['dcg-state-pill'].textContent = 'Ativa'; ids['dcg-state-pill'].className = 'dcg-state on';
  const weekButtons = [element(), element()];
  weekButtons.forEach(button => button.textContent = 'Ativar');
  const weekControls = [...weekButtons, ...['cp-src', 'cp-dst', 'cp-loja', 'cp-overwrite', 'cp-save'].map(id => ids[id])];
  const deliveryCells = ['carmelitas', 'gaia'].map(loja => {
    const cell = element(); cell.dataset = {dia: 'Seg 5', loja}; return cell;
  });
  deliveryCells[0].classList.add('active');
  const calendarCells = ['2026-10-05', '2026-10-12'].map(monday => {
    const dates = element(), holidays = element();
    return {dataset: {monday}, dates, holidays, querySelector: name => name === '.wc-dates' ? dates : holidays};
  });
  const requests = [], messages = [], confirmations = [], timers = [], closed = [], navigations = [], listeners = {};
  const context = vm.createContext({
    console, URLSearchParams,
    document: {
      getElementById: id => ids[id] || null,
      querySelectorAll: selector => {
        if (selector === '.week-cell') return calendarCells;
        if (selector.startsWith('.wc-btn,')) return [...weekControls, ...deliveryCells, ids['dcg-save-btn']];
        if (selector === '#dcg-grid .dcg-day-btn.active') return deliveryCells.filter(cell => cell.classList.contains('active'));
        if (selector === '#dcg-grid .dcg-day-btn, #dcg-save-btn') return [...deliveryCells, ids['dcg-save-btn']];
        throw new Error('Unexpected selector: ' + selector);
      },
    },
    location: {search: '?semana=old-week&cob=coverage-week&view=ceo', hash: '#semanas', assign: url => navigations.push(url)},
    window: {addEventListener: (name, callback) => listeners[name] = callback},
    toast: (message, error) => messages.push({message, error}),
    closeModal: id => closed.push(id),
    setTimeout: callback => { timers.push(callback); return timers.length; },
    confirmDialog: async (message, options) => { confirmations.push({message, options}); return context.confirmReply(); },
    fetch: async (url, init) => {
      requests.push({url, init});
      return init?.method === 'POST' ? context.reply(url, init) : (readReply ? readReply() : response([]));
    },
  });
  context.confirmReply = async () => true;
  context.reply = async url => url.endsWith('/nova') ? response({ok: true, codigo: 'new-week'}) :
    url.endsWith('/descarga') ? response({ok: true, dias: ['Seg 5'], removed: 0}) : response({ok: true, criados: 3});
  const delivery = section('// ── Descarga configuration', '// ── Trocas tab:')
    .replace(/const SEMANA_CODIGO = .*?;/, "const SEMANA_CODIGO = 'selected-week';");
  vm.runInContext(
    section('function dashboardWeekDates(', '// `exists`:') +
    section('async function criarSemana(', '// ── Copy a ready-to-send reminder') +
    delivery + '\nglobalThis.state = {weekBusy: () => weekOperationInProgress, deliveryBusy: () => dcgSaveInProgress};',
    context);
  const writes = () => requests.filter(request => request.init?.method === 'POST');
  const flushTimers = () => { while (timers.length) timers.shift()(); };
  return {context, ids, weekButtons, weekControls, deliveryCells, calendarCells, requests, writes, messages, confirmations, timers, closed, navigations, listeners, flushTimers};
}

async function test(name, body) { await body(); console.log('PASS ' + name); }
async function main() {
  for (const failure of ['network', 'invalid JSON', 'rejected', 'HTTP error with success body']) {
    await test(`create/activate ${failure} unlocks controls without navigating or claiming success`, async () => {
      const h = harness();
      h.context.reply = async () => {
        if (failure === 'network') throw new Error('offline');
        if (failure === 'invalid JSON') return {ok: true, json: async () => { throw new Error('HTML response'); }};
        if (failure === 'HTTP error with success body') return response({ok: true, codigo: 'new-week'}, false);
        return response({ok: false, erro: 'Semana inválida'}, false);
      };
      await h.context.criarSemana('2026-10-12', '12 a 18 de outubro', h.weekButtons[0], true);
      assert.equal(h.writes().length, 1);
      assert.equal(h.context.state.weekBusy(), false);
      assert(h.weekControls.every(control => !control.disabled));
      assert.equal(h.weekButtons[0].textContent, 'Ativar');
      assert.equal(h.weekButtons[0].getAttribute('aria-busy'), undefined);
      assert.equal(h.navigations.length, 0); assert.equal(h.timers.length, 0);
      assert(h.messages.every(message => message.error));
    });
  }
  await test('create locks before confirmation and prevents conflicting duplicate week writes', async () => {
    const h = harness(), confirmation = deferred(), server = deferred();
    h.context.confirmReply = async () => confirmation.promise;
    h.context.reply = async () => server.promise;
    const pending = h.context.criarSemana('2026-10-12', '12 a 18', h.weekButtons[0], false);
    assert(h.weekControls.every(control => control.disabled));
    await h.context.criarSemana('2026-10-19', '19 a 25', h.weekButtons[1], false);
    await h.context.copiarSemana(h.ids['cp-save']);
    assert.equal(h.confirmations.length, 1); assert.equal(h.writes().length, 0);
    confirmation.resolve(true);
    await new Promise(setImmediate);
    assert.equal(h.writes().length, 1);
    await h.context.criarSemana('2026-10-12', '12 a 18', h.weekButtons[0], false);
    server.resolve(response({ok: true, codigo: 'new-week'}));
    await pending;
    assert.equal(h.writes().length, 1);
    assert.equal(h.context.state.weekBusy(), true); // remain locked during success redirect
    h.flushTimers();
    const destination = new URL(h.navigations[0], 'https://escala.example.test');
    assert.equal(destination.searchParams.get('semana'), 'new-week');
    assert.equal(destination.searchParams.get('cob'), 'coverage-week');
    assert.equal(destination.searchParams.get('view'), 'ceo');
    assert.equal(destination.hash, '#semanas');
  });
  await test('cancelled activation restores controls and sends no write', async () => {
    const h = harness(); h.context.confirmReply = async () => false;
    await h.context.criarSemana('2026-10-12', '12 a 18', h.weekButtons[0], false);
    assert.equal(h.writes().length, 0); assert.equal(h.context.state.weekBusy(), false);
    assert(h.weekControls.every(control => !control.disabled));
  });
  for (const failure of ['network', 'invalid JSON', 'rejected']) {
    await test(`copy ${failure} keeps modal, destination, store and overwrite selection retryable`, async () => {
      const h = harness(); h.ids['cp-overwrite'].checked = true;
      h.context.reply = async () => {
        if (failure === 'network') throw new Error('offline');
        if (failure === 'invalid JSON') return {ok: false, json: async () => { throw new Error('HTML'); }};
        return response({ok: false, erro: 'Semanas inválidas'}, false);
      };
      await h.context.copiarSemana(h.ids['cp-save']);
      assert.equal(h.ids['cp-src'].value, 'old-week'); assert.equal(h.ids['cp-dst'].value, 'destination-week');
      assert.equal(h.ids['cp-loja'].value, 'gaia'); assert.equal(h.ids['cp-overwrite'].checked, true);
      assert.equal(h.ids['cp-save'].disabled, false); assert.equal(h.ids['cp-save'].textContent, 'Copiar');
      assert(h.ids['cp-status'].textContent); assert.equal(h.closed.length, 0); assert.equal(h.timers.length, 0);
      h.context.reply = async () => response({ok: true, criados: 3});
      await h.context.copiarSemana(h.ids['cp-save']);
      assert.equal(h.writes().length, 2); assert.deepEqual(h.closed, ['modal-copiar']);
      h.flushTimers(); assert(h.navigations[0].includes('semana=destination-week'));
    });
  }
  await test('overwrite copy captures confirmed destination/store and locks until success navigation', async () => {
    const h = harness(), confirmation = deferred(), server = deferred();
    h.ids['cp-overwrite'].checked = true;
    h.context.confirmReply = async () => confirmation.promise; h.context.reply = async () => server.promise;
    const pending = h.context.copiarSemana(h.ids['cp-save']);
    assert(h.weekControls.every(control => control.disabled));
    assert(h.confirmations[0].message.includes('destination-week')); assert(h.confirmations[0].message.includes('gaia'));
    await h.context.copiarSemana(h.ids['cp-save']); assert.equal(h.confirmations.length, 1);
    confirmation.resolve(true);
    await new Promise(setImmediate);
    assert.equal(h.writes().length, 1);
    // A script or late UI event cannot change the already-confirmed request.
    h.ids['cp-dst'].value = 'other-week'; h.ids['cp-loja'].value = 'norte';
    server.resolve(response({ok: true, criados: 3})); await pending;
    const sent = JSON.parse(h.writes()[0].init.body);
    assert.deepEqual(sent, {src: 'old-week', dst: 'destination-week', loja: 'gaia', overwrite: true});
    h.flushTimers(); assert(h.navigations[0].includes('semana=destination-week'));
    assert.equal(h.context.state.weekBusy(), true);
  });
  await test('cancelled overwrite leaves selections intact and performs no deletion', async () => {
    const h = harness(); h.ids['cp-overwrite'].checked = true; h.context.confirmReply = async () => false;
    await h.context.copiarSemana(h.ids['cp-save']);
    assert.equal(h.writes().length, 0); assert.equal(h.closed.length, 0);
    assert.equal(h.ids['cp-dst'].value, 'destination-week'); assert.equal(h.ids['cp-overwrite'].checked, true);
    assert(h.weekControls.every(control => !control.disabled));
  });
  await test('delivery edits stay pending until save; saving locks grid and prevents duplicate requests', async () => {
    const h = harness(), server = deferred();
    h.context.toggleDescargaCell(h.deliveryCells[1]);
    assert.equal(h.writes().length, 0); assert.equal(h.ids['dcg-status'].textContent, 'Alterações por guardar');
    assert.equal(h.ids['dcg-save-btn'].disabled, false);
    h.context.reply = async () => server.promise;
    const pending = h.context.saveDescargaNow();
    assert(h.deliveryCells.every(cell => cell.disabled)); assert.equal(h.ids['dcg-save-btn'].disabled, true);
    h.context.toggleDescargaCell(h.deliveryCells[0]); await h.context.saveDescargaNow();
    assert.equal(h.deliveryCells[0].classList.contains('active'), true); assert.equal(h.writes().length, 1);
    server.resolve(response({ok: true, dias: ['Seg 5'], removed: 0})); await pending;
    assert.equal(h.context.descargaIsDirty(), false); assert.equal(h.context.state.deliveryBusy(), false);
    assert(h.deliveryCells.every(cell => !cell.disabled)); assert.equal(h.ids['dcg-save-btn'].disabled, true);
    assert.equal(h.ids['dcg-status'].className, 'dcg-save-status ok'); assert.equal(h.timers.length, 0);
  });
  for (const failure of ['network', 'invalid JSON', 'rejected', 'HTTP error with success body']) {
    await test(`delivery removal ${failure} preserves pending selection and confirmed state pill`, async () => {
      const h = harness(); h.context.toggleDescargaCell(h.deliveryCells[0]);
      h.context.reply = async () => {
        if (failure === 'network') throw new Error('offline');
        if (failure === 'invalid JSON') return {ok: true, json: async () => { throw new Error('HTML'); }};
        if (failure === 'HTTP error with success body') return response({ok: true, dias: [], removed: 1}, false);
        return response({ok: false, erro: 'Sem permissão'}, false);
      };
      await h.context.saveDescargaNow();
      assert.equal(h.confirmations[0].options.danger, true);
      assert.equal(h.deliveryCells[0].classList.contains('active'), false);
      assert.equal(h.ids['dcg-state-pill'].textContent, 'Ativa');
      assert.equal(h.ids['dcg-status'].className, 'dcg-save-status err');
      assert(h.ids['dcg-status'].textContent.includes('A seleção foi mantida.'));
      assert.equal(h.context.descargaIsDirty(), true); assert.equal(h.ids['dcg-save-btn'].disabled, false);
      assert(h.deliveryCells.every(cell => !cell.disabled)); assert.equal(h.timers.length, 0);
      h.context.reply = async () => response({ok: true, dias: [], removed: 1});
      await h.context.saveDescargaNow();
      assert.equal(h.writes().length, 2); assert.equal(h.ids['dcg-state-pill'].textContent, 'Desligada');
      assert(h.ids['dcg-status'].textContent.includes('1 atribuição retirada'));
      assert.equal(h.context.descargaIsDirty(), false); assert.equal(h.ids['dcg-save-btn'].disabled, true);
    });
  }
  await test('cancelled delivery removal does not write and warns before leaving pending edits', async () => {
    const h = harness(); h.context.toggleDescargaCell(h.deliveryCells[0]); h.context.confirmReply = async () => false;
    await h.context.saveDescargaNow();
    assert.equal(h.writes().length, 0); assert.equal(h.ids['dcg-state-pill'].textContent, 'Ativa');
    assert.equal(h.ids['dcg-save-btn'].disabled, false); assert.equal(h.context.state.deliveryBusy(), false);
    const event = {preventDefault() { this.prevented = true; }};
    h.listeners.beforeunload(event); assert.equal(event.prevented, true); assert.equal(event.returnValue, '');
    h.context.toggleDescargaCell(h.deliveryCells[0]);
    const clean = {preventDefault() { this.prevented = true; }}; h.listeners.beforeunload(clean);
    assert.equal(clean.prevented, undefined); assert.equal(h.ids['dcg-save-btn'].disabled, true);
  });
  await test('pending delivery edits block week changes without a server write or second confirmation', async () => {
    const h = harness(); h.context.toggleDescargaCell(h.deliveryCells[1]);
    await h.context.criarSemana('2026-10-12', '12 a 18', h.weekButtons[0], false);
    await h.context.copiarSemana(h.ids['cp-save']);
    assert.equal(h.writes().length, 0); assert.equal(h.confirmations.length, 0);
    assert.equal(h.context.descargaIsDirty(), true); assert.equal(h.ids['dcg-save-btn'].disabled, false);
    assert(h.messages.every(message => message.error && message.message.includes('descargas')));
  });
  await test('calendar dates survive a rejected holiday request and ignore device timezone', async () => {
    for (const timezone of ['UTC', 'America/New_York', 'Asia/Tokyo']) {
      process.env.TZ = timezone;
      const h = harness(async () => response({erro: 'Sessão expirada'}, false));
      await new Promise(setImmediate);
      assert.equal(h.calendarCells[0].dates.textContent, '05/10 – 11/10');
      assert.equal(h.context.dashboardWeekDates('2026-12-28'), '28/12 – 03/01');
      assert.equal(h.ids['weeks-status'].hidden, false); assert(h.ids['weeks-status'].textContent.includes('feriados'));
    }
  });
  await test('holiday labels match Monday rather than API response position', async () => {
    const h = harness(async () => response([
      {monday: '2026-10-12', feriados: [{feriado_nome: 'Segundo feriado'}]},
      {monday: '2026-10-05', feriados: [{feriado_nome: 'Primeiro feriado'}]},
    ]));
    await new Promise(setImmediate);
    assert.equal(h.calendarCells[0].holidays.textContent, 'Feriado: Primeiro feriado');
    assert.equal(h.calendarCells[1].holidays.textContent, 'Feriado: Segundo feriado');
  });
}
main().catch(error => { console.error(error); process.exitCode = 1; });
