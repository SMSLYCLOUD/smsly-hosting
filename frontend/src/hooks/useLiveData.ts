'use client';

import { useEffect, useRef, useCallback } from 'react';

/**
 * Universal auto-refresh hook for making any data-fetching component live.
 * Calls `fetchFn` immediately and then every `intervalMs` milliseconds.
 * Automatically pauses when the tab is hidden and resumes when visible.
 * 
 * @param fetchFn - The data-fetching function to call
 * @param intervalMs - Polling interval in milliseconds (default: 5000)
 * @param deps - Dependencies array (re-creates the interval when these change)
 */
export function useLiveData(
    fetchFn: () => Promise<void> | void,
    intervalMs: number = 5000,
    deps: any[] = []
) {
    const intervalRef = useRef<NodeJS.Timeout | null>(null);
    const fetchRef = useRef(fetchFn);

    // Keep fetchRef current without re-creating interval
    useEffect(() => {
        fetchRef.current = fetchFn;
    }, [fetchFn]);

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
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [startPolling, stopPolling, ...deps]);
}
