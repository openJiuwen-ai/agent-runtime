/** ISO / Date → local wall-clock `YYYY-MM-DDTHH:mm` for datetime editors; empty if missing/invalid. */
export function toDatetimeLocalValue(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

/** Split `YYYY-MM-DDTHH:mm` into date and `HH:mm`; empty parts when value is blank/invalid. */
export function splitDatetimeLocal(value: string): { date: string; time: string } {
  const raw = value.trim();
  if (!raw) return { date: '', time: '' };
  const match = raw.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  if (!match) return { date: '', time: '' };
  return { date: match[1]!, time: match[2]! };
}

/** Join date `YYYY-MM-DD` and time `HH:mm` into `YYYY-MM-DDTHH:mm`. */
export function joinDatetimeLocal(date: string, time: string): string {
  if (!date) return '';
  const hhmm = time || '00:00';
  return `${date}T${hhmm}`;
}
