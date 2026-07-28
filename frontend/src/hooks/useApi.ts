'use client';

import { useState, useEffect, useCallback } from 'react';

/**
 * Generic data-fetching hook with optional polling.
 *
 * Usage:
 *   const { data, error, loading, refetch } = useApi<T>('/api/v1/services/', {
 *     refreshInterval: 10_000,
 *   });
 *
 * - Pass `null` as url to skip fetching (useful for conditional fetches).
 * - `refetch` can be called imperatively to force a refresh.
 * - When `refreshInterval` is set, the hook polls that endpoint.
 *   Polling pauses when the tab is hidden and resumes on focus.
 */

interface UseApiOptions {
  /** Polling interval in ms. Omit or pass 0 to disable. */
  refreshInterval?: number;
  /** Whether to include credentials (cookies). Default true. */
  credentials?: RequestCredentials;
}

interface UseApiResult<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  refetch: () => Promise<void>;
}

export function useApi<T>(url: string | null, options?: UseApiOptions): UseApiResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    if (!url) {
      setLoading(false);
      return;
    }
    try {
      const response = await fetch(url, {
        credentials: options?.credentials ?? 'include',
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const json: T = await response.json();
      setData(json);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setLoading(false);
    }
  }, [url, options?.credentials]);

  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      if (cancelled) return;
      await fetchData();
    };

    run();

    // Polling
    const interval = options?.refreshInterval
      ? setInterval(run, options.refreshInterval)
      : undefined;

    // Pause when hidden
    const handleVisibility = () => {
      if (document.hidden || !interval) return;
      // Refresh immediately when tab becomes visible again
      if (!cancelled) run();
    };

    document.addEventListener('visibilitychange', handleVisibility);

    return () => {
      cancelled = true;
      if (interval) clearInterval(interval);
      document.removeEventListener('visibilitychange', handleVisibility);
    };
  }, [fetchData, options?.refreshInterval]);

  return { data, error, loading, refetch: fetchData };
}
