import { ArrowLeft, GitCommit, Activity, Terminal, Shield, Settings, Clock, Globe, Database, List, Timer, HardDrive } from 'lucide-react';
import Link from 'next/link';

import { Service } from '@/lib/api';

interface ServiceLayoutProps {
    service: Service;
    activeTab: string;
    setActiveTab: (tab: string) => void;
    children: React.ReactNode;
}

export function ServiceLayout({ service, activeTab, setActiveTab, children }: ServiceLayoutProps) {
    const tabs = [
        { id: 'overview', label: 'Overview', icon: Activity },
        { id: 'deployments', label: 'Deployments', icon: Clock },
        { id: 'logs', label: 'Logs', icon: List },
        { id: 'console', label: 'Console', icon: Terminal },
        { id: 'storage', label: 'Storage', icon: HardDrive },
        { id: 'env', label: 'Variables', icon: Database },
        { id: 'domains', label: 'Domains', icon: Globe },
        { id: 'metrics', label: 'Metrics', icon: Activity },
        { id: 'cron', label: 'Cron Jobs', icon: Timer },
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
                                    service.latest_deployment?.status === 'ACTIVE'
                                        ? 'bg-emerald-500/10 text-emerald-500'
                                        : 'bg-yellow-500/10 text-yellow-500'
                                }`}>
                                    {service.latest_deployment?.status || 'PENDING'}
                                </span>
                            </div>
                            <div className="flex items-center gap-4 text-sm text-muted-foreground mt-1">
                                <span className="flex items-center gap-1.5">
                                    <Globe size={12} />
                                    {service.public_domain || `${service.name}.smsly.cloud`}
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
                                    onClick={() => setActiveTab(tab.id)}
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
