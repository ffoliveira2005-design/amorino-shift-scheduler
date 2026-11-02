// Execute the application's real fetch wrapper with a simulated browser boundary.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const template = fs.readFileSync(process.argv[2], 'utf8');
const start = template.indexOf('function requestIsSameOrigin(input)');
const end = template.indexOf('// ── Navigation progress bar', start);
assert(start >= 0 && end > start, 'Application fetch wrapper must be present');
const calls = [], notices = [], redirects = [];
const location = {
  href: 'https://escala.example.test/supervisor/escala?semana=current',
  origin: 'https://escala.example.test', pathname: '/supervisor/escala', search: '?semana=current',
};
const context = {
  URL, Headers, location,
  document: {querySelector: () => ({getAttribute: () => 'session-test-token'})},
  toast: message => notices.push(message),
  setTimeout: callback => { redirects.push(callback); return 1; },
  window: {fetch: async (input, init) => {
    calls.push({input, init});
    const url = typeof input === 'string' ? input : input.url || String(input);
    return {status: String(url).includes('expired') ? 401 : 200};
  }},
};
vm.createContext(context);
vm.runInContext(template.slice(start, end), context);

async function check(input, method, shouldCarryToken) {
  await context.window.fetch(input, {method});
  const headers = new Headers(calls.at(-1).init.headers);
  assert.equal(headers.get('X-CSRF-Token'), shouldCarryToken ? 'session-test-token' : null,
    `Token scope failed for ${String(input)} (${method})`);
}

(async () => {
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    await check('/api/save', method, true);
    await check('api/save', method, true);
    await check('https://escala.example.test/api/save', method, true);
    await check(new URL('https://escala.example.test/api/save'), method, true);
    await check(new String('https://external.example/api/save'), method, false);
    await check(new URL('https://external.example/api/save'), method, false);
    await check('//external.example/api/save', method, false);
    await check('https://escala.example.test.attacker.example/api/save', method, false);
    await check('https://escala.example.test@external.example/api/save', method, false);
    await check('http://escala.example.test/api/save', method, false);
    await check('https://escala.example.test:8443/api/save', method, false);
    await check('http://[invalid', method, false);
  }
  await check('/api/read', 'GET', false);
  await context.window.fetch(new Request('https://escala.example.test/api/save', {method: 'POST',
    headers: {'X-Client': 'preserved'}}));
  assert.equal(calls.at(-1).init.headers.get('X-Client'), 'preserved');
  assert.equal(calls.at(-1).init.headers.get('X-CSRF-Token'), 'session-test-token');
  await context.window.fetch('/api/save', {method: 'POST', headers: {'X-CSRF-Token': 'explicit-token'}});
  assert.equal(calls.at(-1).init.headers.get('X-CSRF-Token'), 'explicit-token');
  await check('//external.example/expired', 'POST', false);
  assert.equal(notices.length, 0, 'An external 401 must not expire the local session');
  await check('/api/expired', 'POST', true);
  assert.equal(notices.length, 1);
  assert.equal(redirects.length, 1);
  console.log('Fetch origin, headers and session-expiry behavior verified.');
})().catch(error => { console.error(error); process.exitCode = 1; });
