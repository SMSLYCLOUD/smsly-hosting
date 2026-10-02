"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { getWsUrl } from "@/lib/websocket";
import { TerminalSquare, ShieldAlert } from "lucide-react";

const XtermConsole = dynamic(() => import("@/components/terminal/XtermConsole"), { ssr: false });

export default function AddonConsole({ addonId, disabled }: { addonId: string; disabled?: boolean }) {
    const [wsToken, setWsToken] = useState<string | null>(null);

    useEffect(() => {
        if (disabled || wsToken) return;
        let cancelled = false;
        (async () => {
            try {
                const res = await fetch("/api/v1/auth/session-token/", {
                    method: "POST",
                    credentials: "include",
                    headers: { Accept: "application/json" },
                });
                if (!res.ok) return;
                const data = await res.json();
                if (!cancelled && typeof data?.token === "string") {
                    setWsToken(data.token);
                }
            } catch {
            }
        })();
        return () => { cancelled = true; };
    }, [disabled, wsToken]);

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
            <div className="h-[500px] bg-zinc-950 rounded-xl overflow-hidden border border-border shadow-2xl">
                {wsToken ? (
                    <XtermConsole wsUrl={getWsUrl(`/ws/addon-terminal/${addonId}/`)} wsToken={wsToken} />
                ) : (
                    <div className="h-full w-full flex items-center justify-center text-zinc-400 text-sm">
                        Preparing console session…
                    </div>
                )}
            </div>
        </div>
    );
}
