"use client";

import { useCallback, useEffect, useState } from "react";
import { X } from "lucide-react";
import { SmslyCrossSell } from "@/components/dashboard/SmslyCrossSell";

export const ADS_DISMISSED_KEY = "smsly_ads_dismissed";
const ADS_EVENT = "smsly-ads-changed";

function readDismissed(): boolean {
    if (typeof window === "undefined") return true;
    try {
        return window.localStorage.getItem(ADS_DISMISSED_KEY) === "1";
    } catch {
        return false;
    }
}

/**
 * Trulay ad banner — renders the rotating cross-sell promo (SmslyCrossSell)
 * until the user dismisses it. Dismissal persists in localStorage and can be
 * re-enabled from Settings → Privacy.
 */
export function TrulayAdBanner() {
    const [dismissed, setDismissed] = useAdsDismissed();
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    const dismiss = useCallback(() => {
        setDismissed(true);
    }, [setDismissed]);

    if (!mounted || dismissed) return null;

    return (
        <div className="relative">
            <button
                type="button"
                aria-label="Dismiss ad"
                onClick={dismiss}
                className="absolute top-2 right-2 z-10 text-muted-foreground/60 hover:text-foreground transition-colors p-1 rounded-md"
            >
                <X size={14} />
            </button>
            <SmslyCrossSell />
        </div>
    );
}

/** Shared hook for the banner + Settings → Privacy "Show Trulay promos" toggle. Single source of truth, synced across components and tabs. */
export function useAdsDismissed(): [boolean, (v: boolean) => void] {
    const [dismissed, setDismissedState] = useState<boolean>(readDismissed);

    useEffect(() => {
        const sync = () => setDismissedState(readDismissed());
        window.addEventListener("storage", sync);
        window.addEventListener(ADS_EVENT, sync);
        return () => {
            window.removeEventListener("storage", sync);
            window.removeEventListener(ADS_EVENT, sync);
        };
    }, []);

    const set = useCallback((v: boolean) => {
        setDismissedState(v);
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

    return [dismissed, set];
}
