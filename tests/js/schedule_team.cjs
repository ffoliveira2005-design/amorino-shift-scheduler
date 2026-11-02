/* Exercise the shipped sidebar script; only DOM/editor boundaries are stubbed. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/schedule_team.js'), 'utf8');

function node() {
  const classes = new Set(), attributes = new Map(), handlers = {};
  return {
    hidden:false, value:'', dataset:{}, textContent:'', title:'', focused:false,
    classList:{add:n=>classes.add(n),remove:n=>classes.delete(n),contains:n=>classes.has(n),toggle:(n,on)=>on?classes.add(n):classes.delete(n)},
    setAttribute:(n,v)=>attributes.set(n,String(v)), getAttribute:n=>attributes.get(n),
    addEventListener:(name,fn)=>handlers[name]=fn, emit:(name,event={})=>handlers[name]?.(event),
    focus(){this.focused=true;}, getClientRects:()=>[{}],
  };
}
function harness(mobile=false) {
  const ids = Object.fromEntries(['schedule-team','btn-schedule-team','schedule-team-search','schedule-team-day',
    'team-selection','team-selection-text','schedule-team-count','schedule-team-empty','schedule-team-close','team-selection-cancel'].map(id=>[id,node()]));
  const cards = ['QA01','QA02'].map(sigla=>{
    const card=node(); card.dataset.sigla=sigla;
    card.hours=node(); card.availability=node();
    card.querySelector=s=>s==='.schedule-team-hours'?card.hours:card.availability;
    return card;
  });
  const filters=['todos','contrato','turnos'].map(type=>{const button=node();button.dataset.teamFilter=type;return button;});
  const documentHandlers={}, calls=[], add=node();
  const context=vm.createContext({console,document:{
    body:node(),getElementById:id=>ids[id],
    querySelectorAll:selector=>selector==='.schedule-team-person'?cards:selector==='[data-team-filter]'?filters:selector==='.chip-add'?[add]:[],
    addEventListener:(name,fn)=>documentHandlers[name]=fn,
  },window:{matchMedia:()=>({matches:mobile})},
  normalizeScheduleSearch:v=>String(v||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase(),
  FUNC_INFO:{QA01:{nome:'Inês <img onerror="bad">',tipo_contrato:'contrato',horas_contrato:32},QA02:{nome:'Clara Matos',tipo_contrato:'turnos'}},
  DISPS:{QA01:{'Qua 14':{tipo:'indisponivel',texto:'Nota <script>'}}},
  getTotalHours:s=>s==='QA01'?33.5:12,
  closeQuickAdd:()=>calls.push('close-quick'),
  toggleAssignPanel:()=>{context.panelOpen=!context.panelOpen;calls.push('toggle-suggestions');},
  onDrop:(event,l,h,d)=>{calls.push({drag:{...context._drag},l,h,d});},
  onPanelDragStart:(event,s,t)=>{context._drag={sigla:s,tipo:t,fromPanel:true};event.currentTarget.classList.add('is-dragging');event.dataTransfer.setData('text/plain',s);},
  onPanelDragEnd:()=>{context._drag={};cards.forEach(c=>c.classList.remove('is-dragging'));calls.push('end-drag');},
  panelOpen:false,_assignInFlight:false,_drag:{},scheduleHoursReady:false,
  });
  vm.runInContext(source,context);
  const event=()=>({target:{closest:()=>null},preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}});
  return {context,ids,cards,filters,calls,documentHandlers,add,event};
}
function test(name,fn){fn();console.log('PASS '+name);}

test('desktop opens full roster; mobile starts collapsed and returns focus to grid after selection',()=>{
  const desktop=harness();assert.equal(desktop.ids['schedule-team'].hidden,false);
  const h=harness(true);assert.equal(h.ids['schedule-team'].hidden,true);
  h.ids['btn-schedule-team'].emit('click');assert.equal(h.ids['schedule-team-search'].focused,true);
  h.cards[0].emit('click');assert.equal(h.ids['schedule-team'].hidden,true);assert.equal(h.add.focused,true);
  assert.equal(h.ids['team-selection'].hidden,false);
});
test('accent-insensitive search combines with contract filter and announces empty results',()=>{
  const h=harness();h.ids['schedule-team-search'].value='ines';h.ids['schedule-team-search'].emit('input');
  assert.equal(h.cards[0].hidden,false);assert.equal(h.cards[1].hidden,true);
  h.filters[2].emit('click');assert.equal(h.cards[0].hidden,true);
  assert.equal(h.ids['schedule-team-count'].textContent,'0 de 2 pessoas');assert.equal(h.ids['schedule-team-empty'].hidden,false);
  assert.equal(h.filters[2].getAttribute('aria-pressed'),'true');
  h.ids['schedule-team-search'].value='';h.filters[0].emit('click');assert(h.cards.every(c=>!c.hidden));
});
test('hours stay loading until workload resolves, then show PT decimals and advisory availability',()=>{
  const h=harness();assert.equal(h.cards[0].hours.textContent,'… h');
  h.context.scheduleHoursReady=true;h.ids['schedule-team-day'].value='Qua 14';h.context.refreshScheduleTeam();
  assert.equal(h.cards[0].hours.textContent,'33,5 h planeadas');assert(h.cards[0].hours.classList.contains('over-hours'));
  assert.equal(h.cards[0].availability.textContent,'Qua 14 · Indisponível');assert.equal(h.cards[0].availability.title,'Nota <script>');
  assert(h.cards.every(c=>!c.hidden),'Unavailable and unanswered people remain selectable');
});
test('click selection uses exact cell and consumes event; existing chips remain interactive',()=>{
  const h=harness();h.cards[0].emit('click');
  assert.equal(h.cards[0].getAttribute('aria-pressed'),'true');
  assert(h.ids['team-selection-text'].textContent.includes('Inês <img onerror="bad">'),'Name is plain text');
  const cell={dataset:{loja:'gaia',horario:'12:30-21:00',dia:'Qua 14'}}, event=h.event();
  assert.equal(h.context.assignSelectedScheduleTeam(event,cell),true);assert(event.prevented&&event.stopped);
  const drop=h.calls.find(c=>typeof c==='object');
  assert.deepEqual(JSON.parse(JSON.stringify(drop)),{drag:{sigla:'QA01',fromPanel:true,fromTeam:true,tipo:'contrato'},l:'gaia',h:'12:30-21:00',d:'Qua 14'});
  assert.equal(h.context._drag.sigla,undefined,'Drag cleanup follows synchronous capture');
  event.target.closest=()=>({});assert.equal(h.context.assignSelectedScheduleTeam(event,cell),false);
});
test('duplicate placement is blocked while pending; rejection keeps the chosen person available',()=>{
  const h=harness();h.cards[0].emit('click');h.context._assignInFlight=true;
  assert.equal(h.context.assignSelectedScheduleTeam(h.event(),{dataset:{}}),true);
  assert.equal(h.calls.filter(c=>typeof c==='object').length,0);
  h.cards[1].emit('click');assert.equal(h.cards[0].getAttribute('aria-pressed'),'true');
  h.context._assignInFlight=false;assert.equal(h.ids['team-selection'].hidden,false);
  h.context.clearScheduleTeamSelection();assert.equal(h.ids['team-selection'].hidden,true);
});
test('drag wiring carries full person/type and clears earlier selection; dragend cleans cards',()=>{
  const h=harness();h.cards[0].emit('click');const transfer=[];
  h.cards[1].emit('dragstart',{currentTarget:h.cards[1],dataTransfer:{setData:(...v)=>transfer.push(v)}});
  assert.equal(h.ids['team-selection'].hidden,true);assert.equal(h.context._drag.sigla,'QA02');
  assert.equal(h.context._drag.tipo,'turnos');assert.equal(h.context._drag.fromTeam,true);
  assert.deepEqual(transfer,[['text/plain','QA02']]);
  h.cards[1].emit('dragend');assert(!h.cards[1].classList.contains('is-dragging'));
});
test('opening team closes suggestions and Escape cancels selection without assigning',()=>{
  const h=harness();h.context.panelOpen=true;h.context.setScheduleTeamOpen(true);
  assert.equal(h.context.panelOpen,false);h.cards[0].emit('click');
  const event={key:'Escape',defaultPrevented:false,preventDefault(){this.prevented=true;}};
  h.documentHandlers.keydown(event);assert.equal(h.ids['team-selection'].hidden,true);assert(event.prevented);
  assert.equal(h.calls.filter(c=>typeof c==='object').length,0);
});
