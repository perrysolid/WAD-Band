'use strict';
// HTTP layer and handlers. Every write runs validation, checks and mutation in
// one synchronous section after the body has been read (D22); only reset,
// import, signup and login await (password hashing), and they re-read the
// current state after awaiting.

const http = require('node:http');
const { randomUUID, randomBytes } = require('node:crypto');
const { parse, canonical, exactInteger, isObject, JNum } = require('./json');
const {
  State, Invalid, InvariantError, validateFixture, stateFromFixture, stateFromExport, idemScope,
  codePoints, parseInstant, MAX_AMOUNT, MAX_TIME, VISIBILITIES, STATUSES, AUTH_STATUSES,
} = require('./state');
const H = require('./history');
const { createStatic, acceptsHtml, CSP } = require('./static');
const { hashPassword, verifyPassword, hashSeedPasswords, tokenDigest, newToken } = require('./hash');

const MAX_BODY = 64 * 1024 * 1024; // D15
const FORMAT_VERSION = 1; // the envelope; state.schema_version is 2 (D34)
const TRACK = 'pocketful';

class HttpError extends Error {
  constructor(status, code, message) {
    super(message || code);
    this.status = status;
    this.code = code;
  }
}
const fail = (status, code, message) => { throw new HttpError(status, code, message); };
const malformed = (m) => fail(400, 'malformed_request', m || 'malformed request');
const invalid = (m) => fail(422, 'validation_failed', m || 'validation failed');
const notFound = (m) => fail(404, 'not_found', m || 'not found');

// D35: UTC +00:00; fractional seconds only when the millisecond part is non-zero.
function iso(ms) {
  const t = new Date(ms).toISOString();
  return (ms % 1000 === 0 ? t.replace('.000Z', '+00:00') : t.replace('Z', '+00:00'));
}

// ---- application ----------------------------------------------------------

// One JSON line per call. `out` receives the per-request access log, `err` the
// error log. Neither ever receives bodies, tokens, passwords or keys.
const defaultLog = {
  out: (line) => process.stdout.write(line + '\n'),
  err: (line) => process.stderr.write(line + '\n'),
};

const newRequestId = () => randomUUID();

