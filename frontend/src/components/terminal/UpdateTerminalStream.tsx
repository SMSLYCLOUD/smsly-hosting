'use client';

import React, { useEffect, useState } from 'react';
import dynamic from 'next/dynamic';
import { Terminal as TerminalIcon, ShieldCheck } from 'lucide-react';
import { getWsUrl } from '@/lib/websocket';

const XtermConsole = dynamic(() => import('@/components/terminal/XtermConsole'), { ssr: false });

interface UpdateTerminalStreamProps {
  updateId: string;
}

export default function UpdateTerminalStream({ updateId }: UpdateTerminalStreamProps) {
  const [wsToken, setWsToken] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [cookieFallback, setCookieFallback] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!updateId || wsToken || cookieFallback) return;
    let cancelled = false;
    setTokenError(null);
    (async () => {
      try {
        const res = await fetch('/api/v1/auth/session-token/', {
          method: 'POST',
          credentials: 'include',
          headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        });
        if (!res.ok) {
          if (!cancelled) {
            setTokenError(`Terminal session handshake failed (HTTP ${res.status}).`);
          }
          return;
        }
        const data = await res.json();
        if (!cancelled) {
          if (typeof data?.token === 'string' && data.token) {
            setWsToken(data.token);
          } else {
            setTokenError('Terminal session token was not returned by the server.');
          }
        }
      } catch (err) {
        console.error('[UpdateTerminalStream] Failed to fetch session token:', err);
        if (!cancelled) {
          setTokenError('Unable to reach session-token endpoint.');
        }
      }
    })();
    return () => { cancelled = true; };
  }, [updateId, wsToken, cookieFallback, attempt]);

  const wsUrl = getWsUrl(`/ws/platform-updates/${updateId}/`);

  return (
    <div className="mt-4 rounded-lg border border-zinc-800 bg-[#09090b] shadow-xl overflow-hidden animate-in fade-in duration-300">
      <div className="flex items-center justify-between px-4 py-2 bg-zinc-900/90 border-b border-zinc-800/80 text-xs text-zinc-300">
        <div className="flex items-center gap-2 font-medium">
          <TerminalIcon className="h-4 w-4 text-emerald-400" />
          <span>Platform Update Stream</span>
          <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-950/60 border border-emerald-800/50 text-emerald-400 text-[10px]">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
            LIVE
          </span>
        </div>
        <div className="flex items-center gap-3 text-zinc-400 font-mono text-[11px]">
          <span className="hidden sm:inline">Task: {updateId}</span>
          <span className="flex items-center gap-1 text-emerald-400/80">
            <ShieldCheck className="h-3.5 w-3.5" /> Admin Tunnel
          </span>
        </div>
      </div>
      <div role="region" aria-label={`Platform update terminal stream for task ${updateId}`} className="h-[360px] w-full p-2 bg-[#09090b]">
        {wsToken || cookieFallback ? (
          <XtermConsole wsUrl={wsUrl} wsToken={wsToken} ariaLabel={`Platform update terminal stream for task ${updateId}`} />
        ) : tokenError ? (
          <div className="h-full w-full flex flex-col items-center justify-center gap-3 p-4 text-center text-zinc-300 text-xs font-mono">
            <span className="text-red-400">{tokenError}</span>
            <div className="flex flex-wrap items-center justify-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setTokenError(null);
                  setAttempt((n) => n + 1);
                }}
                className="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-sans font-medium"
              >
                Retry
              </button>
              <button
                type="button"
                onClick={() => {
                  setTokenError(null);
                  setCookieFallback(true);
                }}
                className="px-3 py-1.5 rounded border border-zinc-700 hover:bg-zinc-800 text-zinc-300 text-xs font-sans font-medium"
              >
                Connect with Cookie Auth
              </button>
            </div>
          </div>
        ) : (
          <div className="h-full w-full flex flex-col items-center justify-center gap-2 text-zinc-400 text-xs font-mono">
            <div className="h-4 w-4 rounded-full border-2 border-emerald-500 border-t-transparent animate-spin" />
            <span>Establishing secure terminal session...</span>
          </div>
        )}
      </div>
    </div>
  );
}
