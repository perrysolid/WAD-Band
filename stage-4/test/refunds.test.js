'use strict';
// S4: refunds and batch corrections.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, fixture, newKey, expectError } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

const refund = (token, id, body, key = newKey()) => t.call('POST', `/payments/${id}/refunds`, { token, key, body });
const correct = (token, id, body) => t.call('POST', `/payments/${id}/corrections`, { token, key: newKey(), body });
const batch = (token, corrections, key = newKey()) => t.call('POST', '/correction-batches', { token, key, body: { corrections } });
const me = async (token) => (await t.call('GET', '/me', { token })).body;
const item = (p, extra = {}) => ({ payment_id: p.payment_id, expected_revision: 1, amount: 0, effective_at: p.created_at, reason: 'r', ...extra });

test('S4 refunds: shape, replay, bounds, targets, funds', async () => {
  const w = await t.world();
  const p = (await t.pay(w.ada, 'bob', 1000, { note: 'n', visibility: 'private' })).body;
  assert.equal(p.refund_of, null);
  const key = newKey();
  const r = await refund(w.bob, p.payment_id, { amount: 300 }, key);
  assert.equal(r.status, 201);
  assert.deepEqual([r.body.refund_of, r.body.from_handle, r.body.to_handle, r.body.amount, r.body.note, r.body.visibility, r.body.request_id, r.body.authorization_id], [p.payment_id, 'bob', 'ada', 300, 'n', 'private', null, null]);
  const again = await refund(w.bob, p.payment_id, { amount: 300 }, key);
  assert.equal(again.status, 200); assert.deepEqual(again.body, r.body);
  expectError(assert, await refund(w.ada, p.payment_id, { amount: 1 }), 403, 'forbidden');
  expectError(assert, await refund(w.bob, 'p_99', { amount: 1 }), 404, 'not_found');
  expectError(assert, await refund(w.bob, p.payment_id, { amount: 0 }), 422, 'validation_failed');
  expectError(assert, await refund(w.bob, p.payment_id, { amount: 701 }), 422, 'refund_exceeds_payment');
  expectError(assert, await refund(w.ada, r.body.payment_id, { amount: 1 }), 422, 'invalid_refund_target');
  assert.equal((await refund(w.bob, p.payment_id, { amount: 700 })).status, 201);
  assert.equal((await me(w.ada)).balance, 10000);
  // correction below refunded; refunds and captures are immutable
  expectError(assert, await correct(w.bob, r.body.payment_id, { expected_revision: 1, amount: 1, effective_at: r.body.created_at, reason: 'x' }), 422, 'linked_payment_immutable');
  const q = (await t.pay(w.ada, 'bob', 500)).body;
  await refund(w.bob, q.payment_id, { amount: 200 });
  expectError(assert, await correct(w.ada, q.payment_id, { expected_revision: 1, amount: 100, effective_at: q.created_at, reason: 'x' }), 422, 'refund_exceeds_payment');
  assert.equal((await correct(w.ada, q.payment_id, { expected_revision: 1, amount: 250, effective_at: q.created_at, reason: 'x' })).status, 201);
  // refund bound follows the corrected amount; held funds are not spendable
  expectError(assert, await refund(w.bob, q.payment_id, { amount: 51 }), 422, 'refund_exceeds_payment');
  const h = (await t.pay(w.ada, 'bob', 1000)).body;
  await t.call('POST', '/authorizations', { token: w.bob, key: newKey(), body: { to_handle: 'cy', amount: (await me(w.bob)).balance } });
  expectError(assert, await refund(w.bob, h.payment_id, { amount: 1 }), 409, 'insufficient_funds');
});

test('S4 correction batches', async () => {
  const w = await t.world(fixture({ settlement_operator_ids: ['u_ada'] }));
  const s = (await t.call('POST', '/settlements', { token: w.ada, key: newKey(), body: { transfers: [
    { from_handle: 'ada', to_handle: 'bob', amount: 100 }, { from_handle: 'ada', to_handle: 'cy', amount: 50 }] } })).body;
  const [m1, m2] = s.payments;
  const direct = (await t.pay(w.ada, 'bob', 10)).body;
  expectError(assert, await batch(w.bob, [item(direct)]), 403, 'forbidden');
  expectError(assert, await batch(w.ada, []), 422, 'validation_failed');
  expectError(assert, await batch(w.ada, [item(direct), item(direct)]), 422, 'validation_failed');
  expectError(assert, await batch(w.ada, [item({ payment_id: 'nope', created_at: direct.created_at })]), 404, 'not_found');
  expectError(assert, await batch(w.ada, [item(m1)]), 422, 'incomplete_settlement');
  const other = new Date(Date.parse(s.committed_at) - 5000).toISOString();
  expectError(assert, await batch(w.ada, [item(m1, { effective_at: other }), item(m2)]), 422, 'validation_failed');
  expectError(assert, await batch(w.ada, [item(direct, { expected_revision: 3 })]), 409, 'stale_revision');
  const before = [(await me(w.ada)).balance, (await me(w.bob)).balance];
  const key = newKey();
  const ok = await batch(w.ada, [item(m2), item(m1, { effective_at: s.committed_at.replace('+00:00', 'Z') }), item(direct, { amount: 20 })], key);
  assert.equal(ok.status, 201);
  assert.equal(ok.body.revisions.length, 3);
  assert.equal(ok.body.revisions[0].payment_id, m2.payment_id);
  assert.ok(ok.body.revisions.every((r) => r.correction_batch_id === ok.body.correction_batch_id && r.revision === 2));
  assert.equal((await me(w.ada)).balance, before[0] + 150 - 10);
  const rep = await batch(w.ada, [item(m2), item(m1, { effective_at: s.committed_at.replace('+00:00', 'Z') }), item(direct, { amount: 20 })], key);
  assert.equal(rep.status, 200); assert.deepEqual(rep.body, ok.body);
  const single = await correct(w.ada, direct.payment_id, { expected_revision: 2, amount: 5, effective_at: direct.created_at, reason: 'x' });
  assert.ok(!('correction_batch_id' in single.body));
  // refund of a settlement member is allowed
  assert.equal((await refund(w.cy, m2.payment_id, { amount: 0 })).status, 422);
});

test('S4 export v4 round trips; v3 imports', async () => {
  const w = await t.world();
  const p = (await t.pay(w.ada, 'bob', 100)).body;
  await refund(w.bob, p.payment_id, { amount: 40 });
  const exp = (await t.call('GET', '/_test/export')).body;
  assert.equal(exp.state.schema_version, 4);
  assert.equal((await t.call('POST', '/_test/import', { body: exp })).status, 204);
  assert.deepEqual((await t.call('GET', '/_test/export')).body, exp);
  expectError(assert, await refund(w.bob, p.payment_id, { amount: 61 }), 422, 'refund_exceeds_payment');
  const v3 = JSON.parse(JSON.stringify(exp)); v3.state.schema_version = 3; delete v3.state.counters.cb;
  for (const x of v3.state.payments) delete x.refund_of;
  assert.equal((await t.call('POST', '/_test/import', { body: v3 })).status, 204);
  assert.equal((await t.pay(w.ada, 'bob', 1)).body.refund_of, null);
});
