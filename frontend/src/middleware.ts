import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import {
  isProtectedPath,
  isAuthPage,
  isCallbackPage,
} from "@/lib/paths";

const DEV_SHORT_CIRCUIT_ENABLED = false;

if (process.env.NODE_ENV === "production" && DEV_SHORT_CIRCUIT_ENABLED) {
  throw new Error(
    "DEV_SHORT_CIRCUIT_ENABLED must not be true in production builds",
  );
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

/**
 * Cookie name issued by the rust_twin backend. In production this carries
 * the `__Host-` prefix and is `Secure` + `HttpOnly`; in development (HTTP)
 * the same logical token is also accepted under the `__Host-smsly_token`
 * name for parity. We do NOT touch the Django `__Host-auth_token` cookie
 * here — that backend reads it from `Cookie:` itself, and rewriting the
 * Authorization header for Django requests would just cause duplicate
 * auth and would break pages that already carry the Django token.
 */
const RUST_TWIN_AUTH_COOKIES = [
  "__Host-smsly_token",
  "smsly_token",
] as const;

function getRustTwinToken(request: NextRequest): string | null {
  for (const name of RUST_TWIN_AUTH_COOKIES) {
    const value = request.cookies.get(name)?.value;
    if (value && value.trim().length > 0) {
      return value;
    }
  }
  return null;
}

function isStaticAsset(pathname: string): boolean {
  if (pathname.startsWith("/_next/static/")) return true;
  if (pathname.startsWith("/_next/image/")) return true;
  if (pathname === "/favicon.ico") return true;
  if (pathname === "/robots.txt") return true;
  if (pathname === "/sitemap.xml") return true;
  if (pathname.startsWith("/_next/data/")) return true;
  if (pathname === "/manifest.json") return true;
  if (/\.(?:png|jpg|jpeg|gif|webp|avif|svg|ico|css|js|map|woff2?|ttf|otf|eot|mp4|webm|mp3|wav|ogg|pdf|txt)$/i.test(pathname)) {
    return true;
  }
  return false;
}

function isApiRequest(pathname: string): boolean {
  if (pathname.startsWith("/api/")) return true;
  if (pathname === "/health" || pathname.startsWith("/health/")) return true;
  if (pathname === "/metrics") return true;
  if (pathname === "/openapi.json") return true;
  return false;
}

function injectRustTwinAuthHeader(request: NextRequest): NextResponse {
  const requestHeaders = new Headers(request.headers);
  const existingAuth = requestHeaders.get("authorization");
  if (existingAuth && existingAuth.trim().length > 0) {
    return NextResponse.next({ request: { headers: requestHeaders } });
  }
  const token = getRustTwinToken(request);
  if (token) {
    requestHeaders.set("authorization", `Token ${token}`);
  }
  return NextResponse.next({ request: { headers: requestHeaders } });
}

export async function middleware(request: NextRequest) {
  if (process.env.NODE_ENV === "development" && DEV_SHORT_CIRCUIT_ENABLED) {
    return NextResponse.next();
  }
  const pathname = request.nextUrl.pathname;

  // Skip static assets entirely — no auth header injection, no logging.
  if (isStaticAsset(pathname)) {
    return NextResponse.next();
  }

  // API/health/metrics/openapi paths: inject the rust_twin auth header
  // (if a token cookie is present) and pass through. We do NOT enforce
  // page-level auth on these — that's the backend's job.
  if (isApiRequest(pathname)) {
    return injectRustTwinAuthHeader(request);
  }

  // Hostname validation: reject requests for unknown service subdomains.
  // Only the platform domain (APP_URL) and localhost serve the dashboard.
  // Everything else is a service domain that should have been routed to
  // a container by Caddy — if we're here, the service doesn't exist.
  const hostname = request.nextUrl.hostname || '';
  const appUrl = process.env.NEXT_PUBLIC_APP_URL || '';
  const platformHost = appUrl ? new URL(appUrl).hostname : '';
  const isPlatformHost =
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === platformHost ||
    hostname.endsWith('.' + platformHost);
  if (!isPlatformHost) {
    const notFoundUrl = request.nextUrl.clone();
    notFoundUrl.pathname = '/not-found';
    return NextResponse.rewrite(notFoundUrl);
  }

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
    // Auth-header injection for the upstream API proxy.
    "/api/:path*",
    "/health",
    "/health/:path*",
    "/metrics",
    "/openapi.json",
    // Page-level protection (unchanged from before).
    "/dashboard/:path*",
    "/new/:path*",
    "/services/:path*",
    "/deployments/:path*",
    "/topology/:path*",
    "/billing/:path*",
    "/admin-dashboard/:path*",
    "/project/:path*",
    "/projects/:path*",
    "/store/:path*",
    "/marketplace/:path*",
    "/settings/:path*",
    "/ecosystem/:path*",
    "/intelligence/:path*",
    "/servers/:path*",
    "/tunnels/:path*",
    "/templates/:path*",
    "/reseller/:path*",
    "/backups/:path*",
    "/transfers/:path*",
    "/functions/:path*",
    "/activity/:path*",
    "/autoscaler/:path*",
    "/blueprints/:path*",
    "/client/:path*",
    "/domains/:path*",
    "/grafana/:path*",
    "/logs/:path*",
    "/monitoring/:path*",
    "/network/:path*",
    "/replication/:path*",
    "/restore/:path*",
    "/addons/:path*",
    "/settings/:path*",
    "/login",
    "/register",
    "/auth/:path*",
  ],
};
