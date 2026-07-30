import { api } from "@/lib/api"

export const tokenManager = {
  getAccessToken: (): string | null => {
    if (typeof document === "undefined") return null
    const match = document.cookie.match(/(?:^|;\s*)sessionid=([^;]*)/)
    return match ? match[1] : null
  },
}
