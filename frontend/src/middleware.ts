import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  const session = request.cookies.get("sessionid"); // Standard Django session cookie
  const isAuthPage = request.nextUrl.pathname.startsWith("/login") || request.nextUrl.pathname.startsWith("/auth");
  const isProtectedPage =
    request.nextUrl.pathname.startsWith("/dashboard") ||
    request.nextUrl.pathname.startsWith("/new") ||
    request.nextUrl.pathname.startsWith("/services") ||
    request.nextUrl.pathname.startsWith("/marketplace");

  if (isProtectedPage && !session) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  if (isAuthPage && session) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/dashboard/:path*",
    "/new/:path*",
    "/services/:path*",
    "/marketplace/:path*",
    "/login",
    "/auth/:path*"
  ],
};
