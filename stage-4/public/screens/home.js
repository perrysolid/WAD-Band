import { h, clear } from '../lib/dom.js';
import { load, request } from '../lib/api.js';
import { formatMoney, parseDecimal, cleanHandle } from '../lib/money.js';
import { moneyForm } from '../lib/form.js';
import { walletPanel } from '../lib/wallet.js';
import { session, refreshMe, onMe, humanTime } from '../lib/session.js';

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

function feedItem(p, me) {
  const id = p.payment_id;
  return h('li', { class: 'item', testid: `activity-item-${id}`, 'data-visibility': p.visibility },
    h('div', { class: 'item-top' },
      h('p', { class: 'item-who', testid: `activity-parties-${id}` }, `@${p.from_handle} paid @${p.to_handle}`),
      h('p', { class: 'item-amount', testid: `activity-amount-${id}`, text: formatMoney(p.amount, me.minor_units, me.currency) })),
    h('p', { class: 'item-note', testid: `activity-note-${id}`, text: p.note }),
    h('div', { class: 'item-meta' },
      h('span', { class: `badge badge-${p.visibility}`, text: p.visibility === 'private' ? 'Private' : 'Public' }),
      p.authorization_id && h('span', { text: 'Collected from a hold' }),
      h('time', { datetime: p.created_at, text: humanTime(p.created_at) })));
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

  refreshActivity(); // the shell loads `me`
  const nodes = [
    h('div', { class: 'page-head' }, h('h1', { text: 'Home' }), h('p', { text: 'Your money, what is on hold, and what has happened lately.' })),
    wallet.el,
    h('div', { class: 'grid-2' },
      h('div', { class: 'stack' }, pay.el, ask.el, hold.el),
      h('section', { 'aria-labelledby': 'feed-title' }, h('div', { class: 'section-title' }, h('h2', { id: 'feed-title', text: 'Activity' })), feed)),
  ];
  return { title: 'Home', nodes };
}
