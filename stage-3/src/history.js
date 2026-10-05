'use strict';
// Effective-time / recorded-time views over the ledger (S3). Pure reads of State; every
// function here is synchronous and never mutates.

const INF = Infinity;

// Latest revision recorded at or before K, or null when none had been recorded yet.
function selectRev(p, K) {
  for (let i = p.revisions.length - 1; i >= 0; i -= 1) if (p.revisions[i].recorded_at <= K) return p.revisions[i];
  return null;
}

// Ids are opaque strings: ties break by plain string order.
const idCmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

// The caller's movements under knowledge K: [{p, rev, delta, t}] in (t, id) order.
function moves(s, uid, K) {
  const out = [];
  for (const p of s.userPayments.get(uid) || []) {
    const rev = selectRev(p, K);
    if (!rev) continue;
    const delta = p.from_user_id === p.to_user_id ? 0 : p.to_user_id === uid ? rev.amount : -rev.amount;
    out.push({ p, rev, delta, t: rev.effective_at });
  }
  out.sort((a, b) => a.t - b.t || idCmp(a.p.payment_id, b.p.payment_id));
  return out;
}

function totalAt(s, uid, asOf, K) {
  let b = s.users.get(uid).opening;
  for (const m of moves(s, uid, K)) if (m.t <= asOf) b += m.delta;
  return b;
}

// Hold timeline events of one authorization as seen with knowledge K, up to T.
function holdOf(a, T, K) {
  if (a.created_at > K) return 0;
  let h = 0;
  for (const e of a.events) if (e.t <= T && (e.exp || e.t <= K)) h += e.d;
  if (a.status === 'open' && a.expires_at <= T) h -= a.amount - a.captured_amount; // clock expiry still to come
  return h;
}

function heldAt(s, uid, T, K) {
  let h = 0;
  for (const a of s.userAuths.get(uid) || []) h += holdOf(a, T, K);
  return h;
}

// S3 historical overdraft: total or available negative at any effective/event boundary up to `now`.
function overdrawn(s, uid, now) {
  const ms = moves(s, uid, INF).filter((m) => m.t <= now);
  const evs = [];
  for (const a of s.userAuths.get(uid) || []) for (const e of a.events) if (e.t <= now) evs.push(e);
  evs.sort((a, b) => a.t - b.t);
  const times = [...new Set([...ms.map((m) => m.t), ...evs.map((e) => e.t)])].sort((a, b) => a - b);
  let total = s.users.get(uid).opening; let held = 0; let i = 0; let j = 0;
  if (total < 0) return true;
  for (const t of times) {
    while (i < ms.length && ms[i].t <= t) { total += ms[i].delta; i += 1; }
    while (j < evs.length && evs[j].t <= t) { held += evs[j].d; j += 1; }
    if (total < 0 || total - held < 0) return true;
  }
  return false;
}

// Full statement result for the window [from, to) under knowledge K.
function statement(s, uid, from, to, K) {
  const ms = moves(s, uid, K);
  let running = s.users.get(uid).opening;
  let i = 0;
  while (i < ms.length && ms[i].t < from) { running += ms[i].delta; i += 1; }
  const opening = running;
  const entries = [];
  for (; i < ms.length && ms[i].t < to; i += 1) {
    running += ms[i].delta;
    entries.push({ m: ms[i], balance_after: running });
  }
  while (i < ms.length && ms[i].t < to) i += 1;
  return { opening, closing: running, entries };
}

module.exports = { selectRev, moves, totalAt, heldAt, overdrawn, statement, INF };
