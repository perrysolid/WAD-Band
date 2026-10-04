'use strict';
// R14–R18, R28, R29 (D1, D2, D3, D4, D7): payments and the feed.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, newKey, expectError } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

test('R14 payment response shape, defaults and atomic movement', async () => {
  const w = await t.world();
  const r = await t.pay(w.ada, 'bob', 1500);
  assert.equal(r.status, 201);
  assert.deepEqual(Object.keys(r.body).sort(), ['amount', 'created_at', 'currency', 'from_handle', 'from_user_id', 'note',
    'payment_id', 'request_id', 'settlement_id', 'to_handle', 'to_user_id', 'visibility']);
  assert.equal(r.body.note, '');
  assert.equal(r.body.visibility, 'public');
  assert.equal(r.body.request_id, null);
  assert.equal(r.body.settlement_id, null);
  assert.match(r.body.created_at, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00$/);
  assert.equal(await t.balance(w.ada), 8500);
  assert.equal(await t.balance(w.bob), 4000);
});

test('R15 R16 invalid amounts are 422 and change nothing', async () => {
  const w = await t.world();
  for (const raw of ['0', '-1', '1000000001', '1000.5', '1e400', '"1000"', 'true', 'null', '1e-1', '[1]', '{}']) {
    const r = await t.call('POST', '/payments', { token: w.ada, key: newKey(), raw: `{"to_handle":"bob","amount":${raw}}` });
    expectError(assert, r, 422, 'validation_failed');
  }
  expectError(assert, await t.call('POST', '/payments', { token: w.ada, key: newKey(), body: { to_handle: 'bob' } }), 422, 'validation_failed');
  assert.equal(await t.balance(w.ada), 10000);
  assert.equal((await t.call('GET', '/activity', { token: w.ada })).body.payments.length, 0);
});

test('R16 integral spellings are valid; 1 and the whole balance succeed', async () => {
  const w = await t.world();
  for (const raw of ['1000', '1000.0', '1e3', '1.0e3']) {
    const r = await t.call('POST', '/payments', { token: w.ada, key: newKey(), raw: `{"to_handle":"bob","amount":${raw}}` });
    assert.equal(r.status, 201, raw);
    assert.equal(r.body.amount, 1000);
  }
  assert.equal((await t.pay(w.ada, 'bob', 1)).status, 201);
  expectError(assert, await t.pay(w.cy, 'bob', 1e9), 409, 'insufficient_funds');
  assert.equal((await t.pay(w.cy, 'bob', 500)).status, 201);
  assert.equal(await t.balance(w.cy), 0);
  expectError(assert, await t.pay(w.cy, 'bob', 1), 409, 'insufficient_funds');
});

test('R15 field error precedence (D1, D2, D7)', async () => {
  const w = await t.world();
  const p = (body) => t.call('POST', '/payments', { token: w.ada, key: newKey(), body });
  expectError(assert, await p({ to_handle: 5, amount: 'x' }), 400, 'malformed_request');
  expectError(assert, await p({ to_handle: null, amount: 100 }), 400, 'malformed_request');
  expectError(assert, await p({ to_handle: 'ada', amount: 0 }), 422, 'validation_failed');
  expectError(assert, await p({ to_handle: 'ada', amount: 100, note: 5 }), 422, 'self_payment');
  expectError(assert, await p({ to_handle: 'nobody', amount: 100, note: null }), 422, 'validation_failed');
  expectError(assert, await p({ to_handle: 'nobody', amount: 100, visibility: 'friends' }), 422, 'validation_failed');
  expectError(assert, await p({ to_handle: 'nobody', amount: 100, visibility: null }), 422, 'validation_failed');
  expectError(assert, await p({ to_handle: 'nobody', amount: 100 }), 404, 'not_found');
  expectError(assert, await p({ to_handle: '', amount: 100 }), 404, 'not_found');
  expectError(assert, await p({ to_handle: '@bob', amount: 100 }), 404, 'not_found');
  expectError(assert, await p({ to_handle: 'Bob', amount: 100 }), 404, 'not_found');
  expectError(assert, await p({ to_handle: 'nobody', amount: 999999999 }), 404, 'not_found');
  expectError(assert, await p({ to_handle: 'bob', amount: 999999999, note: 'x'.repeat(201) }), 422, 'validation_failed');
  expectError(assert, await t.call('POST', '/payments', { token: w.ada, key: newKey(), raw: 'not json' }), 400, 'malformed_request');
  expectError(assert, await t.call('POST', '/payments', { token: w.ada, key: newKey(), raw: '' }), 400, 'malformed_request');
  expectError(assert, await t.call('POST', '/payments', { raw: 'not json' }), 400, 'malformed_request');
  expectError(assert, await t.call('POST', '/payments', { body: { to_handle: 'bob', amount: 1 } }), 401, 'unauthenticated');
  expectError(assert, await t.call('POST', '/payments', { token: w.ada, body: { to_handle: 'bob', amount: 0 } }), 400, 'missing_idempotency_key');
  expectError(assert, await t.call('POST', '/payments', { token: w.ada, key: '', body: { to_handle: 'bob', amount: 0 } }), 400, 'missing_idempotency_key');
  assert.equal(await t.balance(w.ada), 10000);
});

