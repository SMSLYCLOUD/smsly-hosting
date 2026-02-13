import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  // Check for any auth indicator: Django session cookie OR DRF token cookie
  const session = request.cookies.get("sessionid");
  const authToken = request.cookies.get("auth_token");
  const isAuthenticated = !!(session || authToken);

  // Allow the callback page through so it can parse the token from the URL
  const isCallbackPage = request.nextUrl.pathname.startsWith("/auth/callback");

  const isAuthPage =
    request.nextUrl.pathname === "/login" ||
    request.nextUrl.pathname === "/register";

  const isProtectedPage =
    request.nextUrl.pathname.startsWith("/dashboard") ||
    request.nextUrl.pathname.startsWith("/new") ||
    request.nextUrl.pathname.startsWith("/services") ||
    request.nextUrl.pathname.startsWith("/deployments") ||
    request.nextUrl.pathname.startsWith("/topology") ||
    request.nextUrl.pathname.startsWith("/billing") ||
    request.nextUrl.pathname.startsWith("/admin-dashboard") ||
    request.nextUrl.pathname.startsWith("/project") ||
    request.nextUrl.pathname.startsWith("/store") ||
    request.nextUrl.pathname.startsWith("/marketplace") ||
    request.nextUrl.pathname.startsWith("/settings");

  // Protect dashboard routes — redirect to login if not authenticated
  if (isProtectedPage && !isAuthenticated) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  // If already logged in and visiting login/register, redirect to dashboard
  // But never redirect from the callback page
  if (isAuthPage && isAuthenticated && !isCallbackPage) {
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
