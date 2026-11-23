'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { systemApi } from '@/lib/api';
import { useAuth } from '@/components/auth-provider';
import Link from 'next/link';
import {
  Server, Loader2, CheckCircle2, AlertTriangle, RefreshCw, Shield, Wrench, Pencil, Database, Cpu,
} from 'lucide-react';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { useState, useEffect, useCallback, useRef } from 'react';

const SERVICE_GROUPS = [
  { label: 'Core', keys: ['backend', 'frontend', 'celery', 'celery-beat', 'celery-fast', 'celery-deploy'] },
  { label: 'Database', keys: ['db', 'db-replica', 'postgres-primary', 'postgres-replica', 'pgcat', 'pgcat-tenants', 'shared-postgres', 'shared-postgres-replica', 'pgbouncer', 'pgbouncer-readonly'] },
  { label: 'Redis HA', keys: ['redis', 'redis-primary', 'redis-replica', 'redis-sentinel-1', 'redis-sentinel-2', 'redis-sentinel-3'] },
  { label: 'Queue', keys: ['rabbitmq'] },
  { label: 'Proxy', keys: ['traefik', 'caddy', 'route-fallback', 'socket-proxy', 'frps'] },
  { label: 'Observability', keys: ['grafana', 'loki', 'promtail', 'prometheus', 'alertmanager', 'cadvisor', 'node-exporter', 'loki-log-bridge'] },
  { label: 'Security', keys: ['crowdsec', 'cloudflare-bouncer', 'smsly-falco', 'infisical', 'spire-server', 'spire-agent', 'spire-server-ecosystem', 'spire-agent-ecosystem', 'appsec-agent', 'appsec-envoy', 'appsec-db', 'appsec-smartsync', 'appsec-tuning-svc', 'appsec-shared-storage'] },
  { label: 'Registry & Build', keys: ['registry', 'docker-mirror', 'verdaccio', 'buildkit'] },
  { label: 'Other', keys: ['apt-cacher', 'docker-labels', 'mcp-server'] },
];

const CORE_REQUIRED_SERVICES = new Set([
  'backend', 'frontend', 'celery', 'celery-beat', 'db', 'postgres-primary', 'redis', 'redis-primary', 'rabbitmq', 'traefik',
  'shared-postgres', 'pgcat-tenants',
]);

const OPTIONAL_SERVICES = new Set([
  'db-replica', 'postgres-replica', 'pgbouncer', 'pgbouncer-readonly', 'pgcat',
  'shared-postgres-replica',
  'redis-replica', 'redis-sentinel-1', 'redis-sentinel-2', 'redis-sentinel-3',
  'celery-fast', 'celery-deploy', 'caddy', 'route-fallback', 'socket-proxy', 'frps',
  'grafana', 'loki', 'promtail', 'prometheus', 'alertmanager', 'cadvisor', 'node-exporter', 'loki-log-bridge',
  'crowdsec', 'cloudflare-bouncer', 'smsly-falco', 'infisical',
  'spire-server', 'spire-agent', 'spire-server-ecosystem', 'spire-agent-ecosystem',
  'appsec-agent', 'appsec-envoy', 'appsec-db', 'appsec-smartsync', 'appsec-tuning-svc', 'appsec-shared-storage',
  'registry', 'docker-mirror', 'verdaccio', 'buildkit',
  'mcp-server',
  'apt-cacher', 'docker-labels',
]);

const HOST_SECURITY_ITEMS = [
  { label: 'UFW Firewall', key: 'ufw' },
  { label: 'fail2ban Intrusion Defense', key: 'fail2ban' },
  { label: 'auditd Audit Logging', key: 'auditd' },
];

interface PublicHealthPayload {
  status: string;
  version?: string;
  uptime_seconds?: number;
  database?: string;
  cache?: string;
  deployments_total?: number;
}

