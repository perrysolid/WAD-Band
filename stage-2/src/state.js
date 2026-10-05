'use strict';
// In-memory service state (D22). All mutation happens synchronously inside one
// event-loop turn; this module never awaits.

const { COST_LADDER } = require('./hash');

const SCHEMA = 'pocketful-state';
const SCHEMA_VERSION = 2;
const MAX_TTL = 1e10;               // seconds; keeps expires_at inside the D40 time range
const MAX_BALANCE = 2 ** 53;
const MAX_AMOUNT = 1000000000;
const MAX_TIME = 253402300799999; // 9999-12-31T23:59:59.999Z, the widest D10 instant (D40)
const MAX_COUNTER = 2 ** 50;        // counters stay exact integers with room to grow (D40)
const HANDLE_RE = /^[a-z0-9_]{1,20}$/;
const VISIBILITIES = new Set(['public', 'private']);
const STATUSES = new Set(['pending', 'paid', 'declined', 'cancelled']);
const AUTH_STATUSES = new Set(['open', 'captured', 'voided', 'expired']);

class Invalid extends Error {}
function need(cond, msg) {
  if (!cond) throw new Invalid(msg);
}

class State {
  constructor(currency, minorUnits) {
    this.currency = currency;
    this.minor_units = minorUnits;
    this.clock = 0;
    this.counters = { u: 0, p: 0, rq: 0, sp: 0, st: 0, au: 0, seq: 0 };
    this.users = new Map();      // id -> user
    this.byHandle = new Map();   // handle -> user
    this.byEmail = new Map();    // lower(email) -> user
    this.tokens = new Map();     // sha256(token) -> user id
    this.payments = [];          // creation order
    this.paymentById = new Map();
    this.requests = [];          // creation order
    this.requestById = new Map();
    this.splits = new Map();
    this.settlements = new Map();
    this.operators = new Set();
    this.idem = new Map();       // scope key -> record
    this.ttl = 600;              // authorization_ttl_seconds (S2-R2)
    this.authorizations = [];    // creation order
    this.authById = new Map();
    this.openAuths = new Map();  // id -> auth whose stored status is 'open'
  }

  now() {
    const t = Math.max(Date.now(), this.clock);
    if (t > MAX_TIME) throw new InvariantError('invariant: clock beyond 9999-12-31');
    this.clock = t;
    return t;
  }

  nextSeq() {
    this.counters.seq += 1;
    return this.counters.seq;
  }

  // D12: <prefix>_<counter>, skipping ids already in use.
  newId(prefix, taken) {
    let id;
    do {
      this.counters[prefix] += 1;
      id = `${prefix}_${this.counters[prefix]}`;
    } while (taken.has(id));
    return id;
  }

  addUser(u) {
    this.users.set(u.id, u);
    this.byHandle.set(u.handle, u);
    this.byEmail.set(u.email.toLowerCase(), u);
  }

  addPayment(p) {
    this.payments.push(p);
    this.paymentById.set(p.payment_id, p);
  }

  addRequest(r) {
    this.requests.push(r);
    this.requestById.set(r.request_id, r);
  }

  addAuthorization(a) {
    this.authorizations.push(a);
    this.authById.set(a.authorization_id, a);
    if (a.status === 'open') this.openAuths.set(a.authorization_id, a);
  }

  // D32: every open hold whose expires_at has passed releases its remainder. Called at
  // the start of each request's synchronous section; there is no background job.
  expire(now) {
    for (const [id, a] of this.openAuths) {
      if (a.expires_at <= now) { a.status = 'expired'; this.openAuths.delete(id); }
    }
  }

  held(userId) {
    let h = 0;
    for (const a of this.openAuths.values()) if (a.from_user_id === userId) h += a.amount - a.captured_amount;
    return h;
  }

  available(userId) {
    return this.users.get(userId).balance - this.held(userId);
  }

  // D39: would `balance + d` stay within [0, 2^53]? Exact: every operand is an
  // integer of magnitude ≤ 2^53, so neither comparison rounds.
  fits(userId, d) {
    const b = this.users.get(userId).balance;
    return d >= 0 ? b <= MAX_BALANCE - d : b >= -d;
  }

