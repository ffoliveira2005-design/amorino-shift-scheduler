/* Exercise the shipped week-link controller with browser boundaries stubbed. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/schedule_week.js'), 'utf8');

function node(value='') {
  const classes = new Set(), listeners = {};
  return {value, dataset:{}, style:{}, focused:false, listeners,
    classList:{contains:value=>classes.has(value),toggle:value=>classes.has(value)?classes.delete(value):classes.add(value)},
    addEventListener:(name,fn)=>listeners[name]=fn, focus(){this.focused=true;},
    closest(){return this;}};
}
function harness(query='?semana=middle', empty=false) {
  const nav=node(); nav.dataset.semana=empty?'':'middle';
  const previous=node(), next=node();
  previous.href='/supervisor/escala?semana=earlier'; previous.dataset.weekDirection='previous';
  next.href='/supervisor/escala?semana=later'; next.dataset.weekDirection='next';
  nav.querySelectorAll=()=>[previous,next];
  const ids={'chip-search':node(),'schedule-team-search':node(),'qa-pop':node(),
    'qa-func':node(),'qa-nota':node()};
  const day=node(); day.options=Array.from({length:8},(_,n)=>({value:n?'day-'+n:''})); day.selectedIndex=0;
  Object.defineProperty(day,'value',{get:()=>day.options[day.selectedIndex].value}); ids['schedule-team-day']=day;
  const body=node(), tab=node(); tab.dataset.lojaTab='all';
  const documentListeners={}, windowListeners={}, replacements=[], navigations=[], messages=[], confirmations=[];
  const location={href:'https://amorino.example/supervisor/escala'+query+'#store-gaia',assign:url=>navigations.push(url)};
  Object.defineProperties(location,{search:{get:()=>new URL(location.href).search},hash:{get:()=>new URL(location.href).hash}});
  let context;
  context=vm.createContext({console,URL,URLSearchParams,location,
    history:{state:{kept:'state'},replaceState:(state,unused,url)=>{replacements.push({state,url});location.href=new URL(url,location.href).href;}},
    document:{body,getElementById:id=>ids[id]||null,
      querySelector:selector=>selector==='.schedule-week-nav'?nav:
        selector.startsWith('.loja-tab')?tab:selector.startsWith('[data-team-filter=')?{click:()=>context.scheduleTeamFilter=selector.match(/="([^"]+)"/)[1]}:null,
      querySelectorAll:selector=>selector==='.targets-grid input'?context.targetInputs:[],
      addEventListener:(name,fn)=>documentListeners[name]=fn},
    window:{setLojaTab:store=>tab.dataset.lojaTab=store,toggleMyShifts:()=>body.classList.toggle('my-shifts-mode'),addEventListener:(name,fn)=>windowListeners[name]=fn},
    activeSearch:null,activeFilter:'todos',scheduleTeamFilter:'todos',RANK_FILTER:new Set(),
    FUNC_INFO:{EMP:{nome:'Inês'}},setFilter:value=>context.activeFilter=value,
    pickEmployee:sigla=>{context.activeSearch=sigla;ids['chip-search'].value=sigla+' — Inês';},
    onSearchInput:()=>context.activeSearch=null,refreshScheduleTeam:()=>{},toggleRankFilter:rank=>context.RANK_FILTER.add(rank),
    _assignInFlight:false,quickAddInProgress:false,movesInFlight:new Set(),targetInputs:[],
    toast:(message,error)=>messages.push({message,error}),
    confirmDialog:async(message,options)=>{confirmations.push({message,options});return context.confirmReply;},confirmReply:false,
  });
  vm.runInContext(source,context);
  function event(target=next,extra={}) {return {target,button:0,preventDefault(){this.prevented=true;},...extra};}
  return {context,nav,previous,next,ids,tab,body,replacements,navigations,messages,confirmations,documentListeners,windowListeners,event};
}
function test(name,fn){return Promise.resolve().then(fn).then(()=>console.log('PASS '+name));}
(async()=>{
  await test('deep-link filters, calendar week, fragment and history state survive navigation',()=>{
    const h=harness('?q=In%C3%AAs&tipo=contrato&loja=gaia&meus=1&equipa_q=Clara&equipa_tipo=turnos&equipa_dia=2&rank=staff&view=compact');
    assert.equal(h.ids['chip-search'].value,'Inês'); assert.equal(h.context.activeFilter,'contrato');
    assert.equal(h.ids['schedule-team-search'].value,'Clara'); assert.equal(h.context.scheduleTeamFilter,'turnos');
    assert.equal(h.ids['schedule-team-day'].selectedIndex,3); assert(h.body.classList.contains('my-shifts-mode'));
    for(const [arrow,week] of [[h.previous,'earlier'],[h.next,'later']]){
      const url=new URL(arrow.href,'https://amorino.example');
      assert.equal(url.searchParams.get('semana'),week); assert.equal(url.searchParams.get('q'),'Inês');
      assert.equal(url.searchParams.get('tipo'),'contrato'); assert.equal(url.searchParams.get('loja'),'gaia');
      assert.equal(url.searchParams.get('equipa_dia'),'2'); assert.equal(url.searchParams.get('view'),'compact');
      assert.equal(url.hash,'#store-gaia');
    }
    assert.equal(new URL(h.context.location.href).searchParams.get('semana'),'middle');
    assert.deepEqual(h.replacements[0].state,{kept:'state'}); assert.equal(h.navigations.length,0);
    h.ids['chip-search'].value='Clara'; h.documentListeners.input(h.event(h.ids['chip-search']));
    assert.equal(new URL(h.next.href,'https://amorino.example').searchParams.get('q'),'Clara');
  });
  await test('exact employee search survives links without substituting the display name',()=>{
    const h=harness('?semana=middle&pessoa=EMP');
    assert.equal(h.context.activeSearch,'EMP');
    const url=new URL(h.next.href,'https://amorino.example');
    assert.equal(url.searchParams.get('pessoa'),'EMP'); assert.equal(url.searchParams.has('q'),false);
  });
  await test('arrows accept keyboard focus without hijacking Enter or native modified clicks',async()=>{
    const h=harness();const right=h.event(h.previous,{key:'ArrowRight'});
    h.nav.listeners.keydown(right);assert(right.prevented&&h.next.focused);
    const enter=h.event(h.next,{key:'Enter'});h.nav.listeners.keydown(enter);assert(!enter.prevented);
    h.context._assignInFlight=true;
    const modified=h.event(h.next,{ctrlKey:true});await h.nav.listeners.click(modified);
    assert(!modified.prevented); assert.equal(h.navigations.length,0);
  });
  await test('saving assignments block same-tab navigation until the database reply completes',async()=>{
    const h=harness();h.context.movesInFlight.add('123');
    const event=h.event();await h.nav.listeners.click(event);
    assert(event.prevented);assert.equal(h.messages.length,1);assert.equal(h.navigations.length,0);
    h.context.movesInFlight.clear();const ready=h.event();await h.nav.listeners.click(ready);
    assert(!ready.prevented);assert.equal(h.confirmations.length,0);
  });
  await test('uncommitted local edits use the existing confirmation and honour its decision',async()=>{
    const h=harness();h.context.targetInputs=[{value:'5',defaultValue:'4'}];
    const cancelled=h.event();await h.nav.listeners.click(cancelled);
    assert(cancelled.prevented);assert.equal(h.navigations.length,0);assert.equal(h.confirmations.length,1);
    h.context.confirmReply=true;await h.nav.listeners.click(h.event());
    assert.equal(h.navigations.length,1);assert.equal(new URL(h.navigations[0],'https://amorino.example').searchParams.get('semana'),'later');
  });
  await test('an empty authorised list leaves URL and navigation inert',()=>{
    const h=harness('',true);assert.equal(h.replacements.length,0);assert.deepEqual(h.nav.listeners,{});
  });
})().catch(error=>{console.error(error);process.exitCode=1;});
