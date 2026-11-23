"use client";

import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { SmslyCrossSell } from "@/components/dashboard/SmslyCrossSell";
import { useAuth } from "@/components/auth-provider";

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
 * Only rendered for authenticated sessions to avoid triggering staff-gated
 * /system/config/ requests on public pages.
 */
export function TrulayAdBanner() {
    const { user, loading } = useAuth();
    const [optedOut] = useAdsDismissed();
    const [sessionDismissed, setSessionDismissed] = useState(false);
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    if (!mounted || loading || !user || optedOut || sessionDismissed) return null;

    return (
        <div className="relative">
            <button
                type="button"
                aria-label="Dismiss ad for this visit"
                title="Dismiss for this visit (reappears next visit; disable permanently in Settings → Privacy)"
                onClick={() => setSessionDismissed(true)}
                className="absolute top-1 right-1 z-10 inline-flex items-center justify-center min-w-[44px] min-h-[44px] text-muted-foreground hover:text-foreground transition-colors rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
                <X size={16} aria-hidden="true" />
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
