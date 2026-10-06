// Payment detail: one payment's receipt, its revision history, and the refund / correct actions.
import { h, clear } from '../lib/dom.js';
import { request, load } from '../lib/api.js';
import { formatMoney, formatPlain, parseDecimal } from '../lib/money.js';
import { moneyForm } from '../lib/form.js';
import { walletPanel } from '../lib/wallet.js';
import { session, refreshMe, onMe } from '../lib/session.js';
import { localToRfc3339 } from '../lib/instants.js';
import { avatar, privacyBadge, timeEl } from '../lib/ui.js';
import { sentence } from './home.js';
import { stashPrefill } from '../lib/prefill.js';

const ZERO = /^\s*0+(\.0+)?\s*$/;

// Like parseDecimal but 0 is allowed (a correction to zero reverses the whole payment).
export function parseAmountOrZero(text, mu) {
  if (ZERO.test(String(text)) && (mu > 0 || /^\s*0+\s*$/.test(String(text)))) return { ok: true, minor: 0 };
  return parseDecimal(text, mu);
}

const nowLocal = () => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

async function findPayment(id) {
  for (let offset = 0, n = 0; n < 50; offset += 200, n += 1) {
    const r = await request('GET', `/activity?limit=200&offset=${offset}`);
    if (r.outcome !== 'ok') return { error: true };
    const hit = (r.body.payments || []).find((p) => p.payment_id === id);
    if (hit) return { payment: hit };
    if (!r.body.has_more) return { payment: null };
  }
  return { payment: null };
}

