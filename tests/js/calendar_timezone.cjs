const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync(process.argv[2], 'utf8');
const start = template.indexOf('function _fmtGCalDate(');
const end = template.indexOf('function toggleCalMenu(', start);
assert(start >= 0 && end > start);
const context = {Date, Intl};
vm.createContext(context);
vm.runInContext(template.slice(start, end), context);
for (const timezone of ['UTC', 'America/New_York', 'Asia/Tokyo']) {
  process.env.TZ = timezone;
  for (const [date, hour, minute, expected] of [
    ['2026-01-15', 8, 30, '20260115T083000Z'],
    ['2026-07-15', 8, 30, '20260715T073000Z'],
    ['2026-03-29', 2, 30, '20260329T013000Z'],
    ['2026-03-29', 1, 30, '20260329T013000Z'],
    ['2026-10-25', 2, 30, '20261025T023000Z'],
    ['2026-07-16', 0, 30, '20260715T233000Z'],
  ]) assert.equal(context._fmtGCalDate(date, hour, minute), expected,
    `${date} ${hour}:${minute} with device timezone ${timezone}`);
}
console.log('Lisbon calendar times verified across seasons, midnight and device timezones.');
