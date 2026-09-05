import {
    ArrowLeft, GitCommit, Activity, Terminal, Shield, Settings,
    Clock, Globe, Database, List, Timer, HardDrive, Puzzle, Network, Route,
    HeartPulse, Cpu, BarChart3, Box, FolderOpen, ShieldCheck, Sparkles, FileSearch, Layers, Cloud,
    CheckCircle2, Eye
} from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';

import { Service } from '@/lib/api';

interface ServiceLayoutProps {
    service: Service;
    activeTab: string;
    setActiveTab: (tab: string) => void;
    children: React.ReactNode;
}

export function ServiceLayout({ service, activeTab, setActiveTab, children }: ServiceLayoutProps) {
    const router = useRouter();
    const isAiRouter = (service.docker_image || '').includes('ghcr.io/berriai/litellm') || service.name.startsWith('ai-router');

    const tabs: Array<{
        id: string;
        label: string;
        icon: any;
        href?: string;
    }> = [
        { id: 'overview', label: 'Overview', icon: Activity },
        { id: 'deployments', label: 'Deployments', icon: Clock },
        { id: 'logs', label: 'Logs', icon: List },
        { id: 'console', label: 'Console', icon: Terminal },
        { id: 'build', label: 'Build', icon: Box },
        { id: 'files', label: 'Files', icon: FolderOpen },
        { id: 'addons', label: 'Addons', icon: Puzzle },
        { id: 'storage', label: 'Storage', icon: HardDrive },
        { id: 'env', label: 'Variables', icon: Database },
        { id: 'ai-insights', label: 'Insights', icon: Sparkles },
        ...(isAiRouter ? [{ id: 'router', label: 'AI Router', icon: Route }] : []),
        { id: 'domains', label: 'Domains', icon: Globe },
        { id: 'metrics', label: 'Metrics', icon: BarChart3 },
        { id: 'resources', label: 'Resources', icon: Cpu },
        { id: 'health', label: 'Health', icon: HeartPulse },
        { id: 'container-logs', label: 'Container Logs', icon: FileSearch },
        { id: 'monitoring', label: 'Monitoring', icon: BarChart3 },
        { id: 'topology', label: 'Topology', icon: Network },
        { id: 'scaling', label: 'Scaling', icon: Layers },
        { id: 'ha', label: 'High Availability', icon: ShieldCheck },
        { id: 'cloud-storage', label: 'Cloud Storage', icon: Cloud },
        { id: 'cron', label: 'Cron Jobs', icon: Timer },
        { id: 'backups', label: 'Backups', icon: HardDrive },
        { id: 'approvals', label: 'Approvals', icon: CheckCircle2 },
        { id: 'previews', label: 'Previews', icon: Eye },
        { id: 'safedeploy', label: 'SafeDeploy', icon: ShieldCheck },
        { id: 'settings', label: 'Settings', icon: Settings },
        { id: 'advanced', label: 'Advanced', icon: Shield },
    ];

    return (
        <main className="min-h-screen flex flex-col text-foreground relative">

            <div className="border-b border-border bg-card/60 backdrop-blur-md">
                <div className="container mx-auto py-6">
                    <div className="flex items-center gap-4 mb-6">
                        <Link href="/dashboard" className="p-2 hover:bg-muted rounded-full transition-colors text-muted-foreground hover:text-foreground">
                            <ArrowLeft size={20} />
                        </Link>
                        <div>
                            <div className="flex items-center gap-3">
                                <h1 className="text-2xl font-bold tracking-tight">{service.name}</h1>
                                <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                                    {
                                        ACTIVE: 'bg-emerald-500/10 text-emerald-500',
                                        LIVE: 'bg-emerald-500/10 text-emerald-500',
                                        FAILED: 'bg-red-500/10 text-red-500',
                                        CANCELLED: 'bg-gray-500/10 text-gray-500',
                                        REVIEW: 'bg-amber-500/10 text-amber-500',
                                        QUEUED: 'bg-blue-500/10 text-blue-500',
                                        BUILDING: 'bg-amber-500/10 text-amber-500',
                                        DEPLOYING: 'bg-amber-500/10 text-amber-500',
                                        HEALTH_CHECK: 'bg-cyan-500/10 text-cyan-500',
                                        TRAFFIC_SHIFTING: 'bg-indigo-500/10 text-indigo-500',
                                        STAGED: 'bg-teal-500/10 text-teal-500',
                                        INACTIVE: 'bg-gray-500/10 text-gray-500',
                                        ROLLING_BACK: 'bg-orange-500/10 text-orange-500',
                                        ROLLED_BACK: 'bg-orange-500/10 text-orange-500',
                                    }[service.latest_deployment?.status ?? ''] ?? 'bg-blue-500/10 text-blue-500'
                                }`}>
                                    {service.latest_deployment?.status || 'Ready to Deploy'}
                                </span>
                            </div>
                            <div className="flex items-center gap-4 text-sm text-muted-foreground mt-1">
                                <span className="flex items-center gap-1.5">
                                    <Globe size={12} />
                                    {service.public_domain || `${service.name}.cloud.Trulay.co`}
                                </span>
                                <span className="flex items-center gap-1.5">
                                    <GitCommit size={12} />
                                    {service.branch || 'main'}
                                </span>
                            </div>
                        </div>
                    </div>

                    <div className="flex gap-1 overflow-x-auto pb-2 scrollbar-hide">
                        {tabs.map((tab) => {
                            const Icon = tab.icon;
                            const isActive = activeTab === tab.id;
                            return (
                                <button
                                    key={tab.id}
                                    onClick={() => {
                                        if (tab.href) {
                                            router.push(tab.href);
                                        } else {
                                            setActiveTab(tab.id);
                                        }
                                    }}
                                    className={`
                                        flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all whitespace-nowrap
                                        ${isActive
                                            ? 'bg-primary text-primary-foreground shadow-sm'
                                            : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                                        }
                                    `}
                                >
                                    <Icon size={16} />
                                    {tab.label}
                                </button>
                            );
                        })}
                    </div>
                </div>
            </div>

            <div className="flex-1 relative z-10">
                <div className="container mx-auto py-8">
                    {children}
                </div>
            </div>
        </main>
    );
}