test('R17 notes are verbatim; length counted in code points', async () => {
  const w = await t.world();
  for (const note of ['  padded  ', '<script>alert(1)</script>', 'e\u0301', 'שלום', '😀👍🏽', '\u0000x', '😀'.repeat(200), '']) {
    const r = await t.pay(w.ada, 'bob', 1, { note });
    assert.equal(r.status, 201);
    assert.equal(r.body.note, note);
  }
  expectError(assert, await t.pay(w.ada, 'bob', 1, { note: '😀'.repeat(201) }), 422, 'validation_failed');
  const feed = await t.call('GET', '/activity', { token: w.bob });
  assert.equal(feed.body.payments[1].note, '😀'.repeat(200));
});

test('R18 Idempotency-Key length 1..255 code points', async () => {
  const w = await t.world();
  assert.equal((await t.pay(w.ada, 'bob', 1, {}, 'k')).status, 201);
  assert.equal((await t.pay(w.ada, 'bob', 1, {}, 'x'.repeat(255))).status, 201);
  expectError(assert, await t.pay(w.ada, 'bob', 1, {}, 'x'.repeat(256)), 422, 'validation_failed');
  expectError(assert, await t.pay(w.ada, 'bob', 1, {}, 'k'.repeat(10000)), 422, 'validation_failed');
});

test('R28 R29 feed visibility rule, payments only, newest first', async () => {
  const w = await t.world();
  const a = (await t.pay(w.ada, 'bob', 10, { visibility: 'private' })).body;
  const b = (await t.pay(w.bob, 'cy', 20)).body;
  await t.ask(w.bob, 'ada', 5);
  const feed = async (tok) => (await t.call('GET', '/activity', { token: tok })).body;
  assert.deepEqual((await feed(w.ada)).payments.map((p) => p.payment_id), [b.payment_id, a.payment_id]);
  assert.deepEqual((await feed(w.bob)).payments.map((p) => p.payment_id), [b.payment_id, a.payment_id]);
  assert.deepEqual((await feed(w.cy)).payments.map((p) => p.payment_id), [b.payment_id]);
  const fa = (await feed(w.ada)).payments[1];
  const fb = (await feed(w.bob)).payments[1];
  assert.deepEqual(fa, fb);
  assert.equal(fa.visibility, 'private');
});

test('R28 R25 paging and integer query parameters', async () => {
  const w = await t.world();
  for (let i = 0; i < 5; i += 1) await t.pay(w.ada, 'bob', 1);
  const get = (qs) => t.call('GET', `/activity?${qs}`, { token: w.cy });
  let r = await get('limit=2');
  assert.equal(r.body.payments.length, 2);
  assert.equal(r.body.has_more, true);
  r = await get('limit=2&offset=4');
  assert.equal(r.body.payments.length, 1);
  assert.equal(r.body.has_more, false);
  r = await get('limit=5');
  assert.equal(r.body.has_more, false);
  r = await get('limit=007&offset=00&direction=both&as_of=x');
  assert.equal(r.status, 200);
  for (const qs of ['limit=0', 'limit=201', 'limit=1e1', 'limit=4.0', 'limit=%2B4', 'limit=+4', 'limit=-1', 'limit=', 'limit=abc', 'offset=-1', 'offset=1.0', 'limit=%204']) {
    expectError(assert, await get(qs), 422, 'validation_failed');
  }
  assert.equal((await get('limit=200')).status, 200);
});
