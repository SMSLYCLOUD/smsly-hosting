/**
 * CLI configuration — persists server URL + API token to ~/.smsly/config.json
 * with url/serverUrl interoperability across Python and Node CLIs.
 */

import { chmodSync, existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';

const CONFIG_DIR = join(homedir(), '.smsly');
const CONFIG_FILE = join(CONFIG_DIR, 'config.json');
const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]']);

export function validateServerUrl(rawUrl, allowInsecureHttp = false) {
    let candidate = String(rawUrl || '').trim().replace(/\/+$/, '');
    if (!candidate) {
        throw new Error('Server URL is required.');
    }
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(?:\/|$))/.test(candidate) && !/^https?:\/\//i.test(candidate)) {
        throw new Error(`Unsupported URL scheme: ${candidate.split(':', 1)[0]}:`);
    }
    if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(candidate)) {
        candidate = `https://${candidate}`;
    }
    let parsed;
    try {
        parsed = new URL(candidate);
    } catch {
        throw new Error(`Invalid server URL: ${rawUrl}`);
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        throw new Error(`Unsupported URL scheme: ${parsed.protocol}`);
    }
    const host = parsed.hostname.toLowerCase();
    const insecureAllowed = Boolean(allowInsecureHttp || process.env?.SMSLY_ALLOW_INSECURE_HTTP === '1');
    if (parsed.protocol === 'http:' && !LOOPBACK_HOSTS.has(host) && !insecureAllowed) {
        throw new Error(
            `Refusing plaintext http:// for non-loopback host "${host}". Use https:// or pass --allow-insecure-http (or set SMSLY_ALLOW_INSECURE_HTTP=1).`
        );
    }
    return candidate.replace(/\/+$/, '');
}

function readRawConfig() {
    if (!existsSync(CONFIG_FILE)) return {};
    try {
        const raw = readFileSync(CONFIG_FILE, 'utf8');
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
        return {};
    }
}

export function getConfig() {
    const store = readRawConfig();
    const rawUrl = String(store.serverUrl || store.url || '').trim().replace(/\/+$/, '');
    const token = String(store.token || '').trim();

    if (!rawUrl || !token) {
        return null;
    }

    const allowInsecureHttp = Boolean(store.allowInsecureHttp ?? store.allow_insecure_http);
    const resolvedUrl = validateServerUrl(rawUrl, allowInsecureHttp);

    return {
        ...store,
        url: resolvedUrl,
        serverUrl: resolvedUrl,
        token,
        allowInsecureHttp,
    };
}

export function setConfig(data) {
    mkdirSync(CONFIG_DIR, { recursive: true, mode: 0o700 });
    try {
        chmodSync(CONFIG_DIR, 0o700);
    } catch {
        // Ignore permission errors on unsupported platforms (e.g., Windows).
    }

    const next = { ...(data || {}) };
    const allowInsecureHttp = Boolean(next.allowInsecureHttp ?? next.allow_insecure_http);
    const rawUrl = String(next.serverUrl || next.url || '').trim().replace(/\/+$/, '');
    if (rawUrl) {
        const resolvedUrl = validateServerUrl(rawUrl, allowInsecureHttp);
        next.url = resolvedUrl;
        next.serverUrl = resolvedUrl;
    }
    next.allowInsecureHttp = allowInsecureHttp;

    writeFileSync(CONFIG_FILE, JSON.stringify(next, null, 2), { mode: 0o600 });
    try {
        chmodSync(dirname(CONFIG_FILE), 0o700);
        chmodSync(CONFIG_FILE, 0o600);
    } catch {
        // Ignore permission errors on unsupported platforms (e.g., Windows).
    }
}

export function clearConfig() {
    try {
        if (existsSync(CONFIG_FILE)) {
            unlinkSync(CONFIG_FILE);
        }
    } catch {
        // Ignore unlink errors.
    }
}
