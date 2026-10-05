'use strict';
// S3: as_of / known_at, statements, snapshots, corrections, revisions, historical holds, upgrade.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, fixture, newKey, expectError } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

const iso = (ms) => new Date(ms).toISOString();
const correct = (token, id, body, key = newKey()) => t.call('POST', `/payments/${id}/corrections`, { token, key, body });
const get = async (token, path) => (await t.call('GET', path, { token })).body;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test('S3 seeded created_at, opening balance, future created_at is 422', async () => {
  const past = iso(Date.now() - 3600e3);
  const w = await t.world(fixture({ payments: [{ id: 'sp1', from_user_id: 'u_ada', to_user_id: 'u_bob', amount: 500, created_at: past }] }));
  assert.equal((await get(w.ada, '/me')).balance, 10000);
  const before = await get(w.ada, `/me?as_of=${encodeURIComponent(iso(Date.now() - 7200e3))}`);
  assert.equal(before.balance, 10500);
  const st = await get(w.ada, '/statement');
  assert.equal(st.opening_balance, 10500); assert.equal(st.closing_balance, 10000);
  assert.equal(st.entries[0].delta, -500);
  const bad = await t.call('POST', '/_test/reset', { body: fixture({ payments: [{ id: 'x', from_user_id: 'u_ada', to_user_id: 'u_bob', amount: 5, created_at: iso(Date.now() + 3600e3) }] }) });
  expectError(assert, bad, 422, 'validation_failed');
  assert.equal((await t.call('GET', '/me', { token: w.ada })).status, 200); // the rejected reset changed nothing
});

test('S3 as_of validation and echo', async () => {
  const w = await t.world();
  for (const v of ['', '2026-01-01', '2026-01-01T10:00:00', '2026-01-01T10:00:00 00:00']) {
    expectError(assert, await t.call('GET', `/me?as_of=${v.replace('+', ' ')}`, { token: w.ada }), 422, 'validation_failed');
  }
  const x = '2999-01-01T00:00:00+00:00';
  const r = await get(w.ada, `/me?as_of=${encodeURIComponent(x)}&known_at=${encodeURIComponent(x)}`);
  assert.equal(r.as_of, x); assert.equal(r.known_at, x);
});

test('S3 correction moves money, revisions, replay, errors', async () => {
  const w = await t.world();
  const p = (await t.pay(w.ada, 'bob', 1000)).body;
  const eff = p.created_at;
  const key = newKey();
  const body = { expected_revision: 1, amount: 400, effective_at: eff, reason: 'oops' };
  const c = await correct(w.ada, p.payment_id, body, key);
  assert.equal(c.status, 201); assert.equal(c.body.revision, 2);
  assert.equal((await get(w.ada, '/me')).balance, 9600); assert.equal((await get(w.bob, '/me')).balance, 2900);
  const again = await correct(w.ada, p.payment_id, body, key);
  assert.equal(again.status, 200); assert.deepEqual(again.body, c.body);
  expectError(assert, await correct(w.ada, p.payment_id, { ...body, amount: 5 }, key), 409, 'idempotency_key_reuse');
  expectError(assert, await correct(w.ada, p.payment_id, body), 409, 'stale_revision');
  expectError(assert, await correct(w.bob, p.payment_id, { ...body, expected_revision: 2 }), 403, 'forbidden');
  expectError(assert, await correct(w.ada, 'p_999', body), 404, 'not_found');
  expectError(assert, await correct(w.ada, p.payment_id, { ...body, expected_revision: 2, reason: '' }), 422, 'validation_failed');
  expectError(assert, await correct(w.ada, p.payment_id, { ...body, expected_revision: 2, effective_at: iso(Date.now() + 3600e3) }), 422, 'validation_failed');
  expectError(assert, await correct(w.ada, p.payment_id, { ...body, expected_revision: 2, amount: 99999 }), 409, 'insufficient_funds');
  const revs = (await get(w.bob, `/payments/${p.payment_id}/revisions`)).revisions;
  assert.deepEqual(revs.map((r) => [r.revision, r.amount, r.reason]), [[1, 1000, ''], [2, 400, 'oops']]);
  assert.ok(Date.parse(revs[1].recorded_at) > Date.parse(revs[0].recorded_at));
  expectError(assert, await t.call('GET', `/payments/${p.payment_id}/revisions`, { token: w.cy }), 404, 'not_found');
  // original receipt and feed unchanged
  assert.equal((await get(w.ada, '/activity')).payments[0].amount, 1000);
  // zero reverses; decreasing debits the receiver
  const z = await correct(w.ada, p.payment_id, { ...body, expected_revision: 2, amount: 0 });
  assert.equal(z.status, 201);
  assert.equal((await get(w.ada, '/me')).balance, 10000);
});

