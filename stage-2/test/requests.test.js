'use strict';
// R20–R27 (D5, D11, D24): requests, pay/decline/cancel, listing, splits.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, newKey, expectError } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

const payReq = (tok, id, body = {}, key = newKey()) => t.call('POST', `/requests/${id}/pay`, { token: tok, key, body });

test('R20 request shape; payer balance not checked; error order', async () => {
  const w = await t.world();
  const r = await t.ask(w.bob, 'cy', 999999);
  assert.equal(r.status, 201);
  assert.deepEqual(Object.keys(r.body).sort(), ['amount', 'created_at', 'currency', 'note', 'payer_handle', 'payer_id',
    'payment_id', 'request_id', 'requester_handle', 'requester_id', 'status']);
  assert.equal(r.body.status, 'pending');
  assert.equal(r.body.payment_id, null);
  assert.equal(r.body.requester_handle, 'bob');
  assert.equal(r.body.payer_handle, 'cy');
  const a = (body) => t.call('POST', '/requests', { token: w.bob, key: newKey(), body });
  expectError(assert, await a({ payer_handle: 1, amount: 1 }), 400, 'malformed_request');
  expectError(assert, await a({ payer_handle: 'bob', amount: 0 }), 422, 'validation_failed');
  expectError(assert, await a({ payer_handle: 'bob', amount: 1, note: 3 }), 422, 'self_request');
  expectError(assert, await a({ payer_handle: 'zed', amount: 1, note: 'x'.repeat(201) }), 422, 'validation_failed');
  expectError(assert, await a({ payer_handle: 'zed', amount: 1 }), 404, 'not_found');
});

test('R21 pay a request: payment with request_id, request paid, replay 200', async () => {
  const w = await t.world();
  const rq = (await t.ask(w.bob, 'ada', 1200, { note: 'taxi' })).body;
  const key = newKey();
  const p = await payReq(w.ada, rq.request_id, { visibility: 'private' }, key);
  assert.equal(p.status, 201);
  assert.equal(p.body.request_id, rq.request_id);
  assert.equal(p.body.visibility, 'private');
  assert.equal(p.body.note, 'taxi');
  assert.equal(p.body.to_handle, 'bob');
  const list = (await t.call('GET', '/requests', { token: w.bob })).body.requests;
  assert.equal(list[0].status, 'paid');
  assert.equal(list[0].payment_id, p.body.payment_id);
  const again = await payReq(w.ada, rq.request_id, { visibility: 'private' }, key);
  assert.equal(again.status, 200);
  assert.deepEqual(again.body, p.body);
  expectError(assert, await payReq(w.ada, rq.request_id, {}, key), 409, 'idempotency_key_reuse');
  expectError(assert, await payReq(w.ada, rq.request_id, { visibility: 'private' }), 409, 'request_not_pending');
  assert.equal(await t.balance(w.ada), 8800);
});

test('R21 pay errors: 404, 403 (incl. third party), funds, later payable', async () => {
  const w = await t.world();
  expectError(assert, await payReq(w.ada, 'rq_nope'), 404, 'not_found');
  const rq = (await t.ask(w.bob, 'cy', 600)).body;
  expectError(assert, await payReq(w.ada, rq.request_id), 403, 'forbidden');
  expectError(assert, await payReq(w.bob, rq.request_id), 403, 'forbidden');
  expectError(assert, await payReq(w.cy, rq.request_id), 409, 'insufficient_funds');
  expectError(assert, await payReq(w.cy, rq.request_id, { visibility: 'x' }), 422, 'validation_failed');
  assert.equal((await t.call('GET', '/requests', { token: w.cy })).body.requests[0].status, 'pending');
  await t.pay(w.ada, 'cy', 100);
  assert.equal((await payReq(w.cy, rq.request_id)).status, 201);
  assert.equal(await t.balance(w.cy), 0);
});

