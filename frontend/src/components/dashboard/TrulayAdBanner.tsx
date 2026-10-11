"use client";

import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { SmslyCrossSell } from "@/components/dashboard/SmslyCrossSell";

export const ADS_DISMISSED_KEY = "smsly_ads_dismissed";
const ADS_EVENT = "smsly-ads-changed";

function readOptedOut(): boolean {
    if (typeof window === "undefined") return true;
    try {
        return window.localStorage.getItem(ADS_DISMISSED_KEY) === "1";
    } catch {
        return false;
    }
}

/**
 * Trulay ad banner — renders the rotating cross-sell promo (SmslyCrossSell).
 * Three levels: admin kill-switch (server, inside SmslyCrossSell) >
 * permanent opt-out (localStorage, Settings → Privacy) > session dismiss
 * (in-memory X button, reappears on next visit).
 */
export function TrulayAdBanner() {
    const [optedOut] = useAdsDismissed();
    const [sessionDismissed, setSessionDismissed] = useState(false);
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    if (!mounted || optedOut || sessionDismissed) return null;

    return (
        <div className="relative">
            <button
                type="button"
                aria-label="Dismiss ad for this visit"
                title="Dismiss for this visit (reappears next visit; disable permanently in Settings → Privacy)"
                onClick={() => setSessionDismissed(true)}
                className="absolute top-2 right-2 z-10 text-muted-foreground/60 hover:text-foreground transition-colors p-1 rounded-md"
            >
                <X size={14} />
            </button>
            <SmslyCrossSell />
        </div>
    );
}

/**
 * Shared hook for the permanent opt-out (Settings → Privacy toggle).
 * Single source of truth, synced across components and tabs.
 */
export function useAdsDismissed(): [boolean, (v: boolean) => void] {
    const [optedOut, setOptedOutState] = useState<boolean>(readOptedOut);

    useEffect(() => {
        const sync = () => setOptedOutState(readOptedOut());
        window.addEventListener("storage", sync);
        window.addEventListener(ADS_EVENT, sync);
        return () => {
            window.removeEventListener("storage", sync);
            window.removeEventListener(ADS_EVENT, sync);
        };
    }, []);

    const set = useCallback((v: boolean) => {
        setOptedOutState(v);
        try {
            if (v) {
                window.localStorage.setItem(ADS_DISMISSED_KEY, "1");
            } else {
                window.localStorage.removeItem(ADS_DISMISSED_KEY);
            }
        } catch {
            // ignore
        }
        window.dispatchEvent(new Event(ADS_EVENT));
    }, []);

    return [optedOut, set];
}
