"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { SmslyCrossSell } from "@/components/dashboard/SmslyCrossSell";

const ADS_DISMISSED_KEY = "smsly_ads_dismissed";

/**
 * Trulay ad banner — renders the rotating cross-sell promo (SmslyCrossSell)
 * until the user dismisses it. Dismissal persists in localStorage and can be
 * re-enabled from Settings → Privacy.
 */
export function TrulayAdBanner() {
    const [dismissed, setDismissed] = useState(false);
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
        try {
            setDismissed(window.localStorage.getItem(ADS_DISMISSED_KEY) === "1");
        } catch {
            // storage unavailable — show the banner
        }
    }, []);

    if (!mounted || dismissed) return null;

    const dismiss = () => {
        setDismissed(true);
        try {
            window.localStorage.setItem(ADS_DISMISSED_KEY, "1");
        } catch {
            // ignore
        }
    };

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

/** Hook for the Settings → Privacy "Show Trulay promos" toggle. */
export function useAdsDismissed(): [boolean, (v: boolean) => void] {
    const [dismissed, setDismissed] = useState<boolean>(() => {
        if (typeof window === "undefined") return true;
        try {
            return window.localStorage.getItem(ADS_DISMISSED_KEY) === "1";
        } catch {
            return true;
        }
    });

    const set = (v: boolean) => {
        setDismissed(v);
        try {
            if (v) {
                window.localStorage.setItem(ADS_DISMISSED_KEY, "1");
            } else {
                window.localStorage.removeItem(ADS_DISMISSED_KEY);
            }
        } catch {
            // ignore
        }
    };

    return [dismissed, set];
}