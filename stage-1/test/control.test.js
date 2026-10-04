'use strict';
// R1, R3–R6, R12, R30–R32, R35, R36 (D10, D12–D14, D18, D21): reset, export/import, routing.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, fixture, newKey, expectError, ADA, BOB, CY } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

test('R1 health', async () => {
  const r = await t.call('GET', '/health');
  assert.equal(r.status, 200);
  assert.deepEqual(r.body, { status: 'ok' });
});

test('R3 reset replaces all state; old tokens are 401', async () => {
  const w = await t.world();
  await t.pay(w.ada, 'bob', 10);
  await t.reset();
  expectError(assert, await t.call('GET', '/me', { token: w.ada }), 401, 'unauthenticated');
  const ada = await t.login('ada@example.com');
  assert.equal((await t.call('GET', '/activity', { token: ada })).body.payments.length, 0);
  assert.equal((await t.call('GET', '/_test/reset')).status, 404);
});

test('R4 invalid fixtures are 422 and change nothing', async () => {
  const w = await t.world();
  const bad = [
    fixture({ users: [{ ...ADA, balance: -1 }, BOB] }),
    fixture({ minor_units: 1 }),
    fixture({ currency: '' }),
    fixture({ users: [ADA, { ...BOB, handle: 'ada' }] }),
    fixture({ users: [ADA, { ...BOB, id: 'u_ada' }] }),
    fixture({ users: [ADA, { ...BOB, email: 'ADA@example.com' }] }),
    fixture({ users: [ADA, { ...BOB, handle: 'Bob' }] }),
    fixture({ users: [ADA, { ...BOB, balance: 1.5 }] }),
    fixture({ payments: [{ id: 'p1', from_user_id: 'u_ada', to_user_id: 'u_zed', amount: 1 }] }),
    fixture({ requests: [{ id: 'r1', requester_id: 'u_ada', payer_id: 'u_bob', amount: 1, status: 'open' }] }),
    fixture({ settlement_operator_ids: ['u_zed'] }),
    { minor_units: 2, users: [] },
  ];
  for (const fx of bad) {
    expectError(assert, await t.call('POST', '/_test/reset', { body: fx }), 422, 'validation_failed');
  }
  expectError(assert, await t.call('POST', '/_test/reset', { raw: '{' }), 400, 'malformed_request');
  // D18: a fixture that parses but is not an object is a fixture error
  for (const raw of ['[]', '"x"', '7', 'null']) {
    expectError(assert, await t.call('POST', '/_test/reset', { raw }), 422, 'validation_failed');
    expectError(assert, await t.call('POST', '/_test/import', { raw }), 422, 'validation_failed');
  }
  assert.equal(await t.balance(w.ada), 10000);
});

test('R5 seeded payments, requests and operators', async () => {
  const fx = fixture({
    payments: [{ id: 'p_1', from_user_id: 'u_ada', to_user_id: 'u_bob', amount: 500, note: 'coffee', visibility: 'private' }],
    requests: [
      { id: 'rq_1', requester_id: 'u_bob', payer_id: 'u_ada', amount: 1200, note: 'taxi', status: 'pending' },
      { id: 'rq_2', requester_id: 'u_bob', payer_id: 'u_ada', amount: 1, note: '', status: 'declined' },
    ],
    settlement_operator_ids: ['u_cy'],
  });
  const w = await t.world(fx);
  assert.equal(await t.balance(w.ada), 10000);
  const feedAda = (await t.call('GET', '/activity', { token: w.ada })).body.payments;
  assert.equal(feedAda.length, 1);
  assert.equal(feedAda[0].payment_id, 'p_1');
  assert.equal(feedAda[0].note, 'coffee');
  assert.equal((await t.call('GET', '/activity', { token: w.cy })).body.payments.length, 0);
  const p = await t.call('POST', '/requests/rq_1/pay', { token: w.ada, key: newKey(), body: {} });
  assert.equal(p.status, 201);
  assert.notEqual(p.body.payment_id, 'p_1');
  expectError(assert, await t.call('POST', '/requests/rq_2/pay', { token: w.ada, key: newKey(), body: {} }), 409, 'request_not_pending');
  const st = await t.call('POST', '/settlements', { token: w.cy, key: newKey(), body: { transfers: [{ from_handle: 'ada', to_handle: 'bob', amount: 1 }] } });
  assert.equal(st.status, 201);
  // ids continue without collision with seeded ids
  const r = await t.ask(w.bob, 'ada', 5);
  assert.ok(!['rq_1', 'rq_2'].includes(r.body.request_id));
  assert.ok(r.body.request_id.length <= 64);
});

