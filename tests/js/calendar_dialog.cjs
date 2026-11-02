// Run the actual calendar UI against deterministic browser/API boundaries.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../templates/minha_escala.html'), 'utf8');
const start = source.indexOf('let calOpenRequest =');
const end = source.indexOf('async function rotateCalToken()', start);
assert(start >= 0 && end > start);
function element() {
  const classes = new Set();
  return {innerHTML:'', textContent:'', value:'', children:[], listeners:{},
    classList:{add:s=>classes.add(s), remove:s=>classes.delete(s), contains:s=>classes.has(s)},
    append(...children){this.children.push(...children);},
    replaceChildren(...children){this.children=children;this.innerHTML='';},
    addEventListener(name, handler){this.listeners[name]=handler;}, select(){},
  };
}
const nodes = Object.fromEntries(['cal-modal','cal-modal-body','cal-url-input','cal-copy-btn'].map(id=>[id,element()]));
const notices=[];
let request, clipboard, fallback=false;
const context={URL, location:{href:'http://127.0.0.1:5002/minha-escala'},
  document:{getElementById:id=>nodes[id]||null,createElement:element,execCommand:()=>fallback},
  navigator:{clipboard:{writeText:()=>clipboard()}},setTimeout(){},
  fetch:()=>request(),toast:message=>notices.push(message),
  openModal:()=>nodes['cal-modal'].classList.add('open'),
  escHtml:value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'),
};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
const response=data=>({json:async()=>data});
(async()=>{
  request=async()=>{throw new Error('network');};
  await context.openCalModal();
  let error=nodes['cal-modal-body'].children[0];
  assert(error.children[0].textContent.includes('Verificar a ligação'));
  assert.equal(error.children[1].textContent,'Tentar novamente');
  request=async()=>response({ok:true,url:'http://127.0.0.1:5002/cal/TEST/local.ics',sigla:'TEST'});
  await error.children[1].listeners.click();
  assert(nodes['cal-modal-body'].innerHTML.includes('Descarregar calendário'));
  assert.match(nodes['cal-modal-body'].innerHTML,/id="cal-google" hidden/);
  console.log('PASS calendar network failure provides a working retry and local download');

  let finishOld;
  request=()=>new Promise(resolve=>{finishOld=resolve;});
  const old=context.openCalModal();
  request=async()=>response({ok:true,url:'https://app.example/cal/TEST/new.ics',sigla:'NEW'});
  await context.openCalModal();
  finishOld(response({ok:true,url:'https://app.example/cal/TEST/old.ics',sigla:'OLD'}));
  await old;
  assert(nodes['cal-modal-body'].innerHTML.includes('/new.ics'));
  assert(!nodes['cal-modal-body'].innerHTML.includes('/old.ics'));
  console.log('PASS stale calendar response cannot overwrite a reopened dialog');

  request=()=>new Promise(resolve=>{finishOld=resolve;});
  const closed=context.openCalModal();
  nodes['cal-modal'].classList.remove('open');
  finishOld(response({ok:true,url:'https://app.example/cal/TEST/closed.ics',sigla:'CLOSED'}));
  await closed;
  assert(!nodes['cal-modal-body'].innerHTML.includes('/closed.ics'));
  console.log('PASS calendar response leaves a closed dialog untouched');

  context.renderCalModal({url:'https://app.example/cal/TEST/with"quote.ics',sigla:'<img src=x>'});
  assert(nodes['cal-modal-body'].innerHTML.includes('&lt;img src=x&gt;'));
  assert(!nodes['cal-modal-body'].innerHTML.includes('<img src=x>'));
  assert(nodes['cal-modal-body'].innerHTML.includes('with&quot;quote.ics'));
  console.log('PASS calendar labels and URL attributes escape response text');

  clipboard=async()=>{throw new Error('denied');};fallback=false;
  await context.copyCalUrl();
  assert.equal(nodes['cal-copy-btn'].classList.contains('copied'),false);
  assert(notices.at(-1).includes('copiar manualmente'));
  fallback=true;await context.copyCalUrl();
  assert.equal(nodes['cal-copy-btn'].classList.contains('copied'),true);
  console.log('PASS copy success is shown only after clipboard or fallback succeeds');
})().catch(error=>{console.error(error);process.exitCode=1;});
