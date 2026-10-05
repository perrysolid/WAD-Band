import { h, clear } from '../lib/dom.js';
import { load, request, messageOf, identityBook } from '../lib/api.js';
import { formatMoney, formatPlain, parseDecimal } from '../lib/money.js';
import { walletPanel } from '../lib/wallet.js';
import { session, refreshMe, onMe, humanTime } from '../lib/session.js';
import { holdForm } from './home.js';
import { toast } from '../lib/toast.js';
import { avatar } from '../lib/ui.js';

const STATUS_LABEL = { open: 'Open', captured: 'Captured', voided: 'Voided', expired: 'Expired' };

export function authorizations() {
  let list = null;
  let failed = false;
  const drafts = new Map();          // authorization id -> { amount, keepOpen } typed but not yet sent
  const identityFor = identityBook(); // (authorization id + body) identity, D29
  const notice = h('div', { class: 'notice' });
  const box = h('div', { class: 'auth-box' }, h('p', { class: 'skeleton', text: 'Loading holds…' }));

  function say(kind, text) {
    clear(notice);
    if (kind === 'error') notice.append(h('p', { class: 'msg msg-error', role: 'alert', testid: 'authorization-error', text }));
    else if (kind === 'uncertain') notice.append(h('p', { class: 'msg msg-uncertain', role: 'status', testid: 'authorization-uncertain' }, h('strong', { text: 'Outcome unknown' }), text));
  }

  async function capture(a, amountEl, keepEl) {
    const me = session.me;
    if (!me) return;
    const parsed = parseDecimal(amountEl.value, me.minor_units);
    if (!parsed.ok) { say('error', parsed.error); return; }
    const body = { amount: parsed.minor, ...(keepEl.checked ? { final: false } : {}) };
    const path = `/authorizations/${encodeURIComponent(a.authorization_id)}/capture`;
    const res = await request('POST', path, { body, key: identityFor(a.authorization_id).keyFor(path, body) });
    if (res.outcome === 'ok') { say(null); toast('Captured.'); drafts.delete(a.authorization_id); } else if (res.outcome === 'refused') say('error', messageOf(res));
    else say('uncertain', 'We did not get an answer, so we cannot tell whether the capture happened. Press Capture again: the same request is sent and can only count once.');
    if (res.outcome !== 'uncertain') refreshAll();
  }

  async function voidIt(a) {
    const res = await request('POST', `/authorizations/${encodeURIComponent(a.authorization_id)}/void`, {});
    if (res.outcome === 'ok') { say(null); toast('Hold voided.'); }
    else if (res.outcome === 'refused') say('error', messageOf(res));
    else say('uncertain', 'We did not get an answer. Voiding is safe to repeat: press Void again.');
    if (res.outcome !== 'uncertain') refreshAll();
  }

  function item(a, mine, me) {
    const id = a.authorization_id;
    const money = (n) => formatMoney(n, me.minor_units, me.currency);
    const outgoing = a.from_user_id === mine;
    const open = a.status === 'open';
    const draft = drafts.get(id) || {};
    let actions = null;
    if (open && !outgoing) {
      const amountId = `capture-amount-${id}`;
      const amountEl = h('input', {
        id: amountId, type: 'text', inputmode: 'decimal', autocomplete: 'off', testid: `authorization-capture-amount-${id}`,
        value: draft.amount ?? formatPlain(a.remaining_amount, me.minor_units),
        oninput: (e) => drafts.set(id, { ...drafts.get(id), amount: e.target.value }),
      });
      const keepEl = h('input', {
        id: `keep-${id}`, type: 'checkbox', testid: `authorization-keep-open-${id}`,
        onchange: (e) => drafts.set(id, { ...drafts.get(id), keepOpen: e.target.checked }),
      });
      keepEl.checked = !!draft.keepOpen;
      actions = h('div', { class: 'item-actions' },
        h('div', { class: 'field' }, h('label', { for: amountId, text: 'Amount to capture' }), amountEl),
        h('button', { type: 'button', class: 'btn btn-small', testid: `authorization-capture-${id}`, text: 'Capture', onclick: () => capture(a, amountEl, keepEl) }),
        h('label', { class: 'checkline', for: `keep-${id}` }, keepEl, 'Keep the rest on hold'));
    } else if (open && outgoing) {
      actions = h('div', { class: 'item-actions' },
        h('button', { type: 'button', class: 'btn btn-danger btn-small', testid: `authorization-void-${id}`, text: 'Void hold', onclick: () => voidIt(a) }));
    }
    return h('li', { class: 'item', testid: `authorization-item-${id}`, 'data-status': a.status },
      h('div', { class: 'item-top' },
        h('div', { class: 'item-id' }, avatar(outgoing ? a.to_handle : a.from_handle),
          h('p', { class: 'item-who', text: outgoing ? `You hold for @${a.to_handle}` : `@${a.from_handle} holds for you` })),
        h('p', { class: 'item-amount', testid: `authorization-amount-${id}`, text: money(a.amount) })),
      h('p', { class: 'item-note', testid: `authorization-note-${id}`, text: a.note }),
      h('div', { class: 'item-meta' },
        h('span', { class: `badge badge-${a.status}`, text: STATUS_LABEL[a.status] || a.status }),
        a.status === 'captured' && h('span', {}, 'Captured ', h('strong', { testid: `authorization-captured-${id}`, text: money(a.captured_amount) })),
        open && a.captured_amount > 0 && h('span', { text: `${money(a.captured_amount)} captured so far, ${money(a.remaining_amount)} still held` }),
        h('span', {}, a.status === 'open' ? 'Expires ' : 'Expiry ', h('time', { datetime: a.expires_at, text: humanTime(a.expires_at) })),
        h('span', { class: 'raw-time', testid: `authorization-expires-${id}`, text: a.expires_at })),
      actions);
  }

  function paint() {
    const me = session.me;
    if (!me) return;
    clear(box);
    if (list === null) {
      if (failed) box.append(h('p', { class: 'msg msg-info', role: 'status', text: 'We could not load your holds. Try Refresh.' }));
      return;
    }
    if (list.length === 0) {
      box.append(h('div', { class: 'empty', testid: 'empty-authorizations' }, h('strong', { text: 'No holds yet' }), 'Holds you place or receive will appear here.'));
    } else {
      box.append(h('ul', { class: 'list', testid: 'authorization-list' }, list.map((a) => item(a, me.user_id, me))));
    }
  }

  const refreshList = () => load('authorizations', '/authorizations?limit=200', (r) => {
    if (r.outcome === 'ok') { list = (r.body.authorizations || []).map((a) => ({ ...a, payment_ids: a.payment_ids || [] })); failed = false; }
    else if (r.status === 404) { list = []; failed = false; } // D38: an older service has no such list
    else if (list === null) failed = true;
    paint();
  });
  const refreshAll = () => Promise.all([refreshMe(), refreshList()]);

  const wallet = walletPanel({ onRefresh: refreshAll });
  onMe((me, ok) => { if (me) { wallet.update(me); paint(); } else if (!ok) wallet.failed(); });
  const form = holdForm({ onDone: refreshAll });
  refreshList();
  return {
    title: 'Holds',
    nodes: [
      h('div', { class: 'page-head' }, h('h1', { text: 'Holds' }), h('p', { text: 'Money set aside for a payment that has not happened yet.' })),
      wallet.el,
      h('div', { class: 'grid-2' },
        form.el,
        h('section', { 'aria-labelledby': 'holds-title' }, h('div', { class: 'section-title' }, h('h2', { id: 'holds-title', text: 'Your holds' })), notice, box)),
    ],
  };
}