  // Apply a set of transfers atomically. Defence in depth (D22, D39): the deltas
  // must net to zero and every touched balance must stay within [0, 2^53];
  // otherwise nothing is applied and an error is thrown.
  // `release` (userId -> amount) is hold money consumed by this very step (a capture), so a
  // wallet may drop to its remaining hold but never below it: available stays >= 0.
  applyTransfers(transfers, release = new Map()) {
    const delta = new Map();
    let net = 0;
    for (const t of transfers) {
      if (!Number.isSafeInteger(t.amount) || t.amount < 0 || t.amount > MAX_AMOUNT) throw new InvariantError('invariant: bad amount');
      if (!this.users.has(t.from) || !this.users.has(t.to)) throw new InvariantError('invariant: unknown wallet');
      delta.set(t.from, (delta.get(t.from) || 0) - t.amount);
      delta.set(t.to, (delta.get(t.to) || 0) + t.amount);
    }
    const after = new Map();
    for (const [id, d] of delta) {
      net += d;
      if (!this.fits(id, d)) throw new InvariantError('invariant: balance out of range');
      const b = this.users.get(id).balance + d;
      if (d < 0 && b < this.held(id) - (release.get(id) || 0)) throw new InvariantError('invariant: available would go negative');
      after.set(id, b);
    }
    if (net !== 0) throw new InvariantError('invariant: transfers do not net to zero');
    for (const [id, b] of after) this.users.get(id).balance = b;
  }

  // ---- export -----------------------------------------------------------

  toJSON() {
    return {
      schema: SCHEMA,
      schema_version: SCHEMA_VERSION,
      currency: this.currency,
      minor_units: this.minor_units,
      clock: this.clock,
      counters: { ...this.counters },
      users: [...this.users.values()].map((u) => ({ ...u, cred: { ...u.cred } })),
      tokens: [...this.tokens.entries()].map(([digest, userId]) => ({ digest, user_id: userId })),
      payments: this.payments.map((p) => ({ ...p })),
      requests: this.requests.map((r) => ({ ...r })),
      splits: [...this.splits.values()].map((s) => ({ ...s, shares: s.shares.map((x) => ({ ...x })), request_ids: [...s.request_ids] })),
      settlements: [...this.settlements.values()].map((s) => ({ ...s, payment_ids: [...s.payment_ids] })),
      operators: [...this.operators],
      authorization_ttl_seconds: this.ttl,
      authorizations: this.authorizations.map((a) => ({ ...a, payment_ids: [...a.payment_ids] })),
      idempotency: [...this.idem.values()].map((r) => ({ ...r })),
    };
  }
}

class InvariantError extends Error {}

// ---- validation helpers -------------------------------------------------

const isStr = (v) => typeof v === 'string';
const isId = (v) => isStr(v) && v.length >= 1 && v.length <= 64;
const isInt = (v) => typeof v === 'number' && Number.isInteger(v);
const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const codePoints = (s) => { let n = 0; for (const _ of s) n += 1; return n; };
const isAmount = (v) => isInt(v) && v >= 1 && v <= MAX_AMOUNT;
const isNote = (v) => isStr(v) && codePoints(v) <= 200;

// RFC 3339 with an explicit offset -> epoch ms (fraction truncated to ms), or null.
function parseInstant(v) {
  if (!isStr(v)) return null;
  const m = /^(\d{4})-(\d\d)-(\d\d)[Tt](\d\d):(\d\d):(\d\d)(?:\.(\d+))?(?:[Zz]|([+-])(\d\d):(\d\d))$/.exec(v);
  if (!m) return null;
  const [Y, M, D, h, mi, sec] = m.slice(1, 7).map(Number);
  if (M < 1 || M > 12 || D < 1 || h > 23 || mi > 59 || sec > 59) return null;
  const dim = new Date(Date.UTC(2000, M, 0)).getUTCDate(); // days in M of a leap year
  const leap = (Y % 4 === 0 && Y % 100 !== 0) || Y % 400 === 0;
  if (D > (M === 2 ? (leap ? 29 : 28) : dim)) return null;
  let off = 0;
  if (m[8]) {
    const oh = Number(m[9]); const om = Number(m[10]);
    if (oh > 23 || om > 59) return null;
    off = (m[8] === '-' ? -1 : 1) * (oh * 60 + om) * 60000;
  }
  const ms = Date.UTC(Y, M - 1, D, h, mi, sec, Number(((m[7] || '') + '000').slice(0, 3))) - off;
  return ms >= 0 && ms <= MAX_TIME ? ms : null;
}

// ---- fixture (reset) ------------------------------------------------------