test('R30 R31 R32 export/import round trip into a fresh instance', async () => {
  const w = await t.world(fixture({ settlement_operator_ids: ['u_ada'] }));
  const pkey = newKey();
  const p = await t.pay(w.ada, 'bob', 100, { note: '😀' }, pkey);
  const failedKey = newKey();
  await t.pay(w.cy, 'bob', 999999, {}, failedKey);
  const rq = (await t.ask(w.bob, 'ada', 50)).body;
  const sp = await t.call('POST', '/splits', { token: w.ada, key: newKey(), body: { amount: 10, participant_handles: ['bob', 'cy'] } });
  const st = await t.call('POST', '/settlements', { token: w.ada, key: newKey(), body: { transfers: [{ from_handle: 'bob', to_handle: 'cy', amount: 7 }] } });
  const signup = await t.call('POST', '/auth/signup', { body: { email: 'dee@x.io', password: 'correct horse', display_name: 'Dee' } });
  const ex = await t.call('GET', '/_test/export');
  assert.equal(ex.status, 200);
  assert.equal(ex.body.track, 'pocketful');
  assert.equal(ex.body.format_version, 1);
  // snapshot is not affected by later writes
  const snapshot = JSON.stringify(ex.body);
  await t.pay(w.ada, 'bob', 1);
  const t2 = await start();
  try {
    await t2.reset(fixture({ users: [{ ...ADA, id: 'u_other', handle: 'other', email: 'other@x.io' }] }));
    const otherTok = await t2.login('other@x.io');
    assert.equal((await t2.call('POST', '/_test/import', { raw: snapshot })).status, 204);
    assert.equal((await t2.call('POST', '/_test/import', { raw: snapshot })).status, 204);
    expectError(assert, await t2.call('GET', '/me', { token: otherTok }), 401, 'unauthenticated');
    assert.equal((await t2.call('GET', '/me', { token: w.ada })).body.balance, 10000 - 100 - 0);
    assert.equal((await t2.call('GET', '/me', { token: signup.body.token })).body.handle, 'dee');
    assert.equal((await t2.call('POST', '/auth/login', { body: { email: 'bob@example.com', password: 'correct horse' } })).status, 200);
    const replay = await t2.pay(w.ada, 'bob', 100, { note: '😀' }, pkey);
    assert.equal(replay.status, 200);
    assert.deepEqual(replay.body, p.body);
    expectError(assert, await t2.pay(w.ada, 'bob', 101, {}, pkey), 409, 'idempotency_key_reuse');
    assert.equal((await t2.pay(w.cy, 'bob', 10, {}, failedKey)).status, 201);
    const feed = (await t2.call('GET', '/activity', { token: w.bob })).body.payments;
    assert.equal(feed.length, 3); // the freed-key payment, the settlement member, the original payment
    assert.equal(feed[1].settlement_id, st.body.settlement_id);
    assert.equal(feed[1].created_at, st.body.committed_at);
    assert.deepEqual(feed[2], p.body);
    const reqs = (await t2.call('GET', '/requests', { token: w.bob })).body.requests;
    assert.deepEqual(reqs.map((r) => r.request_id).sort(), [rq.request_id, sp.body.requests[0].request_id].sort());
    // sequences continue without collision
    const np = await t2.pay(w.ada, 'bob', 1);
    assert.ok(![p.body.payment_id, st.body.payments[0].payment_id].includes(np.body.payment_id));
    const nr = await t2.ask(w.bob, 'ada', 1);
    assert.ok(!reqs.map((r) => r.request_id).includes(nr.body.request_id));
    const ns = await t2.call('POST', '/auth/signup', { body: { email: 'eve@x.io', password: 'correct horse', display_name: 'Eve' } });
    assert.notEqual(ns.body.user_id, signup.body.user_id);
    const nst = await t2.call('POST', '/settlements', { token: w.ada, key: newKey(), body: { transfers: [{ from_handle: 'bob', to_handle: 'cy', amount: 1 }] } });
    assert.notEqual(nst.body.settlement_id, st.body.settlement_id);
    // reset clears imported state
    await t2.reset();
    expectError(assert, await t2.call('GET', '/me', { token: signup.body.token }), 401, 'unauthenticated');
  } finally {
    await t2.close();
  }
});

