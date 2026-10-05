// The only place that talks to the service. Every call reports one of three outcomes
// (D37): ok (2xx), refused (any other 4xx), uncertain (no usable response, 5xx, 408, 429).
import { uuid } from './dom.js';

const TOKEN_KEY = 'pf.token';
const WRITE_TIMEOUT_MS = 8000;  // D37: no response within 8 s is uncertain
const READ_TIMEOUT_MS = 30000;

export const token = {
  get: () => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set: (t) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => { try { localStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ } },
};

export async function request(method, path, { body, key, timeoutMs } = {}) {
  const headers = { Accept: 'application/json' };
  const t = token.get();
  if (t) headers.Authorization = `Bearer ${t}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (key) headers['Idempotency-Key'] = key;
  const limit = timeoutMs || (method === 'GET' ? READ_TIMEOUT_MS : WRITE_TIMEOUT_MS);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), limit);
  let status = 0;
  let data = null;
  try {
    const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: ctl.signal, cache: 'no-store' });
    status = res.status;
    const text = await res.text();
    try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  } catch {
    return { outcome: 'uncertain', status: 0, body: null };
  } finally {
    clearTimeout(timer);
  }
  if (status >= 200 && status < 300) return { outcome: 'ok', status, body: data };
  if (status >= 500 || status === 408 || status === 429) return { outcome: 'uncertain', status, body: data };
  if (status === 401 && !path.startsWith('/auth/')) {
    token.clear();
    location.replace('/login');
  }
  return { outcome: 'refused', status, body: data };
}

export const errorOf = (r) => (r && r.body && r.body.error) || { code: 'unknown', message: 'Something went wrong.' };

const FRIENDLY = {
  insufficient_funds: 'There is not enough available money for that. Money on hold cannot be spent.',
  not_found: 'We could not find that person, or it no longer exists.',
  self_payment: 'You cannot pay or hold money for yourself.',
  self_request: 'You cannot request money from yourself.',
  request_not_pending: 'That request is no longer pending.',
  authorization_not_open: 'That hold is already closed.',
  authorization_expired: 'That hold has expired.',
  capture_exceeds_authorization: 'That is more than is still held.',
  idempotency_key_reuse: 'That attempt was already used for something different. Change a field and try again.',
  forbidden: 'You are not allowed to do that.',
  email_taken: 'That email is already registered.',
  handle_taken: 'The handle derived from that email is already taken.',
  unauthenticated: 'The email or password is not right.',
  refund_exceeds_payment: 'That is more than can still be refunded or removed on this payment. Refunds cannot add up to more than its current amount.',
  invalid_refund_target: 'A refund cannot itself be refunded.',
  linked_payment_immutable: 'Payments that came from a settlement, a hold capture or a refund cannot be corrected here.',
  stale_revision: 'This payment was changed since you opened it. Refresh it, then try again.',
  historical_overdraft: 'That change would have left someone with a negative balance at an earlier moment, so it was refused.',
};

export function messageOf(r) {
  const e = errorOf(r);
  if (e.code === 'validation_failed' && e.message) return e.message.charAt(0).toUpperCase() + e.message.slice(1) + (/[.!?]$/.test(e.message) ? '' : '.');
  return FRIENDLY[e.code] || e.message || 'The request was refused.';
}

// ---- latest wins (D31): one sequence counter per data domain ----

const seq = { me: 0, activity: 0, requests: 0, authorizations: 0 };

// Issue a read; apply its result only if no later read of the same domain was issued.
export async function load(domain, path, apply) {
  const mine = ++seq[domain];
  const r = await request('GET', path);
  if (mine !== seq[domain]) return false;
  apply(r);
  return true;
}

// ---- idempotency identity (D29) ----

export class Identity {
  constructor() { this.fingerprint = null; this.key = null; }
  // The key is reused while the exact body is unchanged; any change mints a new one.
  keyFor(path, body) {
    const fp = `${path}\n${JSON.stringify(body)}`;
    if (fp !== this.fingerprint) { this.fingerprint = fp; this.key = uuid(); }
    return this.key;
  }
}

export function identityBook() {
  const m = new Map();
  return (id) => { if (!m.has(id)) m.set(id, new Identity()); return m.get(id); };
}

// D38: read /me tolerantly so an older service shape never throws.
export function normalizeMe(b) {
  const x = b || {};
  return {
    ...x,
    total: x.total ?? x.balance,
    available: x.available ?? x.balance,
    held: x.held ?? 0,
  };
}
