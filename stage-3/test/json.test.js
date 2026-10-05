'use strict';
// R16, R19 (D3): literal-exact amounts and value-based canonical bodies.
const test = require('node:test');
const assert = require('node:assert/strict');
const { parse, canonical, exactInteger } = require('../src/json');

const amt = (lit) => exactInteger(parse(`{"a":${lit}}`).a, 1000000000);

test('R16 integral literals in any spelling are exact integers', () => {
  for (const lit of ['1000', '1000.0', '1e3', '1.0e3', '10000e-1', '1E3', '1e+3', '1000000000', '1e9']) {
    assert.equal(amt(lit), lit.startsWith('1e9') || lit === '1000000000' ? 1e9 : 1000, lit);
  }
  assert.equal(amt('-1'), -1);
  assert.equal(amt('0'), 0);
  assert.equal(amt('-0'), 0);
});

test('R16 fractional, huge and out-of-range literals are rejected', () => {
  for (const lit of ['1000.5', '1e-1', '1000.0000000000001', '1000.00000000000000001', '1e400', '1000000001', '1e10', '99999999999999999999']) {
    assert.equal(amt(lit), null, lit);
  }
});

test('R19 canonical form ignores key order, whitespace and number spelling', () => {
  const a = canonical(parse('{"to_handle":"bob","amount":1500}'));
  const b = canonical(parse('{ "amount" : 1.5e3, "to_handle" : "bob" }'));
  const c = canonical(parse('{"amount":1500.0,"to_handle":"bob"}'));
  assert.equal(a, b);
  assert.equal(a, c);
  assert.notEqual(canonical(parse('{}')), canonical(parse('{"visibility":"public"}')));
  assert.notEqual(canonical(parse('{"x":[1,2]}')), canonical(parse('{"x":[2,1]}')));
  assert.notEqual(canonical(parse('{"x":"1"}')), canonical(parse('{"x":1}')));
});

test('D3 NaN and Infinity are not JSON', () => {
  assert.throws(() => parse('{"a":NaN}'));
  assert.throws(() => parse('{"a":Infinity}'));
});
