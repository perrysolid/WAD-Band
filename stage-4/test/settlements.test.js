'use strict';
// R33 (D19): atomic net settlements.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, fixture, newKey, expectError } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

const opWorld = () => t.world(fixture({ settlement_operator_ids: ['u_ada'] }));
const settle = (tok, transfers, key = newKey()) => t.call('POST', '/settlements', { token: tok, key, body: { transfers } });

test('R33 auth, operator and key precedence', async () => {
  const w = await opWorld();
  expectError(assert, await t.call('POST', '/settlements', { body: { transfers: [] } }), 401, 'unauthenticated');
  expectError(assert, await t.call('POST', '/settlements', { token: w.bob, body: { transfers: [] } }), 403, 'forbidden');
  expectError(assert, await t.call('POST', '/settlements', { token: w.bob, key: newKey(), body: {} }), 403, 'forbidden');
  expectError(assert, await t.call('POST', '/settlements', { token: w.ada, body: { transfers: [] } }), 400, 'missing_idempotency_key');
  expectError(assert, await t.call('POST', '/settlements', { token: w.ada, raw: '{' }), 400, 'malformed_request');
});

test('R33 net affordability: a wallet may pay out what it receives in the batch', async () => {
  const w = await opWorld();
  const r = await settle(w.ada, [
    { from_handle: 'bob', to_handle: 'cy', amount: 2000 },
    { from_handle: 'cy', to_handle: 'ada', amount: 2400, note: 'n', visibility: 'private' },
  ]);
  assert.equal(r.status, 201, JSON.stringify(r.body));
  assert.deepEqual(Object.keys(r.body).sort(), ['committed_at', 'payments', 'settlement_id']);
  assert.equal(r.body.payments.length, 2);
  for (const p of r.body.payments) {
    assert.equal(p.settlement_id, r.body.settlement_id);
    assert.equal(p.request_id, null);
    assert.equal(p.created_at, r.body.committed_at);
  }
  assert.equal(r.body.payments[0].note, '');
  assert.equal(r.body.payments[0].visibility, 'public');
  assert.equal(r.body.payments[1].visibility, 'private');
  assert.equal(await t.balance(w.bob), 500);
  assert.equal(await t.balance(w.cy), 100);
  assert.equal(await t.balance(w.ada), 12400);
  // feed visibility: the private member is hidden from bob
  const bobFeed = (await t.call('GET', '/activity', { token: w.bob })).body.payments;
  assert.deepEqual(bobFeed.map((p) => p.payment_id), [r.body.payments[0].payment_id]);
});

test('R33 insufficient collective funds moves nothing and claims no key', async () => {
  const w = await opWorld();
  const key = newKey();
  expectError(assert, await settle(w.ada, [
    { from_handle: 'cy', to_handle: 'bob', amount: 400 },
    { from_handle: 'cy', to_handle: 'ada', amount: 101 },
  ], key), 409, 'insufficient_funds');
  assert.equal(await t.balance(w.cy), 500);
  assert.equal((await t.call('GET', '/activity', { token: w.cy })).body.payments.length, 0);
  const ok = await settle(w.ada, [{ from_handle: 'cy', to_handle: 'bob', amount: 400 }], key);
  assert.equal(ok.status, 201);
});

test('R33 entry errors in input order, before funds (D19)', async () => {
  const w = await opWorld();
  const big = { from_handle: 'cy', to_handle: 'bob', amount: 999999 };
  expectError(assert, await settle(w.ada, []), 422, 'validation_failed');
  expectError(assert, await settle(w.ada, Array.from({ length: 33 }, () => ({ from_handle: 'ada', to_handle: 'bob', amount: 1 }))), 422, 'validation_failed');
  assert.equal((await settle(w.ada, Array.from({ length: 32 }, () => ({ from_handle: 'ada', to_handle: 'bob', amount: 1 })))).status, 201);
  expectError(assert, await t.call('POST', '/settlements', { token: w.ada, key: newKey(), body: { transfers: 'x' } }), 422, 'validation_failed');
  expectError(assert, await t.call('POST', '/settlements', { token: w.ada, key: newKey(), body: {} }), 422, 'validation_failed');
  expectError(assert, await settle(w.ada, [big, 5]), 422, 'validation_failed');
  expectError(assert, await settle(w.ada, [big, { from_handle: 1, to_handle: 'bob', amount: 1 }]), 422, 'validation_failed');
  expectError(assert, await settle(w.ada, [big, { from_handle: 'bob', to_handle: 'bob', amount: 1 }]), 422, 'self_payment');
  expectError(assert, await settle(w.ada, [big, { from_handle: 'bob', to_handle: 'bob', amount: 0 }]), 422, 'validation_failed');
  expectError(assert, await settle(w.ada, [big, { from_handle: 'zed', to_handle: 'bob', amount: 1 }]), 404, 'not_found');
  expectError(assert, await settle(w.ada, [{ from_handle: 'zed', to_handle: 'bob', amount: 1 }, { from_handle: 'bob', to_handle: 'bob', amount: 1 }]), 404, 'not_found');
  expectError(assert, await settle(w.ada, [big, { from_handle: 'bob', to_handle: 'cy', amount: 1, note: null }]), 422, 'validation_failed');
  expectError(assert, await settle(w.ada, [big, { from_handle: 'bob', to_handle: 'cy', amount: 1, visibility: 'x' }]), 422, 'validation_failed');
  expectError(assert, await settle(w.ada, [big]), 409, 'insufficient_funds');
});

test('R33 replay returns the original; operator gains no access to others', async () => {
  const w = await opWorld();
  const key = newKey();
  const tr = [{ from_handle: 'bob', to_handle: 'cy', amount: 100, visibility: 'private' }];
  const first = await settle(w.ada, tr, key);
  const again = await settle(w.ada, tr, key);
  assert.equal(again.status, 200);
  assert.deepEqual(again.body, first.body);
  expectError(assert, await settle(w.ada, [{ ...tr[0], amount: 101 }], key), 409, 'idempotency_key_reuse');
  assert.equal(await t.balance(w.bob), 2400);
  assert.equal((await t.call('GET', '/activity', { token: w.ada })).body.payments.length, 0);
  const rq = (await t.ask(w.bob, 'cy', 5)).body.request_id;
  assert.deepEqual((await t.call('GET', '/requests', { token: w.ada })).body.requests, []);
  expectError(assert, await t.call('POST', `/requests/${rq}/decline`, { token: w.ada }), 403, 'forbidden');
});
