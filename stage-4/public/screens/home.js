import { h, clear } from '../lib/dom.js';
import { load, request } from '../lib/api.js';
import { formatMoney, parseDecimal, cleanHandle } from '../lib/money.js';
import { moneyForm } from '../lib/form.js';
import { walletPanel } from '../lib/wallet.js';
import { session, refreshMe, onMe } from '../lib/session.js';
import { avatar, privacyBadge, timeEl } from '../lib/ui.js';
import { stashPrefill, takePrefill, PREFILL_EVENT } from '../lib/prefill.js';

export const VISIBILITY = [['public', 'Public: anyone can see it in the feed'], ['private', 'Private: only you and the other person']];

// Shared parsing for the handle / amount / note fields of the money forms.
export function readCommon(v, { handleMsg }) {
  const me = session.me;
  if (!me) return { error: 'Still loading your wallet. Try again in a moment.' };
  const handle = cleanHandle(v.handle);
  if (!handle) return { error: handleMsg };
  const amt = parseDecimal(v.amount, me.minor_units);
  if (!amt.ok) return { error: amt.error };
  if ([...v.note].length > 200) return { error: 'The note can be at most 200 characters.' };
  return { handle, amount: amt.minor, note: v.note };
}

export const handleField = (label, hint) => ({ name: 'handle', label, placeholder: 'e.g. bob', hint });
export const amountField = () => ({ name: 'amount', label: 'Amount', inputmode: 'decimal', placeholder: '0.00' });
export const noteField = () => ({ name: 'note', label: 'Note (optional)', placeholder: 'What is it for?' });
export const visibilityField = () => ({ name: 'visibility', label: 'Who can see it', kind: 'select', options: VISIBILITY });

// The "Hold money" form, used on / (D30) and on /authorizations.
export function holdForm({ onDone }) {
  return moneyForm({
    prefix: 'authorize',
    title: 'Hold money',
    lede: 'Set money aside for someone to collect later. It leaves your available balance but stays yours until they capture it.',
    submitLabel: 'Place hold',
    path: '/authorizations',
    fields: [handleField('Hold for'), amountField(), noteField(), visibilityField()],
    build(v) {
      const c = readCommon(v, { handleMsg: 'Enter who the hold is for.' });
      if (c.error) return c;
      return { body: { to_handle: c.handle, amount: c.amount, ...(c.note ? { note: c.note } : {}), visibility: v.visibility } };
    },
    success: () => 'Hold placed.',
    onDone,
  });
}

// ---- activity feed ----

// The sentence is told from the viewer's side; the parties line always names both handles.
export function sentence(p, meId) {
  const sent = p.from_user_id === meId; const got = p.to_user_id === meId;
  const from = `@${p.from_handle}`; const to = `@${p.to_handle}`;
  if (p.refund_of) return sent ? `You refunded ${to}` : got ? `${from} refunded you` : `${from} refunded ${to}`;
  if (p.authorization_id) return sent ? `${to} collected from your hold` : got ? `You collected from ${from}'s hold` : `${to} collected from ${from}'s hold`;
  if (p.request_id) return sent ? `You paid ${to}'s request` : got ? `${from} paid your request` : `${from} paid ${to}'s request`;
  if (p.settlement_id) return sent ? `You settled with ${to}` : got ? `${from} settled with you` : `${from} settled with ${to}`;
  return sent ? `You paid ${to}` : got ? `${from} paid you` : `${from} paid ${to}`;
}

function feedItem(p, me) {
  const id = p.payment_id;
  const sent = p.from_user_id === me.user_id;
  const other = sent ? p.to_handle : p.from_handle;
  const open = (e) => { if (!e.target.closest('a, button')) location.assign(`/payment/${encodeURIComponent(id)}`); };
  return h('li', { onclick: open, class: `item item-click ${sent ? 'item-sent' : p.to_user_id === me.user_id ? 'item-received' : ''}`, testid: `activity-item-${id}`, 'data-visibility': p.visibility },
    h('div', { class: 'item-top' },
      h('div', { class: 'item-id' }, avatar(other),
        h('p', { class: 'item-who', testid: `activity-parties-${id}` }, h('a', { class: 'item-link', href: `/payment/${encodeURIComponent(id)}`, text: sentence(p, me.user_id) }), h('span', { class: 'pair', text: ` @${p.from_handle} → @${p.to_handle}` }))),
      h('p', { class: 'item-amount', testid: `activity-amount-${id}`, text: formatMoney(p.amount, me.minor_units, me.currency) })),
    h('p', { class: 'item-note', testid: `activity-note-${id}`, text: p.note }),
    h('div', { class: 'item-meta' },
      privacyBadge(p.visibility),
      timeEl(p.created_at)),
    sent && h('div', { class: 'item-actions' }, h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: `activity-pay-again-${id}`, text: 'Pay again',
      onclick: () => { stashPrefill('pay', p, me); window.dispatchEvent(new Event(PREFILL_EVENT)); } })),
    p.to_user_id === me.user_id && h('div', { class: 'item-actions' }, h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: `activity-request-again-${id}`, text: 'Request again',
      onclick: () => { stashPrefill('request', p, me); window.dispatchEvent(new Event(PREFILL_EVENT)); } })));
}

