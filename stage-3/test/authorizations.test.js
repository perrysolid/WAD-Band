'use strict';
// S2-R1–R13, D25, D32–D35, D39: holds, capture, void, expiry, upgrade.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, fixture, newKey, expectError } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

const authorize = (token, to, amount, extra = {}, key = newKey()) => t.call('POST', '/authorizations', { token, key, body: { to_handle: to, amount, ...extra } });
const capture = (token, id, body = {}, key = newKey()) => t.call('POST', `/authorizations/${id}/capture`, { token, key, body });
const void_ = (token, id) => t.call('POST', `/authorizations/${id}/void`, { token });
const me = async (token) => (await t.call('GET', '/me', { token })).body;
const later = (ms) => new Date(Date.now() + ms).toISOString();
const hold = (id, from, to, amount, extra = {}) => ({
  id, from_user_id: `u_${from}`, to_user_id: `u_${to}`, amount, note: '', visibility: 'public', status: 'open', expires_at: later(3600e3), ...extra,
});

test('S2-R1 /me adds total, available, held; balance == total', async () => {
  const w = await t.world();
  assert.deepEqual(await me(w.ada), { user_id: 'u_ada', display_name: 'Ada', handle: 'ada', balance: 10000, total: 10000, available: 10000, held: 0, currency: 'EUR', minor_units: 2 });
  assert.equal((await authorize(w.ada, 'bob', 2000)).status, 201);
  const m = await me(w.ada);
  assert.deepEqual([m.balance, m.total, m.available, m.held], [10000, 10000, 8000, 2000]);
});

test('S2-R3 authorize shape, ttl, never a feed item, precedence', async () => {
  const w = await t.world(fixture({ authorization_ttl_seconds: 7 }));
  const r = await authorize(w.ada, 'bob', 1500, { note: 'n' });
  assert.equal(r.status, 201);
  assert.deepEqual(Object.keys(r.body).sort(), ['amount', 'authorization_id', 'captured_amount', 'created_at', 'currency', 'expires_at', 'from_handle', 'from_user_id', 'note',
    'payment_id', 'payment_ids', 'remaining_amount', 'status', 'to_handle', 'to_user_id', 'visibility']);
  assert.equal(Date.parse(r.body.expires_at) - Date.parse(r.body.created_at), 7000);
  assert.deepEqual([r.body.status, r.body.captured_amount, r.body.remaining_amount, r.body.payment_id, r.body.payment_ids], ['open', 0, 1500, null, []]);
  assert.equal((await t.call('GET', '/activity', { token: w.ada })).body.payments.length, 0);
  const p = (body) => t.call('POST', '/authorizations', { token: w.ada, key: newKey(), body });
  expectError(assert, await p({ to_handle: 'ada', amount: 0 }), 422, 'validation_failed');
  expectError(assert, await p({ to_handle: 'ada', amount: 1 }), 422, 'self_payment');
  expectError(assert, await p({ to_handle: 'nobody', amount: 1, visibility: 'x' }), 422, 'validation_failed');
  expectError(assert, await p({ to_handle: 'nobody', amount: 1 }), 404, 'not_found');
  expectError(assert, await p({ to_handle: 'bob', amount: 8501 }), 409, 'insufficient_funds');
  assert.equal((await p({ to_handle: 'bob', amount: 8500 })).status, 201);
});

test('S2-R10 funds checks use available; capture may spend the reservation', async () => {
  const w = await t.world();
  const a = (await authorize(w.cy, 'bob', 400)).body;
  expectError(assert, await t.pay(w.cy, 'ada', 101), 409, 'insufficient_funds');
  assert.equal((await t.pay(w.cy, 'ada', 100)).status, 201);
  expectError(assert, await authorize(w.cy, 'ada', 1), 409, 'insufficient_funds');
  const c = await capture(w.bob, a.authorization_id);
  assert.equal(c.status, 201);
  assert.equal((await me(w.cy)).total, 0);
});

