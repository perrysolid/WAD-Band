// Money in minor units, exact (D27, D28). Nothing here uses floating point.

const MAX_MINOR = 1000000000n;

const pow10 = (mu) => 10n ** BigInt(mu);

// 1500, mu 2 -> "15.00"; mu 0 -> "1500". No separators, no sign.
export function formatPlain(minor, mu) {
  const m = BigInt(minor);
  const d = pow10(mu);
  const whole = m / d;
  if (mu === 0) return String(whole);
  return `${whole}.${String(m % d).padStart(mu, '0')}`;
}

export function formatMoney(minor, mu, currency) {
  return `${formatPlain(minor, mu)} ${currency}`;
}

// D28: trim, then ^[0-9]+(\.[0-9]{1,mu})?$ (integers only when mu = 0); >= 1 and <= 1e9 minor units.
export function parseDecimal(input, mu) {
  const text = String(input).trim();
  const re = mu === 0 ? /^[0-9]+$/ : new RegExp(`^[0-9]+(\\.[0-9]{1,${mu}})?$`);
  if (!re.test(text)) {
    return { ok: false, error: mu === 0 ? 'Enter a whole number, like 15.' : `Enter an amount like 15 or 15.${'0'.repeat(Math.max(mu, 1))}, with at most ${mu} decimal place${mu === 1 ? '' : 's'}.` };
  }
  const [whole, frac = ''] = text.split('.');
  const minor = BigInt(whole) * pow10(mu) + BigInt(frac.padEnd(mu, '0') || '0');
  if (minor < 1n) return { ok: false, error: 'The amount must be greater than zero.' };
  if (minor > MAX_MINOR) return { ok: false, error: `The amount can be at most ${formatPlain(MAX_MINOR, mu)}.` };
  return { ok: true, minor: Number(minor) };
}

// §9: the first (amount mod n) participants get one extra minor unit.
export function splitShares(minor, n) {
  const base = Math.floor(minor / n);
  const rem = minor - base * n;
  return Array.from({ length: n }, (_, i) => base + (i < rem ? 1 : 0));
}

// "@ada, bob,,@cy" -> ["ada","bob","cy"]: trimmed, empties dropped, one leading @ stripped.
export function parseHandles(text) {
  return String(text).split(',').map((s) => s.trim()).filter((s) => s !== '').map((s) => (s.startsWith('@') ? s.slice(1) : s));
}

export function cleanHandle(text) {
  const s = String(text).trim();
  return s.startsWith('@') ? s.slice(1) : s;
}
