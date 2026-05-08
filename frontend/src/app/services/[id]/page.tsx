"use client"
import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { servicesApi, Service } from '@/lib/api';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Loader2 } from 'lucide-react';
import dynamic from 'next/dynamic';

// Lazy‑load the heavy DAG component to keep the initial bundle small.
const DependencyGraph = dynamic(() => import('@/components/DependencyGraph'), { ssr: false });

export default function ServiceDetailPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [service, setService] = useState<Service | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    servicesApi
      .retrieve(id)
      .then(setService)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <DashboardShell>
        <div className="flex items-center justify-center py-20">
          <Loader2 className="animate-spin w-8 h-8 text-muted-foreground" />
        </div>
      </DashboardShell>
    );
  }

  if (!service) {
    return (
      <DashboardShell>
        <p className="text-red-500">Service not found.</p>
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
      <div className="container max-w-5xl py-8">
        <h1 className="text-2xl font-bold mb-4">{service.name}</h1>
        <div className="grid md:grid-cols-2 gap-6 mb-8">
          <Card>
            <CardHeader>
              <CardTitle>General Info</CardTitle>
            </CardHeader>
            <CardContent>
              <p><strong>Repository:</strong> {service.repository_url}</p>
              <p><strong>Branch:</strong> {service.branch}</p>
              <p><strong>Port:</strong> {service.internal_port}</p>
              <p><strong>Status:</strong> <Badge>{service.status}</Badge></p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Environment</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-2">
              {service.env_vars?.map((ev: any) => (
                <div key={ev.key} className="text-sm">
                  <span className="font-medium">{ev.key}</span>: {ev.value}
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Dependency Graph</CardTitle>
          </CardHeader>
          <CardContent className="h-96">
            <DependencyGraph serviceId={id} />
          </CardContent>
        </Card>
      </div>
    </DashboardShell>
  );
}
// removed stray directive (already declared at top)

import { useEffect, useState, useRef, useCallback } from 'react';
import { servicesApi, Service, Deployment, EnvVar } from '@/lib/api';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { useParams, useSearchParams } from 'next/navigation';
import { ServiceLayout } from '@/components/layout/ServiceLayout';
import { Activity, Shield, Terminal, Zap, DollarSign, Globe, Rocket, Loader2 as Spinner } from 'lucide-react';
import Editor from "@monaco-editor/react";
import dynamic from 'next/dynamic';
import { LogsTab } from '@/components/logs/LogsTab';
import { AdvancedTab } from '@/components/settings/AdvancedTab';
import { EnvVarsTab } from '@/components/settings/EnvVarsTab';
import { DomainsTab } from '@/components/settings/DomainsTab';
import { DeploymentsTab } from '@/components/settings/DeploymentsTab';
import { MetricsTab } from '@/components/metrics/MetricsTab';
import { CronTab } from '@/components/cron/CronTab';
import { StorageTab } from '@/components/storage/StorageTab';
import { AddonsTab } from '@/components/addons/AddonsTab';
import { ResourcesTab } from '@/components/settings/ResourcesTab';
import { HealthTab } from '@/components/settings/HealthTab';
import { BuildTab } from '@/components/settings/BuildTab';
import { FilesTab } from '@/components/settings/FilesTab';
import { AiRouterTab } from '@/components/settings/AiRouterTab';
import BackupsTab from '@/components/settings/BackupsTab';
import { SafeDeployPanel } from '@/components/deployments/SafeDeployPanel';
import { toast } from '@/components/ui/use-toast';
import { ResourceAlerts } from '@/components/dashboard/ResourceAlerts';

const XtermConsole = dynamic(() => import('@/components/terminal/XtermConsole'), { ssr: false });
type ServiceEnvMap = Record<string, { id: number; value: string }>;

const getAuthTokenFromCookie = (): string | null => {
    if (typeof document === 'undefined') return null;
    const match = document.cookie.match(/(?:^|;\s*)auth_token=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : null;
};

const parseBool = (value: string | undefined, fallback: boolean) => {
    if (typeof value !== 'string') return fallback;
    const normalized = value.trim().toLowerCase();
    if (!normalized) return fallback;
    return ['1', 'true', 'yes', 'on', 'enabled'].includes(normalized);
};

export default function ServiceDetailPage() {
    const confirm = useConfirm();
    const params = useParams();
    const searchParams = useSearchParams();
    const id = params.id as string;
    const [service, setService] = useState<Service | null>(null);
    const [deployment, setDeployment] = useState<Deployment | null>(null);
    const [activeTab, setActiveTab] = useState('overview');
    const [aiKey, setAiKey] = useState('');
    const [redeploying, setRedeploying] = useState(false);
    const [watchConfigLoading, setWatchConfigLoading] = useState(false);
    const [watchConfigSaving, setWatchConfigSaving] = useState(false);
    const [serviceEnvMap, setServiceEnvMap] = useState<ServiceEnvMap>({});
    const [runtimeWatchEnabled, setRuntimeWatchEnabled] = useState(true);
    const [notifyInApp, setNotifyInApp] = useState(true);
    const [notifySms, setNotifySms] = useState(true);
    const [notifyEmail, setNotifyEmail] = useState(true);
    const [notifyTelegram, setNotifyTelegram] = useState(false);
    const [notifyWhatsapp, setNotifyWhatsapp] = useState(false);
    const [alertEmail, setAlertEmail] = useState('');
    const [alertPhone, setAlertPhone] = useState('');
    const [telegramChatId, setTelegramChatId] = useState('');
    const [whatsappTo, setWhatsappTo] = useState('');
    const logsEndRef = useRef<HTMLDivElement>(null);

    const loadWatchConfig = useCallback(async (serviceId: string) => {
        setWatchConfigLoading(true);
        try {
            const envVars: EnvVar[] = await servicesApi.getEnvVars(serviceId);
            const map: ServiceEnvMap = {};
            envVars.forEach((env) => {
                map[(env.key || '').toUpperCase()] = { id: env.id, value: env.value || '' };
            });
            setServiceEnvMap(map);

            setRuntimeWatchEnabled(parseBool(map.JULES_RUNTIME_WATCH?.value, true));
            setNotifyInApp(parseBool(map.JULES_NOTIFY_IN_APP?.value, true));
            setNotifySms(parseBool(map.JULES_NOTIFY_SMS?.value, true));
            setNotifyEmail(parseBool(map.JULES_NOTIFY_EMAIL?.value, true));
            setNotifyTelegram(parseBool(map.JULES_NOTIFY_TELEGRAM?.value, false));
            setNotifyWhatsapp(parseBool(map.JULES_NOTIFY_WHATSAPP?.value, false));

            setAlertEmail(map.ALERT_EMAIL?.value || '');
            setAlertPhone(map.ALERT_PHONE?.value || '');
            setTelegramChatId(map.ALERT_TELEGRAM_CHAT_ID?.value || '');
            setWhatsappTo(map.ALERT_WHATSAPP_TO?.value || '');
        } catch (err) {
            console.error(err);
            toast({
                title: 'Failed to load Jules runtime settings',
                description: 'Could not read service environment variables.',
                variant: 'destructive',
            });
        } finally {
            setWatchConfigLoading(false);
        }
    }, []);

    const saveEnvPair = async (serviceId: string, key: string, value: string) => {
        const normalizedKey = key.toUpperCase();
        const existing = serviceEnvMap[normalizedKey];
        const trimmed = value.trim();

        if (!trimmed) {
            if (existing) {
                await servicesApi.deleteEnvVar(serviceId, existing.id);
            }
            return;
        }

        await servicesApi.createEnvVar(serviceId, {
            key: normalizedKey,
            value: trimmed,
            is_secret: false,
        });
    };

    const handleSaveWatchConfig = async () => {
        if (!service) return;
        setWatchConfigSaving(true);
        try {
            await Promise.all([
                saveEnvPair(service.id, 'JULES_RUNTIME_WATCH', runtimeWatchEnabled ? 'true' : 'false'),
                saveEnvPair(service.id, 'JULES_NOTIFY_IN_APP', notifyInApp ? 'true' : 'false'),
                saveEnvPair(service.id, 'JULES_NOTIFY_SMS', notifySms ? 'true' : 'false'),
                saveEnvPair(service.id, 'JULES_NOTIFY_EMAIL', notifyEmail ? 'true' : 'false'),
                saveEnvPair(service.id, 'JULES_NOTIFY_TELEGRAM', notifyTelegram ? 'true' : 'false'),
                saveEnvPair(service.id, 'JULES_NOTIFY_WHATSAPP', notifyWhatsapp ? 'true' : 'false'),
                saveEnvPair(service.id, 'ALERT_EMAIL', alertEmail),
                saveEnvPair(service.id, 'ALERT_PHONE', alertPhone),
                saveEnvPair(service.id, 'ALERT_TELEGRAM_CHAT_ID', telegramChatId),
                saveEnvPair(service.id, 'ALERT_WHATSAPP_TO', whatsappTo),
            ]);

            await loadWatchConfig(service.id);
            toast({
                title: 'Jules runtime watch saved',
                description: 'Deploy once more to apply updated runtime labels and env settings.',
            });
        } catch (err) {
            console.error(err);
            toast({
                title: 'Failed to save Jules runtime settings',
                description: 'Some values could not be persisted.',
                variant: 'destructive',
            });
        } finally {
            setWatchConfigSaving(false);
        }
    };

    const handleRedeploy = async () => {
        if (!service) return;
        if (!await confirm({ title: 'Deploy service?', message: 'Trigger a new deployment for this service now?', confirmText: 'Deploy' })) return;
        try {
            setRedeploying(true);
            const deployResult = await servicesApi.deploy(service.id);
            if (deployResult?.existing_deployment) {
                const statusLabel = deployResult?.existing_deployment?.status || 'in progress';
                toast({
                    title: 'Deployment already in progress',
                    description: `Current deployment status: ${statusLabel}.`,
                });
                setRedeploying(false);
                return;
            }
            toast({ title: 'Redeployment triggered', description: 'A new deployment has started.' });
            // Refresh service data after a short delay
            setTimeout(async () => {
                try {
                    const s = await servicesApi.get(service.id);
                    setService(s);
                    if (s.latest_deployment) {
                        const d = await servicesApi.getDeployment(s.latest_deployment.id);
                        setDeployment(d);
                    }
                } catch (e) { console.error(e); }
                setRedeploying(false);
            }, 2000);
        } catch (err) {
            console.error(err);
            toast({ title: 'Redeploy failed', description: 'Could not trigger redeployment.', variant: 'destructive' });
            setRedeploying(false);
        }
    };

    const handleRestart = async () => {
        if (!service) return;
        if (!await confirm({ title: 'Restart service?', message: 'Fast-restart the container (~5 seconds). No rebuild required.', confirmText: 'Restart' })) return;
        try {
            setRedeploying(true);
            await servicesApi.restart(service.id);
            toast({ title: 'Restart triggered', description: 'A new deployment has been queued.' });
            setTimeout(async () => {
                try {
                    const s = await servicesApi.get(service.id);
                    setService(s);
                    if (s.latest_deployment) {
                        const d = await servicesApi.getDeployment(s.latest_deployment.id);
                        setDeployment(d);
                    }
                } catch (e) { console.error(e); }
                setRedeploying(false);
            }, 2000);
        } catch (err) {
            console.error(err);
            toast({ title: 'Restart failed', description: 'Could not trigger restart.', variant: 'destructive' });
            setRedeploying(false);
        }
    };

    useEffect(() => {
        const key = localStorage.getItem('smsly_ai_key');
        if (key) setAiKey(key);
    }, []);

    useEffect(() => {
        const fetchData = async () => {
            try {
                const s = await servicesApi.get(id);
                setService(s);
                if (s.latest_deployment) {
                    const d = await servicesApi.getDeployment(s.latest_deployment.id);
                    setDeployment(d);
                }
            } catch (err) { console.error(err); }
        };
        fetchData();
        // Auto-refresh every 5 seconds
        const interval = setInterval(fetchData, 5000);
        return () => clearInterval(interval);
    }, [id]);

    useEffect(() => {
        if (!id) return;
        loadWatchConfig(id);
    }, [id, loadWatchConfig]);

    // Support deep-links like `/services/:id?tab=logs`
    const tabParam = searchParams.get('tab');
    useEffect(() => {
        if (tabParam) setActiveTab(tabParam);
    }, [tabParam]);

    // Auto-verify domain on first load if domain exists but is not verified.
    // This triggers the backend to check DNS and persist domain_verified=True
    // so the "Pending" badge updates without user intervention.
    const domainVerifyTriggered = useRef(false);
    useEffect(() => {
        if (
            service &&
            service.public_domain &&
            !service.domain_verified &&
            !domainVerifyTriggered.current
        ) {
            domainVerifyTriggered.current = true;
            servicesApi.verifyDomain(service.id, service.public_domain).catch(() => {
                // Silent — verification is best-effort on load
            });
        }
    }, [service]);

    if (!service) return <div className="h-screen flex items-center justify-center bg-background text-muted-foreground">Loading...</div>;

    // Simple cost estimation logic (use defaults if not set)
    const customDomains = Array.isArray(service.custom_domains)
        ? service.custom_domains.filter((domain) => typeof domain === 'string' && domain.trim())
        : [];
    const healthPathLabel = service.health_check_path || '/health';

    return (
        <ServiceLayout service={service} activeTab={activeTab} setActiveTab={setActiveTab}>
            {activeTab === 'overview' && (
                <div className="grid grid-cols-1 md:grid-cols-4 gap-6 animate-in fade-in slide-in-from-bottom-4">
                    <div className="col-span-1 md:col-span-4">
                        <ResourceAlerts serviceId={service.id} />
                    </div>

                    {/* Stats Cards */}
                    <div className="bg-card border border-border p-6 rounded-xl shadow-sm hover:shadow-md transition-shadow">
                        <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider mb-3">Status</h4>
                        <p className={`text-3xl font-bold ${deployment?.status === 'ACTIVE' ? 'text-emerald-500' : deployment?.status === 'FAILED' ? 'text-red-500' : 'text-foreground'}`}>
                            {deployment?.status || 'No Deploy'}
                        </p>
                        <p className="text-xs text-muted-foreground mt-2 font-medium">
                            {deployment?.finished_at ? `Since ${new Date(deployment.finished_at).toLocaleDateString()}` : 'Awaiting deployment'}
                        </p>
                    </div>

                    {/* Health Status */}
                    <div className="bg-card border border-border p-6 rounded-xl shadow-sm hover:shadow-md transition-shadow">
                        <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider mb-3">Health</h4>
                        <div className="flex items-center gap-2">
                            <span className={`w-3 h-3 rounded-full ${
                                service.health_status === 'healthy' ? 'bg-emerald-500 animate-pulse' :
                                service.health_status === 'unhealthy' ? 'bg-red-500 animate-pulse' :
                                service.health_status === 'starting' ? 'bg-yellow-500 animate-pulse' :
                                'bg-zinc-500'
                            }`} />
                            <p className={`text-2xl font-bold capitalize ${
                                service.health_status === 'healthy' ? 'text-emerald-500' :
                                service.health_status === 'unhealthy' ? 'text-red-500' :
                                service.health_status === 'starting' ? 'text-yellow-500' :
                                'text-muted-foreground'
                            }`}>
                                {service.health_status || 'Unknown'}
                            </p>
                        </div>
                        <div className="flex gap-2 mt-3">
                            <span className="text-[10px] font-bold bg-muted px-2 py-0.5 rounded text-muted-foreground">
                                {service.cpu_cores ?? 0.5} vCPU
                            </span>
                            <span className="text-[10px] font-bold bg-muted px-2 py-0.5 rounded text-muted-foreground">
                                {service.memory_mb ?? 512} MB
                            </span>
                        </div>
                    </div>

                    <div className="bg-card border border-border p-6 rounded-xl shadow-sm hover:shadow-md transition-shadow">
                        <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider mb-3">Build Time</h4>
                        <p className="text-3xl font-bold text-foreground">
                            {deployment?.duration_seconds ? `${deployment.duration_seconds.toFixed(1)}s` : '—'}
                        </p>
                        <p className="text-xs text-muted-foreground mt-2 font-medium">Latest deployment</p>
                    </div>
                    <div className="bg-card border border-border p-6 rounded-xl shadow-sm hover:shadow-md transition-shadow">
                        <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider mb-3">Deploy Mode</h4>
                        <p className="text-2xl font-bold text-foreground">
                            {service.deploy_mode === 'COMPOSE' ? 'Compose' : 'Single'}
                        </p>
                        <p className="text-xs text-muted-foreground mt-2 font-medium">
                            {service.deploy_mode === 'COMPOSE' ? service.compose_file || 'docker-compose.yml' : 'Dockerfile'}
                        </p>
                    </div>

                    <div className="col-span-1 md:col-span-4 bg-card border border-border p-8 rounded-xl shadow-sm h-fit">
                        <h3 className="font-bold mb-6 text-lg text-foreground">Configuration</h3>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-5 text-sm">
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Repository</span>
                                <span className="font-mono text-foreground truncate ml-4">{service.repository_url}</span>
                            </div>
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Branch</span>
                                <span className="font-mono bg-muted px-2 py-1 rounded text-foreground">{service.branch}</span>
                            </div>
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Resources</span>
                                <span className="text-foreground">{service.cpu_cores ?? 1} vCPU / {service.memory_mb ?? 512} MB</span>
                            </div>
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Internal DNS</span>
                                <span className="font-mono text-primary bg-primary/10 px-2 py-1 rounded">{service.name}.default.svc</span>
                            </div>
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Public Domain</span>
                                <a
                                    href={`https://${service.public_domain || `${service.name}.cloud.smsly.cloud`}`}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="font-mono text-primary hover:underline flex items-center gap-1"
                                >
                                    <Globe className="w-3 h-3" />
                                    {service.public_domain || `${service.name}.cloud.smsly.cloud`}
                                </a>
                            </div>
                            <div className="flex justify-between border-b border-border pb-3 gap-4">
                                <span className="text-muted-foreground font-medium">Custom Domains</span>
                                <div className="flex flex-wrap items-center justify-end gap-2">
                                    {customDomains.length === 0 ? (
                                        <span className="text-muted-foreground text-xs">Not configured</span>
                                    ) : (
                                        customDomains.map((domain) => (
                                            <a
                                                key={domain}
                                                href={`https://${domain}`}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="font-mono text-xs text-primary hover:underline bg-primary/10 px-2 py-1 rounded"
                                            >
                                                {domain}
                                            </a>
                                        ))
                                    )}
                                </div>
                            </div>
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Domain Verification</span>
                                <span className={`text-xs font-semibold px-2 py-1 rounded ${
                                    service.domain_verified ? 'bg-emerald-500/10 text-emerald-500' : 'bg-yellow-500/10 text-yellow-500'
                                }`}>
                                    {service.domain_verified ? 'Verified' : 'Pending'}
                                </span>
                            </div>
                            <div className="flex justify-between items-center border-b border-border pb-3">
                                <div>
                                    <span className="text-muted-foreground font-medium">Visibility</span>
                                    <p className="text-[10px] text-muted-foreground mt-1 max-w-[200px]">Toggle whether Traefik routes public traffic to this service.</p>
                                </div>
                                <button
                                    onClick={async () => {
                                        const newVal = !(service.is_public !== false);
                                        try {
                                            const updated = await servicesApi.update(service.id, { is_public: newVal });
                                            setService(updated);
                                            toast({
                                                title: newVal ? 'Domain set to Public' : 'Domain set to Private',
                                                description: newVal
                                                    ? 'Service is now publicly accessible. Redeploy to apply.'
                                                    : 'Service is now internal-only. Redeploy to apply.',
                                            });
                                        } catch (err) {
                                            console.error(err);
                                            toast({ title: 'Failed to update visibility', variant: 'destructive' });
                                        }
                                    }}
                                    className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold transition-all ${
                                        service.is_public !== false
                                            ? 'bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20'
                                            : 'bg-zinc-500/10 text-zinc-400 hover:bg-zinc-500/20'
                                    }`}
                                >
                                    {service.is_public !== false ? (
                                        <><Globe className="w-3.5 h-3.5" /> Public</>
                                    ) : (
                                        <><Shield className="w-3.5 h-3.5" /> Private</>
                                    )}
                                </button>
                            </div>
                            <div className="flex justify-between items-center border-b border-border pb-3">
                                <div>
                                    <span className="text-muted-foreground font-medium">Public Domain Routing</span>
                                    <p className="text-[10px] text-muted-foreground mt-1 max-w-[200px]">When hidden, the default cloud.smsly.cloud domain will return 404.</p>
                                </div>
                                <button
                                    onClick={async () => {
                                        const newVal = !service.public_domain_hidden;
                                        try {
                                            const updated = await servicesApi.update(service.id, { public_domain_hidden: newVal });
                                            setService(updated);
                                            toast({
                                                title: newVal ? 'Public Domain Hidden' : 'Public Domain Visible',
                                                description: newVal
                                                    ? 'Traffic is now only routed via custom domains.'
                                                    : 'The default public domain is now active.',
                                            });
                                        } catch (err) {
                                            console.error(err);
                                            toast({ title: 'Failed to update public domain routing', variant: 'destructive' });
                                        }
                                    }}
                                    className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold transition-all ${
                                        !service.public_domain_hidden
                                            ? 'bg-blue-500/10 text-blue-500 hover:bg-blue-500/20'
                                            : 'bg-amber-500/10 text-amber-500 hover:bg-amber-500/20'
                                    }`}
                                >
                                    {!service.public_domain_hidden ? (
                                        <><Globe className="w-3.5 h-3.5" /> Active</>
                                    ) : (
                                        <><Shield className="w-3.5 h-3.5" /> Hidden</>
                                    )}
                                </button>
                            </div>
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Deploy Mode</span>
                                <div className="flex items-center gap-2">
                                    <span className={`text-xs font-bold px-2 py-1 rounded ${
                                        service.deploy_mode === 'COMPOSE'
                                            ? 'bg-blue-500/10 text-blue-400'
                                            : 'bg-muted text-muted-foreground'
                                    }`}>
                                        {service.deploy_mode === 'COMPOSE' ? 'Docker Compose' : 'Single Container'}
                                    </span>
                                    {service.deploy_mode === 'COMPOSE' && service.compose_file && (
                                        <span className="font-mono text-xs text-muted-foreground">
                                            {service.compose_file}
                                        </span>
                                    )}
                                </div>
                            </div>
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Health Check</span>
                                <span className="font-mono text-foreground">
                                    {healthPathLabel} every {service.health_check_interval ?? 30}s
                                </span>
                            </div>
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Restart Policy</span>
                                <span className="font-mono text-foreground">{service.restart_policy || 'unless-stopped'}</span>
                            </div>
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Deploy Type</span>
                                <span className="font-mono text-foreground">{service.deploy_type || 'GIT'}</span>
                            </div>
                        </div>
                    </div>

                    <div className="col-span-1 md:col-span-4 bg-card border border-border p-8 rounded-xl shadow-sm">
                        <h3 className="font-bold mb-6 text-lg text-foreground">Latest Deployment</h3>
                        {deployment ? (
                            <div className="space-y-5 text-sm">
                                <div className="flex justify-between items-center border-b border-border pb-3">
                                    <span className="text-muted-foreground font-medium">Commit</span>
                                    <span className="font-mono bg-muted border border-border px-2 py-1 rounded text-foreground font-bold">{String(deployment.commit_hash || '').substring(0, 7) || 'N/A'}</span>
                                </div>
                                <div className="flex justify-between items-center border-b border-border pb-3">
                                    <span className="text-muted-foreground font-medium">Status</span>
                                    <span className={`font-bold px-2 py-1 rounded text-xs uppercase ${deployment.status === 'ACTIVE' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-yellow-500/10 text-yellow-500'
                                        }`}>{deployment.status}</span>
                                </div>
                                <div className="pt-2 grid grid-cols-1 sm:grid-cols-3 gap-2">
                                    <button
                                        className="border border-border hover:border-foreground/20 hover:bg-muted text-foreground font-bold py-2 rounded-lg transition-all text-sm"
                                        onClick={() => setActiveTab('logs')}
                                    >
                                        View Logs
                                    </button>
                                    <button
                                        className="bg-yellow-500/20 border border-yellow-500/30 text-yellow-400 hover:bg-yellow-500/30 font-bold py-2 rounded-lg transition-all text-sm flex items-center justify-center gap-2 disabled:opacity-50"
                                        onClick={handleRestart}
                                        disabled={redeploying}
                                    >
                                        {redeploying ? <Spinner className="w-4 h-4 animate-spin" /> : <Activity className="w-4 h-4" />}
                                        Restart
                                    </button>
                                    <button
                                        className="bg-primary hover:bg-primary/90 text-white font-bold py-2 rounded-lg transition-all text-sm flex items-center justify-center gap-2 disabled:opacity-50"
                                        onClick={handleRedeploy}
                                        disabled={redeploying}
                                    >
                                        {redeploying ? <Spinner className="w-4 h-4 animate-spin" /> : <Rocket className="w-4 h-4" />}
                                        {redeploying ? 'Deploying...' : 'Redeploy'}
                                    </button>
                                </div>
                            </div>
                        ) : <p className="text-muted-foreground">No deployment found.</p>}
                    </div>
                </div>
            )}

            {activeTab === 'logs' && <LogsTab deployment={deployment} />}

            {activeTab === 'build' && <BuildTab service={service} />}

            {activeTab === 'files' && <FilesTab serviceId={service.id} />}

            {activeTab === 'env' && <EnvVarsTab serviceId={service.id} />}

            {activeTab === 'router' && (
                <AiRouterTab
                    serviceId={service.id}
                    serviceDomain={service.public_domain || `${service.name}.cloud.smsly.cloud`}
                />
            )}

            {activeTab === 'domains' && <DomainsTab service={service} />}

            {activeTab === 'deployments' && <DeploymentsTab serviceId={service.id} />}

            {activeTab === 'metrics' && <MetricsTab serviceId={service.id} />}

            {activeTab === 'resources' && <ResourcesTab serviceId={service.id} service={service} />}

            {activeTab === 'health' && <HealthTab serviceId={service.id} service={service} />}

            {activeTab === 'cron' && <CronTab serviceId={service.id} />}

            {activeTab === 'storage' && <StorageTab serviceId={service.id} />}

            {activeTab === 'addons' && <AddonsTab serviceId={service.id} />}

            {activeTab === 'settings' && (
                <div className="space-y-6">
                    {/* AI Configuration */}
                    <div className="bg-card border border-border p-6 rounded-xl shadow-sm">
                        <h3 className="font-bold mb-4 text-xl">Jules AI Configuration</h3>
                        <p className="text-muted-foreground text-sm mb-4">
                            Configure your personal API key for Jules AI to enable advanced failure analysis and suggestions.
                        </p>
                        <div className="max-w-xl">
                            <label className="block text-sm font-medium mb-2">Jules AI API Key</label>
                            <div className="flex gap-2">
                                <input
                                    type="password"
                                    placeholder="sk_..."
                                    className="flex-1 p-2 border rounded bg-background"
                                    value={aiKey}
                                    onChange={(e) => setAiKey(e.target.value)}
                                />
                                <button
                                    className="bg-primary text-primary-foreground px-4 py-2 rounded font-bold hover:opacity-90"
                                    onClick={() => {
                                        localStorage.setItem('smsly_ai_key', aiKey);
                                        toast({ title: 'AI Key saved', description: 'Key stored locally in your browser.' });
                                    }}
                                >
                                    Save
                                </button>
                            </div>
                        </div>
                    </div>

                    <div className="bg-card border border-border p-6 rounded-xl shadow-sm space-y-6">
                        <div>
                            <h3 className="font-bold text-xl mb-1">Jules Runtime Watch</h3>
                            <p className="text-muted-foreground text-sm">
                                Enable runtime monitoring and multi-channel alerts for deployment/runtime failures.
                            </p>
                        </div>

                        {watchConfigLoading ? (
                            <p className="text-sm text-muted-foreground">Loading runtime watch configuration...</p>
                        ) : (
                            <>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    <label className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
                                        <span className="text-sm font-medium">Runtime watch active</span>
                                        <input type="checkbox" checked={runtimeWatchEnabled} onChange={(e) => setRuntimeWatchEnabled(e.target.checked)} />
                                    </label>
                                    <label className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
                                        <span className="text-sm font-medium">In-app notifications</span>
                                        <input type="checkbox" checked={notifyInApp} onChange={(e) => setNotifyInApp(e.target.checked)} />
                                    </label>
                                    <label className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
                                        <span className="text-sm font-medium">SMS notifications</span>
                                        <input type="checkbox" checked={notifySms} onChange={(e) => setNotifySms(e.target.checked)} />
                                    </label>
                                    <label className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
                                        <span className="text-sm font-medium">Email notifications</span>
                                        <input type="checkbox" checked={notifyEmail} onChange={(e) => setNotifyEmail(e.target.checked)} />
                                    </label>
                                    <label className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
                                        <span className="text-sm font-medium">Telegram notifications</span>
                                        <input type="checkbox" checked={notifyTelegram} onChange={(e) => setNotifyTelegram(e.target.checked)} />
                                    </label>
                                    <label className="flex items-center justify-between gap-3 rounded-lg border border-border p-3">
                                        <span className="text-sm font-medium">WhatsApp notifications</span>
                                        <input type="checkbox" checked={notifyWhatsapp} onChange={(e) => setNotifyWhatsapp(e.target.checked)} />
                                    </label>
                                </div>

                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">Alert Email</label>
                                        <input
                                            type="email"
                                            placeholder="alerts@company.com"
                                            className="w-full p-2 border rounded bg-background"
                                            value={alertEmail}
                                            onChange={(e) => setAlertEmail(e.target.value)}
                                        />
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">Alert Phone (SMS)</label>
                                        <input
                                            type="text"
                                            placeholder="+15551234567"
                                            className="w-full p-2 border rounded bg-background"
                                            value={alertPhone}
                                            onChange={(e) => setAlertPhone(e.target.value)}
                                        />
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">Telegram Chat ID</label>
                                        <input
                                            type="text"
                                            placeholder="123456789"
                                            className="w-full p-2 border rounded bg-background"
                                            value={telegramChatId}
                                            onChange={(e) => setTelegramChatId(e.target.value)}
                                        />
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">WhatsApp Target</label>
                                        <input
                                            type="text"
                                            placeholder="+15551234567"
                                            className="w-full p-2 border rounded bg-background"
                                            value={whatsappTo}
                                            onChange={(e) => setWhatsappTo(e.target.value)}
                                        />
                                    </div>
                                </div>

                                <div className="flex flex-wrap items-center gap-3">
                                    <button
                                        className="bg-primary text-primary-foreground px-4 py-2 rounded font-bold hover:opacity-90 disabled:opacity-60"
                                        onClick={handleSaveWatchConfig}
                                        disabled={watchConfigSaving}
                                    >
                                        {watchConfigSaving ? 'Saving...' : 'Save Runtime Watch'}
                                    </button>
                                    <button
                                        className="border border-border px-4 py-2 rounded font-semibold hover:bg-muted disabled:opacity-60"
                                        onClick={() => loadWatchConfig(id)}
                                        disabled={watchConfigSaving}
                                    >
                                        Refresh
                                    </button>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            )}


            {activeTab === 'backups' && <BackupsTab serviceId={service.id} />}

            {activeTab === 'safedeploy' && <SafeDeployPanel serviceId={service.id} />}

            {activeTab === 'advanced' && <AdvancedTab service={service} />}

            {activeTab === 'console' && (
                <div className="h-[600px] bg-zinc-950 rounded-xl overflow-hidden border border-border shadow-2xl">
                    {(() => {
                        const deploymentId = deployment?.id || service.latest_deployment?.id;
                        if (!deploymentId) {
                            return (
                                <div className="h-full w-full flex items-center justify-center text-zinc-400 text-sm">
                                    Deploy this service first to enable the console.
                                </div>
                            );
                        }

                        const token =
                            typeof window !== 'undefined'
                                ? (localStorage.getItem('auth_token') || getAuthTokenFromCookie())
                                : null;

                        if (!token) {
                            return (
                                <div className="h-full w-full flex items-center justify-center text-zinc-400 text-sm">
                                    Login required to open the console.
                                </div>
                            );
                        }

                        const proto =
                            typeof window !== 'undefined' && window.location.protocol === 'https:'
                                ? 'wss'
                                : 'ws';
                        const host = typeof window !== 'undefined' ? window.location.host : 'localhost';
                        const wsUrl = `${proto}://${host}/ws/terminal/${deploymentId}/?token=${encodeURIComponent(token)}`;

                        return <XtermConsole wsUrl={wsUrl} />;
                    })()}
                </div>
            )}
        </ServiceLayout>
    );
}
