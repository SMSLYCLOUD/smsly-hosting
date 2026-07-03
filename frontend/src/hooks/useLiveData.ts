'use client';

import { useEffect, useRef, useCallback } from 'react';

/**
 * Universal auto-refresh hook for making any data-fetching component live.
 * Calls `fetchFn` immediately and then every `intervalMs` milliseconds.
 * Automatically pauses when the tab is hidden and resumes when visible.
 *
 * @param fetchFn   - The data-fetching function to call. Wrap in useCallback to avoid
 *                    unnecessary interval restarts — its latest reference is always used
 *                    via an internal ref so the interval itself is stable.
 * @param intervalMs - Polling interval in milliseconds (default: 5000)
 */
export function useLiveData(
    fetchFn: () => Promise<void> | void,
    intervalMs: number = 5000,
) {
    const intervalRef = useRef<NodeJS.Timeout | null>(null);
    // Always keep a ref to the latest fetchFn so the interval closure never goes stale
    const fetchRef = useRef(fetchFn);
    useEffect(() => {
        fetchRef.current = fetchFn;
    }); // intentionally no deps — always syncs to latest fetchFn without restarting interval

    const startPolling = useCallback(() => {
        if (intervalRef.current) clearInterval(intervalRef.current);
        intervalRef.current = setInterval(() => {
            fetchRef.current();
        }, intervalMs);
    }, [intervalMs]);

    const stopPolling = useCallback(() => {
        if (intervalRef.current) {
            clearInterval(intervalRef.current);
            intervalRef.current = null;
        }
    }, []);

    useEffect(() => {
        // Initial fetch
        fetchRef.current();

        // Start polling
        startPolling();

        // Pause/resume on visibility change
        const handleVisibility = () => {
            if (document.hidden) {
                stopPolling();
            } else {
                fetchRef.current(); // Refresh immediately when tab becomes visible
                startPolling();
            }
        };

        document.addEventListener('visibilitychange', handleVisibility);

        return () => {
            stopPolling();
            document.removeEventListener('visibilitychange', handleVisibility);
        };
    }, [startPolling, stopPolling]); // stable: only re-runs when intervalMs changes
}
