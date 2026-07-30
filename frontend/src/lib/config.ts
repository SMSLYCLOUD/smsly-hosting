const API_BASE = typeof window !== "undefined"
  ? `${window.location.origin}/api/v1`
  : process.env.NEXT_PUBLIC_API_URL || "/api/v1"

export const config = {
  api: {
    baseUrl: API_BASE,
  },
}
