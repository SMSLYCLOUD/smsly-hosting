'use client';

import React from 'react';
import { useTier } from '@/context/TierContext';

export function PoweredByBadge() {
  const { isCommunity, isLoading } = useTier();

  if (isLoading || !isCommunity) return null;

  return (
    <div className="fixed bottom-4 right-4 z-50">
      <a
        href="https://trulay.co"
        target="_blank"
        rel="noopener noreferrer"
        className="bg-black/80 text-white px-3 py-1.5 rounded-full text-xs font-medium backdrop-blur-sm border border-white/10 hover:bg-black transition-colors flex items-center gap-2 shadow-lg"
      >
        <span>⚡ Powered by SMSLY Hosting</span>
      </a>
    </div>
  );
}
