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

function hasAuthCookies(request: NextRequest): boolean {
  // Keep middleware logic fast and robust: only gate by presence of auth
  // cookies. The client AuthProvider will validate the token with the backend
  // and clear cookies/localStorage if invalid.
  const authToken = request.cookies.get("auth_token")?.value;
  const session = request.cookies.get("sessionid")?.value;
  return Boolean((authToken && authToken.trim()) || (session && session.trim()));
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

  const isAuthenticated = hasAuthCookies(request);

  // Protect dashboard routes - redirect to login if not authenticated.
  if (protectedPage && !isAuthenticated) {
    return NextResponse.redirect(new URL("/login", request.url));
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
