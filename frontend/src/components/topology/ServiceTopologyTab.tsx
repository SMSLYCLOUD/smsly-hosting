'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Loader2, Database, Server, HardDrive, ArrowRight } from 'lucide-react';

interface Addon {
    id: string;
    name: string;
    addon_type: string;
    status: string;
}

interface Volume {
    id: string;
    name: string;
    mount_path: string;
    size_gb: number;
}

function getHeaders(): Record<string, string> {
    const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
    return token ? { 'Authorization': `Token ${token}` } : {};
}

function apiUrl(path: string) {
    const base = typeof window !== 'undefined' ? `${window.location.origin}/api/v1` : '/api/v1';
    return `${base}${path}`;
}

const ADDON_COLORS: Record<string, { bg: string; border: string; text: string; icon: string }> = {
    POSTGRES: { bg: 'bg-blue-500/10', border: 'border-blue-500/30', text: 'text-blue-400', icon: '🐘' },
    REDIS:    { bg: 'bg-red-500/10', border: 'border-red-500/30', text: 'text-red-400', icon: '🔴' },
    MYSQL:    { bg: 'bg-cyan-500/10', border: 'border-cyan-500/30', text: 'text-cyan-400', icon: '🐬' },
    MONGODB:  { bg: 'bg-green-500/10', border: 'border-green-500/30', text: 'text-green-400', icon: '🍃' },
};

export function ServiceTopologyTab({ serviceId, serviceName }: { serviceId: string; serviceName: string }) {
    const [addons, setAddons] = useState<Addon[]>([]);
    const [volumes, setVolumes] = useState<Volume[]>([]);
    const [loading, setLoading] = useState(true);

    const fetchData = useCallback(async () => {
        try {
            const [addonsRes, volumesRes] = await Promise.all([
                fetch(apiUrl('/addons/'), { headers: getHeaders() }),
                fetch(apiUrl(`/services/${serviceId}/storage/`), { headers: getHeaders() }).catch(() => null),
            ]);

            if (addonsRes.ok) {
                const data = await addonsRes.json();
                const list = Array.isArray(data) ? data : (data?.results || []);
                setAddons(list.filter((a: any) => a.service === serviceId));
            }

            if (volumesRes?.ok) {
                const data = await volumesRes.json();
                setVolumes(Array.isArray(data) ? data : (data?.results || []));
            }
        } catch (e) {
            console.error('Topology fetch error:', e);
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    useEffect(() => {
        fetchData();
        const interval = setInterval(fetchData, 10000);
        return () => clearInterval(interval);
    }, [fetchData]);

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64 text-muted-foreground gap-2">
                <Loader2 className="w-5 h-5 animate-spin" /> Loading topology...
            </div>
        );
    }

    const statusDot = (s: string) => {
        if (s === 'ACTIVE') return 'bg-emerald-500';
        if (s === 'PROVISIONING') return 'bg-yellow-500 animate-pulse';
        if (s === 'FAILED') return 'bg-red-500';
        return 'bg-zinc-500';
    };

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            <div>
                <h2 className="text-xl font-bold text-foreground">Service Topology</h2>
                <p className="text-sm text-muted-foreground mt-1">Visual map of {serviceName}&apos;s connected infrastructure</p>
            </div>

            <div className="bg-card border border-border rounded-xl p-8 min-h-[400px]">
                {/* Central Service Node */}
                <div className="flex flex-col items-center">
                    <div className="relative">
                        <div className="w-32 h-32 rounded-2xl bg-primary/10 border-2 border-primary flex flex-col items-center justify-center gap-2 shadow-lg shadow-primary/10">
                            <Server className="w-8 h-8 text-primary" />
                            <span className="text-sm font-bold text-foreground truncate max-w-[100px]">{serviceName}</span>
                            <span className="text-[10px] font-medium text-primary uppercase">Service</span>
                        </div>
                        <div className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-emerald-500 border-2 border-card" />
                    </div>

                    {/* Connections */}
                    {(addons.length > 0 || volumes.length > 0) && (
                        <>
                            <div className="w-px h-8 bg-border" />
                            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                <ArrowRight size={10} /> Connected Resources
                            </div>
                            <div className="w-px h-8 bg-border" />
                        </>
                    )}

                    {/* Addon + Volume grid */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 mt-2 w-full max-w-3xl">
                        {addons.map(addon => {
                            const colors = ADDON_COLORS[addon.addon_type] || ADDON_COLORS.POSTGRES;
                            return (
                                <div
                                    key={addon.id}
                                    className={`p-5 rounded-xl border-2 ${colors.bg} ${colors.border} flex flex-col items-center gap-2 transition-transform hover:scale-105`}
                                >
                                    <span className="text-3xl">{colors.icon}</span>
                                    <span className="text-sm font-semibold text-foreground">{addon.name}</span>
                                    <span className={`text-xs font-medium ${colors.text}`}>{addon.addon_type}</span>
                                    <div className="flex items-center gap-1.5 mt-1">
                                        <div className={`w-2 h-2 rounded-full ${statusDot(addon.status)}`} />
                                        <span className="text-[10px] text-muted-foreground uppercase">{addon.status}</span>
                                    </div>
                                </div>
                            );
                        })}

                        {volumes.map(vol => (
                            <div
                                key={vol.id}
                                className="p-5 rounded-xl border-2 bg-amber-500/10 border-amber-500/30 flex flex-col items-center gap-2 transition-transform hover:scale-105"
                            >
                                <HardDrive className="w-7 h-7 text-amber-400" />
                                <span className="text-sm font-semibold text-foreground">{vol.name}</span>
                                <span className="text-xs text-amber-400">{vol.mount_path}</span>
                                <span className="text-[10px] text-muted-foreground">{vol.size_gb} GB</span>
                            </div>
                        ))}
                    </div>

                    {addons.length === 0 && volumes.length === 0 && (
                        <div className="mt-8 text-center">
                            <Database className="w-10 h-10 text-muted-foreground/30 mx-auto mb-3" />
                            <p className="text-sm text-muted-foreground">No connected resources</p>
                            <p className="text-xs text-muted-foreground/60 mt-1">Add addons from the Addons tab to see them here</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
