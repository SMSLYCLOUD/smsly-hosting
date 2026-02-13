import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const AUTH_VALIDATE_PATH = "/api/v1/auth/user/";

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

async function validateAuth(request: NextRequest): Promise<boolean> {
  // localStorage isn't accessible from middleware. We rely on cookies, but we
  // *validate* them against the backend to avoid stale/forged cookie access.
  const authToken = request.cookies.get("auth_token")?.value;
  const session = request.cookies.get("sessionid")?.value;

  if (!authToken && !session) {
    return false;
  }

  const headers: HeadersInit = { Accept: "application/json" };

  // Forward cookies so Django session auth works.
  const cookieHeader = request.headers.get("cookie");
  if (cookieHeader) {
    headers.cookie = cookieHeader;
  }

  // Prefer DRF Token auth when present.
  if (authToken) {
    headers.authorization = `Token ${decodeURIComponent(authToken)}`;
  }

  try {
    const res = await fetch(new URL(AUTH_VALIDATE_PATH, request.url), {
      method: "GET",
      headers,
      redirect: "manual",
      cache: "no-store",
    });
    return res.ok;
  } catch {
    return false;
  }
}

function redirectToLogin(request: NextRequest): NextResponse {
  const res = NextResponse.redirect(new URL("/login", request.url));

  // Clear stale cookies to prevent auth redirect loops.
  res.cookies.set("auth_token", "", { path: "/", maxAge: 0 });
  res.cookies.set("sessionid", "", { path: "/", maxAge: 0 });

  return res;
}

export async function middleware(request: NextRequest) {
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

  const isAuthenticated = await validateAuth(request);

  // Protect dashboard routes - redirect to login if not authenticated.
  if (protectedPage && !isAuthenticated) {
    return redirectToLogin(request);
  }

  // If already logged in and visiting login/register, redirect to dashboard.
  if (authPage && isAuthenticated) {
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
    "/login",
    "/register",
    "/auth/:path*",
  ],
};

