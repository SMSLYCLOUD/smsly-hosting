const API_BASE = "/api/v1"

export const config = {
  api: {
    get baseUrl() {
      if (typeof window !== "undefined") {
        return `${window.location.origin}${API_BASE}`;
      }
      return process.env.NEXT_PUBLIC_API_URL || API_BASE;
    },
  },
}
