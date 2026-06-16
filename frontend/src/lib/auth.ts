/**
 * Auth lifecycle helpers.
 *
 * The frontend no longer reads or writes the auth token directly — the
 * backend sets it as an HttpOnly cookie on login and clears it on
 * logout. The only thing the frontend needs to do for logout is hit
 * the backend's logout endpoint (which sends the ``Set-Cookie`` header
 * that drops the cookie) and then reload to reset any in-memory state.
 */
import { api } from "@/lib/api";
import { clearAuthCookies } from "@/lib/auth-cookies";

/**
 * Best-effort logout. Hits the backend's ``POST /api/v1/auth/logout/``
 * endpoint, which both invalidates the server-side token and returns
 * a ``Set-Cookie`` header that clears the HttpOnly auth cookie in the
 * browser. We then clear any legacy client-side state (non-HttpOnly
 * cookies / ``localStorage`` entries from older builds) and reload
 * the page so every component remounts without a stale user.
 */
export async function logout(): Promise<void> {
  try {
    await api.post("/accounts/logout/", {});
  } catch {
    // Swallow network errors: the goal of logout is to land the user
    // on the login page, and a half-failed logout should still get
    // them there. The backend cookie is the source of truth — if the
    // request fails, the next /api/v1/auth/user/ call will return 401
    // and the AuthProvider will redirect anyway.
  }
  clearAuthCookies();
  if (typeof window !== "undefined") {
    window.location.assign("/login");
  }
}
