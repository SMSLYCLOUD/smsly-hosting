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

  useEffect(() => {
    if (!updateId || wsToken) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch('/api/v1/auth/session-token/', {
          method: 'POST',
          credentials: 'include',
          headers: { Accept: 'application/json' },
        });
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled && typeof data?.token === 'string') {
          setWsToken(data.token);
        }
      } catch (err) {
        console.error('[UpdateTerminalStream] Failed to fetch session token:', err);
      }
    })();
    return () => { cancelled = true; };
  }, [updateId, wsToken]);

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
      <div className="h-[360px] w-full p-2 bg-[#09090b]">
        {!wsToken ? (
          <div className="h-full w-full flex flex-col items-center justify-center gap-2 text-zinc-400 text-xs font-mono">
            <div className="h-4 w-4 rounded-full border-2 border-emerald-500 border-t-transparent animate-spin" />
            <span>Establishing secure terminal session...</span>
          </div>
        ) : (
          <XtermConsole wsUrl={wsUrl} wsToken={wsToken} />
        )}
      </div>
    </div>
  );
}
