export function parseApiError(error: any): string {
  const data = error?.response?.data;
  if (!data) return error?.message || 'Request failed';
  if (typeof data.error === 'string') {
    const details = data.details && typeof data.details === 'object'
      ? Object.entries(data.details).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join('; ')
      : '';
    return details ? `${data.error} (${details})` : data.error;
  }
  if (typeof data === 'string') return data;
  const first = Object.entries(data)[0];
  if (first) {
    const [k, v] = first;
    return `${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`;
  }
  return 'Request failed';
}