test('S3 known_at selects revisions; statement ordering and zero entries', async () => {
  const w = await t.world();
  const p = (await t.pay(w.ada, 'bob', 1000)).body;
  await sleep(20);
  const mid = iso(Date.now());
  await sleep(20);
  await correct(w.ada, p.payment_id, { expected_revision: 1, amount: 0, effective_at: p.created_at, reason: 'undo' });
  const k = await get(w.ada, `/me?known_at=${encodeURIComponent(mid)}`);
  assert.equal(k.balance, 9000);
  const k0 = await get(w.ada, `/me?known_at=${encodeURIComponent(iso(Date.parse(p.created_at) - 1000))}`);
  assert.equal(k0.balance, 10000);
  const st = await get(w.ada, '/statement');
  assert.equal(st.entries.length, 1); assert.equal(st.entries[0].delta, 0); assert.equal(st.entries[0].revision, 2); assert.equal(st.entries[0].payment.amount, 0);
  const old = await get(w.ada, `/statement?known_at=${encodeURIComponent(mid)}`);
  assert.equal(old.entries[0].delta, -1000);
});

test('S3 statement snapshots page a frozen result', async () => {
  const w = await t.world();
  for (let i = 0; i < 5; i += 1) await t.pay(w.ada, 'bob', 10 + i);
  const first = await get(w.ada, '/statement?limit=2');
  assert.equal(first.entries.length, 2); assert.equal(first.has_more, true);
  await t.pay(w.ada, 'bob', 7);
  const q = (o) => `/statement?snapshot=${first.snapshot}&limit=2&offset=${o}`;
  const p2 = await get(w.ada, q(2)); const p3 = await get(w.ada, q(4));
  assert.equal(p2.entries.length, 2); assert.equal(p3.entries.length, 1); assert.equal(p3.has_more, false);
  assert.equal(p3.closing_balance, first.closing_balance);
  expectError(assert, await t.call('GET', `/statement?snapshot=${first.snapshot}&from=${encodeURIComponent(iso(0))}`, { token: w.ada }), 422, 'validation_failed');
  expectError(assert, await t.call('GET', `/statement?snapshot=${first.snapshot}`, { token: w.bob }), 404, 'not_found');
  expectError(assert, await t.call('GET', '/statement?snapshot=nope', { token: w.ada }), 404, 'not_found');
  assert.equal((await get(w.ada, '/statement')).entries.length, 6);
});

test('S3 settlement members and captures are immutable', async () => {
  const w = await t.world(fixture({ settlement_operator_ids: ['u_ada'] }));
  const s = await t.call('POST', '/settlements', { token: w.ada, key: newKey(), body: { transfers: [{ from_handle: 'ada', to_handle: 'bob', amount: 5 }] } });
  const pid = s.body.payments[0].payment_id;
  expectError(assert, await correct(w.ada, pid, { expected_revision: 1, amount: 1, effective_at: s.body.committed_at, reason: 'r' }), 422, 'linked_payment_immutable');
  const a = (await t.call('POST', '/authorizations', { token: w.ada, key: newKey(), body: { to_handle: 'bob', amount: 50 } })).body;
  const c = (await t.call('POST', `/authorizations/${a.authorization_id}/capture`, { token: w.bob, key: newKey(), body: {} })).body;
  expectError(assert, await correct(w.ada, c.payment_id, { expected_revision: 1, amount: 1, effective_at: c.created_at, reason: 'r' }), 422, 'linked_payment_immutable');
});

