/**
 * HTTP client wrapper for SMSLY Hosting API.
 *
 * Handles token auth headers and JSON responses.
 */

export async function api(serverUrl, token, path, method = 'GET', body = null) {
    const url = `${serverUrl}${path}`;

    const headers = {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
    };

    const opts = { method, headers };
    if (body && method !== 'GET') {
        opts.body = JSON.stringify(body);
    }

    const res = await fetch(url, opts);

    if (!res.ok) {
        let errMsg = `HTTP ${res.status}`;
        try {
            const errBody = await res.json();
            errMsg = errBody.error || errBody.detail || errMsg;
        } catch { /* ignore */ }
        throw new Error(errMsg);
    }

    return res.json();
}