export function payment(id) {
  let revisions = null;   // null until loaded
  let pay = null;
  let state = 'loading';  // loading | ready | missing | failed
  const box = h('div', { class: 'detail-box' }, h('p', { class: 'skeleton', testid: 'payment-detail-loading', text: 'Loading this payment…' }));
  const wallet = walletPanel({ onRefresh: () => reload() });
  onMe((me, ok) => { if (me) { wallet.update(me); paint(); } else if (!ok) wallet.failed(); });
  let forms = null;
  let seq = 0;

  async function reload() {
    const mine = ++seq;
    const rev = await request('GET', `/payments/${encodeURIComponent(id)}/revisions`);
    if (mine !== seq) return;
    if (rev.status === 404) { state = 'missing'; paint(); return; }
    if (rev.outcome !== 'ok') { if (state === 'loading') state = 'failed'; paint(); return; }
    const found = await findPayment(id);
    if (mine !== seq) return;
    if (found.error) { if (state === 'loading') state = 'failed'; paint(); return; }
    if (!found.payment) { state = 'missing'; paint(); return; }
    revisions = rev.body.revisions || [];
    pay = found.payment;
    state = 'ready';
    paint();
  }
  const refreshAll = () => Promise.all([refreshMe(), reload()]);

  function buildForms(me) {
    const sender = pay.from_user_id === me.user_id; const receiver = pay.to_user_id === me.user_id;
    const linked = pay.settlement_id || pay.authorization_id || pay.refund_of;
    const out = { refund: null, correct: null };
    if (receiver && !pay.refund_of) {
      out.refund = moneyForm({
        prefix: 'payment-detail-refund',
        title: 'Refund',
        lede: 'Send some or all of this payment back from your available balance.',
        submitLabel: 'Refund',
        path: `/payments/${encodeURIComponent(id)}/refunds`,
        fields: [{ name: 'amount', label: 'Amount to refund', inputmode: 'decimal', placeholder: '0.00' }],
        build(v) {
          const a = parseDecimal(v.amount, session.me.minor_units);
          return a.ok ? { body: { amount: a.minor } } : { error: a.error };
        },
        success: () => 'Refund sent.',
        onDone: (kind) => { if (kind === 'ok') refreshAll(); },
      });
    }
    if (sender && !linked) {
      const cur = revisions[revisions.length - 1];
      out.correct = moneyForm({
        prefix: 'payment-detail-correct',
        title: 'Correct the amount',
        lede: 'Record a new amount for this payment. The original receipt stays as it was; the change is added to its history. Zero reverses it entirely.',
        submitLabel: 'Record correction',
        path: `/payments/${encodeURIComponent(id)}/corrections`,
        fields: [
          { name: 'amount', label: 'Correct amount', inputmode: 'decimal', placeholder: formatPlain(cur.amount, me.minor_units) },
          { name: 'effective_at', label: 'Took effect at', type: 'datetime-local', hint: 'Not in the future. Defaults to now.' },
          { name: 'reason', label: 'Reason', placeholder: 'Why is it being corrected?' },
        ],
        build(v) {
          const a = parseAmountOrZero(v.amount, session.me.minor_units);
          if (!a.ok) return { error: a.error };
          const at = localToRfc3339(v.effective_at);
          if (!at) return { error: 'Choose when the correction took effect.' };
          const reason = v.reason.trim();
          if (!reason) return { error: 'Give a short reason.' };
          if ([...reason].length > 200) return { error: 'The reason can be at most 200 characters.' };
          return { body: { expected_revision: revisions[revisions.length - 1].revision, amount: a.minor, effective_at: at, reason } };
        },
        success: () => 'Correction recorded.',
        onDone: () => { refreshAll(); },
      });
      out.correct.controls.effective_at.value = nowLocal();
    }
    return out;
  }

  function revisionRow(r, me) {
    return h('li', { class: 'item', testid: `payment-detail-revision-${r.revision}` },
      h('div', { class: 'item-top' },
        h('p', { class: 'item-who', text: r.revision === 1 ? 'Original payment' : `Revision ${r.revision}` }),
        h('p', { class: 'item-amount', testid: `payment-detail-revision-amount-${r.revision}`, 'data-amount': r.amount, text: formatMoney(r.amount, me.minor_units, me.currency) })),
      r.reason && h('p', { class: 'item-note', text: r.reason }),
      h('div', { class: 'item-meta' }, h('span', {}, 'Took effect ', timeEl(r.effective_at)), h('span', {}, 'Recorded ', timeEl(r.recorded_at)),
        r.correction_batch_id && h('span', { class: 'badge badge-pending', text: 'Batch correction' })));
  }

  function paint() {
    const me = session.me;
    clear(box);
    if (state === 'loading' || !me && state === 'ready') { box.append(h('p', { class: 'skeleton', testid: 'payment-detail-loading', text: 'Loading this payment…' })); return; }
    if (state === 'missing') {
      box.append(h('div', { class: 'empty', testid: 'payment-detail-unavailable' }, h('strong', { text: 'This payment is not available' }), 'It does not exist, or it is not one of yours.'));
      return;
    }
    if (state === 'failed') {
      box.append(h('p', { class: 'msg msg-error', role: 'alert', testid: 'payment-detail-error', text: 'We could not load this payment. Try Refresh.' }));
      return;
    }
    const money = (n) => formatMoney(n, me.minor_units, me.currency);
    const cur = revisions[revisions.length - 1];
    const sent = pay.from_user_id === me.user_id;
    if (!forms) forms = buildForms(me);   // built once: a reload must not wipe a form's outcome message
    if (forms.correct) forms.correct.controls.amount.placeholder = formatPlain(cur.amount, me.minor_units);
    box.append(...[
      h('section', { class: 'card detail-head', 'aria-labelledby': 'payment-detail-title' },
        h('div', { class: 'item-id' }, avatar(sent ? pay.to_handle : pay.from_handle),
          h('div', {}, h('h2', { id: 'payment-detail-title', testid: 'payment-detail-sentence', text: sentence(pay, me.user_id) }),
            h('p', { class: 'pair', testid: 'payment-detail-parties', text: `@${pay.from_handle} → @${pay.to_handle}` }))),
        h('p', { class: 'wallet-available', testid: 'payment-detail-amount', 'data-amount': pay.amount, text: money(pay.amount) }),
        cur.revision > 1 && h('p', { class: 'fineprint', testid: 'payment-detail-current', 'data-amount': cur.amount, text: `Corrected amount: ${money(cur.amount)}` }),
        h('p', { class: 'item-note', testid: 'payment-detail-note', text: pay.note }),
        h('div', { class: 'item-meta' }, h('span', { testid: 'payment-detail-privacy', 'data-visibility': pay.visibility }, privacyBadge(pay.visibility)),
          h('span', { testid: 'payment-detail-time' }, timeEl(pay.created_at)),
          pay.refund_of && h('a', { href: `/payment/${encodeURIComponent(pay.refund_of)}`, testid: 'payment-detail-refund-of', text: 'Refund of an earlier payment' }),
          pay.authorization_id && h('span', { text: 'Collected from a hold' }),
          pay.settlement_id && h('span', { text: 'Part of a settlement' }))),
      h('section', { 'aria-labelledby': 'payment-detail-history' },
        h('div', { class: 'section-title' }, h('h2', { id: 'payment-detail-history', text: 'History of this payment' })),
        h('ul', { class: 'list', testid: 'payment-detail-revisions' }, revisions.map((r) => revisionRow(r, me)))),
      h('div', { class: 'item-actions' },
        sent && h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: 'payment-detail-pay-again', text: 'Pay again', onclick: () => { stashPrefill('pay', pay, me); location.assign('/'); } }),
        pay.to_user_id === me.user_id && h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: 'payment-detail-request-again', text: 'Request again', onclick: () => { stashPrefill('request', pay, me); location.assign('/'); } })),
      forms.refund && forms.refund.el,
      forms.correct && forms.correct.el,
      !forms.refund && !forms.correct && h('p', { class: 'fineprint', testid: 'payment-detail-no-actions', text: pay.refund_of || pay.authorization_id || pay.settlement_id ? 'This payment cannot be refunded or corrected from here.' : 'Only the person who received this payment can refund it, and only the sender can correct it.' })].filter(Boolean));
  }

  reload();
  return {
    title: 'Payment',
    nodes: [
      h('div', { class: 'page-head' }, h('h1', { text: 'Payment' }), h('p', {}, h('a', { href: '/', text: '← Back to activity' }), ' '),
        h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: 'payment-detail-refresh', text: 'Refresh this payment', onclick: () => reload() })),
      wallet.el, box,
    ],
  };
}
