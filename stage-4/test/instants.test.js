'use strict';
// UI helper: datetime-local -> RFC 3339 with an explicit offset.
const test = require('node:test');
const assert = require('node:assert/strict');

test('localToRfc3339 yields an offset instant equal to the local time', async () => {
  const { localToRfc3339 } = await import('../public/lib/instants.js');
  for (const v of ['2026-09-24T13:20', '2026-01-05T00:00:30', '2026-12-31T23:59']) {
    const r = localToRfc3339(v);
    assert.match(r, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$/);
    assert.equal(new Date(r).getTime(), new Date(v).getTime());
  }
  for (const bad of ['', '2026-09-24', 'nope', null, undefined, '2026-09-24T13']) assert.equal(localToRfc3339(bad), null);
});
