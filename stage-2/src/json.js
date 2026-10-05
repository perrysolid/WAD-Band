'use strict';
// JSON parsing that keeps every number's exact literal (D3), plus canonical
// encoding used for idempotent-replay body comparison (§7).

class JNum {
  constructor(literal) {
    this.literal = literal;
  }
}

// Normalise a JSON number literal to { neg, digits, exp } meaning
// (neg ? -1 : 1) * digits * 10^exp, digits without leading/trailing zeros.
function normalise(literal) {
  const m = /^(-)?(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(literal);
  if (!m) return null;
  const frac = m[3] || '';
  let digits = (m[2] + frac).replace(/^0+/, '');
  let exp = BigInt(m[4] || '0') - BigInt(frac.length);
  if (digits === '') return { neg: false, digits: '0', exp: 0n };
  const trimmed = digits.replace(/0+$/, '');
  exp += BigInt(digits.length - trimmed.length);
  digits = trimmed;
  return { neg: !!m[1], digits, exp };
}

// Exact integer value of a JNum if it is integral and |value| <= limit, else null.
function exactInteger(v, limit) {
  if (!(v instanceof JNum)) return null;
  const n = normalise(v.literal);
  if (!n) return null;
  if (n.digits === '0') return 0;
  if (n.exp < 0n) return null;
  if (BigInt(n.digits.length) + n.exp > BigInt(String(limit).length)) return null;
  const big = BigInt(n.digits) * 10n ** n.exp;
  if (big > BigInt(limit)) return null;
  const num = Number(big);
  return n.neg ? -num : num;
}

function parse(text) {
  return JSON.parse(text, function reviver(key, value, ctx) {
    if (typeof value === 'number') return new JNum(ctx && ctx.source !== undefined ? ctx.source : String(value));
    return value;
  });
}

// Canonical string of a parsed value: key order and number spelling do not matter,
// array order does.
function canonical(v) {
  if (v === null) return 'null';
  if (v instanceof JNum) {
    const n = normalise(v.literal);
    if (n.digits === '0') return 'n0';
    return 'n' + (n.neg ? '-' : '') + n.digits + 'e' + n.exp.toString();
  }
  if (typeof v === 'string') return JSON.stringify(v);
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
  const keys = Object.keys(v).sort();
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}';
}

function isObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v) && !(v instanceof JNum);
}

module.exports = { JNum, parse, canonical, exactInteger, normalise, isObject };
