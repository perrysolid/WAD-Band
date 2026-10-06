// The signed-in user's wallet figures, loaded through the one `me` loader (D31, D38).
import { load, normalizeMe } from './api.js';

export const session = { me: null };
const listeners = new Set();

export const onMe = (fn) => { listeners.add(fn); if (session.me) fn(session.me); };

// Resolves with the wallet once it is loaded, or null if loading failed.
export const whenMe = () => new Promise((resolve) => {
  if (session.me) { resolve(session.me); return; }
  const fn = (me, ok) => { if (me || ok === false) { listeners.delete(fn); resolve(me); } };
  listeners.add(fn);
});

export function refreshMe() {
  return load('me', '/me', (r) => {
    if (r.outcome === 'ok') {
      session.me = normalizeMe(r.body);
      listeners.forEach((fn) => fn(session.me, true));
    } else {
      listeners.forEach((fn) => fn(null, false));
    }
  });
}

const when = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
export function humanTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : when.format(d);
}
