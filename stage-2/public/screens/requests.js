import { h, clear } from '../lib/dom.js';
import { load, request, messageOf, identityBook } from '../lib/api.js';
import { formatMoney } from '../lib/money.js';
import { session, onMe, humanTime } from '../lib/session.js';

export function requests() {
  let rows = null;
  let failed = false;
  const identityFor = identityBook();
  const notice = h('div', { class: 'notice' });
  const incoming = h('ul', { class: 'list', testid: 'incoming-list' });
  const outgoing = h('ul', { class: 'list', testid: 'outgoing-list' });
  const inSection = h('section', { 'aria-labelledby': 'incoming-title' }, h('div', { class: 'section-title' }, h('h2', { id: 'incoming-title', text: 'Asked of you' })), incoming);
  const outSection = h('section', { 'aria-labelledby': 'outgoing-title' }, h('div', { class: 'section-title' }, h('h2', { id: 'outgoing-title', text: 'You asked' })), outgoing);
  const empty = h('div', { class: 'empty', testid: 'empty-requests' }, h('strong', { text: 'No requests yet' }), 'Requests you send or receive will appear here.');
  const emptySlot = h('div', { class: 'empty-slot' });
  const loading = h('p', { class: 'skeleton', text: 'Loading requests…' });

  function say(kind, text) {
    clear(notice);
    if (kind === 'error') notice.append(h('p', { class: 'msg msg-error', role: 'alert', testid: 'request-error', text }));
    else if (kind === 'uncertain') notice.append(h('p', { class: 'msg msg-uncertain', role: 'status', testid: 'request-uncertain' }, h('strong', { text: 'Outcome unknown' }), text));
  }

  async function act(kind, r) {
    const path = `/requests/${encodeURIComponent(r.request_id)}/${kind}`;
    const res = kind === 'pay'
      ? await request('POST', path, { body: {}, key: identityFor(r.request_id).keyFor(path, {}) })
      : await request('POST', path, {});
    if (res.outcome === 'ok') say(null);
    else if (res.outcome === 'refused') say('error', messageOf(res));
    else say('uncertain', 'We did not get an answer, so we cannot tell whether it went through. Try again: paying is safe to repeat.');
    if (res.outcome !== 'uncertain') refresh();
  }

  function item(r, mine, me) {
    const id = r.request_id;
    const isIncoming = r.payer_id === mine;
    const pending = r.status === 'pending';
    return h('li', { class: 'item', testid: `request-item-${id}`, 'data-status': r.status },
      h('div', { class: 'item-top' },
        h('p', { class: 'item-who', text: isIncoming ? `@${r.requester_handle} asks you for` : `You asked @${r.payer_handle} for` }),
        h('p', { class: 'item-amount', testid: `request-amount-${id}`, text: formatMoney(r.amount, me.minor_units, me.currency) })),
      h('p', { class: 'item-note', testid: `request-note-${id}`, text: r.note }),
      h('div', { class: 'item-meta' }, h('span', { class: `badge badge-${r.status}`, text: r.status.charAt(0).toUpperCase() + r.status.slice(1) }), h('time', { datetime: r.created_at, text: humanTime(r.created_at) })),
      pending && isIncoming && h('div', { class: 'item-actions' },
        h('button', { type: 'button', class: 'btn btn-small', testid: `request-pay-${id}`, text: 'Pay', onclick: () => act('pay', r) }),
        h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: `request-decline-${id}`, text: 'Decline', onclick: () => act('decline', r) })),
      pending && !isIncoming && h('div', { class: 'item-actions' },
        h('button', { type: 'button', class: 'btn btn-danger btn-small', testid: `request-cancel-${id}`, text: 'Cancel request', onclick: () => act('cancel', r) })));
  }

  function paint() {
    const me = session.me;
    if (!me) return;
    if (rows === null) {
      if (failed) { clear(incoming); clear(outgoing); say('error', 'We could not load your requests. Try again in a moment.'); }
      return;
    }
    loading.remove();
    clear(incoming); clear(outgoing);
    for (const r of rows) (r.payer_id === me.user_id ? incoming : outgoing).append(item(r, me.user_id, me));
    const none = rows.length === 0;
    if (none) emptySlot.append(empty); else empty.remove();
    inSection.hidden = none;
    outSection.hidden = none;
    incoming.hidden = none;
    outgoing.hidden = none;
  }

  function refresh() {
    return load('requests', '/requests?limit=200', (r) => {
      if (r.outcome === 'ok') { rows = r.body.requests || []; failed = false; } else if (rows === null) failed = true;
      paint();
    });
  }
  onMe((me) => { if (me) paint(); });
  refresh();
  return {
    title: 'Requests',
    nodes: [
      h('div', { class: 'page-head' }, h('h1', { text: 'Requests' }), h('p', { text: 'Pay or decline what people ask of you, and keep track of what you asked.' })),
      notice, loading, emptySlot, inSection, outSection,
    ],
  };
}
