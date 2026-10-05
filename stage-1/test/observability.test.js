'use strict';
// W1-X (product extra): X-Request-Id on every response, one structured JSON log
// line per request, and the invariant guard's refusal logs the request_id.
const test = require('node:test');
const assert = require('node:assert/strict');
const net = require('node:net');
const { start, newKey, expectError } = require('./helpers');

let t;
const out = [];
const err = [];
test.before(async () => {
  t = await start({ log: { out: (line) => out.push(line), err: (line) => err.push(line) } });
});
test.after(() => t.close());

const LOG_KEYS = ['duration_ms', 'idempotency', 'method', 'path', 'request_id', 'status', 'ts', 'user_id'];

// The log line is written when the response finishes; give the event loop a turn.
const settle = () => new Promise((r) => setImmediate(r));

async function logFor(res) {
  await settle();
  const id = res.headers.get('x-request-id');
  const lines = out.map((l) => JSON.parse(l)).filter((l) => l.request_id === id);
  assert.equal(lines.length, 1, `exactly one log line for ${id}`);
  return lines[0];
}

function rawRequest(text) {
  return new Promise((resolve, reject) => {
    const s = net.connect(new URL(t.base).port, '127.0.0.1');
    let data = '';
    s.on('data', (c) => { data += c; });
    s.on('end', () => resolve(data));
    s.on('error', reject);
    s.write(text);
  });
}

test('W1-X every response carries a distinct X-Request-Id of at most 64 characters', async () => {
  const w = await t.world();
  const responses = [
    await t.call('GET', '/health'),
    await t.call('POST', '/_test/reset', { body: { currency: 'EUR', minor_units: 2, users: [] } }), // 204
    await t.call('GET', '/nope'), // 404
    await t.call('GET', '/me'), // 401
    await t.call('POST', '/payments', { token: w.ada, body: {} }), // 400 missing key
  ];
  assert.equal(responses[1].status, 204);
  const ids = responses.map((r) => r.headers.get('x-request-id'));
  for (const id of ids) {
    assert.equal(typeof id, 'string');
    assert.ok(id.length >= 1 && id.length <= 64, id);
  }
  assert.equal(new Set(ids).size, ids.length);
});

test('W1-X a malformed HTTP request still gets an X-Request-Id', async () => {
  const text = await rawRequest('NOT A REQUEST\r\n\r\n');
  assert.match(text, /^HTTP\/1\.1 400/);
  assert.match(text, /\r\nX-Request-Id: [^\r\n]{1,64}\r\n/i);
});

test('W1-X one log line per request with exactly the agreed fields', async () => {
  const w = await t.world();
  const key = newKey();
  const first = await t.call('POST', '/payments?trace=1', { token: w.ada, key, body: { to_handle: 'bob', amount: 10, note: 'secret-note' } });
  assert.equal(first.status, 201);
  const l1 = await logFor(first);
  assert.deepEqual(Object.keys(l1).sort(), LOG_KEYS);
  assert.equal(l1.method, 'POST');
  assert.equal(l1.path, '/payments');
  assert.equal(l1.status, 201);
  assert.equal(l1.user_id, 'u_ada');
  assert.equal(l1.idempotency, 'first');
  assert.equal(typeof l1.duration_ms, 'number');
  assert.ok(l1.duration_ms >= 0);
  assert.match(l1.ts, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/);

  const replay = await t.call('POST', '/payments', { token: w.ada, key, body: { to_handle: 'bob', amount: 10, note: 'secret-note' } });
  assert.equal(replay.status, 200);
  assert.equal((await logFor(replay)).idempotency, 'replay');

  const reuse = await t.call('POST', '/payments', { token: w.ada, key, body: { to_handle: 'bob', amount: 11 } });
  expectError(assert, reuse, 409, 'idempotency_key_reuse');
  assert.equal((await logFor(reuse)).idempotency, 'none');

  const me = await t.call('GET', '/me', { token: w.ada });
  const l2 = await logFor(me);
  assert.equal(l2.idempotency, 'none');
  assert.equal(l2.user_id, 'u_ada');

  const anon = await t.call('GET', '/me', { token: 'bogus' });
  const l3 = await logFor(anon);
  assert.equal(l3.user_id, null);
  assert.equal(l3.status, 401);

  const missing = await t.call('GET', '/nowhere?x=1');
  const l4 = await logFor(missing);
  assert.equal(l4.path, '/nowhere');
  assert.equal(l4.status, 404);
});

test('W1-X log lines never contain bodies, tokens, passwords or keys', async () => {
  const w = await t.world();
  const key = `key-${newKey()}`;
  await t.call('POST', '/payments', { token: w.ada, key, body: { to_handle: 'bob', amount: 10, note: 'secret-note' } });
  const su = await t.call('POST', '/auth/signup', { body: { email: 'zed@example.com', password: 'hunter2-hunter2', display_name: 'Zed' } });
  assert.equal(su.status, 201);
  await t.call('POST', '/auth/login', { body: { email: 'ada@example.com', password: 'correct horse' } });
  await settle();
  const all = out.join('\n') + err.join('\n');
  for (const secret of [w.ada, su.body.token, key, 'secret-note', 'hunter2-hunter2', 'correct horse', 'zed@example.com']) {
    assert.ok(!all.includes(secret), `log leaks ${secret}`);
  }
});

test('W1-X the invariant guard refusal logs the request_id and changes nothing', async () => {
  const w = await t.world();
  const s = t.app.getState();
  const real = s.applyTransfers;
  // Simulate a logic bug: the handler asks for a transfer that does not net to zero.
  s.applyTransfers = function broken(transfers) {
    return real.call(this, [...transfers, { from: 'u_cy', to: 'u_cy', amount: -1 }]);
  };
  let res;
  try {
    res = await t.pay(w.ada, 'bob', 10);
  } finally {
    s.applyTransfers = real;
  }
  assert.equal(res.status, 500);
  assert.equal(res.body.error.code, 'invariant_violation');
  const id = res.headers.get('x-request-id');
  await settle();
  const refusal = err.map((l) => JSON.parse(l)).find((l) => l.request_id === id);
  assert.ok(refusal, 'refusal is logged with the request_id');
  assert.equal(refusal.code, 'invariant_violation');
  assert.equal((await logFor(res)).status, 500);
  assert.equal(await t.balance(w.ada), 10000);
  assert.equal(await t.balance(w.bob), 2500);
});

test('W1-F a client that disconnects mid-body yields exactly one log line, with a null status', async () => {
  const before = out.length;
  await new Promise((resolve) => {
    const s = net.connect(new URL(t.base).port, '127.0.0.1');
    s.on('error', () => {});
    s.write('POST /payments HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{"to_hand');
    setTimeout(() => { s.destroy(); setTimeout(resolve, 200); }, 100);
  });
  const lines = out.slice(before).map((l) => JSON.parse(l));
  assert.equal(lines.length, 1, JSON.stringify(lines));
  assert.equal(lines[0].method, 'POST');
  assert.equal(lines[0].path, '/payments');
  assert.equal(lines[0].status, null);
});
