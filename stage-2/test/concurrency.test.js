'use strict';
// R34, R7, R21 (D22): conservation and non-negativity under 50 concurrent writers.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, fixture, newKey } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

async function oracle(w, toks) {
  const bs = await Promise.all(toks.map((k) => t.balance(k)));
  for (const b of bs) assert.ok(b >= 0, `negative balance ${b}`);
  assert.equal(bs.reduce((a, b) => a + b, 0), w.total);
}

test('R34 opposite-direction transfers between the same pair', async () => {
  const w = await t.world();
  const rs = await Promise.all(Array.from({ length: 50 }, (_, i) => (i % 2 ? t.pay(w.ada, 'bob', 300) : t.pay(w.bob, 'ada', 300))));
  assert.ok(rs.every((r) => r.status === 201 || r.status === 409));
  await oracle(w, [w.ada, w.bob, w.cy]);
});

test('R34 many payers draining one wallet', async () => {
  const w = await t.world();
  const tokens = Array.from({ length: 10 }, () => w.cy);
  const rs = await Promise.all(Array.from({ length: 50 }, () => t.pay(tokens[0], 'bob', 30)));
  assert.equal(rs.filter((r) => r.status === 201).length, 16);
  assert.ok(rs.every((r) => r.status === 201 || r.body.error.code === 'insufficient_funds'));
  assert.equal(await t.balance(w.cy), 20);
  await oracle(w, [w.ada, w.bob, w.cy]);
});

test('R21 R34 many concurrent pays of one request move money once', async () => {
  const w = await t.world();
  const rq = (await t.ask(w.bob, 'ada', 700)).body.request_id;
  const rs = await Promise.all(Array.from({ length: 50 }, () => t.call('POST', `/requests/${rq}/pay`, { token: w.ada, key: newKey(), body: {} })));
  assert.equal(rs.filter((r) => r.status === 201).length, 1);
  assert.ok(rs.filter((r) => r.status !== 201).every((r) => r.body.error.code === 'request_not_pending'));
  assert.equal(await t.balance(w.ada), 9300);
  await oracle(w, [w.ada, w.bob, w.cy]);
});

test('R34 settlements racing payments and splits', async () => {
  const w = await t.world(fixture({ settlement_operator_ids: ['u_ada'] }));
  const jobs = [];
  for (let i = 0; i < 50; i += 1) {
    if (i % 3 === 0) jobs.push(t.call('POST', '/settlements', { token: w.ada, key: newKey(), body: { transfers: [{ from_handle: 'cy', to_handle: 'bob', amount: 90 }, { from_handle: 'bob', to_handle: 'cy', amount: 40 }] } }));
    else if (i % 3 === 1) jobs.push(t.pay(w.cy, 'ada', 25));
    else jobs.push(t.call('POST', '/splits', { token: w.bob, key: newKey(), body: { amount: 10, participant_handles: ['bob', 'cy'] } }));
  }
  const rs = await Promise.all(jobs);
  assert.ok(rs.every((r) => r.status < 500));
  await oracle(w, [w.ada, w.bob, w.cy]);
});
