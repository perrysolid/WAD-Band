'use strict';
const { createServer } = require('../src/app');

let seq = 0;
const newKey = () => `k-${process.pid}-${Date.now()}-${++seq}`;

const ADA = { id: 'u_ada', email: 'ada@example.com', password: 'correct horse', display_name: 'Ada', handle: 'ada', balance: 10000 };
const BOB = { id: 'u_bob', email: 'bob@example.com', password: 'correct horse', display_name: 'Bob', handle: 'bob', balance: 2500 };
const CY = { id: 'u_cy', email: 'cy@example.com', password: 'correct horse', display_name: 'Cy', handle: 'cy', balance: 500 };

function fixture(extra = {}) {
  return { currency: 'EUR', minor_units: 2, users: [ADA, BOB, CY], payments: [], requests: [], ...extra };
}

async function start(opts = {}) {
  // The access log is silenced unless a test supplies its own sink.
  const { server, app } = createServer({ log: { out() {}, err: (l) => process.stderr.write(l + '\n') }, ...opts });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;

  async function call(method, path, { body, raw, token, key, headers = {} } = {}) {
    const h = { ...headers };
    if (token) h.Authorization = `Bearer ${token}`;
    if (key !== undefined) h['Idempotency-Key'] = key;
    let payload;
    if (raw !== undefined) payload = raw;
    else if (body !== undefined) payload = JSON.stringify(body);
    if (payload !== undefined) h['Content-Type'] = 'application/json';
    const res = await fetch(base + path, { method, headers: h, body: payload });
    const text = await res.text();
    let json = null;
    try { json = text ? JSON.parse(text) : null; } catch { json = text; }
    return { status: res.status, body: json, headers: res.headers };
  }

  async function reset(fx = fixture()) {
    const r = await call('POST', '/_test/reset', { body: fx });
    if (r.status !== 204) throw new Error(`reset failed ${r.status} ${JSON.stringify(r.body)}`);
  }

  async function login(email, password = 'correct horse') {
    const r = await call('POST', '/auth/login', { body: { email, password } });
    if (r.status !== 200) throw new Error(`login failed ${r.status}`);
    return r.body.token;
  }

  async function world(fx = fixture()) {
    await reset(fx);
    const w = { total: fx.users.reduce((a, u) => a + u.balance, 0) };
    for (const u of fx.users) w[u.handle] = await login(u.email, u.password);
    return w;
  }

  async function balance(token) {
    const r = await call('GET', '/me', { token });
    return r.body.balance;
  }

  const pay = (token, to, amount, extra = {}, key = newKey()) => call('POST', '/payments', { token, key, body: { to_handle: to, amount, ...extra } });
  const ask = (token, payer, amount, extra = {}, key = newKey()) => call('POST', '/requests', { token, key, body: { payer_handle: payer, amount, ...extra } });

  return { base, app, call, reset, login, world, balance, pay, ask, close: () => new Promise((r) => { server.closeAllConnections(); server.close(r); }) };
}

function expectError(assert, res, status, code) {
  assert.equal(res.status, status, JSON.stringify(res.body));
  assert.equal(res.body.error.code, code, JSON.stringify(res.body));
  assert.equal(typeof res.body.error.message, 'string');
}

module.exports = { start, fixture, newKey, ADA, BOB, CY, expectError };