test('R31 invalid imports are rejected and change nothing', async () => {
  const w = await t.world();
  const ex = (await t.call('GET', '/_test/export')).body;
  const bad = [
    {}, { track: 'pocketful', format_version: 1 }, { ...ex, track: 'other' }, { ...ex, format_version: 2 },
    { ...ex, state: {} }, { ...ex, state: { ...ex.state, schema_version: 99 } },
    { ...ex, state: { ...ex.state, users: [{ ...ex.state.users[0], balance: -5 }] } },
    { ...ex, state: { ...ex.state, tokens: [{ digest: 'zz', user_id: 'u_ada' }] } },
  ];
  for (const b of bad) expectError(assert, await t.call('POST', '/_test/import', { body: b }), 422, 'validation_failed');
  expectError(assert, await t.call('POST', '/_test/import', { raw: '{nope' }), 400, 'malformed_request');
  assert.equal(await t.balance(w.ada), 10000);
});

test('R12 large seeded reset is fast and logins work (D14)', async () => {
  const users = Array.from({ length: 10000 }, (_, i) => ({ id: `u${i}`, email: `u${i}@x.io`, password: 'correct horse', display_name: `U${i}`, handle: `u${i}`, balance: 1 }));
  let t0 = Date.now();
  await t.reset(fixture({ users }));
  assert.ok(Date.now() - t0 < 3000, `reset took ${Date.now() - t0} ms`);
  assert.ok(await t.login('u9999@x.io'));
  const distinct = users.slice(0, 2000).map((u, i) => ({ ...u, password: `pw-${i}-secret` }));
  t0 = Date.now();
  await t.reset(fixture({ users: distinct }));
  assert.ok(Date.now() - t0 < 8000, `distinct reset took ${Date.now() - t0} ms`);
  assert.ok(await t.login('u1999@x.io', 'pw-1999-secret'));
  t0 = Date.now();
  const logins = await Promise.all(Array.from({ length: 50 }, (_, i) => t.call('POST', '/auth/login', { body: { email: `u${i}@x.io`, password: `pw-${i}-secret` } })));
  assert.ok(logins.every((r) => r.status === 200));
  assert.ok(Date.now() - t0 < 5000);
});

test('R35 R36 unknown routes and later-stage surfaces are 404', async () => {
  const w = await t.world();
  for (const [m, p] of [['GET', '/'], ['GET', '/authorizations'], ['POST', '/authorizations'], ['GET', '/statement'],
    ['POST', '/payments/p_1/refunds'], ['POST', '/payments/p_1/corrections'], ['GET', '/payments/p_1/revisions'],
    ['POST', '/correction-batches'], ['GET', '/payments'], ['DELETE', '/me'], ['PUT', '/requests'], ['GET', '/requests/x']]) {
    expectError(assert, await t.call(m, p, { token: w.ada }), 404, 'not_found');
  }
  const me = await t.call('GET', '/me', { token: w.ada });
  for (const k of ['total', 'available', 'held']) assert.ok(!(k in me.body));
  const html = await t.call('GET', '/requests', { token: w.ada, headers: { Accept: 'text/html' } });
  assert.equal(html.headers.get('content-type'), 'application/json; charset=utf-8');
  const p = await t.pay(w.ada, 'bob', 1);
  for (const k of ['authorization_id', 'refund_of']) assert.ok(!(k in p.body));
});
