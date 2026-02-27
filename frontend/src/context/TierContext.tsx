'use client';

import React, { createContext, useContext, useEffect, useState } from 'react';
import api from '@/lib/api';

export type PlatformTier = 'community' | 'pro' | 'enterprise';

export interface LicenseStatus {
  tier: PlatformTier;
  is_valid: boolean;
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

export function TierProvider({ children }: { children: React.ReactNode }) {
  const [license, setLicense] = useState<LicenseStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refreshLicense = async () => {
    try {
      const { data } = await api.get('/licensing/status/');
      setLicense(data);
    } catch (error) {
      console.error('Failed to fetch license status:', error);
      // Fallback to community if fetch fails
      setLicense({
        tier: 'community',
        is_valid: false,
        expires_at: null,
        features: {
          ai_features: false,
          autoscaler: false,
          custom_domains: false,
          ssl_certificates: false,
          marketplace: false,
          functions: false,
          tunnels: false,
          topology: false,
          transfers: false,
          backups_automated: false,
          sso: false,
          audit_logs: false,
          white_label: false,
          rbac: false,
          multi_node: false,
        },
        max_services: 3,
        max_team_members: 1,
      });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    refreshLicense();
  }, []);

  const isCommunity = license?.tier === 'community';
  const isPro = license?.tier === 'pro' || license?.tier === 'enterprise';
  const isEnterprise = license?.tier === 'enterprise';

  return (
    <TierContext.Provider value={{ license, isLoading, refreshLicense, isCommunity, isPro, isEnterprise }}>
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
