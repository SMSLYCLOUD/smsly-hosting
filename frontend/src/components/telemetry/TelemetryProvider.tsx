"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { useReportWebVitals } from "next/web-vitals";
import { Activity, X } from "lucide-react";
import {
    hasTelemetryConsent,
    installTelemetryListeners,
    isTelemetryOptedOut,
    setTelemetryConsent,
    setTelemetryOptedOut,
    trackPageView,
    trackWebVital,
} from "@/lib/telemetry";

const VITALS: Record<string, string> = {
    "web-vital-fcp": "FCP",
    "web-vital-lcp": "LCP",
    "web-vital-cls": "CLS",
    "web-vital-inp": "INP",
    "web-vital-ttfb": "TTFB",
    FCP: "FCP",
    LCP: "LCP",
    CLS: "CLS",
    INP: "INP",
    TTFB: "TTFB",
};

/**
 * Global telemetry provider:
 *  - collects Web Vitals + page views + JS errors (anonymous, opt-out-able)
 *  - shows the one-time first-use consent banner
 */
export function TelemetryProvider({ children }: { children: React.ReactNode }) {
    const pathname = usePathname();
    const [showConsent, setShowConsent] = useState(false);

    useEffect(() => {
        installTelemetryListeners();
        if (!hasTelemetryConsent()) {
            const timer = setTimeout(() => setShowConsent(true), 2500);
            return () => clearTimeout(timer);
        }
    }, []);

    useEffect(() => {
        trackPageView(pathname || "/");
    }, [pathname]);

    useReportWebVitals((metric) => {
        const name = VITALS[metric.name];
        if (name) trackWebVital(name, metric.value);
    });

    return (
        <>
            {children}
            {showConsent && (
                <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[100] w-[calc(100%-2rem)] max-w-md rounded-xl border border-border bg-card/95 backdrop-blur p-4 shadow-2xl">
                    <div className="flex items-start gap-3">
                        <div className="p-2 rounded-lg bg-primary/10 text-primary flex-shrink-0">
                            <Activity size={16} />
                        </div>
                        <div className="flex-1 min-w-0">
                            <p className="text-sm font-semibold">Help improve Grid</p>
                            <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                                Grid collects anonymous performance and error telemetry to make the
                                platform faster. No personal data, API keys, or secrets — ever.
                                You can opt out anytime in{" "}
                                <a href="/settings?tab=account" className="underline underline-offset-2 hover:text-foreground">
                                    Settings
                                </a>
                                .
                            </p>
                            <div className="flex items-center gap-2 mt-3">
                                <button
                                    type="button"
                                    onClick={() => {
                                        setTelemetryConsent(true);
                                        setTelemetryOptedOut(false);
                                        setShowConsent(false);
                                    }}
                                    className="px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-xs font-semibold hover:opacity-90"
                                >
                                    Accept
                                </button>
                                <button
                                    type="button"
                                    onClick={() => {
                                        setTelemetryConsent(false);
                                        setShowConsent(false);
                                    }}
                                    className="px-3 py-1.5 rounded-md border border-border text-xs font-medium text-muted-foreground hover:text-foreground"
                                >
                                    Decline
                                </button>
                            </div>
                        </div>
                        <button
                            type="button"
                            aria-label="Dismiss"
                            onClick={() => {
                                setTelemetryConsent(true);
                                setShowConsent(false);
                            }}
                            className="text-muted-foreground hover:text-foreground flex-shrink-0"
                        >
                            <X size={14} />
                        </button>
                    </div>
                </div>
            )}
        </>
    );
}

/** Hook for the Settings → Privacy toggle. */
export function useTelemetryOptOut(): [boolean, (v: boolean) => void] {
    const [optedOut, setOptedOut] = useState<boolean>(true);

    useEffect(() => {
        setOptedOut(isTelemetryOptedOut());
    }, []);

    const toggle = (v: boolean) => {
        setTelemetryOptedOut(v);
        setOptedOut(v);
    };

    return [optedOut, toggle];
}