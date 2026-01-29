import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export function middleware(request: NextRequest) {
  const token = request.cookies.get('auth_token');

  // If the user is on the root path and NOT authenticated, redirect to login
  if (request.nextUrl.pathname === '/') {
    if (!token) {
      return NextResponse.redirect(new URL('/login', request.url));
    }
    // If authenticated, allow access to dashboard (/)
    return NextResponse.next();
  }

  // If user is on /login but HAS token, redirect to dashboard
  if (request.nextUrl.pathname === '/login' && token) {
    return NextResponse.redirect(new URL('/', request.url));
  }
}

export const config = {
  matcher: '/',
}
