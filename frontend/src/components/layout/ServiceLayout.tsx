'use client';

import * as React from 'react';
import {
    ArrowLeft, GitCommit, Activity, Terminal, Shield, Settings,
    Clock, Globe, Database, List, Timer, HardDrive, Puzzle, Network, Route,
    HeartPulse, Cpu, BarChart3, Box, FolderOpen, ShieldCheck, Sparkles, FileSearch, Layers, Cloud,
    CheckCircle2, Eye, ChevronLeft, ChevronRight,
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

type TabCategory = 'core' | 'data' | 'network' | 'operations' | 'release';

interface ServiceTabItem {
    id: string;
    label: string;
    icon: React.ComponentType<{ size?: number; className?: string }>;
    category: TabCategory;
    href?: string;
}

export function ServiceLayout({ service, activeTab, setActiveTab, children }: ServiceLayoutProps) {
    const router = useRouter();
    const isAiRouter = (service.docker_image || '').includes('ghcr.io/berriai/litellm') || service.name.startsWith('ai-router');
    const tablistRef = React.useRef<HTMLDivElement>(null);
    const tabRefs = React.useRef<Array<HTMLButtonElement | null>>([]);
    const [canScrollLeft, setCanScrollLeft] = React.useState(false);
    const [canScrollRight, setCanScrollRight] = React.useState(false);

    const tabs: ServiceTabItem[] = React.useMemo(() => [
        { id: 'overview', label: 'Overview', icon: Activity, category: 'core' },
        { id: 'deployments', label: 'Deployments', icon: Clock, category: 'core' },
        { id: 'logs', label: 'Logs', icon: List, category: 'core' },
        { id: 'console', label: 'Console', icon: Terminal, category: 'core' },
        { id: 'build', label: 'Build', icon: Box, category: 'core' },
        { id: 'files', label: 'Files', icon: FolderOpen, category: 'core' },
        { id: 'addons', label: 'Addons', icon: Puzzle, category: 'data' },
        { id: 'storage', label: 'Storage', icon: HardDrive, category: 'data' },
        { id: 'env', label: 'Variables', icon: Database, category: 'data' },
        { id: 'ai-insights', label: 'Insights', icon: Sparkles, category: 'data' },
        ...(isAiRouter ? [{ id: 'router', label: 'AI Router', icon: Route, category: 'data' as const }] : []),
        { id: 'domains', label: 'Domains', icon: Globe, category: 'network' },
        { id: 'mtls', label: 'mTLS / Envoy', icon: ShieldCheck, category: 'network' },
        { id: 'topology', label: 'Topology', icon: Network, category: 'network' },
        { id: 'network', label: 'Network', icon: Globe, category: 'network' },
        { id: 'metrics', label: 'Metrics', icon: BarChart3, category: 'operations' },
        { id: 'resources', label: 'Resources', icon: Cpu, category: 'operations' },
        { id: 'health', label: 'Health', icon: HeartPulse, category: 'operations' },
        { id: 'container-logs', label: 'Container Logs', icon: FileSearch, category: 'operations' },
        { id: 'monitoring', label: 'Monitoring', icon: BarChart3, category: 'operations' },
        { id: 'scaling', label: 'Scaling', icon: Layers, category: 'operations' },
        { id: 'ha', label: 'High Availability', icon: ShieldCheck, category: 'operations' },
        { id: 'cloud-storage', label: 'Cloud Storage', icon: Cloud, category: 'operations' },
        { id: 'cron', label: 'Cron Jobs', icon: Timer, category: 'operations' },
        { id: 'backups', label: 'Backups', icon: HardDrive, category: 'operations' },
        { id: 'approvals', label: 'Approvals', icon: CheckCircle2, category: 'release' },
        { id: 'previews', label: 'Previews', icon: Eye, category: 'release' },
        { id: 'safedeploy', label: 'SafeDeploy', icon: ShieldCheck, category: 'release' },
        { id: 'settings', label: 'Settings', icon: Settings, category: 'release' },
        { id: 'advanced', label: 'Advanced', icon: Shield, category: 'release' },
    ], [isAiRouter]);

    const updateScrollArrows = React.useCallback(() => {
        const el = tablistRef.current;
        if (!el) return;
        const maxScroll = el.scrollWidth - el.clientWidth;
        setCanScrollLeft(el.scrollLeft > 2);
        setCanScrollRight(maxScroll - el.scrollLeft > 2);
    }, []);

    React.useEffect(() => {
        updateScrollArrows();
        const el = tablistRef.current;
        if (!el) return;
        let observer: ResizeObserver | null = null;
        if (typeof ResizeObserver !== 'undefined') {
            observer = new ResizeObserver(updateScrollArrows);
            observer.observe(el);
        }
        window.addEventListener('resize', updateScrollArrows);
        return () => {
            observer?.disconnect();
            window.removeEventListener('resize', updateScrollArrows);
        };
    }, [updateScrollArrows, tabs.length]);

    React.useEffect(() => {
        const activeIndex = tabs.findIndex((t) => t.id === activeTab);
        if (activeIndex >= 0) {
            tabRefs.current[activeIndex]?.scrollIntoView?.({
                behavior: 'smooth',
                block: 'nearest',
                inline: 'nearest',
            });
        }
    }, [activeTab, tabs]);

    const scrollTabsBy = (delta: number) => {
        tablistRef.current?.scrollBy?.({ left: delta, behavior: 'smooth' });
    };

    const handleTabKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
        let nextIndex: number | null = null;
        if (e.key === 'ArrowRight') {
            nextIndex = (index + 1) % tabs.length;
        } else if (e.key === 'ArrowLeft') {
            nextIndex = (index - 1 + tabs.length) % tabs.length;
        } else if (e.key === 'Home') {
            nextIndex = 0;
        } else if (e.key === 'End') {
            nextIndex = tabs.length - 1;
        }
        if (nextIndex === null) return;
        e.preventDefault();
        const nextBtn = tabRefs.current[nextIndex];
        nextBtn?.focus();
        nextBtn?.scrollIntoView?.({
            behavior: 'smooth',
            block: 'nearest',
            inline: 'nearest',
        });
    };

    const backHref = service.project ? `/projects/${service.project}` : '/dashboard';
    const backLabel = service.project
        ? `Back to ${service.project_name || 'project'}`
        : 'Back to dashboard';

    return (
        <div className="min-h-screen flex flex-col text-foreground relative">
            <div className="bg-card/60 backdrop-blur-md">
                <div className="container mx-auto pt-5 pb-3">
                    <div className="flex items-center gap-4">
                        <Link
                            href={backHref}
                            aria-label={backLabel}
                            title={backLabel}
                            className="p-2 hover:bg-muted rounded-full transition-colors text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring shrink-0"
                        >
                            <ArrowLeft size={20} aria-hidden="true" />
                        </Link>
                        <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2 sm:gap-3">
                                <h1 className="text-2xl font-bold tracking-tight truncate">{service.name}</h1>
                                <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider shrink-0 ${
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
                            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground mt-1">
                                <span className="flex items-center gap-1.5 min-w-0">
                                    <Globe size={12} className="shrink-0" aria-hidden="true" />
                                    <span className="truncate">{service.public_domain || `${service.name}.cloud.trulay.co`}</span>
                                </span>
                                <span className="flex items-center gap-1.5 shrink-0">
                                    <GitCommit size={12} aria-hidden="true" />
                                    {service.branch || 'main'}
                                </span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Sticky Tab Bar — stacks cleanly below Navbar (z-30 < Navbar z-50) */}
            <div className="sticky top-[var(--navbar-height,3.5rem)] md:top-[var(--navbar-height,8.25rem)] z-30 border-b border-border bg-card/90 backdrop-blur-md">
                <div className="container mx-auto py-2">
                    <div className="flex items-center gap-1">
                        <button
                            type="button"
                            onClick={() => scrollTabsBy(-240)}
                            disabled={!canScrollLeft}
                            aria-label="Scroll tabs left"
                            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-background/80 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30 disabled:pointer-events-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                            <ChevronLeft size={16} aria-hidden="true" />
                        </button>

                        <div
                            ref={tablistRef}
                            role="tablist"
                            aria-label="Service configuration tabs"
                            onScroll={updateScrollArrows}
                            className="flex flex-1 min-w-0 items-center gap-1 overflow-x-auto scrollbar-hide py-0.5"
                        >
                            {tabs.map((tab, index) => {
                                const Icon = tab.icon;
                                const isActive = activeTab === tab.id;
                                const prevCategory = index > 0 ? tabs[index - 1].category : tab.category;
                                const showDivider = index > 0 && prevCategory !== tab.category;
                                return (
                                    <React.Fragment key={tab.id}>
                                        {showDivider && (
                                            <div
                                                role="separator"
                                                aria-orientation="vertical"
                                                className="h-5 w-px bg-border mx-1.5 shrink-0 self-center"
                                            />
                                        )}
                                        <button
                                            ref={(el) => {
                                                tabRefs.current[index] = el;
                                            }}
                                            type="button"
                                            role="tab"
                                            id={`service-tab-${tab.id}`}
                                            aria-selected={isActive}
                                            tabIndex={isActive ? 0 : -1}
                                            onKeyDown={(e) => handleTabKeyDown(e, index)}
                                            onClick={() => {
                                                if (tab.href) {
                                                    router.push(tab.href);
                                                } else {
                                                    setActiveTab(tab.id);
                                                    if (typeof window !== 'undefined') {
                                                        const url = new URL(window.location.href);
                                                        if (tab.id === 'overview') {
                                                            url.searchParams.delete('tab');
                                                        } else {
                                                            url.searchParams.set('tab', tab.id);
                                                        }
                                                        window.history.replaceState(null, '', url.toString());
                                                    }
                                                }
                                            }}
                                            className={`
                                                flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm font-medium transition-all whitespace-nowrap shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
                                                ${isActive
                                                    ? 'bg-primary text-primary-foreground shadow-sm'
                                                    : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                                                }
                                            `}
                                        >
                                            <Icon size={16} aria-hidden="true" />
                                            {tab.label}
                                        </button>
                                    </React.Fragment>
                                );
                            })}
                        </div>

                        <button
                            type="button"
                            onClick={() => scrollTabsBy(240)}
                            disabled={!canScrollRight}
                            aria-label="Scroll tabs right"
                            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-background/80 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30 disabled:pointer-events-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                            <ChevronRight size={16} aria-hidden="true" />
                        </button>
                    </div>
                </div>
            </div>

            <div className="flex-1 relative z-10">
                <div className="container mx-auto py-8">
                    {children}
                </div>
            </div>
        </div>
    );
}
