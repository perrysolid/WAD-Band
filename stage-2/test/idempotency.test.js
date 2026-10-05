'use strict';
// R18, R19 (D1 steps 5–6, D3, D6): idempotency across the five write paths.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, fixture, newKey, expectError } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

test('R19 replay is 200 with the identical body; body compared as parsed JSON', async () => {
  const w = await t.world();
  const key = newKey();
  const first = await t.call('POST', '/payments', { token: w.ada, key, raw: '{"to_handle":"bob","amount":1500,"note":"x"}' });
  assert.equal(first.status, 201);
  const again = await t.call('POST', '/payments', { token: w.ada, key, raw: '{ "note":"x",  "amount": 1.5e3, "to_handle":"bob", }'.replace(', }', '}') });
  assert.equal(again.status, 200);
  assert.deepEqual(again.body, first.body);
  expectError(assert, await t.call('POST', '/payments', { token: w.ada, key, body: { to_handle: 'bob', amount: 1500 } }), 409, 'idempotency_key_reuse');
  assert.equal(await t.balance(w.ada), 8500);
});

test('R19 a claimed key beats field validation and resource checks', async () => {
  const w = await t.world();
  const key = newKey();
  await t.pay(w.ada, 'bob', 100, {}, key);
  expectError(assert, await t.call('POST', '/payments', { token: w.ada, key, body: { to_handle: 5, amount: 'x' } }), 409, 'idempotency_key_reuse');
  expectError(assert, await t.call('POST', '/payments', { token: w.ada, key, body: { to_handle: 'ada', amount: 1 } }), 409, 'idempotency_key_reuse');
  // but body parse and auth still come first
  expectError(assert, await t.call('POST', '/payments', { token: w.ada, key, raw: '{' }), 400, 'malformed_request');
  expectError(assert, await t.call('POST', '/payments', { key, body: {} }), 401, 'unauthenticated');
});

test('R19 scope is per user and per path', async () => {
  const w = await t.world();
  const key = 'shared-key';
  assert.equal((await t.pay(w.ada, 'cy', 100, {}, key)).status, 201);
  assert.equal((await t.pay(w.bob, 'cy', 100, {}, key)).status, 201);
  assert.equal((await t.call('POST', '/requests', { token: w.ada, key, body: { payer_handle: 'cy', amount: 100 } })).status, 201);
  assert.equal((await t.call('POST', '/splits', { token: w.ada, key, body: { amount: 100, participant_handles: ['cy'] } })).status, 201);
  assert.equal(await t.balance(w.cy), 700);
});

test('R19 a failed first use leaves the key free', async () => {
  const w = await t.world();
  const key = newKey();
  expectError(assert, await t.pay(w.cy, 'bob', 600, {}, key), 409, 'insufficient_funds');
  expectError(assert, await t.pay(w.cy, 'bob', 0, {}, key), 422, 'validation_failed');
  assert.equal((await t.pay(w.cy, 'bob', 600 - 100, {}, key)).status, 201);
});

test('R19 replay survives later state changes', async () => {
  const w = await t.world();
  const key = newKey();
  const rq = await t.call('POST', '/requests', { token: w.bob, key, body: { payer_handle: 'ada', amount: 10 } });
  await t.call('POST', `/requests/${rq.body.request_id}/cancel`, { token: w.bob });
  const again = await t.call('POST', '/requests', { token: w.bob, key, body: { payer_handle: 'ada', amount: 10 } });
  assert.equal(again.status, 200);
  assert.deepEqual(again.body, rq.body);
  assert.equal(again.body.status, 'pending');

  const pkey = newKey();
  const p = await t.pay(w.cy, 'bob', 500, {}, pkey);
  const p2 = await t.pay(w.cy, 'bob', 500, {}, pkey);
  assert.equal(p2.status, 200);
  assert.deepEqual(p2.body, p.body);
  assert.equal(await t.balance(w.cy), 0);
});

test('R19 concurrent identical first uses: one 201, rest 200, one effect', async () => {
  for (const path of ['/payments', '/requests', '/splits', 'pay', '/settlements']) {
    const w = await t.world(fixture({ settlement_operator_ids: ['u_ada'] }));
    let url = path; let body;
    if (path === '/payments') body = { to_handle: 'bob', amount: 100 };
    if (path === '/requests') body = { payer_handle: 'bob', amount: 100 };
    if (path === '/splits') body = { amount: 100, participant_handles: ['ada', 'bob'] };
    if (path === '/settlements') body = { transfers: [{ from_handle: 'bob', to_handle: 'cy', amount: 100 }] };
    if (path === 'pay') {
      const rq = (await t.ask(w.bob, 'ada', 100)).body.request_id;
      url = `/requests/${rq}/pay`; body = {};
    }
    const key = newKey();
    const rs = await Promise.all(Array.from({ length: 50 }, () => t.call('POST', url, { token: w.ada, key, body })));
    const codes = rs.map((r) => r.status).sort();
    assert.equal(codes.filter((c) => c === 201).length, 1, `${path} ${codes}`);
    assert.equal(codes.filter((c) => c === 200).length, 49, `${path} ${codes}`);
    for (const r of rs) assert.deepEqual(r.body, rs[0].body);
    const sum = (await t.balance(w.ada)) + (await t.balance(w.bob)) + (await t.balance(w.cy));
    assert.equal(sum, w.total);
  }
});
