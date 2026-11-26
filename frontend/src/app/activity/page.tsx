'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Activity, Clock, Rocket, Database, Settings, Trash2, ArrowRightLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { formatDistanceToNow } from 'date-fns';
import api from '@/lib/api';

interface AuditLog {
    id: string;
    timestamp: string;
    user?: string | number | null;
    project?: string | null;
    actor: string;
    action: string;
    target?: string;
    metadata?: Record<string, unknown> | null;
}

export default function ActivityPage() {
    const [logs, setLogs] = useState<AuditLog[]>([]);
    const [loading, setLoading] = useState(true);
    const [filter, setFilter] = useState('ALL');
    const [expanded, setExpanded] = useState<string | null>(null);

    const fetchLogs = useCallback(async () => {
        try {
            const res = await api.get('/audit-logs/', {
                params: { ordering: '-timestamp', limit: 50 },
            });
            const data = res.data;
            setLogs(Array.isArray(data) ? data : (data?.results || []));
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
        const upper = (action || '').toUpperCase();
        if (upper.includes('DEPLOY')) return <Rocket size={16} className="text-emerald-500" />;
        if (upper.includes('ADDON')) return <Database size={16} className="text-blue-500" />;
        if (upper.includes('ENV')) return <Settings size={16} className="text-yellow-500" />;
        if (upper.includes('DELETE')) return <Trash2 size={16} className="text-red-500" />;
        if (upper.includes('TRANSFER')) return <ArrowRightLeft size={16} className="text-purple-500" />;
        return <Activity size={16} className="text-zinc-500" />;
    };

    const getDotColor = (action: string) => {
        const upper = (action || '').toUpperCase();
        if (upper.includes('DEPLOY')) return 'bg-emerald-500';
        if (upper.includes('ADDON')) return 'bg-blue-500';
        if (upper.includes('ENV')) return 'bg-yellow-500';
        if (upper.includes('DELETE')) return 'bg-red-500';
        if (upper.includes('TRANSFER')) return 'bg-purple-500';
        return 'bg-zinc-500';
    };

    const filteredLogs = logs.filter(log => {
        const upper = (log.action || '').toUpperCase();
        if (filter === 'ALL') return true;
        if (filter === 'DEPLOYMENTS') return upper.includes('DEPLOY');
        if (filter === 'ADDONS') return upper.includes('ADDON');
        if (filter === 'ENV') return upper.includes('ENV');
        if (filter === 'TRANSFERS') return upper.includes('TRANSFER');
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
                        filteredLogs.map((log) => {
                            const hasMetadata = Boolean(log.metadata && Object.keys(log.metadata).length > 0);
                            const ipAddress = typeof log.metadata?.ip_address === 'string' ? log.metadata.ip_address : null;
                            return (
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
                                                    <span className="font-bold">{log.actor || (log.user ? `User ${log.user}` : 'System')}</span> {(log.action || '').toLowerCase().replace(/_/g, ' ')}
                                                    {log.target && (
                                                        <>
                                                            {' on '}
                                                            <span className="text-primary font-mono text-xs">{log.target}</span>
                                                        </>
                                                    )}
                                                </p>
                                                <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-2">
                                                    <Clock size={10} />
                                                    {(() => {
                                                        try {
                                                            const d = new Date(log.timestamp);
                                                            return isNaN(d.getTime()) ? 'recently' : formatDistanceToNow(d, { addSuffix: true });
                                                        } catch { return 'recently'; }
                                                    })()}
                                                    {ipAddress && (
                                                        <>
                                                            <span className="w-1 h-1 rounded-full bg-zinc-700" />
                                                            {ipAddress}
                                                        </>
                                                    )}
                                                </p>
                                            </div>
                                        </div>
                                        {hasMetadata && (
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

                                    {expanded === log.id && hasMetadata && (
                                        <div className="mt-3 pt-3 border-t border-border">
                                            <pre className="text-[10px] font-mono text-zinc-400 bg-black/30 p-2 rounded overflow-x-auto whitespace-pre-wrap">
                                                {JSON.stringify(log.metadata, null, 2)}
                                            </pre>
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                        })
                    )}
                </div>
            </div>
        </DashboardShell>
    );
}