test('R22 R23 decline and cancel transitions', async () => {
  const w = await t.world();
  const r1 = (await t.ask(w.bob, 'ada', 10)).body.request_id;
  const act = (tok, id, what) => t.call('POST', `/requests/${id}/${what}`, { token: tok });
  expectError(assert, await act(w.bob, r1, 'decline'), 403, 'forbidden');
  expectError(assert, await act(w.cy, r1, 'decline'), 403, 'forbidden');
  expectError(assert, await act(w.ada, 'zzz', 'decline'), 404, 'not_found');
  let r = await act(w.ada, r1, 'decline');
  assert.equal(r.status, 200);
  assert.equal(r.body.status, 'declined');
  r = await act(w.ada, r1, 'decline');
  assert.equal(r.status, 200);
  assert.equal(r.body.status, 'declined');
  expectError(assert, await act(w.bob, r1, 'cancel'), 409, 'request_not_pending');
  expectError(assert, await payReq(w.ada, r1), 409, 'request_not_pending');

  const r2 = (await t.ask(w.bob, 'ada', 10)).body.request_id;
  expectError(assert, await act(w.ada, r2, 'cancel'), 403, 'forbidden');
  assert.equal((await act(w.bob, r2, 'cancel')).body.status, 'cancelled');
  assert.equal((await act(w.bob, r2, 'cancel')).status, 200);
  expectError(assert, await act(w.ada, r2, 'decline'), 409, 'request_not_pending');

  const r3 = (await t.ask(w.bob, 'ada', 10)).body.request_id;
  await payReq(w.ada, r3);
  expectError(assert, await act(w.ada, r3, 'decline'), 409, 'request_not_pending');
  expectError(assert, await act(w.bob, r3, 'cancel'), 409, 'request_not_pending');
});

test('R24 listing filters, ordering and parameter validation', async () => {
  const w = await t.world();
  const out = (await t.ask(w.ada, 'bob', 1)).body.request_id;
  const inc = (await t.ask(w.bob, 'ada', 2)).body.request_id;
  await t.ask(w.bob, 'cy', 3); // not ada's
  const list = async (qs) => (await t.call('GET', `/requests${qs}`, { token: w.ada })).body;
  assert.deepEqual((await list('')).requests.map((r) => r.request_id), [inc, out]);
  assert.deepEqual((await list('?direction=incoming')).requests.map((r) => r.request_id), [inc]);
  assert.deepEqual((await list('?direction=outgoing')).requests.map((r) => r.request_id), [out]);
  assert.deepEqual((await list('?status=paid')).requests, []);
  assert.equal((await list('?limit=1')).has_more, true);
  for (const qs of ['?direction=both', '?direction=', '?status=open', '?status=PAID', '?limit=0', '?limit=201', '?offset=-1', '?offset=abc', '?limit=1e9']) {
    expectError(assert, await t.call('GET', `/requests${qs}`, { token: w.ada }), 422, 'validation_failed');
  }
});

