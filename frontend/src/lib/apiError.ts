export function parseApiError(error: any, fallback = 'Request failed'): string {
  const data = error?.response?.data;
  if (!data) return error?.message || fallback;
  if (typeof data === 'string') return data;
  if (typeof data.error === 'string') return data.error;
  if (data.error?.message) return data.error.message;
  if (data.detail) return data.detail;
  if (typeof data === 'object') {
    return Object.values(data).flat().join(', ') || fallback;
  }
  return fallback;
}