test('S2-R4/R5 capture returns a payment; partial, extended, final', async () => {
  const w = await t.world();
  const a = (await authorize(w.ada, 'bob', 3000, { note: 'deposit', visibility: 'private' })).body;
  const id = a.authorization_id;
  const c1 = await capture(w.bob, id, { amount: 1000, final: false });
  assert.equal(c1.status, 201);
  assert.equal(c1.body.authorization_id, id);
  assert.deepEqual([c1.body.amount, c1.body.note, c1.body.visibility, c1.body.request_id, c1.body.settlement_id], [1000, 'deposit', 'private', null, null]);
  let list = (await t.call('GET', '/authorizations', { token: w.ada })).body.authorizations[0];
  assert.deepEqual([list.status, list.captured_amount, list.remaining_amount, list.payment_id], ['open', 1000, 2000, c1.body.payment_id]);
  assert.equal((await me(w.ada)).held, 2000);
  const c2 = await capture(w.bob, id, { amount: 500 }); // final defaults true: releases the rest
  list = (await t.call('GET', '/authorizations', { token: w.bob })).body.authorizations[0];
  assert.deepEqual([list.status, list.captured_amount, list.remaining_amount, list.payment_ids], ['captured', 1500, 0, [c1.body.payment_id, c2.body.payment_id]]);
  assert.deepEqual([(await me(w.ada)).held, (await me(w.ada)).total, (await me(w.bob)).total], [0, 8500, 4000]);
  expectError(assert, await capture(w.bob, id), 409, 'authorization_not_open');
  const b = (await authorize(w.ada, 'bob', 100)).body;
  assert.equal((await capture(w.bob, b.authorization_id, { final: false })).status, 201);
  assert.equal((await t.call('GET', '/authorizations', { token: w.ada })).body.authorizations[0].status, 'captured');
});

test('S2-R6/D25 capture error precedence and replay', async () => {
  const w = await t.world();
  const a = (await authorize(w.ada, 'bob', 1000)).body.authorization_id;
  const c = (body, token = w.bob, id = a) => t.call('POST', `/authorizations/${id}/capture`, { token, key: newKey(), body });
  expectError(assert, await c({ final: 'yes', amount: 0 }), 400, 'malformed_request');
  expectError(assert, await c({ amount: 0 }), 422, 'validation_failed');
  expectError(assert, await c({ amount: 1.5 }), 422, 'validation_failed');
  expectError(assert, await c({ amount: 1 }, w.bob, 'nope'), 404, 'not_found');
  expectError(assert, await c({ amount: 1 }, w.ada), 403, 'forbidden');
  expectError(assert, await c({ amount: 1 }, w.cy), 403, 'forbidden');
  expectError(assert, await c({ amount: 1001 }), 422, 'capture_exceeds_authorization');
  const key = newKey();
  const first = await capture(w.bob, a, { amount: 400 }, key);
  assert.equal(first.status, 201);
  expectError(assert, await capture(w.bob, a, {}, key), 409, 'idempotency_key_reuse');
  const again = await capture(w.bob, a, { amount: 400 }, key);
  assert.equal(again.status, 200);
  assert.deepEqual(again.body, first.body);
  expectError(assert, await capture(w.bob, a), 409, 'authorization_not_open');
  assert.equal((await capture(w.bob, a, { amount: 400 }, key)).status, 200);
  expectError(assert, await t.call('POST', `/authorizations/${a}/capture`, { token: w.bob, body: {} }), 400, 'missing_idempotency_key');
});

test('S2-R7 void: payer only, repeatable, releases the remainder', async () => {
  const w = await t.world();
  const id = (await authorize(w.ada, 'bob', 1000)).body.authorization_id;
  assert.equal((await void_(w.bob, id)).status, 403);
  assert.equal((await void_(w.cy, id)).status, 403);
  expectError(assert, await void_(w.ada, 'nope'), 404, 'not_found');
  const v = await void_(w.ada, id);
  assert.equal(v.status, 200);
  assert.equal(v.body.status, 'voided');
  assert.equal((await void_(w.ada, id)).body.status, 'voided');
  assert.equal((await me(w.ada)).held, 0);
  expectError(assert, await capture(w.bob, id), 409, 'authorization_not_open');
  const id2 = (await authorize(w.ada, 'bob', 1000)).body.authorization_id;
  await capture(w.bob, id2);
  expectError(assert, await void_(w.ada, id2), 409, 'authorization_not_open');
});

test('S2-R8 listing: parties only, filters, validation', async () => {
  const w = await t.world();
  const a1 = (await authorize(w.ada, 'bob', 10)).body.authorization_id;
  const a2 = (await authorize(w.bob, 'ada', 20)).body.authorization_id;
  await void_(w.ada, a1);
  const get = async (token, q = '') => (await t.call('GET', `/authorizations${q}`, { token })).body;
  assert.deepEqual((await get(w.ada)).authorizations.map((a) => a.authorization_id), [a2, a1]);
  assert.deepEqual((await get(w.ada, '?direction=outgoing')).authorizations.map((a) => a.authorization_id), [a1]);
  assert.deepEqual((await get(w.ada, '?status=open')).authorizations.map((a) => a.authorization_id), [a2]);
  assert.deepEqual((await get(w.cy)).authorizations, []);
  assert.equal((await get(w.ada, '?limit=1')).has_more, true);
  for (const q of ['?direction=x', '?status=pending', '?limit=0']) {
    expectError(assert, await t.call('GET', `/authorizations${q}`, { token: w.ada }), 422, 'validation_failed');
  }
  assert.equal((await t.call('GET', '/authorizations')).status, 401);
});

