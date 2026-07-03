/**
 * Tests for src/lib/auth-cookies.ts — clearAuthCookies().
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { clearAuthCookies } from '@/lib/auth-cookies';

describe('clearAuthCookies', () => {
  beforeEach(() => {
    // Reset the cookie jar between tests.
    document.cookie = 'auth_token=abc; path=/';
    document.cookie = 'sessionid=session-xyz; path=/';
  });

  afterEach(() => {
    document.cookie = 'auth_token=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT';
    document.cookie = 'sessionid=; path=/; expires=Thu, 01 Jan 1970 00:00:01 GMT';
  });

  it('removes a legacy auth_token cookie', () => {
    expect(document.cookie).toContain('auth_token=abc');
    clearAuthCookies();
    expect(document.cookie).not.toContain('auth_token=abc');
  });

  it('does NOT touch the sessionid cookie (security regression)', () => {
    expect(document.cookie).toContain('sessionid=session-xyz');
    clearAuthCookies();
    expect(document.cookie).toContain('sessionid=session-xyz');
  });

  it('is safe to call when document is undefined (SSR safety)', () => {
    const savedDocument = (globalThis as { document?: Document }).document;
    (globalThis as { document?: Document }).document = undefined;
    try {
      expect(() => clearAuthCookies()).not.toThrow();
    } finally {
      (globalThis as { document?: Document }).document = savedDocument;
    }
  });
});
