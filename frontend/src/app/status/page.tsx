'use client';

import React, { useEffect, useState } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { CheckCircle2, XCircle, Loader2, Activity, Server, Database, Globe, Wifi } from 'lucide-react';
import api from '@/lib/api';

interface ServiceHealth {
  name: string;
  status: 'operational' | 'degraded' | 'down' | 'unknown';
  latency_ms?: number;
}

export default function StatusPage() {
  const [checks, setChecks] = useState<ServiceHealth[]>([]);
  const [loading, setLoading] = useState(true);
  const [overallStatus, setOverallStatus] = useState<string>('Checking...');

  useEffect(() => {
    const checkHealth = async () => {
      const results: ServiceHealth[] = [];

      // Check API health
      try {
        const start = Date.now();
        await api.get('/health/');
        results.push({ name: 'API Server', status: 'operational', latency_ms: Date.now() - start });
      } catch {
        try {
          const start = Date.now();
          await api.get('/services/');
          results.push({ name: 'API Server', status: 'operational', latency_ms: Date.now() - start });
        } catch {
          results.push({ name: 'API Server', status: 'down' });
        }
      }

      // Check billing
      try {
        const start = Date.now();
        await api.get('/billing/summary/');
        results.push({ name: 'Billing Service', status: 'operational', latency_ms: Date.now() - start });
      } catch (err: any) {
        results.push({
          name: 'Billing Service',
          status: err?.response?.status ? 'operational' : 'down',
          latency_ms: undefined,
        });
      }

      // Check templates
      try {
        const start = Date.now();
        await api.get('/templates/');
        results.push({ name: 'Template Registry', status: 'operational', latency_ms: Date.now() - start });
      } catch {
        results.push({ name: 'Template Registry', status: 'degraded' });
      }

      setChecks(results);
      const anyDown = results.some(r => r.status === 'down');
      const anyDegraded = results.some(r => r.status === 'degraded');
      setOverallStatus(anyDown ? 'Partial Outage' : anyDegraded ? 'Degraded Performance' : 'All Systems Operational');
      setLoading(false);
    };

    checkHealth();
    const interval = setInterval(checkHealth, 30000);
    return () => clearInterval(interval);
  }, []);

  const statusIcon = (s: string) => {
    if (s === 'operational') return <CheckCircle2 className="h-5 w-5 text-emerald-500" />;
    if (s === 'degraded') return <Activity className="h-5 w-5 text-yellow-500" />;
    if (s === 'down') return <XCircle className="h-5 w-5 text-red-500" />;
    return <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />;
  };

  const statusColor = (s: string) => {
    if (s === 'operational') return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20';
    if (s === 'degraded') return 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20';
    if (s === 'down') return 'bg-red-500/10 text-red-500 border-red-500/20';
    return 'bg-muted text-muted-foreground';
  };

  return (
    <DashboardShell>
      <div className="container max-w-4xl mx-auto p-6 space-y-6">
        <div>
          <h1 className="text-3xl font-bold">System Status</h1>
          <p className="text-muted-foreground">Real-time health checks for SMSLY Hosting services.</p>
        </div>

        <Card className={loading ? '' : overallStatus.includes('Operational') ? 'border-emerald-500/30' : 'border-yellow-500/30'}>
          <CardContent className="py-6 text-center">
            {loading ? (
              <div className="flex items-center justify-center gap-2">
                <Loader2 className="h-5 w-5 animate-spin" />
                <span className="text-muted-foreground">Running health checks...</span>
              </div>
            ) : (
              <div className="flex items-center justify-center gap-3">
                {overallStatus.includes('Operational') ? (
                  <CheckCircle2 className="h-6 w-6 text-emerald-500" />
                ) : (
                  <Activity className="h-6 w-6 text-yellow-500" />
                )}
                <span className="text-lg font-semibold">{overallStatus}</span>
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-3">
          {loading ? (
            Array.from({ length: 3 }).map((_, i) => (
              <Card key={i}>
                <CardContent className="py-4 flex items-center justify-between">
                  <div className="h-4 w-32 bg-muted animate-pulse rounded" />
                  <div className="h-4 w-20 bg-muted animate-pulse rounded" />
                </CardContent>
              </Card>
            ))
          ) : (
            checks.map((check) => (
              <Card key={check.name}>
                <CardContent className="py-4 flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    {statusIcon(check.status)}
                    <span className="font-medium">{check.name}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    {check.latency_ms !== undefined && (
                      <span className="text-xs text-muted-foreground font-mono">{check.latency_ms}ms</span>
                    )}
                    <Badge variant="outline" className={statusColor(check.status)}>
                      {check.status}
                    </Badge>
                  </div>
                </CardContent>
              </Card>
            ))
          )}
        </div>

        <p className="text-xs text-muted-foreground text-center">
          Auto-refreshes every 30 seconds.
        </p>
      </div>
    </DashboardShell>
  );
}