// Validate a fixture (D18). Returns the list of plaintext passwords to hash.
function validateFixture(fx) {
  need(isObj(fx), 'fixture must be an object');
  need(isStr(fx.currency) && fx.currency.length > 0, 'currency');
  need(fx.minor_units === 0 || fx.minor_units === 2 || fx.minor_units === 3, 'minor_units');
  need(Array.isArray(fx.users), 'users');
  const ids = new Set(); const handles = new Set(); const emails = new Set();
  for (const u of fx.users) {
    need(isObj(u), 'user');
    need(isId(u.id) && !ids.has(u.id), 'user id');
    need(isStr(u.email) && u.email.length > 0 && !emails.has(u.email.toLowerCase()), 'user email');
    need(isStr(u.password), 'user password');
    need(isStr(u.display_name), 'user display_name');
    need(isStr(u.handle) && HANDLE_RE.test(u.handle) && !handles.has(u.handle), 'user handle');
    need(isInt(u.balance) && u.balance >= 0 && u.balance <= MAX_BALANCE, 'user balance');
    ids.add(u.id); handles.add(u.handle); emails.add(u.email.toLowerCase());
  }
  const payments = fx.payments === undefined ? [] : fx.payments;
  const requests = fx.requests === undefined ? [] : fx.requests;
  const operators = fx.settlement_operator_ids === undefined ? [] : fx.settlement_operator_ids;
  need(Array.isArray(payments) && Array.isArray(requests) && Array.isArray(operators), 'arrays');
  const pids = new Set();
  for (const p of payments) {
    need(isObj(p), 'payment');
    need(isId(p.id) && !pids.has(p.id), 'payment id');
    need(ids.has(p.from_user_id) && ids.has(p.to_user_id), 'payment users');
    need(isAmount(p.amount), 'payment amount');
    need(p.note === undefined || isNote(p.note), 'payment note');
    need(p.visibility === undefined || VISIBILITIES.has(p.visibility), 'payment visibility');
    pids.add(p.id);
  }
  const rids = new Set();
  for (const r of requests) {
    need(isObj(r), 'request');
    need(isId(r.id) && !rids.has(r.id), 'request id');
    need(ids.has(r.requester_id) && ids.has(r.payer_id), 'request users');
    need(isAmount(r.amount), 'request amount');
    need(r.note === undefined || isNote(r.note), 'request note');
    need(r.status === undefined || STATUSES.has(r.status), 'request status');
    need(r.payment_id === undefined || r.payment_id === null || pids.has(r.payment_id), 'request payment');
    rids.add(r.id);
  }
  for (const o of operators) need(ids.has(o), 'operator');
  need(fx.authorization_ttl_seconds === undefined
    || (isInt(fx.authorization_ttl_seconds) && fx.authorization_ttl_seconds >= 1 && fx.authorization_ttl_seconds <= MAX_TTL), 'authorization_ttl_seconds');
  const auths = fx.authorizations === undefined ? [] : fx.authorizations;
  need(Array.isArray(auths), 'authorizations');
  const aids = new Set();
  const balance = new Map(fx.users.map((u) => [u.id, u.balance]));
  const reserved = new Map();
  const now = Date.now();
  for (const a of auths) {
    need(isObj(a), 'authorization');
    need(isId(a.id) && !aids.has(a.id), 'authorization id');
    need(ids.has(a.from_user_id) && ids.has(a.to_user_id), 'authorization users');
    need(isAmount(a.amount), 'authorization amount');
    need(a.note === undefined || isNote(a.note), 'authorization note');
    need(a.visibility === undefined || VISIBILITIES.has(a.visibility), 'authorization visibility');
    need(isStr(a.status) && AUTH_STATUSES.has(a.status), 'authorization status');
    const exp = parseInstant(a.expires_at);
    need(exp !== null, 'authorization expires_at');
    need(a.captured_amount === undefined || (isInt(a.captured_amount) && a.captured_amount >= 0 && a.captured_amount <= a.amount), 'authorization captured_amount');
    need(a.payment_id === undefined || a.payment_id === null || pids.has(a.payment_id), 'authorization payment_id');
    const cap = a.captured_amount === undefined ? (a.status === 'captured' ? a.amount : 0) : a.captured_amount;
    need(a.status !== 'open' || cap < a.amount, 'authorization captured_amount');
    aids.add(a.id);
    if (a.status === 'open' && exp > now) reserved.set(a.from_user_id, (reserved.get(a.from_user_id) || 0) + a.amount - cap);
  }
  for (const [id, r] of reserved) need(r <= balance.get(id), 'holds exceed balance');
  return fx.users.map((u) => u.password);
}

