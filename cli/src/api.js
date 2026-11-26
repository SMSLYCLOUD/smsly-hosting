/**
 * HTTP client wrapper for SMSLY Hosting API.
 *
 * Handles token auth headers and JSON responses.
 */

import { validateServerUrl } from './config.js';

export function validateApiPath(path) {
    const str = String(path || '');
    if (!str.startsWith('/api/') || str.startsWith('//')) {
        throw new Error(`Invalid API path "${str}": must start with "/api/" and not "//".`);
    }
    return str;
}

export function validateNextPath(nextUrl, serverUrl) {
    const base = new URL(serverUrl);
    const parsed = new URL(String(nextUrl || ''), base);
    if (parsed.host !== base.host) {
        throw new Error(`Refusing cross-origin pagination URL: ${nextUrl}`);
    }
    return validateApiPath(`${parsed.pathname}${parsed.search}`);
}

export async function api(serverUrl, token, path, method = 'GET', body = null, options = undefined) {
    const rawBaseUrl = String(serverUrl || '').trim().replace(/\/+$/, '');
    if (!rawBaseUrl) {
        throw new Error('Server URL is missing. Run: smsly login <url>');
    }
    const baseUrl = validateServerUrl(rawBaseUrl, Boolean(options?.allowInsecureHttp));
    const safePath = validateApiPath(path);
    const rawToken = String(token || '').trim();
    if (!rawToken) {
        throw new Error('API token is missing or empty. Run: smsly login <url>');
    }

    const url = `${baseUrl}${safePath}`;
    const authHeader = /^(Bearer|Token)\s+/i.test(rawToken)
        ? rawToken
        : (rawToken.startsWith('smsly_') ||
            rawToken.startsWith('sk_') ||
            (rawToken.startsWith('eyJ') && rawToken.split('.').length === 3)
            ? `Bearer ${rawToken}`
            : `Token ${rawToken}`);

    const headers = {
        'Authorization': authHeader,
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
    };

    const opts = { method, headers };
    if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
        opts.signal = AbortSignal.timeout(60_000);
    }
    if (body && method !== 'GET') {
        opts.body = JSON.stringify(body);
    }

    const res = await fetch(url, opts);

    if (!res.ok) {
        let errMsg = `HTTP ${res.status}`;
        try {
            if (typeof res.text === 'function') {
                const rawErr = await res.text();
                if (rawErr && rawErr.trim()) {
                    const errBody = JSON.parse(rawErr);
                    errMsg = errBody.error || errBody.detail || errMsg;
                }
            } else {
                const errBody = await res.json();
                errMsg = errBody.error || errBody.detail || errMsg;
            }
        } catch { /* ignore */ }
        const err = new Error(errMsg);
        err.status = res.status;
        throw err;
    }

    if (res.status === 204) return null;
    if (typeof res.text === 'function') {
        const text = await res.text();
        if (!text || !text.trim()) return null;
        try {
            return JSON.parse(text);
        } catch {
            return null;
        }
    }
    return res.json();
}
