'use strict';
// S2-U1, S2-U13, D26: HTML shells vs JSON, static assets, CSP, 404 JSON.
const test = require('node:test');
const assert = require('node:assert/strict');
const { start, expectError } = require('./helpers');

let t;
test.before(async () => { t = await start(); });
test.after(() => t.close());

const HTML = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8';
const get = (path, accept) => fetch(t.base + path, { headers: accept ? { Accept: accept } : {} });

test('D26 UI-only routes are always the HTML shell, with a CSP', async () => {
  for (const p of ['/', '/split', '/signup', '/login']) {
    for (const accept of [undefined, 'application/json', '*/*', HTML]) {
      const r = await get(p, accept);
      assert.equal(r.status, 200, p);
      assert.match(r.headers.get('content-type'), /^text\/html/);
      assert.match(r.headers.get('content-security-policy'), /default-src 'self'/);
      assert.match(await r.text(), /<title>[^<]+<\/title>/);
    }
  }
});

test('D26 /requests and /authorizations are HTML only for text/html with q > 0', async () => {
  const w = await t.world();
  for (const p of ['/requests', '/authorizations']) {
    for (const accept of [HTML, 'text/html', 'text/html;q=0.1, application/json']) {
      const r = await get(p, accept);
      assert.match(r.headers.get('content-type'), /^text\/html/, accept);
    }
    for (const accept of [undefined, 'application/json', '*/*', 'text/html;q=0', 'text/html; q=0.0, */*', 'text/plain']) {
      const r = await t.call('GET', p, { token: w.ada, headers: accept ? { Accept: accept } : {} });
      assert.equal(r.status, 200, `${p} ${accept}`);
      assert.ok(Array.isArray(r.body.requests || r.body.authorizations));
      expectError(assert, await t.call('GET', p, { headers: accept ? { Accept: accept } : {} }), 401, 'unauthenticated');
    }
  }
});

test('assets are served with real content types; unknown routes and traversal are 404 JSON', async () => {
  const html = await (await get('/')).text();
  const refs = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);
  assert.ok(refs.some((r) => r.endsWith('.js')) && refs.some((r) => r.endsWith('.css')));
  for (const ref of refs) {
    const r = await get(ref);
    assert.equal(r.status, 200, ref);
    const ct = r.headers.get('content-type');
    if (ref.endsWith('.js')) assert.match(ct, /javascript/);
    if (ref.endsWith('.css')) assert.match(ct, /^text\/css/);
  }
  const font = await get('/assets/fonts/inter-latin-wght.woff2');
  assert.equal(font.headers.get('content-type'), 'font/woff2');
  for (const p of ['/nope', '/assets/missing.js', '/assets/../src/app.js', '/assets/%2e%2e/src/app.js', '/assets/', '/me/html', '/history']) {
    const r = await get(p, HTML);
    assert.equal(r.status, 404, p);
    assert.match(r.headers.get('content-type'), /^application\/json/);
    assert.equal((await r.json()).error.code, 'not_found');
  }
});

test('served files reference no external URL', async () => {
  const seen = new Set();
  const todo = ['/', '/assets/app.css'];
  while (todo.length) {
    const p = todo.pop();
    if (seen.has(p)) continue;
    seen.add(p);
    const r = await get(p);
    if (r.status !== 200 || !/html|css|javascript/.test(r.headers.get('content-type'))) continue;
    const body = await r.text();
    assert.doesNotMatch(body.replace(/https?:\/\/www\.w3\.org\/\d+\/(svg|xlink)/g, ''), /(https?:)?\/\/[a-z0-9-]+\.[a-z]{2,}/i, p);
    for (const m of body.matchAll(/(?:src|href)="(\/assets\/[^"]+)"|from\s+'(\.{1,2}\/[^']+)'|url\(([^)]+)\)/g)) {
      const ref = m[1] || m[2] || m[3].replace(/['"]/g, '');
      todo.push(ref.startsWith('/') ? ref : new URL(ref, 'http://x' + p).pathname);
    }
  }
  assert.ok(seen.size >= 6, [...seen].join());
});
