/**
 * Tests for helpers and Axios client/interceptor behaviour exported from src/lib/api.ts.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios';

import { aiAdminApi, api, downloadBlob, edgeApi, extractDataList, organizationsApi } from '@/lib/api';
import * as authCookies from '@/lib/auth-cookies';
import * as paths from '@/lib/paths';

describe('downloadBlob', () => {
  let createObjectURL: ReturnType<typeof vi.fn>;
  let revokeObjectURL: ReturnType<typeof vi.fn>;
  let appendChildSpy: ReturnType<typeof vi.spyOn>;
  let removeChildSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    createObjectURL = vi.fn(() => 'blob:mock-url');
    revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    });
    appendChildSpy = vi.spyOn(document.body, 'appendChild');
    removeChildSpy = vi.spyOn(document.body, 'removeChild');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    appendChildSpy.mockRestore();
    removeChildSpy.mockRestore();
  });

  it('creates a hidden anchor with the right href and download attribute, clicks it, then removes it', () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click');

    const payload = new Blob(['hello,world\n']);
    downloadBlob(payload, 'reports/path/to/file.csv');

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const createdBlob = createObjectURL.mock.calls[0]?.[0] as Blob;
    expect(createdBlob).toBeInstanceOf(Blob);

    const appended = appendChildSpy.mock.calls
      .map((call) => call[0])
      .find((el): el is HTMLAnchorElement => el instanceof HTMLAnchorElement);
    expect(appended).toBeDefined();
    expect(appended!.href).toBe('blob:mock-url');
    expect(appended!.getAttribute('download')).toBe('file.csv');

    expect(clickSpy).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
    expect(removeChildSpy).toHaveBeenCalled();

    clickSpy.mockRestore();
  });

  it('sanitizes unsafe characters and leading dots from the download filename', () => {
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click');
    downloadBlob(new Blob(['x']), '../../..hidden?name*bad<>.csv');

    const appended = appendChildSpy.mock.calls
      .map((call) => call[0])
      .find((el): el is HTMLAnchorElement => el instanceof HTMLAnchorElement);
    expect(appended).toBeDefined();
    expect(appended!.getAttribute('download')).toBe('hidden_name_bad__.csv');

    clickSpy.mockRestore();
  });
});

describe('extractDataList', () => {
  it('returns the array when response.data is already an array', () => {
    const arr = [{ id: 1 }, { id: 2 }];
    expect(extractDataList({ data: arr })).toBe(arr);
  });

  it('returns response.data.results when it is an array', () => {
    const arr = [{ id: 1 }];
    expect(extractDataList({ data: { results: arr } })).toBe(arr);
  });

  it('returns [] when response.data is { results: [] }', () => {
    expect(extractDataList({ data: { results: [] } })).toEqual([]);
  });

  it('returns [] when response.data is null', () => {
    expect(extractDataList({ data: null })).toEqual([]);
  });

  it('returns [] when response.data is a non-object primitive', () => {
    expect(extractDataList({ data: 42 })).toEqual([]);
    expect(extractDataList({ data: 'string' })).toEqual([]);
  });

  it('returns [] when response.data is an object without results', () => {
    expect(extractDataList({ data: { count: 1 } })).toEqual([]);
  });
});

describe('api client & interceptors', () => {
  let originalAdapter: AxiosAdapter | typeof api.defaults.adapter;
  const originalLocation = window.location;

  beforeEach(() => {
    originalAdapter = api.defaults.adapter;
    localStorage.clear();
    sessionStorage.clear();
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    vi.restoreAllMocks();
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: originalLocation,
    });
  });

  it('sends X-Requested-With: XMLHttpRequest and withCredentials by default', async () => {
    expect(api.defaults.withCredentials).toBe(true);
    expect(api.defaults.headers['X-Requested-With']).toBe('XMLHttpRequest');

    let capturedConfig: InternalAxiosRequestConfig | undefined;
    api.defaults.adapter = vi.fn(async (config) => {
      capturedConfig = config;
      return {
        data: { ok: true },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    });

    await api.get('/services/');
    expect(capturedConfig?.headers?.['X-Requested-With']).toBe('XMLHttpRequest');
    expect(capturedConfig?.withCredentials).toBe(true);
  });

  it('attaches X-Team-ID from localStorage only when valid', async () => {
    const capturedHeaders: Array<Record<string, unknown>> = [];
    api.defaults.adapter = vi.fn(async (config) => {
      capturedHeaders.push({ ...(config.headers as Record<string, unknown>) });
      return { data: [], status: 200, statusText: 'OK', headers: {}, config };
    });

    localStorage.setItem('smsly_active_team', 'team-42');
    await api.get('/services/');
    expect(capturedHeaders[0]['X-Team-ID']).toBe('team-42');

    localStorage.setItem('smsly_active_team', 'null');
    await api.get('/services/');
    expect(capturedHeaders[1]['X-Team-ID']).toBeUndefined();

    localStorage.setItem('smsly_active_team', 'undefined');
    await api.get('/services/');
    expect(capturedHeaders[2]['X-Team-ID']).toBeUndefined();
  });

  it('handles 401 on arbitrary endpoints non-destructively without clearing cookies or redirecting', async () => {
    const clearSpy = vi.spyOn(authCookies, 'clearAuthCookies');
    const replaceSpy = vi.fn();
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: { ...originalLocation, pathname: '/dashboard', replace: replaceSpy },
    });

    const adapterSpy = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const err: any = new Error('Unauthorized');
      err.config = config;
      err.response = { status: 401, statusText: 'Unauthorized', data: { detail: 'Bad integration token' }, headers: {}, config };
      throw err;
    });
    api.defaults.adapter = adapterSpy;

    await expect(api.get('/services/svc-1/')).rejects.toThrow('Unauthorized');
    expect(adapterSpy).toHaveBeenCalledTimes(1);
    expect(clearSpy).not.toHaveBeenCalled();
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it('clears auth cookies and redirects to /login when /auth/user/ returns 401 on a protected path', async () => {
    const clearSpy = vi.spyOn(authCookies, 'clearAuthCookies');
    vi.spyOn(paths, 'canRedirectToLogin').mockReturnValue(true);
    const replaceSpy = vi.fn();
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: { ...originalLocation, pathname: '/dashboard', replace: replaceSpy },
    });

    api.defaults.adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const err: any = new Error('Session expired');
      err.config = config;
      err.response = { status: 401, statusText: 'Unauthorized', data: {}, headers: {}, config };
      throw err;
    });

    await expect(api.get('/auth/user/')).rejects.toThrow('Session expired');
    expect(clearSpy).toHaveBeenCalledTimes(1);
    expect(replaceSpy).toHaveBeenCalledWith('/login');
  });

  it('does not clear local auth cookies on remote-proxied 401 responses', async () => {
    const clearSpy = vi.spyOn(authCookies, 'clearAuthCookies');
    const replaceSpy = vi.fn();
    Object.defineProperty(window, 'location', {
      configurable: true,
      writable: true,
      value: { ...originalLocation, pathname: '/dashboard', replace: replaceSpy },
    });

    localStorage.setItem('smsly_active_server', 'srv-remote');
    api.defaults.adapter = vi.fn(async (config: InternalAxiosRequestConfig) => ({
      data: { status_code: 401, data: { detail: 'Remote unauthorized' } },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }));

    await expect(api.get('/services/')).rejects.toThrow('Remote server returned 401');
    expect(clearSpy).not.toHaveBeenCalled();
    expect(replaceSpy).not.toHaveBeenCalled();
  });

  it('resolves 409 conflict responses on /services/{id}/deploy/ instead of throwing', async () => {
    api.defaults.adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const err: any = new Error('Conflict');
      err.config = config;
      err.response = {
        status: 409,
        statusText: 'Conflict',
        data: { existing_deployment: { id: 'dep-1', status: 'BUILDING' } },
        headers: {},
        config,
      };
      throw err;
    });

    const res = await api.post('/services/svc-1/deploy/', { ref: 'HEAD' });
    expect(res.status).toBe(409);
    expect(res.data).toEqual({ existing_deployment: { id: 'dep-1', status: 'BUILDING' } });
  });

  it('normalizes aiAdminApi.updateProvider flat keys including anthropic -> claude', async () => {
    const calls: Array<{ url?: string; data: unknown }> = [];
    api.defaults.adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      calls.push({ url: config.url, data: JSON.parse(config.data as string) });
      return {
        data: { status: 'updated' },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    });

    await aiAdminApi.updateProvider('openai', { api_key: 'sk-openai-1', model: 'gpt-4o' });
    await aiAdminApi.updateProvider('anthropic', {
      api_key: 'sk-ant-1',
      model: 'claude-3-5-sonnet',
      base_url: 'https://api.anthropic.com',
    });

    expect(calls[0]).toEqual({
      url: '/ai/providers/update/',
      data: { openai_api_key: 'sk-openai-1', openai_model: 'gpt-4o' },
    });
    expect(calls[1]).toEqual({
      url: '/ai/providers/update/',
      data: {
        claude_api_key: 'sk-ant-1',
        claude_model: 'claude-3-5-sonnet',
        claude_base_url: 'https://api.anthropic.com',
      },
    });
  });

  it('bypasses remote server proxy for /organizations/ and /edge/ paths', async () => {
    const calls: Array<{ url?: string; method?: string }> = [];
    api.defaults.adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      calls.push({ url: config.url, method: config.method });
      return {
        data: [],
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    });

    localStorage.setItem('smsly_active_server', 'srv-remote');
    await organizationsApi.pendingInvites();
    await edgeApi.mintToken({ scope: 'service', serviceId: 'svc-1' });

    expect(calls[0]).toEqual({ url: '/organizations/pending-invites/', method: 'get' });
    expect(calls[1]).toEqual({ url: '/edge/token/', method: 'post' });
  });

  it('enforces edgeApi.mintToken serviceId/aud guard for non-admin scope and supports admin scope', async () => {
    const calls: Array<{ url?: string; data: unknown }> = [];
    api.defaults.adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      calls.push({ url: config.url, data: JSON.parse(config.data as string) });
      return {
        data: { token: 'edge-jwt', scope: 'service', expires_in: 900 },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    });

    await expect(edgeApi.mintToken({ scope: 'preview' })).rejects.toThrow(/serviceId or aud is required/i);

    await edgeApi.mintToken({ scope: 'preview', ttl: 300, serviceId: 'svc-1', aud: 'app.example.com' });
    await edgeApi.mintToken({ scope: 'admin', ttl: 600 });

    expect(calls[0]).toEqual({
      url: '/edge/token/',
      data: { scope: 'preview', ttl: 300, service_id: 'svc-1', aud: 'app.example.com' },
    });
    expect(calls[1]).toEqual({
      url: '/edge/token/',
      data: { scope: 'admin', ttl: 600 },
    });
  });

  it('calls organizationsApi pendingInvites, acceptInvite, removeMember, and changeRole endpoints', async () => {
    const calls: Array<{ url?: string; method?: string; data?: unknown }> = [];
    api.defaults.adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      calls.push({
        url: config.url,
        method: config.method,
        data: config.data ? JSON.parse(config.data as string) : undefined,
      });
      return {
        data: [{ id: 'inv-1', organization: 'org/1', organization_name: 'Acme', role: 'MEMBER' }],
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    });

    const invites = await organizationsApi.pendingInvites();
    expect(invites).toHaveLength(1);
    await organizationsApi.acceptInvite('org/1');
    await organizationsApi.removeMember('org/1', 42);
    await organizationsApi.changeRole('org/1', 42, 'ADMIN');

    expect(calls[0]).toEqual({ url: '/organizations/pending-invites/', method: 'get', data: undefined });
    expect(calls[1]).toEqual({ url: '/organizations/org%2F1/accept-invite/', method: 'post', data: undefined });
    expect(calls[2]).toEqual({ url: '/organizations/org%2F1/remove_member/', method: 'post', data: { user_id: 42 } });
    expect(calls[3]).toEqual({ url: '/organizations/org%2F1/change-role/', method: 'post', data: { user_id: 42, role: 'ADMIN' } });
  });
});
