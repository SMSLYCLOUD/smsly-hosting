'use client';

import { useEffect, useState, useRef } from 'react';
import { servicesApi, Service, Deployment, EnvVar } from '@/lib/api';
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
import { toast } from '@/components/ui/use-toast';

const XtermConsole = dynamic(() => import('@/components/terminal/XtermConsole'), { ssr: false });

export default function ServiceDetailPage() {
    const params = useParams();
    const searchParams = useSearchParams();
    const id = params.id as string;
    const [service, setService] = useState<Service | null>(null);
    const [deployment, setDeployment] = useState<Deployment | null>(null);
    const [activeTab, setActiveTab] = useState('overview');
    const [aiKey, setAiKey] = useState('');
    const [redeploying, setRedeploying] = useState(false);
    const logsEndRef = useRef<HTMLDivElement>(null);

    const handleRedeploy = async () => {
        if (!service) return;
        try {
            setRedeploying(true);
            await servicesApi.deploy(service.id);
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

    // Support deep-links like `/services/:id?tab=logs`
    const tabParam = searchParams.get('tab');
    useEffect(() => {
        if (tabParam) setActiveTab(tabParam);
    }, [tabParam]);

    if (!service) return <div className="h-screen flex items-center justify-center bg-background text-muted-foreground">Loading...</div>;

    // Simple cost estimation logic (use defaults if not set)
    const hourlyRate = ((service.cpu_cores ?? 1) * 0.04) + (((service.memory_mb ?? 512) / 1024) * 0.02);
    const monthlyEstimate = hourlyRate * 730;

    return (
        <ServiceLayout service={service} activeTab={activeTab} setActiveTab={setActiveTab}>
            {activeTab === 'overview' && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6 animate-in fade-in slide-in-from-bottom-4">
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
                        <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider mb-3">Est. Cost</h4>
                        <p className="text-3xl font-bold text-foreground">${monthlyEstimate.toFixed(2)}</p>
                        <p className="text-xs text-muted-foreground mt-2 font-medium">/month (approx)</p>
                    </div>

                    <div className="col-span-1 md:col-span-2 bg-card border border-border p-8 rounded-xl shadow-sm h-fit">
                        <h3 className="font-bold mb-6 text-lg text-foreground">Configuration</h3>
                        <div className="space-y-5 text-sm">
                            <div className="flex justify-between border-b border-border pb-3">
                                <span className="text-muted-foreground font-medium">Repository</span>
                                <span className="font-mono text-foreground">{service.repository_url}</span>
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
                        </div>
                    </div>

                    <div className="bg-card border border-border p-8 rounded-xl shadow-sm h-fit">
                        <h3 className="font-bold mb-6 text-lg text-foreground">Latest Deployment</h3>
                        {deployment ? (
                            <div className="space-y-5 text-sm">
                                <div className="flex justify-between items-center border-b border-border pb-3">
                                    <span className="text-muted-foreground font-medium">Commit</span>
                                    <span className="font-mono bg-muted border border-border px-2 py-1 rounded text-foreground font-bold">{deployment.commit_hash.substring(0, 7)}</span>
                                </div>
                                <div className="flex justify-between items-center border-b border-border pb-3">
                                    <span className="text-muted-foreground font-medium">Status</span>
                                    <span className={`font-bold px-2 py-1 rounded text-xs uppercase ${deployment.status === 'ACTIVE' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-yellow-500/10 text-yellow-500'
                                        }`}>{deployment.status}</span>
                                </div>
                                <div className="pt-2 flex gap-2">
                                    <button
                                        className="flex-1 border border-border hover:border-foreground/20 hover:bg-muted text-foreground font-bold py-2 rounded-lg transition-all text-sm"
                                        onClick={() => setActiveTab('logs')}
                                    >
                                        View Logs
                                    </button>
                                    <button
                                        className="flex-1 bg-primary hover:bg-primary/90 text-white font-bold py-2 rounded-lg transition-all text-sm flex items-center justify-center gap-2 disabled:opacity-50"
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

            {activeTab === 'env' && <EnvVarsTab serviceId={service.id} />}

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
                </div>
            )}

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
                                ? localStorage.getItem('auth_token')
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