test('R26 R27 splits: shares, requests, caller handling', async () => {
  const w = await t.world();
  const split = (tok, body, key = newKey()) => t.call('POST', '/splits', { token: tok, key, body });
  let r = await split(w.ada, { amount: 1000, participant_handles: ['ada', 'bob', 'cy'], note: 'dinner' });
  assert.equal(r.status, 201);
  assert.deepEqual(r.body.shares, [{ handle: 'ada', amount: 334 }, { handle: 'bob', amount: 333 }, { handle: 'cy', amount: 333 }]);
  assert.deepEqual(r.body.requests.map((q) => [q.payer_handle, q.amount, q.requester_handle, q.note, q.status]),
    [['bob', 333, 'ada', 'dinner', 'pending'], ['cy', 333, 'ada', 'dinner', 'pending']]);
  assert.equal(r.body.currency, 'EUR');
  r = await split(w.ada, { amount: 1000, participant_handles: ['bob', 'cy', 'ada'] });
  assert.deepEqual(r.body.shares.map((s) => s.amount), [334, 333, 333]);
  assert.equal(r.body.requests[0].amount, 334);
  r = await split(w.ada, { amount: 1, participant_handles: ['ada', 'bob', 'cy'] });
  assert.deepEqual(r.body.shares.map((s) => s.amount), [1, 0, 0]);
  assert.equal(r.body.requests.length, 2);
  assert.equal(r.body.requests[0].amount, 0);
  assert.equal((await payReq(w.bob, r.body.requests[0].request_id)).status, 201);
  for (const [amount, n, exp] of [[10, 3, [4, 3, 3]], [999, 3, [333, 333, 333]], [5, 5, [1, 1, 1, 1, 1]]]) {
    const hs = ['ada', 'bob', 'cy', 'd1', 'd2'].slice(0, n);
    if (n === 5) {
      await t.call('POST', '/auth/signup', { body: { email: 'd1@x.io', password: 'correct horse', display_name: 'D' } });
      await t.call('POST', '/auth/signup', { body: { email: 'd2@x.io', password: 'correct horse', display_name: 'D' } });
    }
    r = await split(w.ada, { amount, participant_handles: hs });
    assert.deepEqual(r.body.shares.map((s) => s.amount), exp);
  }
  r = await split(w.ada, { amount: 50, participant_handles: ['ada'] });
  assert.equal(r.status, 201);
  assert.deepEqual(r.body.requests, []);
  r = await split(w.cy, { amount: 1000000, participant_handles: ['cy', 'ada'] });
  assert.equal(r.status, 201);
  r = await split(w.cy, { amount: 9, participant_handles: ['ada', 'bob'] });
  assert.deepEqual(r.body.shares.map((s) => s.amount), [5, 4]);
  assert.equal(r.body.requests.length, 2);
  assert.equal((await t.call('GET', '/activity', { token: w.cy })).body.payments.length, 1);
});

test('R26 split error order and no partial creation', async () => {
  const w = await t.world();
  const split = (body) => t.call('POST', '/splits', { token: w.ada, key: newKey(), body });
  expectError(assert, await split({ amount: 0, participant_handles: 'bob' }), 400, 'malformed_request');
  expectError(assert, await split({ amount: 0, participant_handles: ['bob', 3] }), 400, 'malformed_request');
  expectError(assert, await split({ amount: 0, participant_handles: [] }), 422, 'validation_failed');
  expectError(assert, await split({ amount: 10 }), 422, 'validation_failed');
  expectError(assert, await split({ amount: 10, participant_handles: [] }), 422, 'validation_failed');
  expectError(assert, await split({ amount: 10, participant_handles: ['bob', 'bob'] }), 422, 'validation_failed');
  expectError(assert, await split({ amount: 10, participant_handles: ['bob', 'zed'], note: 'x'.repeat(201) }), 422, 'validation_failed');
  expectError(assert, await split({ amount: 10, participant_handles: ['bob', 'zed'] }), 404, 'not_found');
  expectError(assert, await split({ amount: 100, participant_handles: Array.from({ length: 1000 }, (_, i) => `h${i}`) }), 404, 'not_found');
  assert.deepEqual((await t.call('GET', '/requests', { token: w.bob })).body.requests, []);
});

test('R27 paying every split request conserves the total', async () => {
  const w = await t.world();
  const r = await t.call('POST', '/splits', { token: w.cy, key: newKey(), body: { amount: 1001, participant_handles: ['ada', 'bob', 'cy'] } });
  for (const q of r.body.requests) {
    const tok = q.payer_handle === 'ada' ? w.ada : w.bob;
    assert.equal((await payReq(tok, q.request_id)).status, 201);
  }
  const sum = (await t.balance(w.ada)) + (await t.balance(w.bob)) + (await t.balance(w.cy));
  assert.equal(sum, w.total);
  assert.equal(await t.balance(w.cy), 500 + 334 + 334);
});
