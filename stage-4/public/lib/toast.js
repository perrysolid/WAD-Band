// Success toasts: a polite live region outside the app root. Never focusable, never given a
// data-testid, so it cannot shadow any *-success / *-error / *-uncertain element.
import { h } from './dom.js';

let region = null;
export function mountToasts() {
  if (region) return;
  region = h('div', { class: 'toasts', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'false' });
  document.body.append(region);
}

export function toast(text) {
  mountToasts();
  const el = h('p', { class: 'toast', text });
  region.append(el);
  setTimeout(() => el.remove(), 4000);
}