interface SystemConfigPayload {
  services?: Record<string, { running: boolean; status: string }>;
  host_security?: Record<string, { installed: boolean; active: boolean }>;
  beat?: {
    scheduler?: string;
    lock_timeout?: number;
    lock_ttl?: number | null;
    healthy?: boolean | null;
  };
  edge_warnings?: Array<{
    code?: string;
    severity?: string;
    message?: string;
    detail?: string;
    since?: string;
  }>;
  cpu_percent?: number;
  ram_percent?: number;
  disk_percent?: number;
  STORAGE_USED_PERCENT?: number;
  uptime_seconds?: number;
  load_avg?: number[];
  ram_used_mb?: number;
  ram_total_mb?: number;
  disk_used_gb?: number;
  disk_total_gb?: number;
  STORAGE_USED_GB?: number;
  STORAGE_TOTAL_GB?: number;
}

function statusMetaForPercent(pct: number) {
  if (pct >= 85) return { color: 'text-red-700 dark:text-red-400', label: 'High' };
  if (pct >= 60) return { color: 'text-amber-700 dark:text-amber-400', label: 'Elevated' };
  return { color: 'text-emerald-700 dark:text-emerald-400', label: 'Normal' };
}

function MetricCard({
  label,
  value,
  subtext,
  color,
  statusLabel,
}: {
  label: string;
  value: string;
  subtext: string;
  color: string;
  statusLabel?: string;
}) {
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{label}</p>
            {statusLabel && (
              <span className={`text-xs font-semibold ${color}`}>({statusLabel})</span>
            )}
          </div>
          <p className={`text-2xl font-bold mt-1 tabular-nums ${color}`}>{value}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{subtext}</p>
        </div>
        <Server className="w-7 h-7 opacity-30 shrink-0" aria-hidden="true" />
      </div>
    </Card>
  );
}

function formatUptime(seconds: number) {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  return { days, detail: `${hours}h ${mins}m` };
}

