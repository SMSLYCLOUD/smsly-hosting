'use client';

import React, { createContext, useContext } from 'react';

export type PlatformTier = 'community' | 'pro' | 'enterprise';

export interface LicenseStatus {
  tier: PlatformTier;
  is_valid: boolean;
  licensed_to?: string;
  expires_at: string | null;
  features: {
    ai_features: boolean;
    autoscaler: boolean;
    custom_domains: boolean;
    ssl_certificates: boolean;
    marketplace: boolean;
    functions: boolean;
    tunnels: boolean;
    topology: boolean;
    transfers: boolean;
    backups_automated: boolean;
    sso: boolean;
    audit_logs: boolean;
    white_label: boolean;
    rbac: boolean;
    multi_node: boolean;
  };
  max_services: number;
  max_team_members: number;
}

interface TierContextType {
  license: LicenseStatus | null;
  isLoading: boolean;
  refreshLicense: () => Promise<void>;
  isCommunity: boolean;
  isPro: boolean;
  isEnterprise: boolean;
}

const TierContext = createContext<TierContextType | undefined>(undefined);

// Owner edition: all features always unlocked. No API call needed.
const OWNER_LICENSE: LicenseStatus = {
  tier: 'enterprise',
  is_valid: true,
  licensed_to: 'Creator Edition',
  expires_at: null,
  features: {
    ai_features: true,
    autoscaler: true,
    custom_domains: true,
    ssl_certificates: true,
    marketplace: true,
    functions: true,
    tunnels: true,
    topology: true,
    transfers: true,
    backups_automated: true,
    sso: true,
    audit_logs: true,
    white_label: true,
    rbac: true,
    multi_node: true,
  },
  max_services: -1,
  max_team_members: -1,
};

export function TierProvider({ children }: { children: React.ReactNode }) {
  const refreshLicense = async () => { /* no-op for owner edition */ };

  return (
    <TierContext.Provider value={{
      license: OWNER_LICENSE,
      isLoading: false,
      refreshLicense,
      isCommunity: false,
      isPro: true,
      isEnterprise: true,
    }}>
      {children}
    </TierContext.Provider>
  );
}

export function useTier() {
  const context = useContext(TierContext);
  if (context === undefined) {
    throw new Error('useTier must be used within a TierProvider');
  }
  return context;
}
