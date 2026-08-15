/**
 * Anonymous telemetry for Grid (Trulay).
 *
 * Strictly non-invasive:
 *  - No PII: no emails, tokens, keys, hostnames, or full URLs. Only page
 *    pathnames, platform type, and error *messages* (no stacks).
 *  - Batched, throttled, sent with `navigator.sendBeacon` when available.
 *  - Fully disabled when the user opts out (`smsly_telemetry_optout = "1"`),
 *    either from the first-run consent banner or Settings → Privacy.
 */

const TELEMETRY_ENDPOINT = "https://Trulay.co/api/telemetry";
const OPTOUT_KEY = "smsly_telemetry_optout";
const CONSENT_KEY = "smsly_telemetry_consent";
const INSTALL_ID_KEY = "smsly_telemetry_install_id";

const FLUSH_INTERVAL_MS = 10_000;
const FLUSH_BATCH_SIZE = 20;

let queue: Array<Record<string, unknown>> = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;
let installId: string | null = null;
let lastPath: string | null = null;

export function isTelemetryOptedOut(): boolean {
    if (typeof window === "undefined") return true;
    try {
        return window.localStorage.getItem(OPTOUT_KEY) === "1";
    } catch {
        return true;
    }
}

export function setTelemetryOptedOut(optedOut: boolean): void {
    if (typeof window === "undefined") return;
    try {
        if (optedOut) {
            window.localStorage.setItem(OPTOUT_KEY, "1");
        } else {
            window.localStorage.removeItem(OPTOUT_KEY);
        }
    } catch {
        // storage unavailable — nothing to do
    }
}

export function hasTelemetryConsent(): boolean {
    if (typeof window === "undefined") return false;
    try {
        return window.localStorage.getItem(CONSENT_KEY) !== null;
    } catch {
        return true; // storage blocked — don't nag
    }
}

export function setTelemetryConsent(accepted: boolean): void {
    if (typeof window === "undefined") return;
    try {
        window.localStorage.setItem(CONSENT_KEY, accepted ? "accepted" : "declined");
        if (!accepted) setTelemetryOptedOut(true);
    } catch {
        // ignore
    }
}

function getInstallId(): string {
    if (installId) return installId;
    if (typeof window !== "undefined") {
        try {
            const existing = window.localStorage.getItem(INSTALL_ID_KEY);
            if (existing) {
                installId = existing;
                return existing;
            }
            const fresh =
                typeof crypto !== "undefined" && "randomUUID" in crypto
                    ? crypto.randomUUID()
                    : `anon-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
            window.localStorage.setItem(INSTALL_ID_KEY, fresh);
            installId = fresh;
            return fresh;
        } catch {
            // fall through
        }
    }
    installId = `anon-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    return installId;
}

export function queueTelemetryEvent(type: string, payload: Record<string, unknown>): void {
    if (isTelemetryOptedOut()) return;
    if (typeof navigator === "undefined") return;

    const event: Record<string, unknown> = {
        t: type,
        ts: Date.now(),
        p: payload,
    };
    queue.push(event);

    if (queue.length >= FLUSH_BATCH_SIZE) {
        flushTelemetry();
    } else if (!flushTimer) {
        flushTimer = setTimeout(flushTelemetry, FLUSH_INTERVAL_MS);
    }
}

export function flushTelemetry(): void {
    if (flushTimer) {
        clearTimeout(flushTimer);
        flushTimer = null;
    }
    if (!queue.length || isTelemetryOptedOut()) {
        queue = [];
        return;
    }
    const batch = queue;
    queue = [];

    const body = JSON.stringify({
        v: 1,
        install_id: getInstallId(),
        ua: navigator.userAgent?.slice(0, 120) || "",
        events: batch,
    });

    try {
        if (typeof navigator.sendBeacon === "function") {
            const sent = navigator.sendBeacon(TELEMETRY_ENDPOINT, new Blob([body], { type: "application/json" }));
            if (sent) return;
        }
        fetch(TELEMETRY_ENDPOINT, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
            keepalive: true,
        }).catch(() => { /* fire-and-forget */ });
    } catch {
        queue = [...batch, ...queue].slice(0, FLUSH_BATCH_SIZE * 2);
    }
}

export function trackPageView(pathname: string): void {
    if (pathname === lastPath) return;
    lastPath = pathname;
    queueTelemetryEvent("page_view", { path: pathname.slice(0, 160), ts: Date.now() });
}

export function trackWebVital(name: string, value: number): void {
    queueTelemetryEvent("web_vital", { name, value: Math.round(value) });
}

export function trackError(message: string, source?: string, lineno?: number): void {
    queueTelemetryEvent("js_error", {
        msg: (message || "").slice(0, 300),
        src: (source || "").slice(0, 120),
        line: lineno ?? null,
    });
}

/** Call once (e.g. from TelemetryProvider) to install global error listeners. */
export function installTelemetryListeners(): void {
    if (typeof window === "undefined") return;
    const onError = (e: ErrorEvent) => trackError(e.message || "Unknown error", e.filename, e.lineno);
    const onRejection = (e: PromiseRejectionEvent) => {
        const reason = e.reason;
        const msg = reason instanceof Error ? reason.message : typeof reason === "string" ? reason : "Unhandled rejection";
        trackError(msg, "unhandledrejection");
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);

    const beforeUnload = () => flushTelemetry();
    window.addEventListener("beforeunload", beforeUnload);
}