function createApp({ log = defaultLog } = {}) {
  let state = new State('EUR', 2);
  let control = Promise.resolve(); // serialises reset/import

  const serial = (fn) => {
    const run = control.then(fn, fn);
    control = run.then(() => {}, () => {});
    return run;
  };

  // ---- views ---------------------------------------------------------------

  const handleOf = (s, id) => s.users.get(id).handle;

  function paymentView(s, p) {
    return {
      payment_id: p.payment_id,
      from_user_id: p.from_user_id,
      from_handle: handleOf(s, p.from_user_id),
      to_user_id: p.to_user_id,
      to_handle: handleOf(s, p.to_user_id),
      amount: p.amount,
      currency: s.currency,
      note: p.note,
      visibility: p.visibility,
      request_id: p.request_id,
      settlement_id: p.settlement_id,
      authorization_id: p.authorization_id,
      refund_of: p.refund_of === undefined ? null : p.refund_of,
      created_at: iso(p.created_at),
    };
  }

  function authView(s, a) {
    const open = a.status === 'open';
    return {
      authorization_id: a.authorization_id,
      from_user_id: a.from_user_id,
      from_handle: handleOf(s, a.from_user_id),
      to_user_id: a.to_user_id,
      to_handle: handleOf(s, a.to_user_id),
      amount: a.amount,
      captured_amount: a.captured_amount,
      remaining_amount: open ? a.amount - a.captured_amount : 0,
      currency: s.currency,
      note: a.note,
      visibility: a.visibility,
      status: a.status,
      expires_at: iso(a.expires_at),
      payment_id: a.payment_ids.length ? a.payment_ids[a.payment_ids.length - 1] : null,
      payment_ids: [...a.payment_ids],
      created_at: iso(a.created_at),
      closed_at: a.closed_at === null ? null : iso(a.closed_at),
    };
  }

  function requestView(s, r) {
    return {
      request_id: r.request_id,
      requester_id: r.requester_id,
      requester_handle: handleOf(s, r.requester_id),
      payer_id: r.payer_id,
      payer_handle: handleOf(s, r.payer_id),
      amount: r.amount,
      currency: s.currency,
      note: r.note,
      status: r.status,
      payment_id: r.payment_id,
      created_at: iso(r.created_at),
    };
  }

  // ---- field rules (§5, D2, D3) ---------------------------------------------

  function amountOf(body) {
    if (!Object.hasOwn(body, 'amount')) invalid('amount is required');
    const v = exactInteger(body.amount, MAX_AMOUNT);
    if (v === null || v < 1) invalid('amount must be an integer from 1 to 1000000000');
    return v;
  }

  function noteOf(body) {
    if (!Object.hasOwn(body, 'note')) return '';
    if (typeof body.note !== 'string') invalid('note must be a string');
    if (codePoints(body.note) > 200) invalid('note is longer than 200 characters');
    return body.note;
  }

  function visibilityOf(body) {
    if (!Object.hasOwn(body, 'visibility')) return 'public';
    if (typeof body.visibility !== 'string' || !VISIBILITIES.has(body.visibility)) {
      invalid('visibility must be public or private');
    }
    return body.visibility;
  }

  // Wrong JSON type for a string field is 400; absence is reported later as 422.
  function stringType(body, field) {
    if (Object.hasOwn(body, field) && typeof body[field] !== 'string') malformed(`${field} must be a string`);
  }

  function requireString(body, field) {
    if (!Object.hasOwn(body, field)) invalid(`${field} is required`);
    return body[field];
  }

  // D39: a credit that would take a wallet above 2^53 is refused before anything changes.
  function creditFits(s, userId, credit) {
    if (!s.fits(userId, credit)) invalid('the credit would take a wallet balance above 2^53');
  }

  // ---- query parameters (§5, D20) ---------------------------------------------

  function intParam(q, name, dflt, min, max) {
    const raw = q.get(name);
    if (raw === null) return dflt;
    if (!/^[0-9]+$/.test(raw)) invalid(`${name} must be plain decimal digits`);
    const n = Number(raw);
    if (!Number.isSafeInteger(n) || n < min || (max !== undefined && n > max)) invalid(`${name} out of range`);
    return n;
  }

  function page(q) {
    const limit = intParam(q, 'limit', 50, 1, 200);
    const offset = intParam(q, 'offset', 0, 0);
    return { limit, offset };
  }

  // Newest first: the arrays are kept in (created_at, seq) order (monotonic clock).
  function pageNewestFirst(items, pred, { limit, offset }) {
    const out = [];
    let skipped = 0;
    for (let i = items.length - 1; i >= 0; i -= 1) {
      if (!pred(items[i])) continue;
      if (skipped < offset) { skipped += 1; continue; }
      if (out.length === limit) return { items: out, has_more: true };
      out.push(items[i]);
    }
    return { items: out, has_more: false };
  }

  // ---- idempotent write wrapper (§7, D1 steps 5–6, D6) ---------------------------

  function idempotent(ctx, run) {
    const key = ctx.req.headers['idempotency-key'];
    if (key === undefined || key === '') fail(400, 'missing_idempotency_key', 'Idempotency-Key header is required');
    if (codePoints(key) > 255) invalid('Idempotency-Key must be 1 to 255 characters');
    const s = state;
    const scope = idemScope(ctx.user.id, 'POST', ctx.path, key);
    const canon = canonical(ctx.body);
    const prior = s.idem.get(scope);
    if (prior) {
      if (prior.canon !== canon) fail(409, 'idempotency_key_reuse', 'key already used with a different body');
      ctx.idem = 'replay';
      return { status: 200, body: prior.body };
    }
    const body = run(s);
    s.idem.set(scope, { user_id: ctx.user.id, method: 'POST', path: ctx.path, key, canon, status: 201, body });
    ctx.idem = 'first';
    return { status: 201, body };
  }

  // ---- handlers -----------------------------------------------------------------

  function instantParam(q, name) {
    const raw = q.get(name);
    if (raw === null) return null;
    const v = parseInstant(raw);
    if (v === null) invalid(`${name} must be an RFC 3339 instant with an offset`);
    return v;
  }

  function me(ctx) {
    const s = state; const u = ctx.user; const q = ctx.query;
    const asOf = instantParam(q, 'as_of'); const knownAt = instantParam(q, 'known_at');
    if (asOf !== null || knownAt !== null) {
      const total = H.totalAt(s, u.id, asOf === null ? H.INF : asOf, knownAt === null ? H.INF : knownAt);
      const held = H.heldAt(s, u.id, asOf === null ? s.now() : asOf, knownAt === null ? H.INF : knownAt);
      const body = {
        user_id: u.id, display_name: u.display_name, handle: u.handle, balance: total, total,
        available: total - held, held, currency: s.currency, minor_units: s.minor_units,
      };
      if (asOf !== null) body.as_of = q.get('as_of');
      if (knownAt !== null) body.known_at = q.get('known_at');
      return { status: 200, body };
    }
    return {
      status: 200,
      body: {
        user_id: u.id, display_name: u.display_name, handle: u.handle, balance: u.balance, total: u.balance,
        available: s.available(u.id), held: s.held(u.id), currency: s.currency, minor_units: s.minor_units,
      },
    };
  }

  function createPayment(ctx) {
    return idempotent(ctx, (s) => {
      const b = ctx.body; const caller = ctx.user;
      stringType(b, 'to_handle');
      const toHandle = requireString(b, 'to_handle');
      const amount = amountOf(b);
      if (toHandle === caller.handle) fail(422, 'self_payment', 'cannot pay yourself');
      const note = noteOf(b);
      const visibility = visibilityOf(b);
      const to = s.byHandle.get(toHandle);
      if (!to) notFound('no user has that handle');
      if (s.available(caller.id) < amount) fail(409, 'insufficient_funds', 'available balance is below amount');
      creditFits(s, to.id, amount);
      const t = s.now();
      const p = {
        payment_id: s.newId('p', s.paymentById), from_user_id: caller.id, to_user_id: to.id, amount, note, visibility,
        request_id: null, settlement_id: null, authorization_id: null, created_at: t, seq: s.nextSeq(),
      };
      const view = paymentView(s, p); // rendered before anything changes
      s.applyTransfers([{ from: caller.id, to: to.id, amount }]);
      s.addPayment(p);
      return view;
    });
  }

  // Builds a request record; the caller renders it and then adds it with s.addRequest.
  function newRequest(s, requester, payer, amount, note, t, splitId) {
    const r = {
      request_id: s.newId('rq', s.requestById), requester_id: requester.id, payer_id: payer.id, amount, note,
      status: 'pending', payment_id: null, created_at: t, seq: s.nextSeq(),
    };
    if (splitId) r.split_id = splitId;
    return r;
  }

  function createRequest(ctx) {
    return idempotent(ctx, (s) => {
      const b = ctx.body; const caller = ctx.user;
      stringType(b, 'payer_handle');
      const payerHandle = requireString(b, 'payer_handle');
      const amount = amountOf(b);
      if (payerHandle === caller.handle) fail(422, 'self_request', 'cannot request from yourself');
      const note = noteOf(b);
      const payer = s.byHandle.get(payerHandle);
      if (!payer) notFound('no user has that handle');
      const r = newRequest(s, caller, payer, amount, note, s.now());
      const view = requestView(s, r);
      s.addRequest(r);
      return view;
    });
  }

  function findRequest(s, id) {
    const r = s.requestById.get(id);
    if (!r) notFound('no such request');
    return r;
  }

  function payRequest(ctx) {
    return idempotent(ctx, (s) => {
      const visibility = visibilityOf(ctx.body);
      const r = findRequest(s, ctx.params.id);
      const caller = ctx.user;
      if (r.payer_id !== caller.id) fail(403, 'forbidden', 'only the payer may pay');
      if (r.status !== 'pending') fail(409, 'request_not_pending', `request is ${r.status}`);
      if (s.available(caller.id) < r.amount) fail(409, 'insufficient_funds', 'available balance is below amount');
      creditFits(s, r.requester_id, r.amount);
      const t = s.now();
      const p = {
        payment_id: s.newId('p', s.paymentById), from_user_id: caller.id, to_user_id: r.requester_id, amount: r.amount,
        note: r.note, visibility, request_id: r.request_id, settlement_id: null, authorization_id: null, created_at: t, seq: s.nextSeq(),
      };
      const view = paymentView(s, p);
      s.applyTransfers([{ from: caller.id, to: r.requester_id, amount: r.amount }]);
      s.addPayment(p);
      r.status = 'paid';
      r.payment_id = p.payment_id;
      return view;
    });
  }

  function transition(ctx, party, target) {
    const s = state;
    const r = findRequest(s, ctx.params.id);
    if (r[party] !== ctx.user.id) fail(403, 'forbidden', 'not permitted on this request');
    if (r.status === 'pending') r.status = target;
    else if (r.status !== target) fail(409, 'request_not_pending', `request is ${r.status}`);
    return { status: 200, body: requestView(s, r) };
  }

  const declineRequest = (ctx) => transition(ctx, 'payer_id', 'declined');
  const cancelRequest = (ctx) => transition(ctx, 'requester_id', 'cancelled');

  function listRequests(ctx) {
    const s = state; const q = ctx.query; const uid = ctx.user.id;
    const direction = q.get('direction');
    if (direction !== null && direction !== 'incoming' && direction !== 'outgoing') invalid('unknown direction');
    const status = q.get('status');
    if (status !== null && !STATUSES.has(status)) invalid('unknown status');
    const pg = page(q);
    const pred = (r) => {
      if (direction === 'incoming' ? r.payer_id !== uid
        : direction === 'outgoing' ? r.requester_id !== uid
          : r.payer_id !== uid && r.requester_id !== uid) return false;
      return status === null || r.status === status;
    };
    const res = pageNewestFirst(s.requests, pred, pg);
    return { status: 200, body: { requests: res.items.map((r) => requestView(s, r)), has_more: res.has_more } };
  }

  function createSplit(ctx) {
    return idempotent(ctx, (s) => {
      const b = ctx.body; const caller = ctx.user;
      if (Object.hasOwn(b, 'participant_handles')) {
        const ph = b.participant_handles;
        if (!Array.isArray(ph) || ph.some((h) => typeof h !== 'string')) malformed('participant_handles must be an array of strings');
      }
      const amount = amountOf(b);
      if (!Object.hasOwn(b, 'participant_handles')) invalid('participant_handles is required');
      const handles = b.participant_handles;
      if (handles.length === 0) invalid('participant_handles is empty');
      if (new Set(handles).size !== handles.length) invalid('participant_handles contains a duplicate');
      const note = noteOf(b);
      const users = handles.map((h) => {
        const u = s.byHandle.get(h);
        if (!u) notFound(`no user has handle ${h}`);
        return u;
      });
      const n = handles.length;
      const base = Math.floor(amount / n);
      const rem = amount - base * n;
      const shares = handles.map((h, i) => ({ handle: h, amount: base + (i < rem ? 1 : 0) }));
      const t = s.now();
      const splitId = s.newId('sp', s.splits);
      const reqs = [];
      users.forEach((u, i) => {
        if (u.id === caller.id) return;
        reqs.push(newRequest(s, caller, u, shares[i].amount, note, t, splitId));
      });
      const view = {
        split_id: splitId, amount, currency: s.currency, note,
        shares: shares.map((x) => ({ ...x })), requests: reqs.map((r) => requestView(s, r)), created_at: iso(t),
      };
      for (const r of reqs) s.addRequest(r);
      s.splits.set(splitId, {
        split_id: splitId, creator_id: caller.id, amount, note, shares, request_ids: reqs.map((r) => r.request_id), created_at: t,
      });
      return view;
    });
  }

  function listActivity(ctx) {
    const s = state; const uid = ctx.user.id;
    const pg = page(ctx.query);
    const pred = (p) => p.visibility === 'public' || p.from_user_id === uid || p.to_user_id === uid;
    const res = pageNewestFirst(s.payments, pred, pg);
    return { status: 200, body: { payments: res.items.map((p) => paymentView(s, p)), has_more: res.has_more } };
  }

  function createSettlement(ctx) {
    return idempotent(ctx, (s) => {
      const b = ctx.body;
      const transfers = b.transfers;
      if (!Array.isArray(transfers) || transfers.length < 1 || transfers.length > 32) invalid('transfers must hold 1 to 32 entries');
      const entries = transfers.map((e, i) => {
        if (!isObject(e) || typeof e.from_handle !== 'string' || typeof e.to_handle !== 'string') invalid(`transfers[${i}] is malformed`);
        const amount = amountOf(e);
        if (e.from_handle === e.to_handle) fail(422, 'self_payment', `transfers[${i}] pays itself`);
        const note = noteOf(e);
        const visibility = visibilityOf(e);
        const from = s.byHandle.get(e.from_handle);
        if (!from) notFound(`transfers[${i}].from_handle is unknown`);
        const to = s.byHandle.get(e.to_handle);
        if (!to) notFound(`transfers[${i}].to_handle is unknown`);
        return { from, to, amount, note, visibility };
      });
      const net = new Map();
      for (const e of entries) {
        net.set(e.from.id, (net.get(e.from.id) || 0) - e.amount);
        net.set(e.to.id, (net.get(e.to.id) || 0) + e.amount);
      }
      for (const [id, d] of net) {
        if (d < 0 && s.available(id) < -d) fail(409, 'insufficient_funds', 'settlement is not affordable');
      }
      for (const [id, d] of net) if (d > 0) creditFits(s, id, d); // D39, after insufficient_funds
      const t = s.now();
      const settlementId = s.newId('st', s.settlements);
      const payments = entries.map((e) => ({
        payment_id: s.newId('p', s.paymentById), from_user_id: e.from.id, to_user_id: e.to.id, amount: e.amount,
        note: e.note, visibility: e.visibility, request_id: null, settlement_id: settlementId, authorization_id: null, created_at: t, seq: s.nextSeq(),
      }));
      const view = { settlement_id: settlementId, committed_at: iso(t), payments: payments.map((p) => paymentView(s, p)) };
      s.applyTransfers(entries.map((e) => ({ from: e.from.id, to: e.to.id, amount: e.amount })));
      for (const p of payments) s.addPayment(p);
      s.settlements.set(settlementId, {
        settlement_id: settlementId, operator_id: ctx.user.id, payment_ids: payments.map((p) => p.payment_id), committed_at: t,
      });
      return view;
    });
  }

  // ---- authorizations (S2-R3..R9) ---------------------------------------------------

  function createAuthorization(ctx) {
    return idempotent(ctx, (s) => {
      const b = ctx.body; const caller = ctx.user;
      stringType(b, 'to_handle');
      const toHandle = requireString(b, 'to_handle');
      const amount = amountOf(b);
      if (toHandle === caller.handle) fail(422, 'self_payment', 'cannot authorize yourself');
      const note = noteOf(b);
      const visibility = visibilityOf(b);
      const to = s.byHandle.get(toHandle);
      if (!to) notFound('no user has that handle');
      if (s.available(caller.id) < amount) fail(409, 'insufficient_funds', 'available balance is below amount');
      const t = s.now();
      const a = {
        authorization_id: s.newId('au', s.authById), from_user_id: caller.id, to_user_id: to.id, amount, captured_amount: 0,
        note, visibility, status: 'open', expires_at: Math.min(t + s.ttl * 1000, MAX_TIME), payment_ids: [], created_at: t, seq: s.nextSeq(),
        events: [{ t, d: amount }], closed_at: null,
      };
      const view = authView(s, a);
      // defence in depth: re-check just before the write
      if (s.available(caller.id) < amount) throw new InvariantError('invariant: hold exceeds available');
      s.addAuthorization(a);
      return view;
    });
  }

  function findAuth(s, id) {
    const a = s.authById.get(id);
    if (!a) notFound('no such authorization');
    return a;
  }

  function captureAuthorization(ctx) {
    return idempotent(ctx, (s) => {
      const b = ctx.body; const caller = ctx.user;
      if (Object.hasOwn(b, 'final') && typeof b.final !== 'boolean') malformed('final must be a boolean');
      let amount = null;
      if (Object.hasOwn(b, 'amount')) amount = amountOf(b);
      const a = findAuth(s, ctx.params.id);
      if (a.to_user_id !== caller.id) fail(403, 'forbidden', 'only the receiver may capture');
      if (a.status === 'captured' || a.status === 'voided') fail(409, 'authorization_not_open', `authorization is ${a.status}`);
      if (a.status === 'expired') fail(409, 'authorization_expired', 'authorization has expired');
      const remaining = a.amount - a.captured_amount;
      if (amount === null) amount = remaining;
      if (amount > remaining) fail(422, 'capture_exceeds_authorization', 'amount exceeds the remaining hold');
      creditFits(s, caller.id, amount);
      const closes = b.final !== false || amount === remaining;
      const t = s.now();
      const p = {
        payment_id: s.newId('p', s.paymentById), from_user_id: a.from_user_id, to_user_id: caller.id, amount, note: a.note,
        visibility: a.visibility, request_id: null, settlement_id: null, authorization_id: a.authorization_id, created_at: t, seq: s.nextSeq(),
      };
      // Move the money first (its guard refuses a write that would break available >= 0), then
      // record the capture; nothing below can throw.
      s.applyTransfers([{ from: a.from_user_id, to: caller.id, amount }], new Map([[a.from_user_id, amount]]));
      s.addPayment(p);
      a.events.push({ t, d: closes ? -remaining : -amount }); // a closing capture releases the remainder in the same step
      a.captured_amount += amount;
      a.payment_ids.push(p.payment_id);
      if (closes) { a.status = 'captured'; a.closed_at = t; s.openAuths.delete(a.authorization_id); }
      return paymentView(s, p);
    });
  }

  function voidAuthorization(ctx) {
    const s = state;
    const a = findAuth(s, ctx.params.id);
    if (a.from_user_id !== ctx.user.id) fail(403, 'forbidden', 'only the payer may void');
    if (a.status === 'open') {
      const t = s.now();
      a.events.push({ t, d: -(a.amount - a.captured_amount) });
      a.status = 'voided'; a.closed_at = t; s.openAuths.delete(a.authorization_id);
    }
    else if (a.status !== 'voided') fail(409, 'authorization_not_open', `authorization is ${a.status}`);
    return { status: 200, body: authView(s, a) };
  }

  function listAuthorizations(ctx) {
    const s = state; const q = ctx.query; const uid = ctx.user.id;
    const direction = q.get('direction');
    if (direction !== null && direction !== 'incoming' && direction !== 'outgoing') invalid('unknown direction');
    const status = q.get('status');
    if (status !== null && !AUTH_STATUSES.has(status)) invalid('unknown status');
    const pg = page(q);
    const pred = (a) => {
      if (direction === 'incoming' ? a.to_user_id !== uid
        : direction === 'outgoing' ? a.from_user_id !== uid
          : a.to_user_id !== uid && a.from_user_id !== uid) return false;
      return status === null || a.status === status;
    };
    const res = pageNewestFirst(s.authorizations, pred, pg);
    return { status: 200, body: { authorizations: res.items.map((a) => authView(s, a)), has_more: res.has_more } };
  }

  // ---- statements (S3) -----------------------------------------------------------------

  const SNAPSHOT_CAP = 20000;

  function statementPage(res, pg, token) {
    const slice = res.entries.slice(pg.offset, pg.offset + pg.limit);
    return {
      opening_balance: res.opening,
      entries: slice,
      closing_balance: res.closing,
      has_more: pg.offset + pg.limit < res.entries.length,
      snapshot: token,
    };
  }

  function statement(ctx) {
    const s = state; const q = ctx.query; const uid = ctx.user.id;
    const pg = page(q);
    const token = q.get('snapshot');
    if (token !== null) {
      for (const f of ['from', 'to', 'known_at']) if (q.has(f)) invalid(`${f} cannot accompany a snapshot`);
      const snap = s.snapshots.get(token);
      if (!snap || snap.user_id !== uid) notFound('no such snapshot');
      return { status: 200, body: statementPage(snap.result, pg, token) };
    }
    const from = instantParam(q, 'from'); const to = instantParam(q, 'to'); const knownAt = instantParam(q, 'known_at');
    const r = H.statement(s, uid, from === null ? -H.INF : from, to === null ? H.INF : to, knownAt === null ? H.INF : knownAt);
    const entries = r.entries.map(({ m, balance_after }) => ({
      payment: { ...paymentView(s, m.p), amount: m.rev.amount },
      delta: m.delta,
      balance_after,
      revision: m.rev.revision,
      effective_at: iso(m.rev.effective_at),
      recorded_at: iso(m.rev.recorded_at),
    }));
    const result = { opening: r.opening, closing: r.closing, entries };
    const tok = `snap_${randomBytes(16).toString('hex')}`;
    if (s.snapshots.size >= SNAPSHOT_CAP) s.snapshots.delete(s.snapshots.keys().next().value);
    s.snapshots.set(tok, { user_id: uid, result });
    return { status: 200, body: statementPage(result, pg, tok) };
  }

  // ---- corrections (S3) ------------------------------------------------------------------

  function revisionView(p, r) {
    return { payment_id: p.payment_id, revision: r.revision, amount: r.amount, effective_at: iso(r.effective_at), recorded_at: iso(r.recorded_at), reason: r.reason, ...(r.correction_batch_id ? { correction_batch_id: r.correction_batch_id } : {}) };
  }

  function correctionFields(s, b) {
    stringType(b, 'effective_at'); stringType(b, 'reason');
    if (!Object.hasOwn(b, 'expected_revision')) invalid('expected_revision is required');
    const expected = exactInteger(b.expected_revision, 2 ** 53);
    if (expected === null || expected < 1) invalid('expected_revision must be a positive integer');
    if (!Object.hasOwn(b, 'amount')) invalid('amount is required');
    const amount = exactInteger(b.amount, MAX_AMOUNT);
    if (amount === null || amount < 0) invalid('amount must be an integer from 0 to 1000000000');
    const eff = parseInstant(requireString(b, 'effective_at'));
    if (eff === null) invalid('effective_at must be an RFC 3339 instant with an offset');
    const now = s.now();
    if (eff > now) invalid('effective_at is in the future');
    const reason = requireString(b, 'reason');
    const rl = codePoints(reason);
    if (rl < 1 || rl > 200) invalid('reason must be 1 to 200 characters');
    return { expected, amount, eff, reason, now };
  }

  function correctPayment(ctx) {
    return idempotent(ctx, (s) => {
      const b = ctx.body; const caller = ctx.user;
      const { expected, amount, eff, reason, now } = correctionFields(s, b);
      const p = s.paymentById.get(ctx.params.id);
      if (!p) notFound('no such payment');
      if (p.from_user_id !== caller.id) fail(403, 'forbidden', 'only the sender may correct a payment');
      if (p.settlement_id !== null || p.authorization_id !== null || p.refund_of !== null) fail(422, 'linked_payment_immutable', 'settlement members, captures and refunds cannot be corrected');
      const cur = p.revisions[p.revisions.length - 1];
      if (expected !== cur.revision) fail(409, 'stale_revision', `current revision is ${cur.revision}`);
      if (amount < (s.refunded.get(p.payment_id) || 0)) fail(422, 'refund_exceeds_payment', 'amount is below the already-refunded total');
      const delta = amount - cur.amount;
      const payer = p.from_user_id; const payee = p.to_user_id;
      const debit = delta > 0 ? payer : payee;
      const credit = delta > 0 ? payee : payer;
      const mag = Math.abs(delta);
      if (mag > 0) {
        if (s.available(debit) < mag) fail(409, 'insufficient_funds', 'the debit is not affordable');
        creditFits(s, credit, mag);
      }
      const rev = {
        revision: cur.revision + 1, amount, effective_at: eff, recorded_at: Math.max(now, cur.recorded_at + 1), reason,
      };
      if (rev.recorded_at > MAX_TIME) throw new InvariantError('invariant: clock beyond 9999-12-31');
      // Judge the draft: the revision is appended only for the check and removed again before any other
      // request can run (this section is synchronous).
      p.revisions.push(rev);
      let bad;
      try { bad = H.overdrawn(s, payer, now) || H.overdrawn(s, payee, now); } finally { p.revisions.pop(); }
      if (bad) fail(409, 'historical_overdraft', 'the correction would overdraw a wallet at a past instant');
      if (mag > 0) s.applyTransfers([{ from: debit, to: credit, amount: mag }]);
      p.revisions.push(rev);
      s.clock = Math.max(s.clock, rev.recorded_at);
      return revisionView(p, rev);
    });
  }

  function refundPayment(ctx) {
    return idempotent(ctx, (s) => {
      const caller = ctx.user;
      const amount = amountOf(ctx.body);
      const p = s.paymentById.get(ctx.params.id);
      if (!p) notFound('no such payment');
      if (p.to_user_id !== caller.id) fail(403, 'forbidden', 'only the receiver may refund');
      if (p.refund_of !== null) fail(422, 'invalid_refund_target', 'a refund cannot be refunded');
      const cur = p.revisions[p.revisions.length - 1];
      if ((s.refunded.get(p.payment_id) || 0) + amount > cur.amount) fail(422, 'refund_exceeds_payment', 'refunds would exceed the payment amount');
      if (s.available(caller.id) < amount) fail(409, 'insufficient_funds', 'available balance is below amount');
      creditFits(s, p.from_user_id, amount);
      const t = s.now();
      const r = {
        payment_id: s.newId('p', s.paymentById), from_user_id: caller.id, to_user_id: p.from_user_id, amount, note: p.note,
        visibility: p.visibility, request_id: null, settlement_id: null, authorization_id: null, refund_of: p.payment_id,
        created_at: t, seq: s.nextSeq(),
      };
      const view = paymentView(s, r);
      s.applyTransfers([{ from: caller.id, to: p.from_user_id, amount }]);
      s.addPayment(r);
      return view;
    });
  }

  function correctionBatch(ctx) {
    return idempotent(ctx, (s) => {
      const items = ctx.body.corrections;
      if (!Array.isArray(items) || items.length < 1 || items.length > 32) invalid('corrections must hold 1 to 32 entries');
      const seen = new Set();
      for (const [i, it] of items.entries()) {
        if (!isObject(it) || typeof it.payment_id !== 'string') invalid(`corrections[${i}] is malformed`);
        if (seen.has(it.payment_id)) invalid('payment_id repeats in corrections');
        seen.add(it.payment_id);
      }
      const plan = [];
      for (const it of items) {
        const f = correctionFields(s, it);
        const p = s.paymentById.get(it.payment_id);
        if (!p) notFound(`no such payment ${it.payment_id}`);
        if (p.authorization_id !== null || p.refund_of !== null) fail(422, 'linked_payment_immutable', 'captures and refunds cannot be corrected');
        const cur = p.revisions[p.revisions.length - 1];
        if (f.expected !== cur.revision) fail(409, 'stale_revision', `current revision of ${p.payment_id} is ${cur.revision}`);
        if (f.amount < (s.refunded.get(p.payment_id) || 0)) fail(422, 'refund_exceeds_payment', 'amount is below the already-refunded total');
        plan.push({ p, cur, ...f });
      }
      const bySettlement = new Map();
      for (const e of plan) if (e.p.settlement_id !== null) {
        if (!bySettlement.has(e.p.settlement_id)) bySettlement.set(e.p.settlement_id, []);
        bySettlement.get(e.p.settlement_id).push(e);
      }
      for (const [sid, es] of bySettlement) {
        const ids = new Set(es.map((e) => e.p.payment_id));
        if (!s.settlements.get(sid).payment_ids.every((id) => ids.has(id))) fail(422, 'incomplete_settlement', 'every member of a settlement must be corrected together');
      }
      for (const es of bySettlement.values()) {
        if (es.some((e) => e.eff !== es[0].eff)) invalid('members of one settlement need identical effective instants');
      }
      const net = new Map();
      for (const e of plan) {
        const d = e.amount - e.cur.amount;
        net.set(e.p.to_user_id, (net.get(e.p.to_user_id) || 0) + d);
        net.set(e.p.from_user_id, (net.get(e.p.from_user_id) || 0) - d);
      }
      for (const [id, d] of net) if (d < 0 && s.available(id) < -d) fail(409, 'insufficient_funds', 'the combined debit is not affordable');
      for (const [id, d] of net) if (d > 0) creditFits(s, id, d);
      const now = s.now();
      const recorded = Math.max(now, ...plan.map((e) => e.cur.recorded_at + 1));
      if (recorded > MAX_TIME) throw new InvariantError('invariant: clock beyond 9999-12-31');
      const batchId = s.newId('cb', new Set());
      const revs = plan.map((e) => ({
        revision: e.cur.revision + 1, amount: e.amount, effective_at: e.eff, recorded_at: recorded, reason: e.reason, correction_batch_id: batchId,
      }));
      plan.forEach((e, i) => e.p.revisions.push(revs[i]));
      let bad;
      try {
        bad = [...new Set(plan.flatMap((e) => [e.p.from_user_id, e.p.to_user_id]))].some((id) => H.overdrawn(s, id, now));
      } finally { plan.forEach((e) => e.p.revisions.pop()); }
      if (bad) fail(409, 'historical_overdraft', 'the batch would overdraw a wallet at a past instant');
      const transfers = [];
      for (const e of plan) {
        const d = e.amount - e.cur.amount;
        if (d > 0) transfers.push({ from: e.p.from_user_id, to: e.p.to_user_id, amount: d });
        else if (d < 0) transfers.push({ from: e.p.to_user_id, to: e.p.from_user_id, amount: -d });
      }
      s.applyTransfers(transfers);
      plan.forEach((e, i) => e.p.revisions.push(revs[i]));
      s.clock = Math.max(s.clock, recorded);
      return {
        correction_batch_id: batchId, recorded_at: iso(recorded),
        revisions: plan.map((e, i) => revisionView(e.p, revs[i])),
      };
    });
  }

  function listRevisions(ctx) {
    const s = state; const p = s.paymentById.get(ctx.params.id);
    if (!p || (p.from_user_id !== ctx.user.id && p.to_user_id !== ctx.user.id)) notFound('no such payment');
    return { status: 200, body: { revisions: p.revisions.map((r) => revisionView(p, r)) } };
  }

  // ---- auth -----------------------------------------------------------------------

  function deriveHandle(email) {
    const local = email.slice(0, email.indexOf('@')).toLowerCase();
    let out = '';
    for (const ch of local) out += /^[a-z0-9_]$/.test(ch) ? ch : '_';
    return [...out].slice(0, 20).join('');
  }

  function validEmail(email) {
    const at = email.indexOf('@');
    return at > 0 && at === email.lastIndexOf('@') && at < email.length - 1 && !/\s/.test(email);
  }

  function issueToken(s, user) {
    const token = newToken();
    s.tokens.set(tokenDigest(token), user.id);
    return token;
  }

  async function signup(ctx) {
    const b = ctx.body;
    for (const f of ['email', 'password', 'display_name']) stringType(b, f);
    if (!Object.hasOwn(b, 'password') || codePoints(b.password) < 8) invalid('password must be at least 8 characters');
    if (!Object.hasOwn(b, 'email') || !validEmail(b.email)) invalid('email must be local@domain');
    if (!Object.hasOwn(b, 'display_name')) invalid('display_name is required');
    const handle = deriveHandle(b.email);
    const check = (s) => {
      if (s.byEmail.has(b.email.toLowerCase())) fail(409, 'email_taken', 'email already registered');
      if (s.byHandle.has(handle)) fail(409, 'handle_taken', 'derived handle already taken');
    };
    check(state);
    const cred = await hashPassword(b.password);
    const s = state;
    check(s);
    const user = {
      id: s.newId('u', s.users), email: b.email, display_name: b.display_name, handle, balance: 0, cred, seq: s.nextSeq(),
    };
    s.addUser(user);
    const token = issueToken(s, user);
    return { status: 201, body: { user_id: user.id, display_name: user.display_name, token } };
  }

  async function login(ctx) {
    const b = ctx.body;
    for (const f of ['email', 'password']) stringType(b, f);
    if (!Object.hasOwn(b, 'email') || !Object.hasOwn(b, 'password')) invalid('email and password are required');
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const s = state;
      const user = s.byEmail.get(b.email.toLowerCase());
      if (!user) fail(401, 'unauthenticated', 'wrong email or password');
      const cred = user.cred;
      const ok = await verifyPassword(b.password, cred);
      if (!ok) fail(401, 'unauthenticated', 'wrong email or password');
      if (state !== s && (state.users.get(user.id) || {}).cred !== cred) continue; // state replaced meanwhile
      const live = state.users.get(user.id);
      const token = issueToken(state, live);
      return { status: 200, body: { user_id: live.id, display_name: live.display_name, token } };
    }
    return fail(401, 'unauthenticated', 'wrong email or password');
  }

  function authenticate(req) {
    const h = req.headers.authorization;
    const m = typeof h === 'string' ? /^bearer ([^\s]+)$/i.exec(h) : null;
    if (!m) fail(401, 'unauthenticated', 'missing or malformed bearer token');
    const uid = state.tokens.get(tokenDigest(m[1]));
    const user = uid === undefined ? undefined : state.users.get(uid);
    if (!user) fail(401, 'unauthenticated', 'unknown token');
    return user;
  }

  // ---- test control (§3.3, §10) -------------------------------------------------------

  async function reset(ctx) {
    return serial(async () => {
      try {
        validateFixture(ctx.body);
      } catch (e) {
        if (e instanceof Invalid) invalid(`invalid fixture: ${e.message}`);
        throw e;
      }
      const creds = await hashSeedPasswords(ctx.body.users.map((u) => u.password));
      state = stateFromFixture(ctx.body, creds);
      return { status: 204 };
    });
  }

  async function doImport(ctx) {
    return serial(async () => {
      const b = ctx.body;
      if (b.track !== TRACK) invalid('track must be pocketful');
      if (b.format_version !== FORMAT_VERSION) invalid('unsupported format_version');
      let next;
      try {
        next = stateFromExport(b.state);
      } catch (e) {
        if (e instanceof Invalid) invalid(`invalid state: ${e.message}`);
        throw e;
      }
      state = next;
      return { status: 204 };
    });
  }

  function doExport() {
    return { status: 200, body: { track: TRACK, format_version: FORMAT_VERSION, state: state.toJSON() } };
  }

  // ---- routing --------------------------------------------------------------------------

  // body: 'api' (literal-preserving parse), 'plain' (JSON.parse), or null (ignored)
  const routes = [
    { method: 'GET', path: '/health', fn: () => ({ status: 200, body: { status: 'ok' } }) },
    { method: 'POST', path: '/_test/reset', body: 'plain', fn: reset },
    { method: 'GET', path: '/_test/export', fn: doExport },
    { method: 'POST', path: '/_test/import', body: 'plain', fn: doImport },
    { method: 'POST', path: '/auth/signup', body: 'api', fn: signup },
    { method: 'POST', path: '/auth/login', body: 'api', fn: login },
    { method: 'GET', path: '/me', auth: true, fn: me },
    { method: 'POST', path: '/payments', body: 'api', auth: true, fn: createPayment },
    { method: 'POST', path: '/requests', body: 'api', auth: true, fn: createRequest },
    { method: 'GET', path: '/requests', auth: true, fn: listRequests },
    { method: 'POST', path: /^\/requests\/([^/]+)\/pay$/, body: 'api', auth: true, fn: payRequest },
    { method: 'POST', path: /^\/requests\/([^/]+)\/decline$/, auth: true, fn: declineRequest },
    { method: 'POST', path: /^\/requests\/([^/]+)\/cancel$/, auth: true, fn: cancelRequest },
    { method: 'POST', path: '/splits', body: 'api', auth: true, fn: createSplit },
    { method: 'GET', path: '/activity', auth: true, fn: listActivity },
    { method: 'POST', path: '/authorizations', body: 'api', auth: true, fn: createAuthorization },
    { method: 'GET', path: '/authorizations', auth: true, fn: listAuthorizations },
    { method: 'POST', path: /^\/authorizations\/([^/]+)\/capture$/, body: 'api', auth: true, fn: captureAuthorization },
    { method: 'POST', path: /^\/authorizations\/([^/]+)\/void$/, auth: true, fn: voidAuthorization },
    { method: 'GET', path: '/statement', auth: true, fn: statement },
    { method: 'POST', path: /^\/payments\/([^/]+)\/corrections$/, body: 'api', auth: true, fn: correctPayment },
    { method: 'POST', path: /^\/payments\/([^/]+)\/refunds$/, body: 'api', auth: true, fn: refundPayment },
    { method: 'POST', path: '/correction-batches', body: 'api', auth: true, operator: true, fn: correctionBatch },
    { method: 'GET', path: /^\/payments\/([^/]+)\/revisions$/, auth: true, fn: listRevisions },
    { method: 'POST', path: '/settlements', body: 'api', auth: true, operator: true, fn: createSettlement },
  ];

  function match(method, path) {
    for (const r of routes) {
      if (r.method !== method) continue;
      if (typeof r.path === 'string') {
        if (r.path === path) return { route: r, params: {} };
      } else {
        const m = r.path.exec(path);
        if (m) {
          let id;
          try { id = decodeURIComponent(m[1]); } catch { id = m[1]; }
          return { route: r, params: { id } };
        }
      }
    }
    return null;
  }

  function readBody(req) {
    return new Promise((resolve) => {
      const chunks = [];
      let size = 0;
      let over = false;
      req.on('data', (c) => {
        if (over) return;
        size += c.length;
        if (size > MAX_BODY) { over = true; chunks.length = 0; return; }
        chunks.push(c);
      });
      req.on('end', () => resolve(over ? null : Buffer.concat(chunks)));
      req.on('error', () => resolve(null));
    });
  }

  const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }); // a BOM is not JSON

  function parseBody(buf, mode) {
    if (buf === null) malformed('body too large or unreadable');
    let text;
    try { text = decoder.decode(buf); } catch { malformed('body is not valid UTF-8'); }
    let v;
    try { v = mode === 'api' ? parse(text) : JSON.parse(text); } catch { malformed('body is not valid JSON'); }
    if (v === null || typeof v !== 'object' || Array.isArray(v) || v instanceof JNum) {
      // D18: a test-control body that parses but is not an object is invalid, not malformed
      if (mode === 'plain') invalid('body must be a JSON object');
      malformed('body must be a JSON object');
    }
    return v;
  }

  const ui = createStatic();
  const SHELLS = new Set(['/', '/split', '/signup', '/login']);
  const SHARED = new Set(['/requests', '/authorizations', '/history']); // HTML only when the client asks for it (D26)

  function sendFile(res, file) {
    res.writeHead(200, {
      'Content-Type': file.type, 'Content-Length': file.body.length, 'Content-Security-Policy': CSP,
      'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff', 'X-Request-Id': res.requestId,
    });
    res.end(file.body);
  }

  // GET UI pages and assets; returns true when it answered.
  function serveUi(req, res, path) {
    if (req.method !== 'GET') return false;
    if (SHELLS.has(path) || ((SHARED.has(path) || /^\/payment\/[^/]+$/.test(path)) && acceptsHtml(req.headers.accept))) { sendFile(res, ui.shell); return true; }
    if (path.startsWith('/assets/')) {
      const f = ui.asset(path);
      if (!f) return false;
      sendFile(res, f);
      return true;
    }
    return false;
  }

  function send(res, status, body) {
    if (res.headersSent) return;
    if (status === 204) {
      res.writeHead(204, { 'X-Request-Id': res.requestId });
      res.end();
      return;
    }
    const data = JSON.stringify(body);
    res.writeHead(status, {
      'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(data), 'X-Request-Id': res.requestId,
    });
    res.end(data);
  }

  const sendError = (res, status, code, message) => send(res, status, { error: { code, message } });

  function accessLog(ctx, res, started) {
    log.out(JSON.stringify({
      ts: new Date().toISOString(),
      request_id: res.requestId,
      method: ctx.req.method,
      path: ctx.path,
      status: res.headersSent ? res.statusCode : null, // null: the client went away before any response
      duration_ms: Math.round(Number(process.hrtime.bigint() - started) / 1e4) / 100,
      user_id: ctx.user ? ctx.user.id : null,
      idempotency: ctx.idem,
    }));
  }

  async function handle(req, res) {
    const started = process.hrtime.bigint();
    res.requestId = newRequestId();
    const ctx = {
      req, path: String(req.url).split('?')[0], params: {}, query: null, body: null, user: null, idem: 'none',
    };
    let logged = false; // exactly one line per request, whichever of finish/close comes first
    const once = () => { if (!logged) { logged = true; accessLog(ctx, res, started); } };
    res.on('finish', once);
    res.on('close', once);
    try {
      const url = new URL(req.url, 'http://localhost');
      const path = url.pathname;
      ctx.path = path;
      ctx.query = url.searchParams;
      if (serveUi(req, res, path)) { req.resume(); return; }
      const hit = match(req.method, path);
      if (!hit) {
        req.resume();
        notFound('no such route');
      }
      const { route, params } = hit;
      ctx.params = params;
      if (route.body) ctx.body = parseBody(await readBody(req), route.body);
      else req.resume();
      // From here to the response, normal routes run synchronously (D22).
      if (route.auth) {
        ctx.user = authenticate(req);
        state.expire(state.now()); // D32
      }
      if (route.operator && !state.operators.has(ctx.user.id)) fail(403, 'forbidden', 'settlement operators only');
      const out = await route.fn(ctx);
      send(res, out.status, out.body);
    } catch (e) {
      if (e instanceof HttpError) {
        sendError(res, e.status, e.code, e.message);
      } else {
        const code = e instanceof InvariantError ? 'invariant_violation' : 'internal_error';
        log.err(JSON.stringify({ level: 'error', request_id: res.requestId, code, message: String(e && e.stack) }));
        sendError(res, 500, code, 'internal error');
      }
    }
  }

  return { handle, getState: () => state };
}

