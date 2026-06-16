import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const DEV_SHORT_CIRCUIT_ENABLED = false;

if (process.env.NODE_ENV === "production" && DEV_SHORT_CIRCUIT_ENABLED) {
  throw new Error(
    "DEV_SHORT_CIRCUIT_ENABLED must not be true in production builds",
  );
}

const PROTECTED_PREFIXES = [
  "/dashboard",
  "/new",
  "/services",
  "/deployments",
  "/topology",
  "/billing",
  "/admin-dashboard",
  "/project",
  "/store",
  "/marketplace",
  "/settings",
  "/ecosystem",
  "/intelligence",
  "/servers",
  "/tunnels",
  "/templates",
  "/reseller",
];

function isProtectedPath(pathname: string): boolean {
  return PROTECTED_PREFIXES.some((p) => pathname.startsWith(p));
}

function isAuthPage(pathname: string): boolean {
  return pathname === "/login" || pathname === "/register";
}

function isCallbackPage(pathname: string): boolean {
  return pathname.startsWith("/auth/callback");
}

function hasAuthTokenCookie(request: NextRequest): boolean {
  // The backend issues two cookie names depending on the environment:
  //   * ``__Host-auth_token`` in production (HTTPS-only, with the
  //     hardened ``__Host-`` prefix)
  //   * ``auth_token`` in development (plain HTTP allowed)
  // The middleware runs on the frontend before the request reaches the
  // backend, so it does not know which environment it is in. Accept
  // either name to keep both code paths working.
  const authToken =
    request.cookies.get("__Host-auth_token")?.value ??
    request.cookies.get("auth_token")?.value;
  return Boolean(authToken && authToken.trim());
}

function hasSessionCookie(request: NextRequest): boolean {
  const session = request.cookies.get("sessionid")?.value;
  return Boolean(session && session.trim());
}

function hasCsrfTokenCookie(request: NextRequest): boolean {
    // The cookie name is whatever the backend sets (Django's default is
    // `csrftoken`, but operators can override it). Read whatever cookie
    // name the browser actually carries and accept any of them.
    // CRITICAL: do NOT block page navigation on CSRF. CSRF is for
    // state-changing requests (POST/PUT/DELETE), not for GET page loads.
    // A previous version of this middleware required a CSRF cookie to
    // exist for protected pages; combined with a backend that sets
    // `csrftoken` (not `csrf_token`), it produced an infinite
    // /dashboard -> /login -> /dashboard redirect loop on every login.
    // Keep this check around only for diagnostic / future use; the
    // middleware below no longer blocks on it.
    void request;
    return true;
}

export async function middleware(request: NextRequest) {
  if (process.env.NODE_ENV === "development" && DEV_SHORT_CIRCUIT_ENABLED) {
    return NextResponse.next();
  }
  const pathname = request.nextUrl.pathname;

  // Allow the callback page through so it can complete auth.
  if (isCallbackPage(pathname)) {
    return NextResponse.next();
  }

  const protectedPage = isProtectedPath(pathname);
  const authPage = isAuthPage(pathname);

  if (!protectedPage && !authPage) {
    return NextResponse.next();
  }

  const hasApiToken = hasAuthTokenCookie(request);
  const hasSession = hasSessionCookie(request);
  const hasCsrf = hasCsrfTokenCookie(request);

  // Protect dashboard routes. Allow session-only users through so the
  // client can exchange session->token without forcing a hard redirect loop.
  if (protectedPage && !hasApiToken && !hasSession) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  // Redirect auth pages only when API token exists. Session-only state can
  // happen transiently during OAuth reconnect and should not cause loops.
  if (authPage && hasApiToken) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/new/:path*",
    "/services/:path*",
    "/deployments/:path*",
    "/topology/:path*",
    "/billing/:path*",
    "/admin-dashboard/:path*",
    "/project/:path*",
    "/store/:path*",
    "/marketplace/:path*",
    "/settings/:path*",
    "/ecosystem/:path*",
    "/intelligence/:path*",
    "/servers/:path*",
    "/tunnels/:path*",
    "/templates/:path*",
    "/reseller/:path*",
    "/login",
    "/register",
    "/auth/:path*",
  ],
};