test('S2-R2/R9/D33 seeded holds; expiry needs no job', async () => {
  const w = await t.world(fixture({
    authorizations: [
      hold('a_open', 'ada', 'bob', 2000, { note: 'x' }),
      hold('a_part', 'ada', 'bob', 1000, { captured_amount: 400 }),
      hold('a_cap', 'ada', 'bob', 700, { status: 'captured' }),
      hold('a_past', 'cy', 'bob', 9999, { expires_at: later(-1000) }),
      hold('a_soon', 'bob', 'ada', 100, { expires_at: later(300) }),
    ],
  }));
  assert.deepEqual([(await me(w.ada)).held, (await me(w.ada)).available], [2600, 7400]);
  const list = (await t.call('GET', '/authorizations', { token: w.ada })).body.authorizations;
  const by = Object.fromEntries(list.map((a) => [a.authorization_id, a]));
  assert.equal(by.a_past, undefined);
  assert.deepEqual([by.a_cap.captured_amount, by.a_cap.remaining_amount, by.a_part.remaining_amount], [700, 0, 600]);
  assert.equal((await t.call('GET', '/authorizations', { token: w.cy })).body.authorizations[0].status, 'expired');
  await new Promise((r) => setTimeout(r, 450));
  assert.equal((await me(w.bob)).held, 0);
  expectError(assert, await capture(w.ada, 'a_soon'), 409, 'authorization_expired');
  expectError(assert, await void_(w.bob, 'a_soon'), 409, 'authorization_not_open');
  assert.equal((await t.call('GET', '/authorizations?status=open', { token: w.bob })).body.authorizations.length, 2);
});

test('S2-R2 fixture errors change nothing', async () => {
  const w = await t.world();
  const bad = [
    fixture({ authorization_ttl_seconds: 0 }), fixture({ authorization_ttl_seconds: 1.5 }), fixture({ authorization_ttl_seconds: '600' }),
    fixture({ authorizations: [hold('a', 'ada', 'zed', 1)] }),
    fixture({ authorizations: [hold('a', 'ada', 'bob', 1, { status: 'pending' })] }),
    fixture({ authorizations: [hold('a', 'ada', 'bob', 1, { expires_at: '2026-02-30T10:00:00+00:00' })] }),
    fixture({ authorizations: [hold('a', 'ada', 'bob', 1, { expires_at: '2026-02-10T10:00:00' })] }),
    fixture({ authorizations: [hold('a', 'ada', 'bob', 1), hold('a', 'bob', 'ada', 1)] }),
    fixture({ authorizations: [hold('a', 'cy', 'bob', 300), hold('b', 'cy', 'ada', 201)] }),
    fixture({ authorizations: 'x' }),
  ];
  for (const fx of bad) expectError(assert, await t.call('POST', '/_test/reset', { body: fx }), 422, 'validation_failed');
  assert.equal((await me(w.ada)).total, 10000);
});

test('S2-R12 payments carry authorization_id', async () => {
  const w = await t.world();
  const p = (await t.pay(w.ada, 'bob', 5)).body;
  assert.equal(p.authorization_id, null);
  const a = (await authorize(w.ada, 'bob', 50)).body.authorization_id;
  const c = (await capture(w.bob, a)).body;
  assert.equal(c.authorization_id, a);
  const feed = (await t.call('GET', '/activity', { token: w.cy })).body.payments;
  assert.deepEqual(feed.map((x) => x.authorization_id), [a, null]);
});

test('S2-R11 concurrent captures/voids/holds keep every invariant', async () => {
  const w = await t.world();
  const holds = await Promise.all(Array.from({ length: 30 }, () => authorize(w.ada, 'bob', 1000)));
  assert.equal(holds.filter((r) => r.status === 201).length, 10);
  assert.ok(holds.every((r) => r.status === 201 || r.status === 409));
  const ids = holds.filter((r) => r.status === 201).map((r) => r.body.authorization_id);
  const ops = ids.flatMap((id) => [capture(w.bob, id, { amount: 600, final: false }), capture(w.bob, id, { amount: 600, final: false }), void_(w.ada, id)]);
  const res = await Promise.all(ops);
  assert.ok(res.every((r) => r.status < 500));
  const [a, b, c] = await Promise.all([me(w.ada), me(w.bob), me(w.cy)]);
  assert.equal(a.total + b.total + c.total, 13000);
  assert.ok(a.available >= 0 && a.total === a.balance && a.available === a.total - a.held);
  const list = (await t.call('GET', '/authorizations?limit=200', { token: w.ada })).body.authorizations;
  const captured = list.reduce((s, x) => s + x.captured_amount, 0);
  assert.equal(10000 - a.total, captured);
  for (const x of list) assert.ok(x.captured_amount <= x.amount);
});

