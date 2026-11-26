"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { getWsUrl } from "@/lib/websocket";
import { TerminalSquare, ShieldAlert } from "lucide-react";

const XtermConsole = dynamic(() => import("@/components/terminal/XtermConsole"), { ssr: false });

export default function AddonConsole({ addonId, disabled }: { addonId: string; disabled?: boolean }) {
    const [wsToken, setWsToken] = useState<string | null>(null);
    const [tokenError, setTokenError] = useState<string | null>(null);
    const [cookieFallback, setCookieFallback] = useState(false);
    const [attempt, setAttempt] = useState(0);

    useEffect(() => {
        if (disabled || wsToken || cookieFallback) return;
        let cancelled = false;
        setTokenError(null);
        (async () => {
            try {
                const res = await fetch("/api/v1/auth/session-token/", {
                    method: "POST",
                    credentials: "include",
                    headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
                });
                if (!res.ok) {
                    if (!cancelled) {
                        setTokenError(`Console session handshake failed (HTTP ${res.status}).`);
                    }
                    return;
                }
                const data = await res.json();
                if (!cancelled) {
                    if (typeof data?.token === "string" && data.token) {
                        setWsToken(data.token);
                    } else {
                        setTokenError("Console session token was not returned by the server.");
                    }
                }
            } catch {
                if (!cancelled) {
                    setTokenError("Unable to reach session-token endpoint.");
                }
            }
        })();
        return () => { cancelled = true; };
    }, [disabled, wsToken, cookieFallback, attempt]);

    if (disabled) {
        return (
            <div className="h-[400px] bg-zinc-950 rounded-xl overflow-hidden border border-border flex flex-col items-center justify-center gap-2 text-zinc-400 text-sm">
                <ShieldAlert size={20} />
                Console is available while the addon container is running.
            </div>
        );
    }

    return (
        <div className="space-y-2">
            <p className="text-xs text-muted-foreground flex items-center gap-1.5">
                <TerminalSquare size={12} />
                Sandboxed shell inside the addon container. Sessions are audit-logged and idle sockets disconnect automatically.
            </p>
            <div role="region" aria-label={`Addon ${addonId} interactive console`} className="h-[500px] bg-zinc-950 rounded-xl overflow-hidden border border-border shadow-2xl">
                {wsToken || cookieFallback ? (
                    <XtermConsole wsUrl={getWsUrl(`/ws/addon-terminal/${addonId}/`)} wsToken={wsToken} ariaLabel={`Addon ${addonId} interactive console`} />
                ) : tokenError ? (
                    <div className="h-full w-full flex flex-col items-center justify-center gap-3 p-6 text-center text-zinc-300 text-sm">
                        <p className="text-red-400 font-medium">{tokenError}</p>
                        <div className="flex flex-wrap items-center justify-center gap-2">
                            <button
                                type="button"
                                onClick={() => {
                                    setTokenError(null);
                                    setAttempt((n) => n + 1);
                                }}
                                className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-medium"
                            >
                                Retry
                            </button>
                            <button
                                type="button"
                                onClick={() => {
                                    setTokenError(null);
                                    setCookieFallback(true);
                                }}
                                className="px-3 py-1.5 rounded border border-zinc-700 hover:bg-zinc-800 text-zinc-300 text-xs font-medium"
                            >
                                Connect with Cookie Auth
                            </button>
                        </div>
                    </div>
                ) : (
                    <div className="h-full w-full flex items-center justify-center text-zinc-400 text-sm">
                        Preparing console session…
                    </div>
                )}
            </div>
        </div>
    );
}
