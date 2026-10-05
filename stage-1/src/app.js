'use strict';
// HTTP layer and handlers. Every write runs validation, checks and mutation in
// one synchronous section after the body has been read (D22); only reset,
// import, signup and login await (password hashing), and they re-read the
// current state after awaiting.

const http = require('node:http');
const { randomUUID } = require('node:crypto');
const { parse, canonical, exactInteger, isObject, JNum } = require('./json');
const {
  State, Invalid, InvariantError, validateFixture, stateFromFixture, stateFromExport, idemScope,
  codePoints, MAX_AMOUNT, VISIBILITIES, STATUSES,
} = require('./state');
const { hashPassword, verifyPassword, hashSeedPasswords, tokenDigest, newToken } = require('./hash');

const MAX_BODY = 64 * 1024 * 1024; // D15
const FORMAT_VERSION = 1;
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

function iso(ms) {
  return new Date(ms).toISOString().replace('Z', '+00:00');
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
      created_at: iso(p.created_at),
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

  function me(ctx) {
    const s = state; const u = ctx.user;
    return {
      status: 200,
      body: { user_id: u.id, display_name: u.display_name, handle: u.handle, balance: u.balance, currency: s.currency, minor_units: s.minor_units },
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
      if (caller.balance < amount) fail(409, 'insufficient_funds', 'balance is below amount');
      s.applyTransfers([{ from: caller.id, to: to.id, amount }]);
      const p = {
        payment_id: s.newId('p', s.paymentById), from_user_id: caller.id, to_user_id: to.id, amount, note, visibility,
        request_id: null, settlement_id: null, created_at: s.now(), seq: s.nextSeq(),
      };
      s.addPayment(p);
      return paymentView(s, p);
    });
  }

  function newRequest(s, requester, payer, amount, note, t, splitId) {
    const r = {
      request_id: s.newId('rq', s.requestById), requester_id: requester.id, payer_id: payer.id, amount, note,
      status: 'pending', payment_id: null, created_at: t, seq: s.nextSeq(),
    };
    if (splitId) r.split_id = splitId;
    s.addRequest(r);
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
      return requestView(s, newRequest(s, caller, payer, amount, note, s.now()));
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
      if (caller.balance < r.amount) fail(409, 'insufficient_funds', 'balance is below amount');
      s.applyTransfers([{ from: caller.id, to: r.requester_id, amount: r.amount }]);
      const p = {
        payment_id: s.newId('p', s.paymentById), from_user_id: caller.id, to_user_id: r.requester_id, amount: r.amount,
        note: r.note, visibility, request_id: r.request_id, settlement_id: null, created_at: s.now(), seq: s.nextSeq(),
      };
      s.addPayment(p);
      r.status = 'paid';
      r.payment_id = p.payment_id;
      return paymentView(s, p);
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
      s.splits.set(splitId, {
        split_id: splitId, creator_id: caller.id, amount, note, shares, request_ids: reqs.map((r) => r.request_id), created_at: t,
      });
      return {
        split_id: splitId, amount, currency: s.currency, note,
        shares: shares.map((x) => ({ ...x })), requests: reqs.map((r) => requestView(s, r)), created_at: iso(t),
      };
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
        if (s.users.get(id).balance + d < 0) fail(409, 'insufficient_funds', 'settlement is not affordable');
      }
      s.applyTransfers(entries.map((e) => ({ from: e.from.id, to: e.to.id, amount: e.amount })));
      const t = s.now();
      const settlementId = s.newId('st', s.settlements);
      const payments = entries.map((e) => {
        const p = {
          payment_id: s.newId('p', s.paymentById), from_user_id: e.from.id, to_user_id: e.to.id, amount: e.amount,
          note: e.note, visibility: e.visibility, request_id: null, settlement_id: settlementId, created_at: t, seq: s.nextSeq(),
        };
        s.addPayment(p);
        return p;
      });
      s.settlements.set(settlementId, {
        settlement_id: settlementId, operator_id: ctx.user.id, payment_ids: payments.map((p) => p.payment_id), committed_at: t,
      });
      return { settlement_id: settlementId, committed_at: iso(t), payments: payments.map((p) => paymentView(s, p)) };
    });
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
      status: res.statusCode,
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
    res.on('finish', () => accessLog(ctx, res, started));
    res.on('close', () => { if (!res.writableFinished) accessLog(ctx, res, started); });
    try {
      const url = new URL(req.url, 'http://localhost');
      const path = url.pathname;
      ctx.path = path;
      ctx.query = url.searchParams;
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
      if (route.auth) ctx.user = authenticate(req);
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
  const server = http.createServer({ maxHeaderSize: 1 << 20, keepAliveTimeout: 30000 }, (req, res) => { app.handle(req, res); });
  server.on('clientError', (err, socket) => {
    if (socket.writable) {
      const requestId = newRequestId();
      const data = JSON.stringify({ error: { code: 'malformed_request', message: 'bad HTTP request' } });
      socket.end(`HTTP/1.1 400 Bad Request\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: ${Buffer.byteLength(data)}\r\nX-Request-Id: ${requestId}\r\nConnection: close\r\n\r\n${data}`);
      log.out(JSON.stringify({
        ts: new Date().toISOString(), request_id: requestId, method: null, path: null, status: 400, duration_ms: 0, user_id: null, idempotency: 'none',
      }));
    } else {
      socket.destroy();
    }
  });
  return { server, app };
}

module.exports = { createServer, createApp, iso };
