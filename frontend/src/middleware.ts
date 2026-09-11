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

/**
 * Canonical platform hostname, baked at image build time from
 * FRONTEND_APP_URL (see lib/fresh_config.sh + docker-compose build
 * args). Empty on images built before the bake existed — the hostname
 * check below stays disabled then (fail-open legacy behavior).
 */
const APP_URL_HOST = (() => {
    try {
        const raw = (process.env.NEXT_PUBLIC_APP_URL || "").trim();
        if (!raw) return "";
        return new URL(raw).hostname.toLowerCase();
    } catch {
        return "";
    }
})();

function isIpHost(host: string): boolean {
    return /^[0-9]{1,3}(\.[0-9]{1,3}){3}$/.test(host);
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

  // Hostname validation: a request for <something>.<platform-domain>
  // that reaches the frontend is an orphaned service subdomain — Caddy
  // routes known service hosts to their containers, so arrival here
  // means the service is gone. Answer 404 instead of serving the
  // dashboard/login shell under a dead hostname. Scoped to subdomains
  // of the baked platform host only (exact host, custom admin domains,
  // localhost and IPs always pass through); disabled entirely while
  // NEXT_PUBLIC_APP_URL is unset (fail-open legacy behavior).
  if (APP_URL_HOST && !isIpHost(APP_URL_HOST)) {
    const reqHost = (request.headers.get("host") || "")
      .split(":")[0]
      .trim()
      .toLowerCase();
    if (
      reqHost &&
      reqHost !== APP_URL_HOST &&
      reqHost.endsWith(`.${APP_URL_HOST}`)
    ) {
      return new NextResponse("Service not found", { status: 404 });
    }
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
    // NOTE: /store and /templates are intentionally PUBLIC (browsing uses
    // the AllowAny templates API; deploy actions 401-gate to /login).
    // /marketplace stays protected — it manages the operator's own
    // addons/services, not the public catalog.
    "/dashboard/:path*",
    "/new/:path*",
    "/services/:path*",
    "/deployments/:path*",
    "/topology/:path*",
    "/billing/:path*",
    "/admin-dashboard/:path*",
    "/project/:path*",
    "/projects/:path*",
    "/marketplace/:path*",
    "/settings/:path*",
    "/ecosystem/:path*",
    "/intelligence/:path*",
    "/servers/:path*",
    "/tunnels/:path*",
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
