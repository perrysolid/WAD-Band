// A money-moving form (pay, request, hold, split). One identity per form (D29): the same
// body reuses the same Idempotency-Key, so re-pressing the button can never act twice.
import { h, clear } from './dom.js';
import { request, messageOf, Identity } from './api.js';
import { toast } from './toast.js';

const UNCERTAIN_TEXT = 'We did not get an answer, so we cannot tell whether it went through. Press the button again: the very same request is sent, and it can only take effect once.';

function field(prefix, f) {
  const id = `${prefix}-${f.name}-input`;
  let control;
  if (f.kind === 'select') {
    control = h('select', { id, testid: `${prefix}-${f.name}`, name: f.name }, f.options.map(([v, label]) => h('option', { value: v, text: label })));
  } else {
    control = h('input', {
      id, testid: `${prefix}-${f.name}`, name: f.name, type: 'text', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false',
      inputmode: f.inputmode, placeholder: f.placeholder, 'aria-describedby': f.hint ? `${id}-hint` : undefined,
    });
  }
  return { control, node: h('div', { class: 'field' }, h('label', { for: id, text: f.label }), control, f.hint && h('p', { class: 'hint', id: `${id}-hint`, text: f.hint })) };
}

// cfg: { prefix, title, lede, submitLabel, path, fields, build(values) -> {body}|{error},
//        success(responseBody) -> string, onDone(kind, response), onInput() }
export function moneyForm(cfg) {
  const { prefix } = cfg;
  const identity = new Identity();
  const controls = {};
  const nodes = cfg.fields.map((f) => {
    const x = field(prefix, f);
    controls[f.name] = x.control;
    x.control.addEventListener('input', () => cfg.onInput && cfg.onInput());
    return x;
  });
  const status = h('div', { class: 'status' });
  let inflight = 0;
  const submit = h('button', { type: 'submit', class: 'btn', testid: `${prefix}-submit`, text: cfg.submitLabel });

  const values = () => Object.fromEntries(Object.entries(controls).map(([k, c]) => [k, c.value]));

  function show(kind, text) {
    clear(status);
    if (kind === 'error') status.append(h('p', { class: 'msg msg-error', role: 'alert', testid: `${prefix}-error`, text }));
    else if (kind === 'uncertain') status.append(h('p', { class: 'msg msg-uncertain', role: 'status', testid: `${prefix}-uncertain` }, h('strong', { text: 'Outcome unknown' }), text));
    else if (kind === 'ok') status.append(h('p', { class: 'msg msg-ok', testid: `${prefix}-success`, text }));
  }

  async function onSubmit(e) {
    e.preventDefault();
    show(null);
    const built = cfg.build(values());
    if (built.error) { show('error', built.error); return; }
    const key = identity.keyFor(cfg.path, built.body);
    inflight += 1;
    submit.setAttribute('aria-busy', 'true');
    const r = await request('POST', cfg.path, { body: built.body, key });
    inflight -= 1;
    if (inflight === 0) submit.removeAttribute('aria-busy');
    if (r.outcome === 'ok') {
      show('ok', cfg.success(r.body));
      toast(cfg.success(r.body));
      if (cfg.onDone) cfg.onDone('ok', r);
    } else if (r.outcome === 'refused') {
      show('error', messageOf(r));
      if (cfg.onDone) cfg.onDone('refused', r);
    } else {
      show('uncertain', UNCERTAIN_TEXT);
    }
  }

  const el = h('section', { class: 'card', 'aria-labelledby': `${prefix}-title` },
    h('h2', { id: `${prefix}-title`, text: cfg.title }),
    cfg.lede && h('p', { class: 'lede', text: cfg.lede }),
    h('form', { class: 'form', novalidate: true, onsubmit: onSubmit },
      ...nodes.map((n) => n.node), cfg.extra, submit, status));
  return { el, controls, values, show };
}