export function home() {
  let payments = null;
  let feedFailed = false;
  const feed = h('div', { class: 'feed' }, h('p', { class: 'skeleton', text: 'Loading activity…' }));

  function paintFeed() {
    if (!session.me) return;
    if (payments === null) {
      if (feedFailed) { clear(feed); feed.append(h('p', { class: 'msg msg-info', role: 'status', text: 'We could not load the activity feed. Try Refresh.' })); }
      return;
    }
    clear(feed);
    if (payments.length === 0) {
      feed.append(h('div', { class: 'empty', testid: 'empty-activity' }, h('strong', { text: 'No activity yet' }), 'Payments you can see will show up here.'));
    } else {
      feed.append(h('ul', { class: 'list', testid: 'activity-list' }, payments.map((p) => feedItem(p, session.me))));
    }
  }

  const refreshActivity = () => load('activity', '/activity?limit=200', (r) => {
    if (r.outcome === 'ok') { payments = r.body.payments || []; feedFailed = false; } else if (payments === null) feedFailed = true;
    paintFeed();
  });
  const refreshAll = () => Promise.all([refreshMe(), refreshActivity()]);

  const wallet = walletPanel({ onRefresh: refreshAll });
  onMe((me, ok) => { if (me) { wallet.update(me); paintFeed(); } else if (!ok) wallet.failed(); });

  const afterMoney = () => { refreshAll(); };
  const pay = moneyForm({
    prefix: 'pay',
    title: 'Pay someone',
    lede: 'Send money from your available balance.',
    submitLabel: 'Pay',
    path: '/payments',
    fields: [handleField('Pay to'), amountField(), noteField(), visibilityField()],
    build(v) {
      const c = readCommon(v, { handleMsg: 'Enter who you are paying.' });
      if (c.error) return c;
      return { body: { to_handle: c.handle, amount: c.amount, ...(c.note ? { note: c.note } : {}), visibility: v.visibility } };
    },
    success: () => 'Payment sent.',
    onDone: afterMoney,
  });
  const ask = moneyForm({
    prefix: 'request',
    title: 'Request money',
    lede: 'Ask someone to pay you. They can accept or decline from their Requests screen.',
    submitLabel: 'Request',
    path: '/requests',
    fields: [handleField('Request from'), amountField(), noteField()],
    build(v) {
      const c = readCommon(v, { handleMsg: 'Enter who you are asking.' });
      if (c.error) return c;
      return { body: { payer_handle: c.handle, amount: c.amount, ...(c.note ? { note: c.note } : {}) } };
    },
    success: () => 'Request sent.',
  });
  const hold = holdForm({ onDone: afterMoney });

  // Narrow screens show one form at a time (pay first); every form stays in the DOM. Wide screens show all.
  const selectTab = (k) => {
    tabs.parentElement.dataset.active = k;
    tabBtns.forEach((b) => b.setAttribute('aria-selected', String(b.id === `tab-${k}`)));
  };
  const tabBtns = [['pay', 'Pay'], ['request', 'Request'], ['authorize', 'Hold']].map(([k, label]) => h('button', {
    type: 'button', role: 'tab', class: 'tab', id: `tab-${k}`, 'aria-selected': k === 'pay' ? 'true' : 'false', text: label,
    onclick: () => selectTab(k),
  }));
  const tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Choose an action' }, tabBtns);

  // Fill (never submit) a form from a "Pay again" / "Request again" action, then focus it.
  function applyPrefill() {
    const pf = takePrefill();
    if (!pf) return;
    const form = pf.kind === 'request' ? ask : pay;
    form.controls.handle.value = pf.handle;
    form.controls.amount.value = pf.amount;
    form.controls.note.value = pf.note;
    if (pf.kind !== 'request' && form.controls.visibility) form.controls.visibility.value = pf.visibility;
    for (const c of Object.values(form.controls)) c.dispatchEvent(new Event('input', { bubbles: true }));
    selectTab(pf.kind === 'request' ? 'request' : 'pay');
    form.el.scrollIntoView({ block: 'center' });
    form.controls.amount.focus();
  }
  window.addEventListener(PREFILL_EVENT, applyPrefill);
  setTimeout(applyPrefill, 0); // after the screen is attached (arrived from a payment page)

  refreshActivity(); // the shell loads `me`
  const nodes = [
    h('div', { class: 'page-head' }, h('h1', { text: 'Home' }), h('p', { text: 'Your money, what is on hold, and what has happened lately.' })),
    wallet.el,
    h('div', { class: 'grid-2' },
      h('div', { class: 'stack', 'data-active': 'pay' }, tabs, pay.el, ask.el, hold.el),
      h('section', { 'aria-labelledby': 'feed-title' }, h('div', { class: 'section-title' }, h('h2', { id: 'feed-title', text: 'Activity' })), feed)),
  ];
  return { title: 'Home', nodes };
}
