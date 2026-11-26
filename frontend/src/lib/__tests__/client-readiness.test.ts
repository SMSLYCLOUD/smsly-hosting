import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { AxiosError } from 'axios';
import { api } from '../api';
import { useWebSocket } from '../websocket';

class Socket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: Socket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onmessage = null;
  onerror = null;
  constructor(public url: string) { Socket.instances.push(this); }
  close() { this.readyState = 3; } // Browser close notification arrives asynchronously.
  closed() { this.onclose?.(new CloseEvent('close')); }
}

beforeEach(() => { localStorage.clear(); Socket.instances = []; });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('proxy authentication boundaries', () => {
  it.each([false, true])('preserves controller authentication on remote 401 (explicit proxy=%s)', async (explicit) => {
    const logout = vi.fn().mockResolvedValue({});
    vi.stubGlobal('fetch', logout);
    document.cookie = 'auth_token=local-session; path=/';
    localStorage.setItem('smsly_active_server', 'remote-1');
    await expect(api.get(explicit ? '/servers/remote-1/proxy/' : '/services/', {
      adapter: async config => ({ config, data: { status_code: 401, data: { detail: 'Remote credentials expired' } }, status: 200, statusText: 'OK', headers: {} }),
    })).rejects.toMatchObject({ response: { status: 401 } });
    expect(logout).not.toHaveBeenCalled();
    expect(document.cookie).toContain('auth_token=local-session');
  });

  it('does not call destructive logout on arbitrary non-auth-user 401s, and clears cookies on /auth/user/ 401', async () => {
    const logout = vi.fn().mockResolvedValue({});
    vi.stubGlobal('fetch', logout);
    document.cookie = 'auth_token=local-session; path=/';

    await expect(api.get('/servers/remote-1/proxy/', {
      adapter: async config => { throw new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, undefined, { config, data: {}, status: 401, statusText: 'Unauthorized', headers: {} }); },
    })).rejects.toMatchObject({ response: { status: 401 } });
    expect(logout).not.toHaveBeenCalled();
    expect(document.cookie).toContain('auth_token=local-session');

    await expect(api.get('/auth/user/', {
      adapter: async config => { throw new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, undefined, { config, data: {}, status: 401, statusText: 'Unauthorized', headers: {} }); },
    })).rejects.toMatchObject({ response: { status: 401 } });
    expect(logout).not.toHaveBeenCalled();
    expect(document.cookie).not.toContain('auth_token=local-session');
  });
});

describe('websocket connection ownership', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('WebSocket', Socket); });

  it('does not reconnect after an intentional disconnect', () => {
    const { result, unmount } = renderHook(() => useWebSocket({ url: 'ws://localhost/status', reconnectInterval: 10 }));
    const socket = Socket.instances[0];
    act(() => result.current.disconnect());
    act(() => { socket.closed(); vi.advanceTimersByTime(100); });
    expect(Socket.instances).toHaveLength(1);
    unmount();
  });

  it('does not reconnect after unmount', () => {
    const { unmount } = renderHook(() => useWebSocket({ url: 'ws://localhost/status', reconnectInterval: 10 }));
    const socket = Socket.instances[0];
    unmount();
    act(() => { socket.closed(); vi.advanceTimersByTime(100); });
    expect(Socket.instances).toHaveLength(1);
  });

  it('ignores old socket close events after changing URL', () => {
    const { rerender, unmount } = renderHook(({ url }) => useWebSocket({ url, reconnectInterval: 10 }), { initialProps: { url: 'ws://localhost/old' } });
    const old = Socket.instances[0];
    rerender({ url: 'ws://localhost/new' });
    act(() => { old.closed(); vi.advanceTimersByTime(100); });
    expect(Socket.instances.map(socket => socket.url)).toEqual(['ws://localhost/old', 'ws://localhost/new']);
    unmount();
  });

  it('keeps one connection while connecting and retries unexpected closure', () => {
    const { result, unmount } = renderHook(() => useWebSocket({ url: 'ws://localhost/status', reconnectInterval: 10 }));
    act(() => result.current.connect());
    expect(Socket.instances).toHaveLength(1);
    act(() => { Socket.instances[0].closed(); vi.advanceTimersByTime(10); });
    expect(Socket.instances).toHaveLength(2);
    unmount();
  });
});
