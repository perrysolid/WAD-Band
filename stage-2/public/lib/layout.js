// The frame every signed-in screen shares: brand, the same four links, who you are, sign out.
import { h } from './dom.js';
import { token } from './api.js';

const NAV = [['/', 'Home'], ['/requests', 'Requests'], ['/split', 'Split'], ['/authorizations', 'Holds']];

export function frame(route, main) {
  const who = h('div', { class: 'who' });
  const top = h('header', { class: 'top' }, h('div', { class: 'top-inner' },
    h('a', { class: 'brand', href: '/', text: 'Pocketful' }),
    h('nav', { class: 'nav', 'aria-label': 'Main' }, NAV.map(([href, label]) => h('a', { href, text: label, 'aria-current': href === route ? 'page' : undefined }))),
    who));
  function setUser(me) {
    who.replaceChildren(
      h('span', { class: 'who-name', testid: 'current-user', text: me.display_name }),
      h('span', { class: 'who-handle', testid: 'current-handle', text: me.handle }),
      h('button', { type: 'button', class: 'btn btn-quiet btn-small', testid: 'logout-button', text: 'Sign out', onclick: () => { token.clear(); location.assign('/login'); } }));
  }
  return { nodes: [top, h('main', {}, ...main)], setUser };
}