// Build a State from a validated fixture and a Map password -> credential.
function stateFromFixture(fx, creds) {
  const s = new State(fx.currency, fx.minor_units);
  const t = s.now();
  for (const u of fx.users) {
    s.addUser({
      id: u.id, email: u.email, display_name: u.display_name, handle: u.handle,
      balance: u.balance, cred: creds.get(u.password), seq: s.nextSeq(),
    });
  }
  for (const p of fx.payments || []) {
    s.addPayment({
      payment_id: p.id, from_user_id: p.from_user_id, to_user_id: p.to_user_id,
      amount: p.amount, note: p.note === undefined ? '' : p.note,
      visibility: p.visibility === undefined ? 'public' : p.visibility,
      request_id: null, settlement_id: null, authorization_id: null, created_at: t, seq: s.nextSeq(),
    });
  }
  for (const r of fx.requests || []) {
    s.addRequest({
      request_id: r.id, requester_id: r.requester_id, payer_id: r.payer_id,
      amount: r.amount, note: r.note === undefined ? '' : r.note,
      status: r.status === undefined ? 'pending' : r.status,
      payment_id: r.payment_id === undefined ? null : r.payment_id,
      created_at: t, seq: s.nextSeq(),
    });
  }
  for (const o of fx.settlement_operator_ids || []) s.operators.add(o);
  if (fx.authorization_ttl_seconds !== undefined) s.ttl = fx.authorization_ttl_seconds;
  for (const a of fx.authorizations || []) {
    const exp = parseInstant(a.expires_at);
    const cap = a.captured_amount === undefined ? (a.status === 'captured' ? a.amount : 0) : a.captured_amount;
    s.addAuthorization({
      authorization_id: a.id, from_user_id: a.from_user_id, to_user_id: a.to_user_id, amount: a.amount, captured_amount: cap,
      note: a.note === undefined ? '' : a.note, visibility: a.visibility === undefined ? 'public' : a.visibility,
      status: a.status === 'open' && exp <= t ? 'expired' : a.status, expires_at: exp,
      payment_ids: a.payment_id ? [a.payment_id] : [], created_at: t, seq: s.nextSeq(),
    });
  }
  return s;
}

// ---- import ---------------------------------------------------------------

