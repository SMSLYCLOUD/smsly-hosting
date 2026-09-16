/**
 * Shared backend-error formatter.
 *
 * The backend speaks several error shapes depending on the path:
 * DRF validation `{field: [msg]}`, custom views `{error:}`, log/task
 * views `{message:}`, and stock 403/404 `{detail:}`. Reading only one
 * key (usually `.detail`) swallows the real message, and some catches
 * read nothing at all. Pass `err?.response?.data`.
 */
export function firstApiError(data: any, fallback: string): string {
  if (!data) return fallback;
  if (typeof data === 'string') return data;
  for (const key of ['detail', 'error', 'message'] as const) {
    const v = (data as any)[key];
    if (typeof v === 'string' && v) return v;
  }
  if (typeof data === 'object') {
    for (const key of Object.keys(data)) {
      const v = (data as any)[key];
      if (Array.isArray(v) && v.length) return `${key}: ${String(v[0])}`;
      if (typeof v === 'string' && v) return `${key}: ${v}`;
    }
  }
  return fallback;
}
