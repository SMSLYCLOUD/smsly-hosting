/**
 * Centralized auth cookie helpers.
 *
 * The auth token is delivered by the backend as an HttpOnly cookie
 * (``__Host-auth_token`` in production, ``auth_token`` in development).
 * HttpOnly cookies CANNOT be read from JavaScript, so this module
 * intentionally does NOT expose a token-reading function — any code
 * that tries to do so would always get ``null`` and silently break
 * auth.
 *
 * The previous implementation wrote a non-HttpOnly client-side
 * ``auth_token`` cookie AND mirrored the token into ``localStorage``.
 * Both have been removed as part of the migration to HttpOnly-only
 * cookies:
 *
 * - ``localStorage`` writes are gone — XSS could exfiltrate the token
 *   and there is no way to scope the storage to the deployment.
 * - ``document.cookie`` writes are gone — the backend now owns the
 *   cookie lifecycle (``Set-Cookie`` on login, ``Delete-Cookie`` on
 *   logout) and a client-written cookie would conflict with the
 *   ``__Host-`` prefix requirements (Secure, Path=/, no Domain).
 *
 * Logout is the only cookie-touching action left. It is implemented
 * as a backend call (``POST /api/v1/auth/logout/``) that returns
 * the ``Set-Cookie`` header with ``Max-Age=0``; the browser then
 * drops the cookie automatically. The wrapper lives in
 * ``@/lib/auth.ts``.
 */

/**
 * Best-effort cleanup of any legacy client-side auth state left over
 * from older builds. Safe to call repeatedly. The HttpOnly cookie
 * itself is cleared by the backend logout endpoint, not by this
 * function.
 */
export function clearAuthCookies(): void {
  if (typeof document === "undefined") return;
  // Clear any non-HttpOnly legacy cookies a previous build may have
  // set. The HttpOnly cookie is removed by the backend's Set-Cookie
  // response on /api/v1/auth/logout/.
  // SECURITY: only clear legacy client-side cookies. NEVER clear the
  // HttpOnly ``sessionid`` cookie — that is the Django session cookie
  // owned entirely by the backend. Deleting it here would immediately
  // log the user out after a single transient 401, causing a permanent
  // redirect loop. The backend clears sessionid on logout via
  // Set-Cookie: sessionid=; Max-Age=0.
  for (const name of ["auth_token"]) {
    document.cookie = `${name}=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT; SameSite=Strict`;
  }
}
