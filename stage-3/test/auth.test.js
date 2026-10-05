'use strict';
// R9–R13, R11 (D8, D17): signup, login, bearer tokens, /me.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, fixture, expectError } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

test('R9 signup returns 201, derives the handle and starts at zero', async () => {
  await t.reset();
  const r = await t.call('POST', '/auth/signup', { body: { email: 'Dee.Ann+tag@example.com', password: 'correct horse', display_name: 'Dee' } });
  assert.equal(r.status, 201);
  assert.deepEqual(Object.keys(r.body).sort(), ['display_name', 'token', 'user_id']);
  const me = await t.call('GET', '/me', { token: r.body.token });
  assert.equal(me.body.handle, 'dee_ann_tag');
  assert.equal(me.body.balance, 0);
});

test('R9 derived handle truncates to 20 characters', async () => {
  await t.reset();
  const r = await t.call('POST', '/auth/signup', { body: { email: 'abcdefghijklmnopqrstuvwxyz@x.io', password: 'correct horse', display_name: 'A' } });
  const me = await t.call('GET', '/me', { token: r.body.token });
  assert.equal(me.body.handle, 'abcdefghijklmnopqrst');
});

test('R9 signup errors in D1 order', async () => {
  await t.reset();
  const s = (body) => t.call('POST', '/auth/signup', { body });
  expectError(assert, await s({ email: 1, password: 'short', display_name: 'X' }), 400, 'malformed_request');
  expectError(assert, await s({ email: 'bad', password: 'short', display_name: 'X' }), 422, 'validation_failed');
  expectError(assert, await s({ email: 'bad', password: 'correct horse', display_name: 'X' }), 422, 'validation_failed');
  expectError(assert, await s({ email: 'a@b@c', password: 'correct horse', display_name: 'X' }), 422, 'validation_failed');
  expectError(assert, await s({ email: 'new@example.com', password: 'correct horse' }), 422, 'validation_failed');
  expectError(assert, await s({ email: 'ADA@example.com', password: 'correct horse', display_name: 'X' }), 409, 'email_taken');
  expectError(assert, await s({ email: 'ada@other.org', password: 'correct horse', display_name: 'X' }), 409, 'handle_taken');
  // no account was created by the handle_taken failure: the email is still free
  expectError(assert, await t.call('POST', '/auth/login', { body: { email: 'ada@other.org', password: 'correct horse' } }), 401, 'unauthenticated');
  assert.equal((await s({ email: 'Ada@other.org', password: 'short' })).status, 422);
  expectError(assert, await t.call('POST', '/auth/signup', { raw: '{"email":' }), 400, 'malformed_request');
  expectError(assert, await t.call('POST', '/auth/signup', { raw: '[]' }), 400, 'malformed_request');
});

test('R9 a password of exactly 8 code points is accepted', async () => {
  await t.reset();
  const r = await t.call('POST', '/auth/signup', { body: { email: 'e@x.io', password: '😀😀😀😀😀😀😀😀', display_name: 'E' } });
  assert.equal(r.status, 201);
  expectError(assert, await t.call('POST', '/auth/signup', { body: { email: 'f@x.io', password: '1234567', display_name: 'F' } }), 422, 'validation_failed');
});

test('R10 login issues a new token each time and all stay valid', async () => {
  await t.reset();
  const a = await t.login('ada@example.com');
  const b = await t.login('ADA@EXAMPLE.COM');
  assert.notEqual(a, b);
  assert.equal((await t.call('GET', '/me', { token: a })).status, 200);
  assert.equal((await t.call('GET', '/me', { token: b })).status, 200);
  expectError(assert, await t.call('POST', '/auth/login', { body: { email: 'ada@example.com', password: 'wrong horse' } }), 401, 'unauthenticated');
  expectError(assert, await t.call('POST', '/auth/login', { body: { email: 'nobody@example.com', password: 'correct horse' } }), 401, 'unauthenticated');
});

test('R11 missing, malformed and unknown tokens are 401', async () => {
  await t.reset();
  const tok = await t.login('ada@example.com');
  expectError(assert, await t.call('GET', '/me'), 401, 'unauthenticated');
  expectError(assert, await t.call('GET', '/me', { headers: { Authorization: `Basic ${tok}` } }), 401, 'unauthenticated');
  expectError(assert, await t.call('GET', '/me', { headers: { Authorization: `Bearer  ${tok}` } }), 401, 'unauthenticated');
  expectError(assert, await t.call('GET', '/me', { token: 'nope' }), 401, 'unauthenticated');
  assert.equal((await t.call('GET', '/me', { headers: { Authorization: `bearer ${tok}` } })).status, 200);
  for (const [m, p] of [['GET', '/activity'], ['GET', '/requests'], ['POST', '/payments'], ['POST', '/splits'], ['POST', '/settlements'], ['POST', '/requests/x/decline']]) {
    expectError(assert, await t.call(m, p, m === 'GET' ? {} : { body: {} }), 401, 'unauthenticated');
  }
});

test('R13 /me has exactly the specified fields', async () => {
  await t.reset(fixture({ currency: 'JPY', minor_units: 0 }));
  const me = await t.call('GET', '/me', { token: await t.login('ada@example.com') });
  assert.deepEqual(me.body, { user_id: 'u_ada', display_name: 'Ada', handle: 'ada', balance: 10000, total: 10000, available: 10000, held: 0, currency: 'JPY', minor_units: 0 });
  assert.equal(me.headers.get('content-type'), 'application/json; charset=utf-8');
});

test('R12 passwords never appear in plaintext in the export', async () => {
  await t.reset();
  const ex = await t.call('GET', '/_test/export');
  assert.ok(!JSON.stringify(ex.body).includes('correct horse'));
});
