// Small presentation helpers: initials avatars, privacy icons, relative and exact time.
import { h } from './dom.js';

const SVG = 'http://www.w3.org/2000/svg';
function svg(children) {
  const el = document.createElementNS(SVG, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', width: '14', height: '14', fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false', class: 'icon' })) el.setAttribute(k, v);
  for (const [tag, attrs] of children) {
    const c = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) c.setAttribute(k, v);
    el.append(c);
  }
  return el;
}

// Decorative: the text next to it carries the meaning.
export const lockIcon = () => svg([['rect', { x: '5', y: '11', width: '14', height: '9', rx: '2' }], ['path', { d: 'M8 11V8a4 4 0 0 1 8 0v3' }]]);
export const globeIcon = () => svg([['circle', { cx: '12', cy: '12', r: '9' }], ['ellipse', { cx: '12', cy: '12', rx: '4', ry: '9' }], ['path', { d: 'M3 12h18' }]]);

// Initials from a handle; the colour is a stable function of the handle.
export function avatar(handle) {
  const letters = String(handle || '?').replace(/[^a-z0-9]/gi, '').slice(0, 2).toUpperCase() || '?';
  let n = 0;
  for (const ch of String(handle)) n = (n * 31 + ch.codePointAt(0)) % 360;
  const el = h('span', { class: 'avatar', 'aria-hidden': 'true', text: letters });
  el.dataset.tone = String(n % 6);
  return el;
}

export function privacyBadge(visibility) {
  const priv = visibility === 'private';
  return h('span', { class: `badge badge-${visibility}`, 'data-icon': priv ? 'lock' : 'globe' }, priv ? lockIcon() : globeIcon(), priv ? 'Private' : 'Public');
}

const exact = new Intl.DateTimeFormat(undefined, { dateStyle: 'full', timeStyle: 'medium' });
const shortDate = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' });

export function relativeText(iso, now = Date.now()) {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const s = Math.round((now - t) / 1000);
  if (s < -60) return shortDate.format(t);
  if (s < 45) return 'just now';
  if (s < 3600) { const m = Math.max(1, Math.round(s / 60)); return `${m} min ago`; }
  if (s < 86400) { const x = Math.round(s / 3600); return `${x} hour${x === 1 ? '' : 's'} ago`; }
  if (s < 7 * 86400) { const x = Math.round(s / 86400); return `${x} day${x === 1 ? '' : 's'} ago`; }
  return shortDate.format(t);
}

// <time> showing "5 min ago"; hovering shows the exact moment.
export function timeEl(iso) {
  const d = new Date(iso);
  const ok = !Number.isNaN(d.getTime());
  return h('time', { datetime: iso, title: ok ? exact.format(d) : undefined, text: relativeText(iso) });
}
