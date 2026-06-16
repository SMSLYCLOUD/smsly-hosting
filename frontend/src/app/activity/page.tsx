'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Activity, Clock, Filter, Rocket, Database, Settings, RefreshCw, Trash2, ArrowRightLeft, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { formatDistanceToNow } from 'date-fns';
import Link from 'next/link';

interface AuditLog {
    id: string;
    action: string;
    service: string | null;
    service_name: string | null;
    user: string;
    details: string;
    ip_address: string;
    created_at: string;
}

function getHeaders(): Record<string, string> {
    return { 'Content-Type': 'application/json' };
}

function apiUrl(path: string) {
    const base = typeof window !== 'undefined' ? `${window.location.origin}/api/v1` : '/api/v1';
    return `${base}${path}`;
}

export default function ActivityPage() {
    const [logs, setLogs] = useState<AuditLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [filter, setFilter] = useState('ALL');
    const [expanded, setExpanded] = useState<string | null>(null);

    const fetchLogs = useCallback(async () => {
        try {
            const res = await fetch(apiUrl('/audit-logs/?ordering=-created_at&limit=50'), {
                credentials: 'include',
                headers: getHeaders(),
            });
            if (res.ok) {
                const data = await res.json();
                setLogs(Array.isArray(data) ? data : data.results);
            }
        } catch (e) {
            console.error('Failed to fetch activity:', e);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchLogs();
        const interval = setInterval(fetchLogs, 10000);
        return () => clearInterval(interval);
    }, [fetchLogs]);

    const getIcon = (action: string) => {
        if (action.includes('DEPLOY')) return <Rocket size={16} className="text-emerald-500" />;
        if (action.includes('ADDON')) return <Database size={16} className="text-blue-500" />;
        if (action.includes('ENV')) return <Settings size={16} className="text-yellow-500" />;
        if (action.includes('DELETE')) return <Trash2 size={16} className="text-red-500" />;
        if (action.includes('TRANSFER')) return <ArrowRightLeft size={16} className="text-purple-500" />;
        return <Activity size={16} className="text-zinc-500" />;
    };

    const getDotColor = (action: string) => {
        if (action.includes('DEPLOY')) return 'bg-emerald-500';
        if (action.includes('ADDON')) return 'bg-blue-500';
        if (action.includes('ENV')) return 'bg-yellow-500';
        if (action.includes('DELETE')) return 'bg-red-500';
        if (action.includes('TRANSFER')) return 'bg-purple-500';
        return 'bg-zinc-500';
    };

    const filteredLogs = logs.filter(log => {
        if (filter === 'ALL') return true;
        if (filter === 'DEPLOYMENTS') return log.action.includes('DEPLOY');
        if (filter === 'ADDONS') return log.action.includes('ADDON');
        if (filter === 'ENV') return log.action.includes('ENV');
        if (filter === 'TRANSFERS') return log.action.includes('TRANSFER');
        return true;
    });

    return (
        <DashboardShell>
            <div className="container max-w-4xl mx-auto px-4 py-8">
                <div className="flex items-center justify-between mb-8">
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-3">
                            <Activity className="text-primary" /> Activity Feed
                        </h1>
                        <p className="text-muted-foreground mt-1">Real-time audit log of platform events.</p>
                    </div>
                    <div className="flex gap-2">
                        {['ALL', 'DEPLOYMENTS', 'ADDONS', 'ENV', 'TRANSFERS'].map(f => (
                            <button
                                key={f}
                                onClick={() => setFilter(f)}
                                className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                                    filter === f
                                    ? 'bg-primary text-primary-foreground'
                                    : 'bg-muted text-muted-foreground hover:bg-muted/80'
                                }`}
                            >
                                {f.charAt(0) + f.slice(1).toLowerCase()}
                            </button>
                        ))}
                    </div>
                </div>

                <div className="relative border-l border-zinc-800 ml-4 space-y-8 pb-12">
                    {loading ? (
                        <div className="pl-8 text-muted-foreground">Loading activity...</div>
                    ) : filteredLogs.length === 0 ? (
                        <div className="pl-8 text-muted-foreground">No activity found.</div>
                    ) : (
                        filteredLogs.map((log) => (
                            <div key={log.id} className="relative pl-8">
                                {/* Dot */}
                                <div className={`absolute left-[-5px] top-1.5 w-2.5 h-2.5 rounded-full ${getDotColor(log.action)} shadow-[0_0_10px_currentColor] opacity-80`} />

                                <div className="bg-card/50 border border-border rounded-xl p-4 hover:bg-card/80 transition-colors">
                                    <div className="flex items-start justify-between">
                                        <div className="flex items-start gap-3">
                                            <div className="mt-1 p-1.5 bg-muted/50 rounded-lg">
                                                {getIcon(log.action)}
                                            </div>
                                            <div>
                                                <p className="text-sm font-medium text-foreground">
                                                    <span className="font-bold">{log.user}</span> {log.action.toLowerCase().replace('_', ' ')}
                                                    {log.service_name && (
                                                        <>
                                                            {' on '}
                                                            <Link href={`/services/${log.service}`} className="text-primary hover:underline">
                                                                {log.service_name}
                                                            </Link>
                                                        </>
                                                    )}
                                                </p>
                                                <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-2">
                                                    <Clock size={10} />
                                                    {(() => {
                                                        try {
                                                            const d = new Date(log.created_at);
                                                            return isNaN(d.getTime()) ? 'recently' : formatDistanceToNow(d, { addSuffix: true });
                                                        } catch { return 'recently'; }
                                                    })()}
                                                    <span className="w-1 h-1 rounded-full bg-zinc-700" />
                                                    {log.ip_address}
                                                </p>
                                            </div>
                                        </div>
                                        {log.details && (
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className="h-6 text-xs text-muted-foreground"
                                                onClick={() => setExpanded(expanded === log.id ? null : log.id)}
                                            >
                                                {expanded === log.id ? 'Hide Details' : 'Details'}
                                            </Button>
                                        )}
                                    </div>

                                    {expanded === log.id && log.details && (
                                        <div className="mt-3 pt-3 border-t border-border">
                                            <pre className="text-[10px] font-mono text-zinc-400 bg-black/30 p-2 rounded overflow-x-auto whitespace-pre-wrap">
                                                {log.details}
                                            </pre>
                                        </div>
                                    )}
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </DashboardShell>
    );
}
