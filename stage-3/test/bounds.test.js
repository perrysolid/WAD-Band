'use strict';
// Reviewer REJECT on e7d2b7d. Defect 1: R7/R14/R34 — a credit that would take a wallet above 2^53
// is refused (422 validation_failed, after insufficient_funds) instead of rounding money away
// or answering 500. Defect 2: R31 — import rejects states the service cannot serve
// (clock beyond 9999-12-31, unusable counters, scrypt parameters the service never writes).
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, newKey, expectError } = require('./helpers');
const { State, InvariantError } = require('../src/state');

const MAX = 2 ** 53;
const MAX_TIME = 253402300799999;

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

const user = (handle, balance) => ({
  id: `u_${handle}`, email: `${handle}@example.com`, password: 'correct horse', display_name: handle, handle, balance,
});

function bigFixture(extra = {}) {
  return {
    currency: 'JPY', minor_units: 0,
    users: [user('ada', MAX), user('bob', 1000), user('cy', MAX - 1), user('op', 0)],
    settlement_operator_ids: ['u_op'],
    ...extra,
  };
}

async function totals(w, handles) {
  let sum = 0n;
  const out = {};
  for (const h of handles) {
    const r = await t.call('GET', '/me', { token: w[h] });
    out[h] = r.body.balance;
    sum += BigInt(r.body.balance);
  }
  return { sum, ...out };
}

const SEEDED = BigInt(MAX) + 1000n + BigInt(MAX - 1);

test('D1 a payment that would credit a wallet above 2^53 is 422 and changes nothing', async () => {
  const w = await t.world(bigFixture());
  for (const amount of [1, 2, 1000]) {
    const key = newKey();
    expectError(assert, await t.pay(w.bob, 'ada', amount, {}, key), 422, 'validation_failed');
    const after = await totals(w, ['ada', 'bob', 'cy']);
    assert.equal(after.sum, SEEDED);
    assert.equal(after.ada, MAX);
    assert.equal(after.bob, 1000);
    // the refusal claimed no key
    assert.equal((await t.pay(w.bob, 'op', amount, {}, key)).status, 201);
    await t.pay(w.op, 'bob', amount);
  }
  assert.equal((await t.call('GET', '/activity', { token: w.ada })).body.payments.filter((p) => p.to_handle === 'ada').length, 0);
});

test('D1 a credit landing exactly on 2^53 succeeds; one more is refused', async () => {
  const w = await t.world(bigFixture());
  assert.equal((await t.pay(w.bob, 'cy', 1)).status, 201);
  assert.equal((await totals(w, ['cy'])).cy, MAX);
  expectError(assert, await t.pay(w.bob, 'cy', 1), 422, 'validation_failed');
  assert.equal((await totals(w, ['ada', 'bob', 'cy'])).sum, SEEDED);
});

test('D1 insufficient_funds takes precedence over the 2^53 bound', async () => {
  const w = await t.world(bigFixture());
  expectError(assert, await t.pay(w.bob, 'ada', 1001), 409, 'insufficient_funds');
});

test('D1 paying a request that would overflow the requester is 422; the request stays pending', async () => {
  const w = await t.world(bigFixture());
  const rq = await t.ask(w.ada, 'bob', 5);
  assert.equal(rq.status, 201);
  const key = newKey();
  const id = rq.body.request_id;
  expectError(assert, await t.call('POST', `/requests/${id}/pay`, { token: w.bob, key, body: {} }), 422, 'validation_failed');
  const list = await t.call('GET', '/requests', { token: w.bob });
  assert.equal(list.body.requests[0].status, 'pending');
  assert.equal((await totals(w, ['ada', 'bob', 'cy'])).sum, SEEDED);
});

test('D1 a settlement whose net would overflow a wallet is 422; affordable nets still commit', async () => {
  const w = await t.world(bigFixture());
  const bad = { transfers: [{ from_handle: 'bob', to_handle: 'cy', amount: 2 }] };
  expectError(assert, await t.call('POST', '/settlements', { token: w.op, key: newKey(), body: bad }), 422, 'validation_failed');
  // cy receives 2 and pays out 1: net +1 lands exactly on 2^53
  const ok = { transfers: [{ from_handle: 'bob', to_handle: 'cy', amount: 2 }, { from_handle: 'cy', to_handle: 'bob', amount: 1 }] };
  assert.equal((await t.call('POST', '/settlements', { token: w.op, key: newKey(), body: ok })).status, 201);
  const after = await totals(w, ['ada', 'bob', 'cy']);
  assert.equal(after.cy, MAX);
  assert.equal(after.sum, SEEDED);
  // short funds still win over overflow
  const short = { transfers: [{ from_handle: 'bob', to_handle: 'ada', amount: 5000 }] };
  expectError(assert, await t.call('POST', '/settlements', { token: w.op, key: newKey(), body: short }), 409, 'insufficient_funds');
});

