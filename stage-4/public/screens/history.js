// History: the balance as of a chosen moment, and a paged statement for a chosen range.
import { h, clear } from '../lib/dom.js';
import { request, messageOf } from '../lib/api.js';
import { formatMoney } from '../lib/money.js';
import { session, onMe } from '../lib/session.js';
import { localToRfc3339, toMillis } from '../lib/instants.js';
import { avatar, timeEl } from '../lib/ui.js';
import { sentence } from './home.js';

const PAGE = 20;

function timezoneNote() {
  const off = -new Date().getTimezoneOffset();
  const sign = off < 0 ? '-' : '+';
  const a = Math.abs(off);
  return `Times are in your time zone (UTC${sign}${String(Math.floor(a / 60)).padStart(2, '0')}:${String(a % 60).padStart(2, '0')}).`;
}

function dateField(prefix, name, label, hint) {
  const id = `${prefix}-${name}-input`;
  const input = h('input', { id, type: 'datetime-local', step: '60', testid: `${prefix}-${name}` });
  return { input, node: h('div', { class: 'field' }, h('label', { for: id, text: label }), input, hint && h('p', { class: 'hint', text: hint })) };
}

const signed = (n, me) => `${n < 0 ? '−' : n > 0 ? '+' : ''}${formatMoney(Math.abs(n), me.minor_units, me.currency)}`;

