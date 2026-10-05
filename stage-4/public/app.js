// Pocketful web app: one shell served for every screen; the path picks the screen.
import { token } from './lib/api.js';
import { frame } from './lib/layout.js';
import { session, onMe, refreshMe } from './lib/session.js';
import { mountToasts } from './lib/toast.js';
import { login, signup } from './screens/auth.js';
import { home } from './screens/home.js';
import { requests } from './screens/requests.js';
import { split } from './screens/split.js';
import { authorizations } from './screens/authorizations.js';
import { history } from './screens/history.js';

const PUBLIC = { '/login': login, '/signup': signup };
const PRIVATE = { '/': home, '/requests': requests, '/split': split, '/authorizations': authorizations, '/history': history };

function boot() {
  mountToasts();
  const root = document.getElementById('app');
  const route = location.pathname.replace(/\/+$/, '') || '/';
  if (PUBLIC[route]) {
    const view = PUBLIC[route]();
    document.title = `${view.title} · Pocketful`;
    root.replaceChildren(...view.nodes);
    return;
  }
  if (!PRIVATE[route]) { location.replace('/'); return; }
  if (!token.get()) { location.replace('/login'); return; }
  const view = PRIVATE[route]();
  const shell = frame(route, view.nodes);
  document.title = `${view.title} · Pocketful`;
  root.replaceChildren(...shell.nodes);
  onMe((me) => { if (me) shell.setUser(me); });
  refreshMe();
}

boot();
