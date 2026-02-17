/**
 * Centralized auth cookie management.
 *
 * L-2 fix: Deduplicated from login/page.tsx, auth-provider.tsx, and auth/callback/page.tsx.
 * H-3 fix: Enforces Secure flag on HTTPS, uses SameSite=Strict for CSRF protection.
 *
 * NOTE: True HttpOnly cookies cannot be set via JavaScript (document.cookie).
 * For full HttpOnly protection, the backend should set the cookie via Set-Cookie header.
 * This utility provides the best client-side security available.
 */

export const AUTH_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30; // 30 days

/**
 * Set the auth_token cookie with security hardening.
 */
export function setAuthTokenCookie(token: string): void {
  const isSecure =
    typeof window !== "undefined" && window.location.protocol === "https:";
  const cookieParts = [
    `auth_token=${encodeURIComponent(token)}`,
    "path=/",
    `max-age=${AUTH_COOKIE_MAX_AGE_SECONDS}`,
    "SameSite=Strict", // H-3 fix: upgraded from Lax to Strict for better CSRF protection
  ];
  if (isSecure) {
    cookieParts.push("Secure");
  }
  document.cookie = cookieParts.join("; ");
}

/**
 * Clear all authentication cookies.
 */
export function clearAuthCookies(): void {
  document.cookie =
    "auth_token=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT; SameSite=Strict";
  document.cookie =
    "sessionid=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT; SameSite=Strict";
}
