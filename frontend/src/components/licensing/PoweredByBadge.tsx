'use client';

import React from 'react';
import { useTier } from '@/context/TierContext';

export function PoweredByBadge() {
  const { isCommunity, isLoading } = useTier();

  if (isLoading || !isCommunity) return null;

  return (
    <div className="fixed bottom-4 left-4 z-40">
      <a
        href="https://Trulay.co"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-2 min-h-[36px] bg-slate-950/90 text-white px-3 py-1.5 rounded-full text-xs font-medium backdrop-blur-sm border border-white/15 hover:bg-slate-900 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
      >
        <span aria-hidden="true">⚡</span>
        <span>Powered by Trulay Grid</span>
      </a>
    </div>
  );
}
