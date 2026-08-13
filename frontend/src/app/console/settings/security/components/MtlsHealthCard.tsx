// MtlsHealthCard Component
// Shows platform-wide SPIRE/mTLS health status with dual trust domains.

'use client';

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Shield, ShieldCheck, ShieldOff, Server, Wifi, AlertTriangle } from 'lucide-react';
import type { MtlsHealth } from '../types';

interface Props {
  health: MtlsHealth | undefined;
  isLoading: boolean;
}

export function MtlsHealthCard({ health, isLoading }: Props) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5" />
            mTLS Status
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (!health) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-500" />
            mTLS Status Unavailable
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Unable to connect to SPIRE infrastructure.
          </p>
        </CardContent>
      </Card>
    );
  }

  const ecosystemHealthy = health.ecosystem?.spire_server_healthy && health.ecosystem?.spire_agent_healthy;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-emerald-500" />
          mTLS Status
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-4">
          {/* Ecosystem SPIRE */}
          <div className="flex items-center gap-3 p-3 rounded-lg border">
            {ecosystemHealthy ? (
              <ShieldCheck className="h-5 w-5 text-emerald-500" />
            ) : (
              <ShieldOff className="h-5 w-5 text-red-500" />
            )}
            <div>
              <p className="text-sm font-medium">Ecosystem SPIRE</p>
              <p className="text-xs text-muted-foreground">
                Trust domain: {health.ecosystem?.trust_domain || "ecosystem.local"}
              </p>
            </div>
            <Badge variant={ecosystemHealthy ? "default" : "destructive"} className="ml-auto">
              {ecosystemHealthy ? "Healthy" : "Unhealthy"}
            </Badge>
          </div>

          {/* Platform SPIRE */}
          <div className="flex items-center gap-3 p-3 rounded-lg border">
            {health.platform?.spire_server_healthy ? (
              <ShieldCheck className="h-5 w-5 text-emerald-500" />
            ) : (
              <ShieldOff className="h-5 w-5 text-slate-400" />
            )}
            <div>
              <p className="text-sm font-medium">Platform SPIRE</p>
              <p className="text-xs text-muted-foreground">
                Trust domain: {health.platform?.trust_domain || "platform.local"}
              </p>
            </div>
            <Badge variant={health.platform?.spire_server_healthy ? "default" : "secondary"}>
              {health.platform?.spire_server_healthy ? "Healthy" : "N/A"}
            </Badge>
          </div>

          {/* Summary stats */}
          <div className="flex items-center gap-4 text-xs text-muted-foreground col-span-2">
            <span>{health.mtls_enabled_services} / {health.total_services} services with mTLS</span>
            {health.expired_svids > 0 && (
              <span className="flex items-center gap-1 text-amber-600">
                <AlertTriangle className="h-3 w-3" />
                {health.expired_svids} expired SVID{health.expired_svids > 1 ? 's' : ''}
              </span>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
