/**
 * Tests for src/lib/paths.ts — protected-path helpers and redirect guard.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  AUTH_REDIRECT_GUARD_KEY,
  PROTECTED_PREFIXES,
  canRedirectToLogin,
  isAuthPage,
  isCallbackPage,
  isProtectedPath,
  resetRedirectGuard,
} from '@/lib/paths';

describe('isProtectedPath', () => {
  it.each(PROTECTED_PREFIXES)('treats %s as protected', (prefix) => {
    expect(isProtectedPath(prefix)).toBe(true);
  });

  it.each([
    '/dashboard',
    '/dashboard/services',
    '/dashboard/services/123',
    '/new',
    '/services',
    '/services/abc',
    '/deployments',
    '/billing/invoices',
    '/admin-dashboard/users',
  ])('treats %s as protected', (path) => {
    expect(isProtectedPath(path)).toBe(true);
  });

  it('treats an exact-match sibling /dashboards as NOT protected', () => {
    expect(isProtectedPath('/dashboards')).toBe(false);
  });

  it('does not treat unrelated paths as protected', () => {
    expect(isProtectedPath('/login')).toBe(false);
    expect(isProtectedPath('/about')).toBe(false);
    expect(isProtectedPath('/')).toBe(false);
  });
});

describe('isAuthPage', () => {
  it('matches /login and /register exactly', () => {
    expect(isAuthPage('/login')).toBe(true);
    expect(isAuthPage('/register')).toBe(true);
  });

  it('does not match auth callback or login subpaths', () => {
    expect(isAuthPage('/auth/callback')).toBe(false);
    expect(isAuthPage('/login/foo')).toBe(false);
  });
});

describe('isCallbackPage', () => {
  it('matches anything under /auth/callback', () => {
    expect(isCallbackPage('/auth/callback')).toBe(true);
    expect(isCallbackPage('/auth/callback/github')).toBe(true);
    expect(isCallbackPage('/auth/callback/google/callback')).toBe(true);
  });

  it('does not match unrelated paths', () => {
    expect(isCallbackPage('/auth/cabc')).toBe(false);
    expect(isCallbackPage('/login')).toBe(false);
  });
});

describe('canRedirectToLogin', () => {
  beforeEach(() => {
    sessionStorage.clear();
    resetRedirectGuard();
  });

  afterEach(() => {
    sessionStorage.clear();
  });

  it('returns true the first time when sessionStorage is available', () => {
    expect(canRedirectToLogin()).toBe(true);
  });

  it('returns false when called twice within 5s', () => {
    expect(canRedirectToLogin()).toBe(true);
    expect(canRedirectToLogin()).toBe(false);
  });

  it('returns false after MAX_REDIRECT_COUNT (3) total attempts', () => {
    // Three redirects pass, the fourth is rejected.
    expect(canRedirectToLogin()).toBe(true);
    // Advance past the 5s window between subsequent calls.
    vi.useFakeTimers();
    vi.advanceTimersByTime(6_000);
    expect(canRedirectToLogin()).toBe(true);
    vi.advanceTimersByTime(6_000);
    expect(canRedirectToLogin()).toBe(true);
    vi.advanceTimersByTime(6_000);
    expect(canRedirectToLogin()).toBe(false);
    vi.useRealTimers();
  });

  it('returns false when sessionStorage is undefined', () => {
    const saved = (globalThis as { sessionStorage?: Storage }).sessionStorage;
    (globalThis as { sessionStorage?: Storage }).sessionStorage = undefined;
    try {
      expect(canRedirectToLogin()).toBe(false);
    } finally {
      (globalThis as { sessionStorage?: Storage }).sessionStorage = saved;
    }
  });
});

describe('resetRedirectGuard', () => {
  it('clears both guard keys', () => {
    sessionStorage.setItem(AUTH_REDIRECT_GUARD_KEY, String(Date.now()));
    sessionStorage.setItem('__auth_redirect_count', '3');

    resetRedirectGuard();

    expect(sessionStorage.getItem(AUTH_REDIRECT_GUARD_KEY)).toBeNull();
    expect(sessionStorage.getItem('__auth_redirect_count')).toBeNull();
  });

  it('is a no-op when sessionStorage is undefined', () => {
    const saved = (globalThis as { sessionStorage?: Storage }).sessionStorage;
    (globalThis as { sessionStorage?: Storage }).sessionStorage = undefined;
    try {
      expect(() => resetRedirectGuard()).not.toThrow();
    } finally {
      (globalThis as { sessionStorage?: Storage }).sessionStorage = saved;
    }
  });
});
