import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

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
  const authToken = request.cookies.get("auth_token")?.value;
  return Boolean(authToken && authToken.trim());
}

function hasSessionCookie(request: NextRequest): boolean {
  const session = request.cookies.get("sessionid")?.value;
  return Boolean(session && session.trim());
}

export async function middleware(request: NextRequest) {
  if (process.env.NODE_ENV === "development") {
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

  // Protect dashboard routes. Allow session-only users through so the client
  // can exchange session->token without forcing a hard redirect loop.
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