export default function StatusPage() {
  const { user, loading: authLoading } = useAuth();
  const { toast } = useToast();
  const confirm = useConfirm();

  const [systemConfig, setSystemConfig] = useState<SystemConfigPayload | null>(null);
  const [publicHealth, setPublicHealth] = useState<PublicHealthPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [pollError, setPollError] = useState('');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const [healing, setHealing] = useState(false);
  const [fbOpen, setFbOpen] = useState(false);
  const [fbTab, setFbTab] = useState<'index.html' | 'disabled.html'>('index.html');
  const [fbPages, setFbPages] = useState<Record<string, string>>({});
  const [fbLoading, setFbLoading] = useState(false);
  const [fbSaving, setFbSaving] = useState(false);

  const fetchStatus = useCallback(
    async (silent = false) => {
      try {
        if (!silent) setLoading(true);
        else setRefreshing(true);
        setPollError('');

        // Always fetch public health summary via direct fetch to avoid 401 logout side-effects.
        const healthRes = await fetch('/api/v1/system/health/', {
          credentials: 'include',
          cache: 'no-store',
        });
        if (healthRes.ok || healthRes.status === 503) {
          const healthJson = (await healthRes.json().catch(() => null)) as PublicHealthPayload | null;
          if (healthJson) setPublicHealth(healthJson);
        }

        // Only query staff-level detailed config when authenticated.
        if (user) {
          const cfgRes = await fetch('/api/v1/system/config/', {
            credentials: 'include',
            cache: 'no-store',
          });
          if (cfgRes.ok) {
            const cfg = await cfgRes.json();
            if (cfg && typeof cfg === 'object' && ('services' in cfg || 'cpu_percent' in cfg)) {
              setSystemConfig(cfg);
            }
          }
        } else {
          setSystemConfig(null);
        }

        setLastUpdated(new Date());
      } catch {
        setPollError('Refresh failed — showing most recent health snapshot.');
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [user],
  );

  useEffect(() => {
    if (!authLoading) {
      fetchStatus(false);
    }
  }, [authLoading, fetchStatus]);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (authLoading) return;
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(() => fetchStatus(true), 30000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [authLoading, fetchStatus]);

  const openFbEditor = async () => {
    setFbOpen(true);
    setFbLoading(true);
    try {
      const res = await systemApi.routeFallback();
      setFbPages(res.pages || {});
    } catch (err: unknown) {
      const eObj = err as { response?: { status?: number; data?: { error?: string } }; message?: string };
      toast({
        title: 'Failed to load fallback pages',
        description: eObj?.response?.status === 403 ? 'Admin access required.' : eObj?.response?.data?.error || eObj?.message,
        variant: 'destructive',
      });
      setFbOpen(false);
    } finally {
      setFbLoading(false);
    }
  };

  const saveFbEditor = async () => {
    setFbSaving(true);
    try {
      const res = await systemApi.updateRouteFallback({ [fbTab]: fbPages[fbTab] || '' });
      toast({ title: 'Fallback page saved', description: `Live on the edge now: ${(res.saved || []).join(', ')}.` });
      setFbOpen(false);
    } catch (err: unknown) {
      const eObj = err as { response?: { data?: { error?: string } }; message?: string };
      toast({ title: 'Save failed', description: eObj?.response?.data?.error || eObj?.message, variant: 'destructive' });
    } finally {
      setFbSaving(false);
    }
  };

  const handleBeatHeal = async () => {
    if (
      !(await confirm({
        title: 'Heal beat scheduler?',
        message: 'Releases a stale redbeat lock and restarts the beat container. The backend refuses if the lock looks live. Continue?',
        confirmText: 'Heal Beat',
        variant: 'destructive',
      }))
    ) {
      return;
    }
    setHealing(true);
    try {
      const res = await systemApi.beatHeal();
      toast({ title: 'Beat healed', description: (res.actions || []).join('; ') || res.message });
      fetchStatus(true);
    } catch (err: unknown) {
      const eObj = err as { response?: { data?: { error?: string } }; message?: string };
      toast({ title: 'Heal refused or failed', description: eObj?.response?.data?.error || eObj?.message, variant: 'destructive' });
    } finally {
      setHealing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20" role="status" aria-label="Loading system status">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" aria-hidden="true" />
      </div>
    );
  }

  if (!systemConfig && !publicHealth) {
    return (
      <div className="container mx-auto max-w-6xl px-4 py-12">
        <div role="alert" className="flex flex-col items-center justify-center py-16 gap-4 rounded-2xl border border-border bg-card">
          <p className="text-muted-foreground font-medium">Unable to reach platform health endpoint.</p>
          <Button type="button" variant="outline" onClick={() => fetchStatus(false)} className="min-h-[44px]">
            <RefreshCw className="w-4 h-4 mr-2" aria-hidden="true" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  // Compute health banner state from detailed config if available, else public health
  const services = systemConfig?.services as Record<string, { running: boolean; status: string }> | undefined;
  const hostSecurity = systemConfig?.host_security as Record<string, { installed: boolean; active: boolean }> | undefined;
  const beat = systemConfig?.beat as
    | { scheduler?: string; lock_timeout?: number; lock_ttl?: number | null; healthy?: boolean | null }
    | undefined;
  const beatService = services?.['celery-beat'];
  const fallbackService = services?.['route-fallback'];

  const coreOffline = services
    ? Array.from(CORE_REQUIRED_SERVICES).filter((s) => !services[s] || !services[s].running)
    : [];
  const isHealthy = systemConfig ? coreOffline.length === 0 : publicHealth?.status === 'healthy';

  const edgeWarnings: Array<{ code?: string; severity?: string; message?: string; detail?: string; since?: string }> =
    Array.isArray(systemConfig?.edge_warnings) ? systemConfig.edge_warnings : [];

  const cpu = Number(systemConfig?.cpu_percent || 0);
  const ram = Number(systemConfig?.ram_percent || 0);
  const disk = Number(systemConfig?.disk_percent || systemConfig?.STORAGE_USED_PERCENT || 0);
  const uptimeSeconds = Number(systemConfig?.uptime_seconds ?? publicHealth?.uptime_seconds ?? 0);
  const uptimeFormatted = formatUptime(uptimeSeconds);

  return (
    <div className="container mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold tracking-tight text-foreground">
            System Status &amp; Platform Health
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            {systemConfig
              ? 'Live telemetry, service availability, and host security probes across the cluster.'
              : 'Public availability and core dependency status for this Grid instance.'}
            {lastUpdated && (
              <span className="ml-2 tabular-nums">
                · Last checked {lastUpdated.toLocaleTimeString()}
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => fetchStatus(true)}
            disabled={refreshing}
            className="min-h-[44px]"
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${refreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
            Refresh
          </Button>
          {!user && (
            <Button asChild variant="default" className="min-h-[44px]">
              <Link href="/login">Operator Sign In</Link>
            </Button>
          )}
        </div>
      </div>

      {pollError && (
        <div role="alert" className="p-3.5 rounded-xl border border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300 text-sm flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
          <span>{pollError}</span>
        </div>
      )}

      {/* Overall System Health Banner */}
      <div
        role="status"
        aria-live="polite"
        className={`p-4 rounded-xl border flex flex-wrap items-center justify-between gap-3 ${
          isHealthy
            ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-800 dark:text-emerald-300'
            : 'bg-red-500/10 border-red-500/30 text-red-800 dark:text-red-300'
        }`}
      >
        <div className="flex items-center gap-3 min-w-0">
          {isHealthy ? (
            <CheckCircle2 className="w-5 h-5 text-emerald-700 dark:text-emerald-400 shrink-0" aria-hidden="true" />
          ) : (
            <AlertTriangle className="w-5 h-5 text-red-700 dark:text-red-400 shrink-0" aria-hidden="true" />
          )}
          <div className="min-w-0">
            <div className="font-semibold text-sm">
              {isHealthy
                ? 'All Core Systems Operational'
                : systemConfig
                ? `Platform Alert: ${coreOffline.length} Core Service(s) Offline`
                : 'Platform Degraded or Dependency Unavailable'}
            </div>
            <div className="text-xs opacity-90">
              {isHealthy
                ? 'Primary database, Redis cache, task scheduler, and ingress gateway are responsive.'
                : systemConfig
                ? `Offline core components: ${coreOffline.join(', ')}`
                : 'One or more platform dependencies reported an unhealthy status.'}
            </div>
          </div>
        </div>
        <Badge variant={isHealthy ? 'default' : 'destructive'} className="text-xs shrink-0">
          {isHealthy ? 'Operational' : 'Degraded'}
        </Badge>
      </div>

      {/* Public Health Summary (always rendered cleanly for visitors & non-admin users) */}
      {publicHealth && (
        <section aria-labelledby="public-health-heading" className="space-y-4">
          <h2 id="public-health-heading" className="text-lg font-bold text-foreground">
            Core Service Availability
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <Card className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Database</p>
                  <p className="text-lg font-bold mt-1 capitalize text-foreground">{publicHealth.database || 'Unknown'}</p>
                </div>
                <Database className="w-6 h-6 text-emerald-700 dark:text-emerald-400" aria-hidden="true" />
              </div>
            </Card>
            <Card className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Cache / Broker</p>
                  <p className="text-lg font-bold mt-1 capitalize text-foreground">{publicHealth.cache || 'Unknown'}</p>
                </div>
                <Cpu className="w-6 h-6 text-emerald-700 dark:text-emerald-400" aria-hidden="true" />
              </div>
            </Card>
            <Card className="p-4">
              <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Control Plane Uptime</p>
              <p className="text-lg font-bold mt-1 tabular-nums text-foreground">
                {uptimeSeconds ? `${uptimeFormatted.days}d ${uptimeFormatted.detail}` : '--'}
              </p>
            </Card>
            <Card className="p-4">
              <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Platform Version</p>
              <p className="text-lg font-bold mt-1 tabular-nums text-foreground">
                {publicHealth.version || '2.0.0'}
              </p>
            </Card>
          </div>
        </section>
      )}

      {/* Detailed Staff Telemetry Sections */}
      {systemConfig && (
        <>
          {edgeWarnings.map((warning, index) => (
            <div
              key={`${warning.code || 'edge'}-${index}`}
              role="alert"
              className="p-4 rounded-xl border flex items-start gap-3 bg-amber-500/10 border-amber-500/30 text-amber-800 dark:text-amber-300"
            >
              <AlertTriangle className="w-5 h-5 text-amber-700 dark:text-amber-400 shrink-0 mt-0.5" aria-hidden="true" />
              <div className="min-w-0">
                <div className="font-semibold text-sm">
                  Edge Warning: {warning.message || 'Edge configuration issue detected.'}
                </div>
                {warning.detail && <div className="mt-1 break-all font-mono text-xs">{warning.detail}</div>}
                {warning.since && <div className="mt-1 text-xs">Since: {warning.since}</div>}
              </div>
            </div>
          ))}

          {(beat !== undefined || beatService || fallbackService) && (
            <section aria-labelledby="scheduler-heading" className="space-y-4">
              <h2 id="scheduler-heading" className="text-lg font-bold text-foreground">
                Scheduler &amp; Edge Fallback
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Card className="border-border/60">
                  <CardContent className="p-4 flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className="text-xs text-muted-foreground font-semibold uppercase tracking-wide">Beat Scheduler</h3>
                      <p className="text-sm font-semibold mt-1 tabular-nums">
                        {beatService && !beatService.running ? (
                          <span className="text-red-700 dark:text-red-400">Beat container offline</span>
                        ) : beat?.lock_ttl === undefined || beat?.lock_ttl === null ? (
                          <span className="text-muted-foreground">Lock state unknown</span>
                        ) : beat.lock_ttl === -2 ? (
                          <span className="text-amber-700 dark:text-amber-400">No lock held — starting or wedged</span>
                        ) : beat.lock_ttl === -1 ? (
                          <span className="text-amber-700 dark:text-amber-400">Persistent lock (unexpected)</span>
                        ) : (
                          <span className={beat.healthy ? 'text-emerald-700 dark:text-emerald-400' : 'text-amber-700 dark:text-amber-400'}>
                            Lock TTL {beat.lock_ttl}s / {beat.lock_timeout ?? 600}s
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-muted-foreground mt-1">
                        redbeat scheduler — retention, watchdog and recovery tasks dispatch through this lock.
                      </p>
                      {(!beat?.healthy || (beatService && !beatService.running)) && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="mt-2 min-h-[44px]"
                          disabled={healing}
                          onClick={handleBeatHeal}
                        >
                          {healing ? (
                            <Loader2 className="w-4 h-4 mr-1.5 animate-spin" aria-hidden="true" />
                          ) : (
                            <Wrench className="w-4 h-4 mr-1.5" aria-hidden="true" />
                          )}
                          {healing ? 'Healing…' : 'Heal Beat'}
                        </Button>
                      )}
                    </div>
                    <Badge
                      variant={beatService && !beatService.running ? 'destructive' : beat?.healthy ? 'default' : 'secondary'}
                      className="text-xs shrink-0"
                    >
                      {beatService && !beatService.running ? 'Offline' : beat?.healthy ? 'Dispatching' : 'Check'}
                    </Badge>
                  </CardContent>
                </Card>

                {fallbackService && (
                  <Card className="border-border/60">
                    <CardContent className="p-4 flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <h3 className="text-xs text-muted-foreground font-semibold uppercase tracking-wide">Edge Fallback Page</h3>
                        <p className="text-sm font-semibold mt-1 truncate" title={fallbackService.status}>
                          {fallbackService.running ? (
                            <span className="text-emerald-700 dark:text-emerald-400">Serving 503 fallback</span>
                          ) : (
                            <span className="text-red-700 dark:text-red-400">Fallback container offline</span>
                          )}
                        </p>
                        <p className="text-xs text-muted-foreground mt-1 truncate" title={fallbackService.status}>
                          {fallbackService.status || 'route-fallback'}
                        </p>
                        <Button type="button" variant="outline" size="sm" className="mt-2 min-h-[44px]" onClick={openFbEditor}>
                          <Pencil className="w-3.5 h-3.5 mr-1.5" aria-hidden="true" /> Edit pages
                        </Button>
                      </div>
                      <Badge variant={fallbackService.running ? 'default' : 'destructive'} className="text-xs shrink-0">
                        {fallbackService.running ? 'Ready' : 'Offline'}
                      </Badge>
                    </CardContent>
                  </Card>
                )}
              </div>
            </section>
          )}

          <Dialog open={fbOpen} onOpenChange={setFbOpen}>
            <DialogContent className="w-[calc(100vw-2rem)] max-w-3xl max-h-[90vh] overflow-y-auto">
              <DialogHeader>
                <DialogTitle>Edge Fallback Pages</DialogTitle>
                <DialogDescription>
                  Live 503 pages served by the edge. Edits apply instantly and must keep the request-ID marker (http.request.uuid).
                </DialogDescription>
              </DialogHeader>
              <div className="flex gap-1 p-1 bg-muted rounded-lg w-fit" role="tablist" aria-label="Fallback page templates">
                {(['index.html', 'disabled.html'] as const).map((t) => {
                  const selected = fbTab === t;
                  return (
                    <button
                      key={t}
                      type="button"
                      role="tab"
                      aria-selected={selected}
                      onClick={() => setFbTab(t)}
                      className={`px-3 min-h-[44px] py-1.5 rounded-md text-xs font-mono transition-colors ${
                        selected ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {t === 'index.html' ? 'Waking up' : 'Disabled'}
                    </button>
                  );
                })}
              </div>
              {fbLoading ? (
                <div className="flex items-center justify-center py-12" role="status" aria-label="Loading page content">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-hidden="true" />
                </div>
              ) : (
                <div>
                  <label htmlFor="fallback-html-editor" className="sr-only">
                    Fallback HTML content for {fbTab}
                  </label>
                  <textarea
                    id="fallback-html-editor"
                    value={fbPages[fbTab] || ''}
                    onChange={(e) => setFbPages((prev) => ({ ...prev, [fbTab]: e.target.value }))}
                    spellCheck={false}
                    className="w-full h-[45vh] font-mono text-xs p-3 rounded-lg bg-background border border-border focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
              )}
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setFbOpen(false)}>Cancel</Button>
                <Button type="button" disabled={fbLoading || fbSaving} onClick={saveFbEditor}>
                  {fbSaving && <Loader2 className="w-4 h-4 mr-1.5 animate-spin" aria-hidden="true" />}
                  Save live
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          {/* Live Host Metrics */}
          <section aria-labelledby="host-metrics-heading" className="space-y-4">
            <h2 id="host-metrics-heading" className="text-lg font-bold text-foreground">
              Host Telemetry
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
              <MetricCard
                label="CPU Utilization"
                value={`${cpu.toFixed(1)}%`}
                subtext={`Load Avg: ${(systemConfig.load_avg || [0, 0, 0]).join(' / ')}`}
                color={statusMetaForPercent(cpu).color}
                statusLabel={statusMetaForPercent(cpu).label}
              />
              <MetricCard
                label="Memory Usage"
                value={`${ram.toFixed(1)}%`}
                subtext={`${systemConfig.ram_used_mb || 0} / ${systemConfig.ram_total_mb || 0} MB`}
                color={statusMetaForPercent(ram).color}
                statusLabel={statusMetaForPercent(ram).label}
              />
              <MetricCard
                label="Disk Storage"
                value={`${disk.toFixed(1)}%`}
                subtext={`${systemConfig.disk_used_gb || systemConfig.STORAGE_USED_GB || 0} / ${systemConfig.disk_total_gb || systemConfig.STORAGE_TOTAL_GB || 0} GB`}
                color={statusMetaForPercent(disk).color}
                statusLabel={statusMetaForPercent(disk).label}
              />
              <MetricCard
                label="Host Uptime"
                value={uptimeSeconds ? `${uptimeFormatted.days}d` : '--'}
                subtext={uptimeSeconds ? uptimeFormatted.detail : ''}
                color="text-cyan-700 dark:text-cyan-400"
              />
            </div>
          </section>

          {/* PaaS Service Health */}
          {services && (
            <section aria-labelledby="services-heading" className="space-y-4">
              <h2 id="services-heading" className="text-lg font-bold text-foreground">
                Service Containers
              </h2>
              {SERVICE_GROUPS.map((group) => {
                const items = group.keys.filter((k) => services[k]);
                if (items.length === 0) return null;
                return (
                  <Card key={group.label} className="border-border/60">
                    <CardHeader className="py-3 px-4 border-b border-border/40 bg-muted/20">
                      <CardTitle className="text-xs font-bold text-muted-foreground uppercase tracking-wider">
                        {group.label}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="p-4">
                      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2.5">
                        {items.map((name) => {
                          const s = services[name];
                          const isStandby = !s.running && OPTIONAL_SERVICES.has(name);
                          let dotColor = 'bg-emerald-500';
                          let badgeText = 'Running';
                          let badgeVariant: 'default' | 'secondary' | 'destructive' = 'default';

                          if (!s.running) {
                            if (isStandby) {
                              dotColor = 'bg-slate-400 dark:bg-slate-600';
                              badgeText = 'Standby / Optional';
                              badgeVariant = 'secondary';
                            } else {
                              dotColor = 'bg-red-500 animate-pulse motion-reduce:animate-none';
                              badgeText = 'Offline';
                              badgeVariant = 'destructive';
                            }
                          }

                          return (
                            <div
                              key={name}
                              className={`flex items-center justify-between p-2.5 rounded-lg border ${
                                s.running ? 'bg-card border-border/60' : isStandby ? 'bg-muted/30 border-border/40 opacity-85' : 'bg-red-500/5 border-red-500/30'
                              }`}
                            >
                              <div className="flex items-center gap-2 min-w-0">
                                <span className={`w-2 h-2 rounded-full shrink-0 ${dotColor}`} aria-hidden="true" />
                                <span className="font-medium text-xs truncate">{name}</span>
                              </div>
                              <Badge variant={badgeVariant} className="text-xs shrink-0 ml-2">
                                {badgeText}
                              </Badge>
                            </div>
                          );
                        })}
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </section>
          )}

          {/* Host Security */}
          {hostSecurity &&
            (() => {
              const items = HOST_SECURITY_ITEMS.filter((i) => hostSecurity[i.key]?.installed);
              if (items.length === 0) return null;
              return (
                <Card className="border-border/60">
                  <CardHeader className="py-3 px-4 border-b border-border/40 bg-muted/20">
                    <CardTitle className="text-xs font-bold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
                      <Shield className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
                      Host Security Controls
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-4">
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      {items.map(({ label, key }) => {
                        const s = hostSecurity[key];
                        return (
                          <div key={key} className="flex items-center justify-between p-3 rounded-lg border border-border/60 bg-card text-xs">
                            <div className="flex items-center gap-2">
                              <span className={`w-2 h-2 rounded-full shrink-0 ${s.active ? 'bg-emerald-500' : 'bg-amber-500'}`} aria-hidden="true" />
                              <span className="font-medium">{label}</span>
                            </div>
                            <Badge variant={s.active ? 'default' : 'secondary'} className="text-xs">
                              {s.active ? 'Active' : 'Inactive'}
                            </Badge>
                          </div>
                        );
                      })}
                    </div>
                  </CardContent>
                </Card>
              );
            })()}
        </>
      )}
    </div>
  );
}
