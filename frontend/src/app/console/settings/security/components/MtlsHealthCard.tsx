// MtlsHealthCard Component
// Shows platform-wide SPIRE/mTLS health status.

'use client';

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Shield, Server, Wifi, AlertTriangle } from 'lucide-react';
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
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            Unable to connect to SPIRE infrastructure.
          </p>
        </CardContent>
      </Card>
    );
  }

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
          {/* SPIRE Server */}
          <div className="flex items-center gap-3">
            <Server className="h-4 w-4" style={{ color: 'var(--text-secondary)' }} />
            <div>
              <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>SPIRE Server</p>
              <Badge variant={health.spire_server_healthy ? 'default' : 'destructive'}>
                {health.spire_server_healthy ? 'Healthy' : 'Unhealthy'}
              </Badge>
            </div>
          </div>

          {/* SPIRE Agent */}
          <div className="flex items-center gap-3">
            <Wifi className="h-4 w-4" style={{ color: 'var(--text-secondary)' }} />
            <div>
              <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>SPIRE Agent</p>
              <Badge variant={health.spire_agent_healthy ? 'default' : 'destructive'}>
                {health.spire_agent_healthy ? 'Healthy' : 'Unhealthy'}
              </Badge>
            </div>
          </div>

          {/* Trust Domain */}
          <div>
            <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>Trust Domain</p>
            <p className="text-sm font-mono">{health.trust_domain}</p>
          </div>

          {/* Services */}
          <div>
            <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>Services with mTLS</p>
            <p className="text-sm font-semibold">
              {health.mtls_enabled_services} / {health.total_services}
            </p>
          </div>

          {/* Expired SVIDs */}
          {health.expired_svids > 0 && (
            <div className="col-span-2">
              <Badge variant="destructive" className="flex items-center gap-1 w-fit">
                <AlertTriangle className="h-3 w-3" />
                {health.expired_svids} expired SVID{health.expired_svids > 1 ? 's' : ''}
              </Badge>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
