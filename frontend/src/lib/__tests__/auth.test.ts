/**
 * Tests for src/lib/auth.ts — the logout() helper.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({
  api: {
    post: vi.fn(),
  },
}));

vi.mock('@/lib/auth-cookies', () => ({
  clearAuthCookies: vi.fn(),
}));

import { api } from '@/lib/api';
import { clearAuthCookies } from '@/lib/auth-cookies';
import { logout } from '@/lib/auth';

const mockedApiPost = vi.mocked(api.post);
const mockedClear = vi.mocked(clearAuthCookies);

describe('logout', () => {
  let originalLocation: Location;

  beforeEach(() => {
    mockedApiPost.mockReset();
    mockedClear.mockReset();
    originalLocation = window.location;
    // Stub window.location.assign so the test never navigates away.
    Object.defineProperty(window, 'location', {
      writable: true,
      configurable: true,
      value: { ...originalLocation, assign: vi.fn() },
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      writable: true,
      configurable: true,
      value: originalLocation,
    });
  });

  it('calls POST /auth/logout/, clears cookies and redirects to /login', async () => {
    mockedApiPost.mockResolvedValueOnce({ status: 200, data: {} });

    await logout();

    expect(mockedApiPost).toHaveBeenCalledTimes(1);
    expect(mockedApiPost).toHaveBeenCalledWith('/auth/logout/', {});
    expect(mockedClear).toHaveBeenCalledTimes(1);
    expect(window.location.assign).toHaveBeenCalledWith('/login');
  });

  it('still clears cookies and redirects when the network call rejects', async () => {
    mockedApiPost.mockRejectedValueOnce(new Error('network down'));

    await expect(logout()).resolves.toBeUndefined();

    expect(mockedApiPost).toHaveBeenCalledWith('/auth/logout/', {});
    expect(mockedClear).toHaveBeenCalledTimes(1);
    expect(window.location.assign).toHaveBeenCalledWith('/login');
  });

  it('does not crash when window is undefined (SSR safety)', async () => {
    mockedApiPost.mockResolvedValueOnce({ status: 200, data: {} });
    // Pretend we are running on the server.
    const win = window as unknown as { location?: unknown };
    const savedLocation = win.location;
    // Use stubGlobal because deleting window.location is observable.
    vi.stubGlobal('window', undefined as unknown as typeof window);

    await expect(logout()).resolves.toBeUndefined();

    expect(mockedClear).toHaveBeenCalledTimes(1);
    // Restore window so other tests aren't broken.
    vi.stubGlobal('window', { location: savedLocation } as unknown as typeof window);
  });
});