test('D1 applyTransfers refuses an out-of-range credit exactly (no float rounding)', () => {
  const s = new State('JPY', 0);
  s.addUser({ id: 'a', email: 'a@x', display_name: 'a', handle: 'a', balance: MAX, cred: {}, seq: 1 });
  s.addUser({ id: 'b', email: 'b@x', display_name: 'b', handle: 'b', balance: 10, cred: {}, seq: 2 });
  assert.throws(() => s.applyTransfers([{ from: 'b', to: 'a', amount: 1 }]), InvariantError);
  assert.equal(s.users.get('a').balance, MAX);
  assert.equal(s.users.get('b').balance, 10);
  s.applyTransfers([{ from: 'a', to: 'b', amount: 1 }]);
  assert.equal(s.users.get('a').balance, MAX - 1);
  assert.equal(s.users.get('b').balance, 11);
});

async function importMutated(mutate) {
  const w = await t.world();
  const exp = (await t.call('GET', '/_test/export')).body;
  mutate(exp.state);
  const r = await t.call('POST', '/_test/import', { body: exp });
  return { r, w };
}

test('D2 import rejects a clock beyond 9999-12-31 and keeps serving', async () => {
  for (const clock of [9e15, MAX_TIME + 1]) {
    const { r, w } = await importMutated((st) => { st.clock = clock; });
    expectError(assert, r, 422, 'validation_failed');
    assert.equal((await t.call('GET', '/me', { token: w.ada })).status, 200);
    const key = newKey();
    assert.equal((await t.pay(w.ada, 'bob', 100, {}, key)).status, 201);
    assert.equal((await t.pay(w.ada, 'bob', 100, {}, key)).status, 200);
    assert.equal(await t.balance(w.ada), 9900);
  }
});

test('D2 a clock at the D10 limit imports and writes still render', async () => {
  const { r, w } = await importMutated((st) => { st.clock = MAX_TIME; });
  assert.equal(r.status, 204);
  const p = await t.pay(w.ada, 'bob', 100);
  assert.equal(p.status, 201);
  assert.equal(p.body.created_at, '9999-12-31T23:59:59.999+00:00');
});

test('D2 import rejects counters that cannot produce fresh ids', async () => {
  for (const v of [1e300, 2 ** 53, -1, 1.5]) {
    const { r } = await importMutated((st) => { st.counters.p = v; });
    expectError(assert, r, 422, 'validation_failed');
  }
});

test('D2 import rejects scrypt parameters the service never writes', async () => {
  const mutations = [
    (c) => { c.N = 1 << 20; },
    (c) => { c.N = 1 << 15; },
    (c) => { c.r = 32; },
    (c) => { c.p = 16; },
    (c) => { c.salt = 'ab'; },
  ];
  for (const m of mutations) {
    const { r, w } = await importMutated((st) => { m(st.users[0].cred); });
    expectError(assert, r, 422, 'validation_failed');
    assert.equal((await t.call('GET', '/me', { token: w.ada })).status, 200);
  }
});

test('D40 import rejects out-of-range balances, amounts, ids, tokens and idempotency records', async () => {
  const w0 = await t.world();
  await t.pay(w0.ada, 'bob', 100, {}, 'seed-key');
  const exp = (await t.call('GET', '/_test/export')).body;
  const mutations = [
    (st) => { st.users[0].balance = MAX + 2; },
    (st) => { st.payments[0].amount = 1e10; },
    (st) => { st.payments[0].created_at = MAX_TIME + 1; },
    (st) => { st.payments[0].payment_id = 'p'.repeat(65); },
    (st) => { st.tokens[0].digest = 'zz'; },
    (st) => { st.idempotency[0].path = '/me'; },
    (st) => { st.idempotency[0].key = 'k'.repeat(256); },
    (st) => { st.idempotency[0].status = 200; },
  ];
  for (const m of mutations) {
    const copy = JSON.parse(JSON.stringify(exp));
    m(copy.state);
    expectError(assert, await t.call('POST', '/_test/import', { body: copy }), 422, 'validation_failed');
    assert.equal(await t.balance(w0.ada), 9900);
  }
  assert.equal((await t.call('POST', '/_test/import', { body: exp })).status, 204);
});
