'use strict';
// Static UI (S2-U13, D36): everything under public/ is read once at start and served
// from memory; nothing is fetched at run time and no path can leave the directory.

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', 'public');
const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.woff2': 'font/woff2',
  '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8',
};

// Everything is same-origin; styles are external files and no inline script is used.
const CSP = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";

function load(dir = ROOT, prefix = '', out = new Map()) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) load(full, `${prefix}/${e.name}`, out);
    else if (e.name !== 'index.html' || prefix !== '') {
      out.set(`${prefix}/${e.name}`, { body: fs.readFileSync(full), type: TYPES[path.extname(e.name)] || 'application/octet-stream' });
    }
  }
  return out;
}

function createStatic() {
  const files = load();
  const shell = { body: fs.readFileSync(path.join(ROOT, 'index.html')), type: TYPES['.html'] };
  return {
    shell,
    // `urlPath` is the decoded-or-not pathname; only exact /assets/<file> names resolve.
    asset(urlPath) {
      if (!urlPath.startsWith('/assets/') || urlPath.includes('%') || urlPath.includes('..') || urlPath.includes('\\')) return null;
      return files.get(urlPath.slice('/assets'.length)) || null;
    },
  };
}

// RFC 9110 Accept: does it list text/html with a non-zero q?
function acceptsHtml(header) {
  if (typeof header !== 'string') return false;
  for (const part of header.split(',')) {
    const [type, ...params] = part.trim().split(';').map((s) => s.trim());
    if (type.toLowerCase() !== 'text/html') continue;
    let q = 1;
    for (const p of params) {
      const m = /^q\s*=\s*([0-9.]+)$/i.exec(p);
      if (m) q = Number(m[1]);
    }
    if (q > 0) return true;
  }
  return false;
}

module.exports = { createStatic, acceptsHtml, CSP };
