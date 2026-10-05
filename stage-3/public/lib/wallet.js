// The wallet panel (S2-U3, D30): available is the headline; total and held sit beneath.
import { h, clear } from './dom.js';
import { formatMoney } from './money.js';

export function walletPanel({ onRefresh }) {
  const body = h('div', { class: 'wallet-body' }, h('p', { class: 'skeleton', text: 'Loading your balance…' }));
  const refresh = h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: 'wallet-refresh', text: 'Refresh', onclick: () => onRefresh() });
  const el = h('section', { class: 'card wallet', 'aria-labelledby': 'wallet-title' },
    h('div', { class: 'wallet-head' }, h('h2', { id: 'wallet-title', text: 'Your wallet' }), refresh), body);

  function update(me) {
    const money = (n) => formatMoney(n, me.minor_units, me.currency);
    clear(body);
    body.append(
      h('div', {}, h('p', { class: 'label', text: 'Available to spend' }),
        h('p', { class: 'wallet-available', testid: 'wallet-available', 'data-amount': me.available, text: money(me.available) })),
      h('dl', { class: 'wallet-sub' },
        h('div', {}, h('dt', { class: 'label', text: 'Total balance' }), h('dd', { testid: 'wallet-balance', 'data-amount': me.total, text: money(me.total) })),
        me.held > 0 && h('div', {}, h('dt', { class: 'label', text: 'On hold' }), h('dd', { class: 'wallet-held', testid: 'wallet-held', 'data-amount': me.held, text: money(me.held) }))));
  }
  function failed() {
    if (!body.querySelector('[data-testid]')) { clear(body); body.append(h('p', { class: 'msg msg-info', role: 'status', text: 'We could not load your balance. Try Refresh.' })); }
  }
  return { el, update, failed };
}
