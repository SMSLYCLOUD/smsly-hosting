'use client';

import React from 'react';
import { useTier, PlatformTier } from '@/context/TierContext';
import { UpgradePrompt } from './UpgradePrompt';

interface Props {
  tier: PlatformTier;
  children: React.ReactNode;
  fallback?: React.ReactNode;
  showPrompt?: boolean;
}

export function RequiresTier({ tier, children, fallback, showPrompt = true }: Props) {
  const { license, isLoading } = useTier();

  if (isLoading) {
    return <div className="animate-pulse h-32 bg-muted rounded-lg"></div>;
  }

  const currentTier = license?.tier || 'community';

  const tiers = ['community', 'pro', 'enterprise'];
  const currentLevel = tiers.indexOf(currentTier);
  const requiredLevel = tiers.indexOf(tier);

  if (currentLevel >= requiredLevel) {
    return <>{children}</>;
  }

  if (fallback) {
    return <>{fallback}</>;
  }

  if (showPrompt) {
    return (
        <div className="p-4">
            <UpgradePrompt requiredTier={tier as 'pro'|'enterprise'} />
        </div>
    );
  }

  return null;
}
