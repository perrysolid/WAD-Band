import { h, clear } from '../lib/dom.js';
import { request, token, messageOf } from '../lib/api.js';

function authForm({ prefix, title, lede, fields, path, submitLabel, build, other }) {
  const status = h('div', { class: 'status' });
  const controls = {};
  const nodes = fields.map((f) => {
    const id = `${prefix}-${f.name}-input`;
    const control = h('input', { id, testid: `${prefix}-${f.name}`, name: f.name, type: f.type || 'text', autocomplete: f.autocomplete, autocapitalize: 'off', spellcheck: 'false' });
    controls[f.name] = control;
    return h('div', { class: 'field' }, h('label', { for: id, text: f.label }), control, f.hint && h('p', { class: 'hint', text: f.hint }));
  });
  const submit = h('button', { type: 'submit', class: 'btn', testid: `${prefix}-submit`, text: submitLabel });
  async function onSubmit(e) {
    e.preventDefault();
    clear(status);
    const values = Object.fromEntries(Object.entries(controls).map(([k, c]) => [k, c.value]));
    const built = build(values);
    if (built.error) { status.append(h('p', { class: 'msg msg-error', role: 'alert', testid: 'auth-error', text: built.error })); return; }
    submit.setAttribute('aria-busy', 'true');
    const r = await request('POST', path, { body: built.body });
    submit.removeAttribute('aria-busy');
    if (r.outcome === 'ok' && r.body && r.body.token) {
      token.set(r.body.token);
      location.assign('/');
      return;
    }
    const text = r.outcome === 'uncertain' ? 'We could not reach the service. Check your connection and try again.' : messageOf(r);
    status.append(h('p', { class: 'msg msg-error', role: 'alert', testid: 'auth-error', text }));
  }
  return h('div', { class: 'auth-wrap' },
    h('a', { class: 'brand', href: '/', text: 'Pocketful' }),
    h('section', { class: 'card', 'aria-labelledby': `${prefix}-title` },
      h('h1', { id: `${prefix}-title`, text: title }), h('p', { class: 'lede', text: lede }),
      h('form', { class: 'form', novalidate: true, onsubmit: onSubmit }, ...nodes, submit, status)),
    h('p', { class: 'fineprint' }, other));
}

export function login() {
  return {
    title: 'Sign in',
    nodes: [authForm({
      prefix: 'login', title: 'Sign in', lede: 'Welcome back.', path: '/auth/login', submitLabel: 'Sign in',
      fields: [{ name: 'email', label: 'Email', autocomplete: 'username' }, { name: 'password', label: 'Password', type: 'password', autocomplete: 'current-password' }],
      build: (v) => (v.email.trim() === '' || v.password === '' ? { error: 'Enter your email and password.' } : { body: { email: v.email.trim(), password: v.password } }),
      other: ['New here? ', h('a', { href: '/signup', text: 'Create an account' }), '.'],
    })],
  };
}

export function signup() {
  return {
    title: 'Create account',
    nodes: [authForm({
      prefix: 'signup', title: 'Create your account', lede: 'Your handle comes from the first part of your email.', path: '/auth/signup', submitLabel: 'Create account',
      fields: [
        { name: 'email', label: 'Email', autocomplete: 'email' },
        { name: 'password', label: 'Password', type: 'password', autocomplete: 'new-password', hint: 'At least 8 characters.' },
        { name: 'display-name', label: 'Your name', autocomplete: 'name' },
      ],
      build: (v) => (v.email.trim() === '' ? { error: 'Enter your email.' } : v['display-name'].trim() === '' ? { error: 'Enter your name.' }
        : { body: { email: v.email.trim(), password: v.password, display_name: v['display-name'].trim() } }),
      other: ['Already have an account? ', h('a', { href: '/login', text: 'Sign in' }), '.'],
    })],
  };
}
