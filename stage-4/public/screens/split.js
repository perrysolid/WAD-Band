import { h, clear } from '../lib/dom.js';
import { parseDecimal, parseHandles, splitShares, formatMoney } from '../lib/money.js';
import { moneyForm } from '../lib/form.js';
import { session, onMe } from '../lib/session.js';

export function split() {
  const preview = h('div', { class: 'split-preview-wrap', 'aria-live': 'polite' });

  function paint() {
    const me = session.me;
    clear(preview);
    if (!me) return;
    const amt = parseDecimal(form.controls.amount.value, me.minor_units);
    const handles = parseHandles(form.controls.handles.value);
    if (!amt.ok || handles.length === 0) return;
    const shares = splitShares(amt.minor, handles.length);
    const seen = new Set();
    const rows = [];
    handles.forEach((handle, i) => {
      if (seen.has(handle)) return;
      seen.add(handle);
      rows.push(h('li', {}, h('span', { class: 'who-share', text: `@${handle}` }), h('span', { testid: `split-share-${handle}`, text: formatMoney(shares[i], me.minor_units, me.currency) })));
    });
    const n = handles.length; const rem = amt.minor % n;
    const unit = formatMoney(1, me.minor_units, me.currency);
    const carriers = [...new Set(handles.slice(0, rem))].map((x) => `@${x}`);
    const why = rem === 0
      ? `${formatMoney(amt.minor, me.minor_units, me.currency)} divides evenly by ${n}, so everyone owes the same.`
      : `${formatMoney(amt.minor, me.minor_units, me.currency)} does not divide evenly by ${n}. ${carriers.join(', ')} ${rem === 1 ? 'carries' : 'carry'} the extra ${unit} (${rem} in all) because the leftover goes one unit each to the first ${rem === 1 ? 'person' : `${rem} people`} in your list. Change the order to change who carries it.`;
    preview.append(h('div', { class: 'card split-preview', testid: 'split-preview' },
      h('h3', { text: 'Each person owes' }), h('ul', {}, rows),
      h('p', { class: 'fineprint', testid: 'split-extra-note', text: why }),
      handles.length !== seen.size && h('p', { class: 'fineprint', text: 'A handle is listed twice, so this split will be refused.' })));
  }

  const form = moneyForm({
    prefix: 'split',
    title: 'Split a bill',
    lede: 'Everyone listed, including you if you add yourself, gets an equal share. The extra cents go to the first people in the list. Everyone else receives a request.',
    submitLabel: 'Send requests',
    path: '/splits',
    fields: [
      { name: 'amount', label: 'Total amount', inputmode: 'decimal', placeholder: '0.00' },
      { name: 'handles', label: 'Who is splitting it', placeholder: 'ada, bob, cy', hint: 'Handles separated by commas.' },
      { name: 'note', label: 'Note (optional)', placeholder: 'Dinner' },
    ],
    onInput: paint,
    build(v) {
      const me = session.me;
      if (!me) return { error: 'Still loading your wallet. Try again in a moment.' };
      const amt = parseDecimal(v.amount, me.minor_units);
      if (!amt.ok) return { error: amt.error };
      const handles = parseHandles(v.handles);
      if (handles.length === 0) return { error: 'Add at least one handle.' };
      if ([...v.note].length > 200) return { error: 'The note can be at most 200 characters.' };
      return { body: { amount: amt.minor, participant_handles: handles, ...(v.note ? { note: v.note } : {}) } };
    },
    success: (b) => `Split sent: ${(b.requests || []).length} request${(b.requests || []).length === 1 ? '' : 's'} created.`,
  });
  form.el.append(preview);
  onMe(() => paint());
  return {
    title: 'Split',
    nodes: [h('div', { class: 'page-head' }, h('h1', { text: 'Split' }), h('p', { text: 'Share a cost and ask everyone for their part.' })), form.el],
  };
}