function createServer(opts = {}) {
  const log = opts.log || defaultLog;
  const app = createApp({ log });
  const busy = new WeakMap(); // socket -> response still being produced on it
  const server = http.createServer({ maxHeaderSize: 1 << 20, keepAliveTimeout: 30000 }, (req, res) => {
    const { socket } = req;
    busy.set(socket, res);
    res.on('close', () => { if (busy.get(socket) === res) busy.delete(socket); });
    app.handle(req, res);
  });
  server.on('clientError', (err, socket) => {
    // A reset, or the peer closing mid-message, ends whatever request is in flight;
    // that request's own access line covers it and there is nobody to answer.
    if (err.code === 'ECONNRESET' || err.code === 'HPE_INVALID_EOF_STATE') {
      socket.destroy();
      return;
    }
    // A parser error behind a request still being answered (pipelining, or an aborted
    // body) waits for that response; if the socket is still usable it then gets its own 400.
    const inflight = busy.get(socket);
    if (inflight) {
      inflight.once('close', () => setImmediate(() => rejectMalformed(socket)));
      return;
    }
    rejectMalformed(socket);
  });

  function rejectMalformed(socket) {
    if (socket.writable && !socket.destroyed) {
      const requestId = newRequestId();
      const data = JSON.stringify({ error: { code: 'malformed_request', message: 'bad HTTP request' } });
      socket.end(`HTTP/1.1 400 Bad Request\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: ${Buffer.byteLength(data)}\r\nX-Request-Id: ${requestId}\r\nConnection: close\r\n\r\n${data}`);
      log.out(JSON.stringify({
        ts: new Date().toISOString(), request_id: requestId, method: null, path: null, status: 400, duration_ms: 0, user_id: null, idempotency: 'none',
      }));
    } else {
      socket.destroy();
    }
  }
  return { server, app };
}

module.exports = { createServer, createApp, iso };