test('S3 historical overdraft and concurrent same-revision corrections', async () => {
  const w = await t.world();
  const p = (await t.pay(w.ada, 'bob', 1000)).body;
  await t.pay(w.bob, 'cy', 3500); // bob: 2500 + 1000 - 3500 = 0
  expectError(assert, await correct(w.ada, p.payment_id, { expected_revision: 1, amount: 0, effective_at: p.created_at, reason: 'r' }), 409, 'insufficient_funds');
  const w3 = await t.world();
  const a = (await t.pay(w3.ada, 'bob', 100)).body;
  await sleep(5);
  const b = (await t.pay(w3.bob, 'cy', 2600)).body; // bob ends at 0
  // moving bob's payment before ada's credit would overdraw bob then
  const bad = await correct(w3.bob, b.payment_id, { expected_revision: 1, amount: 2600, effective_at: iso(Date.parse(a.created_at) - 1000), reason: 'y' });
  expectError(assert, bad, 409, 'historical_overdraft');
  assert.equal((await get(w3.bob, `/payments/${b.payment_id}/revisions`)).revisions.length, 1);
  assert.equal((await get(w3.bob, '/me')).balance, 0);
  const pc = await Promise.all([1, 2, 3, 4].map((i) => correct(w3.ada, a.payment_id, { expected_revision: 1, amount: 100 + i, effective_at: a.created_at, reason: 'c' })));
  assert.equal(pc.filter((x) => x.status === 201).length, 1);
});

test('S3 historical holds, closed_at, export v3 round trip, v2 import', async () => {
  const w = await t.world();
  const a = (await t.call('POST', '/authorizations', { token: w.ada, key: newKey(), body: { to_handle: 'bob', amount: 1000 } })).body;
  assert.equal(a.closed_at, null);
  await sleep(10);
  const mid = iso(Date.now());
  await sleep(10);
  await t.call('POST', `/authorizations/${a.authorization_id}/void`, { token: w.ada });
  const now = await get(w.ada, '/me');
  assert.equal(now.held, 0);
  const h = await get(w.ada, `/me?as_of=${encodeURIComponent(mid)}`);
  assert.deepEqual([h.total, h.held, h.available], [10000, 1000, 9000]);
  const list = (await get(w.ada, '/authorizations')).authorizations[0];
  assert.ok(list.closed_at);
  const far = await get(w.ada, `/me?as_of=${encodeURIComponent('2999-01-01T00:00:00+00:00')}`);
  assert.equal(far.held, 0);
  const snap = (await get(w.ada, '/statement')).snapshot;
  const exp = (await t.call('GET', '/_test/export')).body;
  assert.equal(exp.state.schema_version, 4);
  assert.equal((await t.call('POST', '/_test/import', { body: exp })).status, 204);
  assert.deepEqual((await t.call('GET', '/_test/export')).body, exp);
  assert.equal((await get(w.ada, `/me?as_of=${encodeURIComponent(mid)}`)).held, 1000);
  assert.ok(snap);
  // a v2-shaped export (no revisions/openings/histories) upgrades
  const v2 = JSON.parse(JSON.stringify(exp)); v2.state.schema_version = 2;
  for (const u of v2.state.users) delete u.opening;
  for (const p of v2.state.payments) delete p.revisions;
  for (const x of v2.state.authorizations) { delete x.events; delete x.closed_at; }
  assert.equal((await t.call('POST', '/_test/import', { body: v2 })).status, 204);
  assert.equal((await get(w.ada, '/me')).balance, 10000);
});
