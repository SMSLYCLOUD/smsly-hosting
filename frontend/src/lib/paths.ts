/**
 * Single source of truth for all protected-path definitions used by
 * the three auth layers (middleware, AuthProvider, 401 interceptor).
 *
 * Previously each layer maintained its own copy and they drifted apart,
 * causing the store/marketplace/ecosystem/templates/reseller pages to
 * silently skip 401 redirects.
 */

export const PROTECTED_PREFIXES = [
  "/dashboard",
  "/new",
  "/services",
  "/deployments",
  "/topology",
  "/billing",
  "/admin-dashboard",
  "/project",
  "/projects",
  "/store",
  "/marketplace",
  "/settings",
  "/ecosystem",
  "/intelligence",
  "/servers",
  "/tunnels",
  "/templates",
  "/reseller",
  "/backups",
  "/transfers",
  "/functions",
  "/activity",
  "/autoscaler",
  "/blueprints",
  "/client",
  "/domains",
  "/grafana",
  "/logs",
  "/monitoring",
  "/network",
  "/replication",
  "/restore",
  "/addons",
  "/mcp",
] as const;

export function isProtectedPath(pathname: string): boolean {
  return PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

/** Auth pages — redirect to /dashboard when already authenticated. */
export function isAuthPage(pathname: string): boolean {
  return pathname === "/login" || pathname === "/register";
}

/** OAuth callback pages — always pass through middleware. */
export function isCallbackPage(pathname: string): boolean {
  return pathname.startsWith("/auth/callback");
}

/**
 * Central loop-guard key shared by AuthProvider AND the 401
 * interceptor so they cannot both fire a redirect within the
 * same 5-second window (previously they used independent keys).
 */
export const AUTH_REDIRECT_GUARD_KEY = "__auth_redirect_ts";

/**
 * Maximum number of redirects within a single session before the
 * guard dead-ends to prevent slow infinite loops (every-5-second
 * redirect when the backend is permanently broken).
 */
const MAX_REDIRECT_COUNT = 3;

/**
 * Check whether a redirect to /login is safe to perform.
 * Returns false if we are already rate-limiting (within 5 s)
 * or if the total redirect count for this session exceeds
 * MAX_REDIRECT_COUNT.
 */
export function canRedirectToLogin(): boolean {
  if (typeof sessionStorage === "undefined") return false;
  const now = Date.now();
  const last = Number(sessionStorage.getItem(AUTH_REDIRECT_GUARD_KEY) || 0);
  if (now - last < 5000) return false;

  const count = Number(sessionStorage.getItem("__auth_redirect_count") || 0);
  if (count >= MAX_REDIRECT_COUNT) return false;

  sessionStorage.setItem(AUTH_REDIRECT_GUARD_KEY, String(now));
  sessionStorage.setItem("__auth_redirect_count", String(count + 1));
  return true;
}

/** Reset the redirect counter (call after successful login). */
export function resetRedirectGuard(): void {
  if (typeof sessionStorage === "undefined") return;
  sessionStorage.removeItem(AUTH_REDIRECT_GUARD_KEY);
  sessionStorage.removeItem("__auth_redirect_count");
}