// Load and fully validate an exported state object (D13). Throws Invalid.
function stateFromExport(st) {
  need(isObj(st), 'state');
  need(st.schema === SCHEMA, 'schema');
  need(isInt(st.schema_version) && (st.schema_version === 1 || st.schema_version === SCHEMA_VERSION), 'schema_version');
  const v2 = st.schema_version === 2; // D34: version 1 is upgraded (no authorizations, ttl 600)
  need(isStr(st.currency) && st.currency.length > 0, 'currency');
  need([0, 2, 3].includes(st.minor_units), 'minor_units');
  need(isInt(st.clock) && st.clock >= 0 && st.clock <= MAX_TIME, 'clock');
  need(isObj(st.counters), 'counters');
  for (const k of v2 ? ['u', 'p', 'rq', 'sp', 'st', 'au', 'seq'] : ['u', 'p', 'rq', 'sp', 'st', 'seq']) {
    need(isInt(st.counters[k]) && st.counters[k] >= 0 && st.counters[k] <= MAX_COUNTER, 'counter');
  }
  if (v2) {
    need(isInt(st.authorization_ttl_seconds) && st.authorization_ttl_seconds >= 1 && st.authorization_ttl_seconds <= MAX_TTL, 'authorization_ttl_seconds');
    need(Array.isArray(st.authorizations), 'authorizations');
  }
  for (const k of ['users', 'tokens', 'payments', 'requests', 'splits', 'settlements', 'operators', 'idempotency']) {
    need(Array.isArray(st[k]), k);
  }
  const s = new State(st.currency, st.minor_units);
  s.clock = st.clock;
  s.counters = { u: st.counters.u, p: st.counters.p, rq: st.counters.rq, sp: st.counters.sp, st: st.counters.st, au: v2 ? st.counters.au : 0, seq: st.counters.seq };
  if (v2) s.ttl = st.authorization_ttl_seconds;
  const isTime = (v) => isInt(v) && v >= 0 && v <= s.clock;
  const isSeq = (v) => isInt(v) && v >= 0 && v <= s.counters.seq;

  for (const u of st.users) {
    need(isObj(u) && isId(u.id) && !s.users.has(u.id), 'user id');
    need(isStr(u.email) && u.email.length > 0 && !s.byEmail.has(u.email.toLowerCase()), 'user email');
    need(isStr(u.display_name), 'user display_name');
    need(isStr(u.handle) && HANDLE_RE.test(u.handle) && !s.byHandle.has(u.handle), 'user handle');
    need(isInt(u.balance) && u.balance >= 0 && u.balance <= MAX_BALANCE, 'user balance');
    need(isSeq(u.seq), 'user seq');
    const c = u.cred;
    // D40: only the parameter sets this service writes (hash.js COST_LADDER).
    need(isObj(c) && c.alg === 'scrypt' && COST_LADDER.includes(c.N) && c.r === 8 && c.p === 1
      && isStr(c.salt) && /^[0-9a-f]{32}$/.test(c.salt)
      && isStr(c.hash) && /^[0-9a-f]{64}$/.test(c.hash), 'user cred');
    s.addUser({
      id: u.id, email: u.email, display_name: u.display_name, handle: u.handle, balance: u.balance,
      cred: { alg: 'scrypt', N: c.N, r: c.r, p: c.p, salt: c.salt, hash: c.hash }, seq: u.seq,
    });
  }
  for (const t of st.tokens) {
    need(isObj(t) && isStr(t.digest) && /^[0-9a-f]{64}$/.test(t.digest) && s.users.has(t.user_id)
      && !s.tokens.has(t.digest), 'token');
    s.tokens.set(t.digest, t.user_id);
  }
  for (const p of st.payments) {
    need(isObj(p) && isId(p.payment_id) && !s.paymentById.has(p.payment_id), 'payment id');
    need(s.users.has(p.from_user_id) && s.users.has(p.to_user_id), 'payment users');
    need(isInt(p.amount) && p.amount >= 0 && p.amount <= MAX_AMOUNT, 'payment amount');
    need(isNote(p.note) && VISIBILITIES.has(p.visibility), 'payment note/visibility');
    need(p.request_id === null || isId(p.request_id), 'payment request');
    need(p.settlement_id === null || isId(p.settlement_id), 'payment settlement');
    need(!v2 || p.authorization_id === null || isId(p.authorization_id), 'payment authorization');
    need(isTime(p.created_at) && isSeq(p.seq), 'payment time');
    s.addPayment({
      payment_id: p.payment_id, from_user_id: p.from_user_id, to_user_id: p.to_user_id, amount: p.amount,
      note: p.note, visibility: p.visibility, request_id: p.request_id, settlement_id: p.settlement_id,
      authorization_id: v2 ? p.authorization_id : null, created_at: p.created_at, seq: p.seq,
    });
  }
  for (const r of st.requests) {
    need(isObj(r) && isId(r.request_id) && !s.requestById.has(r.request_id), 'request id');
    need(s.users.has(r.requester_id) && s.users.has(r.payer_id), 'request users');
    need(isInt(r.amount) && r.amount >= 0 && r.amount <= MAX_AMOUNT, 'request amount');
    need(isNote(r.note) && STATUSES.has(r.status), 'request note/status');
    need(r.payment_id === null || s.paymentById.has(r.payment_id), 'request payment');
    need(r.split_id === undefined || r.split_id === null || isId(r.split_id), 'request split');
    need(isTime(r.created_at) && isSeq(r.seq), 'request time');
    const rec = {
      request_id: r.request_id, requester_id: r.requester_id, payer_id: r.payer_id, amount: r.amount,
      note: r.note, status: r.status, payment_id: r.payment_id, created_at: r.created_at, seq: r.seq,
    };
    if (r.split_id) rec.split_id = r.split_id;
    s.addRequest(rec);
  }
  for (const p of s.payments) need(p.request_id === null || s.requestById.has(p.request_id), 'payment request ref');
  for (const sp of st.splits) {
    need(isObj(sp) && isId(sp.split_id) && !s.splits.has(sp.split_id) && s.users.has(sp.creator_id), 'split');
    need(isAmount(sp.amount) && isNote(sp.note) && isTime(sp.created_at), 'split fields');
    need(Array.isArray(sp.shares) && sp.shares.every((x) => isObj(x) && isStr(x.handle) && isInt(x.amount) && x.amount >= 0), 'split shares');
    need(Array.isArray(sp.request_ids) && sp.request_ids.every((id) => s.requestById.has(id)), 'split requests');
    s.splits.set(sp.split_id, {
      split_id: sp.split_id, creator_id: sp.creator_id, amount: sp.amount, note: sp.note,
      shares: sp.shares.map((x) => ({ handle: x.handle, amount: x.amount })), request_ids: [...sp.request_ids],
      created_at: sp.created_at,
    });
  }
  for (const se of st.settlements) {
    need(isObj(se) && isId(se.settlement_id) && !s.settlements.has(se.settlement_id) && s.users.has(se.operator_id), 'settlement');
    need(isTime(se.committed_at), 'settlement time');
    need(Array.isArray(se.payment_ids) && se.payment_ids.length >= 1
      && se.payment_ids.every((id) => s.paymentById.has(id) && s.paymentById.get(id).settlement_id === se.settlement_id), 'settlement payments');
    s.settlements.set(se.settlement_id, {
      settlement_id: se.settlement_id, operator_id: se.operator_id, payment_ids: [...se.payment_ids], committed_at: se.committed_at,
    });
  }
  for (const p of s.payments) need(p.settlement_id === null || s.settlements.has(p.settlement_id), 'payment settlement ref');
  const reserved = new Map();
  for (const a of v2 ? st.authorizations : []) {
    need(isObj(a) && isId(a.authorization_id) && !s.authById.has(a.authorization_id), 'authorization id');
    need(s.users.has(a.from_user_id) && s.users.has(a.to_user_id), 'authorization users');
    need(isAmount(a.amount) && isInt(a.captured_amount) && a.captured_amount >= 0 && a.captured_amount <= a.amount, 'authorization amounts');
    need(isNote(a.note) && VISIBILITIES.has(a.visibility) && isStr(a.status) && AUTH_STATUSES.has(a.status), 'authorization fields');
    need(isInt(a.expires_at) && a.expires_at >= 0 && a.expires_at <= MAX_TIME && isTime(a.created_at) && isSeq(a.seq), 'authorization time');
    need(Array.isArray(a.payment_ids) && a.payment_ids.every((id) => s.paymentById.has(id)), 'authorization payments');
    need(a.status !== 'open' || a.captured_amount < a.amount, 'authorization remainder');
    s.addAuthorization({
      authorization_id: a.authorization_id, from_user_id: a.from_user_id, to_user_id: a.to_user_id, amount: a.amount,
      captured_amount: a.captured_amount, note: a.note, visibility: a.visibility, status: a.status, expires_at: a.expires_at,
      payment_ids: [...a.payment_ids], created_at: a.created_at, seq: a.seq,
    });
    if (a.status === 'open' && a.expires_at > s.clock) reserved.set(a.from_user_id, (reserved.get(a.from_user_id) || 0) + a.amount - a.captured_amount);
  }
  for (const p of s.payments) need(p.authorization_id === null || s.authById.has(p.authorization_id), 'payment authorization ref');
  for (const [id, r] of reserved) need(r <= s.users.get(id).balance, 'holds exceed balance');
  for (const o of st.operators) {
    need(s.users.has(o), 'operator');
    s.operators.add(o);
  }
  for (const r of st.idempotency) {
    need(isObj(r) && s.users.has(r.user_id) && r.method === 'POST' && isStr(r.path) && IDEM_PATH.test(r.path)
      && isStr(r.key) && r.key.length >= 1 && codePoints(r.key) <= 255 && isStr(r.canon) && r.status === 201
      && isObj(r.body), 'idempotency');
    const k = idemScope(r.user_id, r.method, r.path, r.key);
    need(!s.idem.has(k), 'idempotency duplicate');
    s.idem.set(k, { user_id: r.user_id, method: r.method, path: r.path, key: r.key, canon: r.canon, status: r.status, body: r.body });
  }
  return s;
}

// The seven idempotent write paths (§7).
const IDEM_PATH = /^\/(payments|requests|splits|settlements|authorizations|requests\/[^/]+\/pay|authorizations\/[^/]+\/capture)$/;

function idemScope(userId, method, path, key) {
  return JSON.stringify([userId, method, path, key]);
}

module.exports = {
  State, Invalid, InvariantError, validateFixture, stateFromFixture, stateFromExport, idemScope,
  codePoints, parseInstant, MAX_TTL, MAX_AMOUNT, MAX_BALANCE, MAX_TIME, HANDLE_RE, VISIBILITIES, STATUSES, AUTH_STATUSES,
};
