// "Pay again" / "Request again": remember what to put in the form, then let the home screen
// fill it in. Nothing is ever submitted from here.
import { formatPlain } from './money.js';

const KEY = 'pf.prefill';

// kind: 'pay' (a sent payment) or 'request' (a received one). `me` carries minor_units.
export function stashPrefill(kind, p, me) {
  const handle = kind === 'pay' ? p.to_handle : p.from_handle;
  const data = { kind, handle, amount: formatPlain(p.amount, me.minor_units), note: p.note || '', visibility: p.visibility };
  try { sessionStorage.setItem(KEY, JSON.stringify(data)); } catch { /* storage unavailable: nothing to prefill */ }
}

export function takePrefill() {
  try {
    const raw = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

export const PREFILL_EVENT = 'pf-prefill';