export function history() {
  // ---- balance as of ----
  const asofField = dateField('history-asof', 'time', 'Balance as of', 'Pick a date and time in the past.');
  const asofOut = h('div', { class: 'asof-out', 'aria-live': 'polite' });
  const asofBtn = h('button', { type: 'submit', class: 'btn', testid: 'history-asof-submit', text: 'Show balance' });
  let asofSeq = 0;

  async function runAsOf(e) {
    e.preventDefault();
    const me = session.me;
    clear(asofOut);
    if (!me) { asofOut.append(h('p', { class: 'msg msg-info', role: 'status', text: 'Still loading your wallet. Try again in a moment.' })); return; }
    const rfc = localToRfc3339(asofField.input.value);
    if (!rfc) { asofOut.append(h('p', { class: 'msg msg-error', role: 'alert', testid: 'history-asof-error', text: 'Choose a date and time first.' })); return; }
    const mine = ++asofSeq;
    asofOut.append(h('p', { class: 'skeleton', testid: 'history-asof-loading', text: 'Looking up that moment…' }));
    const r = await request('GET', `/me?as_of=${encodeURIComponent(rfc)}`);
    if (mine !== asofSeq) return;
    clear(asofOut);
    if (r.outcome !== 'ok') {
      asofOut.append(h('p', { class: 'msg msg-error', role: 'alert', testid: 'history-asof-error', text: r.outcome === 'uncertain' ? 'We could not reach the service. Try again.' : messageOf(r) }));
      return;
    }
    const b = r.body;
    const money = (n) => formatMoney(n, me.minor_units, me.currency);
    const avail = b.available ?? b.balance; const held = b.held ?? 0;
    asofOut.append(h('div', { class: 'card card-flat' },
      h('p', { class: 'label', text: 'Balance at that moment' }),
      h('p', { class: 'wallet-available', testid: 'history-asof-balance', 'data-amount': b.balance, text: money(b.balance) }),
      h('dl', { class: 'wallet-sub' },
        h('div', {}, h('dt', { class: 'label', text: 'Available' }), h('dd', { testid: 'history-asof-available', 'data-amount': avail, text: money(avail) })),
        h('div', {}, h('dt', { class: 'label', text: 'On hold' }), h('dd', { testid: 'history-asof-held', 'data-amount': held, text: money(held) }))),
      h('p', { class: 'fineprint', testid: 'history-asof-echo', text: `As of ${b.as_of || rfc}` })));
  }

  const asofForm = h('form', { class: 'form', novalidate: true, onsubmit: runAsOf }, asofField.node, asofBtn);
  const asofCard = h('section', { class: 'card', 'aria-labelledby': 'history-asof-title' },
    h('h2', { id: 'history-asof-title', text: 'Balance at a moment' }),
    h('p', { class: 'lede', text: `What your wallet held at a chosen time. ${timezoneNote()}` }), asofForm, asofOut);

  // ---- statement ----
  const from = dateField('history', 'from', 'From', 'Leave empty to start at the very beginning.');
  const to = dateField('history', 'to', 'Until (not included)', 'Leave empty for now.');
  const out = h('div', { class: 'statement-out' });
  const go = h('button', { type: 'submit', class: 'btn', testid: 'history-statement-submit', text: 'Show statement' });
  let snapshot = null;
  let offset = 0;
  let seq = 0;

  function fail(text, extra) {
    clear(out);
    out.append(h('p', { class: 'msg msg-error', role: 'alert', testid: 'history-error', text }), extra);
  }

  function entryRow(en, me) {
    const p = en.payment; const id = p.payment_id;
    const sent = p.from_user_id === me.user_id;
    return h('li', { class: `item ${en.delta < 0 ? 'item-sent' : en.delta > 0 ? 'item-received' : ''}`, testid: `history-entry-${id}` },
      h('div', { class: 'item-top' },
        h('div', { class: 'item-id' }, avatar(sent ? p.to_handle : p.from_handle),
          h('p', { class: 'item-who', testid: `history-entry-parties-${id}` }, h('a', { class: 'item-link', href: `/payment/${encodeURIComponent(id)}`, text: sentence(p, me.user_id) }), p.note && h('span', { class: 'pair', text: p.note }))),
        h('p', { class: 'item-amount', testid: `history-delta-${id}`, 'data-amount': en.delta, text: signed(en.delta, me) })),
      h('div', { class: 'item-meta' },
        h('span', {}, 'Balance after ', h('strong', { testid: `history-balance-after-${id}`, 'data-amount': en.balance_after, text: formatMoney(en.balance_after, me.minor_units, me.currency) })),
        timeEl(en.effective_at),
        en.revision > 1 && h('span', { class: 'badge badge-pending', text: `Corrected (revision ${en.revision})` })));
  }

  function paint(body) {
    const me = session.me;
    clear(out);
    const money = (n) => formatMoney(n, me.minor_units, me.currency);
    const total = body.entries.length;
    out.append(h('dl', { class: 'wallet-sub statement-sum' },
      h('div', {}, h('dt', { class: 'label', text: 'Opening balance' }), h('dd', { testid: 'history-opening', 'data-amount': body.opening_balance, text: money(body.opening_balance) })),
      h('div', {}, h('dt', { class: 'label', text: 'Closing balance' }), h('dd', { testid: 'history-closing', 'data-amount': body.closing_balance, text: money(body.closing_balance) }))));
    if (total === 0 && offset === 0) {
      out.append(h('div', { class: 'empty', testid: 'history-empty' }, h('strong', { text: 'No movements in this range' }), 'Nothing was paid or received between those times.'));
      return;
    }
    if (total === 0) {
      out.append(h('div', { class: 'empty', testid: 'history-empty' }, h('strong', { text: 'No more entries' }), 'You are past the end of this statement.'));
    } else {
      out.append(h('ul', { class: 'list', testid: 'history-entries' }, body.entries.map((en) => entryRow(en, me))));
    }
    const first = total ? offset + 1 : 0; const last = offset + total;
    out.append(h('div', { class: 'pager' },
      h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: 'history-prev', disabled: offset === 0, text: '← Previous entries', onclick: () => page(Math.max(0, offset - PAGE)) }),
      h('span', { class: 'count', testid: 'history-page-info', text: total ? `Entries ${first}–${last}` : 'No entries on this page' }),
      h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: 'history-next', disabled: !body.has_more, text: 'Later entries →', onclick: () => page(offset + PAGE) })));
  }

  async function fetchPage(path) {
    const mine = ++seq;
    out.setAttribute('aria-busy', 'true');
    if (!out.querySelector('[data-testid]')) { clear(out); out.append(h('p', { class: 'skeleton', testid: 'history-loading', text: 'Loading your statement…' })); }
    const r = await request('GET', path);
    if (mine !== seq) return null;
    out.removeAttribute('aria-busy');
    return r;
  }

  async function page(nextOffset) {
    if (!snapshot) return;
    const r = await fetchPage(`/statement?snapshot=${encodeURIComponent(snapshot)}&limit=${PAGE}&offset=${nextOffset}`);
    if (!r) return;
    if (r.outcome === 'ok') { offset = nextOffset; paint(r.body); return; }
    if (r.status === 404) {
      snapshot = null;
      fail('This statement is no longer available (the service was reset). Run it again.');
      return;
    }
    fail(r.outcome === 'uncertain' ? 'We could not reach the service. Try again.' : messageOf(r));
  }

  async function runStatement(e) {
    e.preventDefault();
    const me = session.me;
    if (!me) { fail('Still loading your wallet. Try again in a moment.'); return; }
    const f = from.input.value ? localToRfc3339(from.input.value) : null;
    const t = to.input.value ? localToRfc3339(to.input.value) : null;
    if ((from.input.value && !f) || (to.input.value && !t)) { fail('One of the dates is not valid. Pick it again.'); return; }
    if (f && t && toMillis(f) >= toMillis(t)) { fail('The start must be before the end.'); return; }
    const qs = new URLSearchParams({ limit: String(PAGE), offset: '0' });
    if (f) qs.set('from', f);
    if (t) qs.set('to', t);
    snapshot = null; offset = 0;
    const r = await fetchPage(`/statement?${qs.toString()}`);
    if (!r) return;
    if (r.outcome === 'ok') { snapshot = r.body.snapshot || null; paint(r.body); return; }
    fail(r.outcome === 'uncertain' ? 'We could not reach the service. Try again.' : messageOf(r));
  }

  const stForm = h('form', { class: 'form', novalidate: true, onsubmit: runStatement },
    h('div', { class: 'field-row' }, from.node, to.node), go);
  const stCard = h('section', { class: 'card', 'aria-labelledby': 'history-statement-title' },
    h('h2', { id: 'history-statement-title', text: 'Statement' }),
    h('p', { class: 'lede', text: `Everything you paid or received in a range, oldest first, with your balance after each. ${timezoneNote()}` }), stForm, out);

  onMe(() => {});
  return {
    title: 'History',
    nodes: [
      h('div', { class: 'page-head' }, h('h1', { text: 'History' }), h('p', { text: 'Look back at your balance and your payments.' })),
      h('div', { class: 'grid-2' }, asofCard, stCard),
    ],
  };
}
