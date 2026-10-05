// datetime-local <-> RFC 3339 with an explicit offset. A local time that does not exist
// (spring-forward gap) is moved forward by the browser's Date; one that occurs twice takes the
// first occurrence. Either way the offset sent is the one in force at the resulting instant.

const pad = (n) => String(n).padStart(2, '0');

// "2026-09-24T13:20" or "...:45" -> "2026-09-24T13:20:00+02:00", or null when empty/invalid.
export function localToRfc3339(value) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value || '')) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  const off = -d.getTimezoneOffset();
  const sign = off < 0 ? '-' : '+';
  const a = Math.abs(off);
  const wall = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return `${wall}${sign}${pad(Math.floor(a / 60))}:${pad(a % 60)}`;
}

export const toMillis = (rfc) => new Date(rfc).getTime();