test('S2-R13/D34 export is schema 2 and round-trips; stage-1 exports upgrade', async () => {
  const w = await t.world(fixture({ authorization_ttl_seconds: 90 }));
  const a = (await authorize(w.ada, 'bob', 500)).body.authorization_id;
  await capture(w.bob, a, { amount: 100, final: false });
  const key = newKey();
  await authorize(w.cy, 'bob', 10, {}, key);
  const exp = (await t.call('GET', '/_test/export')).body;
  assert.equal(exp.format_version, 1);
  assert.equal(exp.state.schema_version, 2);
  assert.equal(exp.state.authorization_ttl_seconds, 90);
  assert.equal((await t.call('POST', '/_test/import', { body: exp })).status, 204);
  assert.deepEqual((await t.call('GET', '/_test/export')).body, exp);
  assert.equal((await authorize(w.cy, 'bob', 10, {}, key)).status, 200);
  assert.equal((await me(w.ada)).held, 400);
  assert.equal((await capture(w.bob, a, { amount: 400 })).status, 201);
  // a stage-1 export: no authorizations, ttl 600, payments get authorization_id null
  const v1 = JSON.parse(JSON.stringify(exp));
  v1.state.schema_version = 1;
  delete v1.state.authorizations; delete v1.state.authorization_ttl_seconds; delete v1.state.counters.au;
  for (const p of v1.state.payments) delete p.authorization_id;
  v1.state.idempotency = v1.state.idempotency.filter((r) => r.path === '/payments');
  assert.equal((await t.call('POST', '/_test/import', { body: v1 })).status, 204);
  const up = (await t.call('GET', '/_test/export')).body;
  assert.equal(up.state.schema_version, 2);
  assert.equal(up.state.authorization_ttl_seconds, 600);
  assert.ok(up.state.payments.every((p) => p.authorization_id === null));
  assert.equal((await me(w.ada)).held, 0);
  const v3 = JSON.parse(JSON.stringify(exp)); v3.state.schema_version = 3;
  expectError(assert, await t.call('POST', '/_test/import', { body: v3 }), 422, 'validation_failed');
  const bad = JSON.parse(JSON.stringify(exp)); bad.state.authorizations[0].captured_amount = 10 ** 9;
  expectError(assert, await t.call('POST', '/_test/import', { body: bad }), 422, 'validation_failed');
});

test('D39 capture credit above 2^53 is 422, hold stays open, key unclaimed', async () => {
  const big = 2 ** 53;
  const u = (h, balance) => ({ id: `u_${h}`, email: `${h}@example.com`, password: 'correct horse', display_name: h, handle: h, balance });
  const w = await t.world({ currency: 'JPY', minor_units: 0, users: [u('ada', 1000), u('bob', big - 5)] });
  const a = (await authorize(w.ada, 'bob', 100)).body.authorization_id;
  const key = newKey();
  expectError(assert, await capture(w.bob, a, { amount: 6 }, key), 422, 'validation_failed');
  assert.deepEqual([(await me(w.ada)).held, (await me(w.bob)).total], [100, big - 5]);
  assert.equal((await capture(w.bob, a, { amount: 5 }, key)).status, 201);
  assert.equal((await me(w.bob)).total, big);
});

test('D40 import validates numbers inside recorded idempotency responses', async () => {
  const w = await t.world();
  await authorize(w.ada, 'bob', 777);
  const exp = (await t.call('GET', '/_test/export')).body;
  for (const v of [2 ** 53 + 2, 2 ** 60, 1e20, -1, 1.5]) {
    const bad = JSON.parse(JSON.stringify(exp));
    bad.state.idempotency[0].body.amount = v;
    expectError(assert, await t.call('POST', '/_test/import', { body: bad }), 422, 'validation_failed');
  }
  assert.deepEqual((await t.call('GET', '/_test/export')).body, exp);
  assert.equal((await t.call('POST', '/_test/import', { body: exp })).status, 204);
});
