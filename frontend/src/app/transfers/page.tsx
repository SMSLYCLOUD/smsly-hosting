'use client';

import React, { useCallback, useState, useEffect } from 'react';
import { PageHeader } from '@/components/ui/page-header';
import api, { servicesApi, addonsApi, serversApi } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Database, LayoutTemplate, Box, Server, CheckCircle2, ServerCog, MessagesSquare, Orbit, Globe } from 'lucide-react';
import { toast } from 'sonner';
import { featureFlags, featureDisabledReason } from '@/lib/featureFlags';
import { shouldShowAllNav } from '@/lib/nav-visibility';
import { parseApiError } from '@/lib/apiError';

export default function TransfersPage() {
    const [servers, setServers] = useState<any[]>([]);
    const [services, setServices] = useState<any[]>([]);
    const [addons, setAddons] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [transfers, setTransfers] = useState<any[]>([]);
    const [transfersLoading, setTransfersLoading] = useState(false);
    const [targetDomain, setTargetDomain] = useState('');

    // Grouping structure for DnD
    const [groupedServices, setGroupedServices] = useState<Record<string, any[]>>({});
    const showAll = shouldShowAllNav();
    const transferDisabled = !featureFlags.transfers && !showAll;
    const workloadServers = servers.filter(server => !server.is_primary && server.allow_user_workloads !== false);
    const transferBlockedNoTarget = workloadServers.length === 0;

    const fetchTransfers = useCallback(async () => {
        setTransfersLoading(true);
        try {
            const res = await api.get('/transfers/');
            setTransfers(Array.isArray(res.data) ? res.data : res.data?.results || []);
        } catch (error) {
            console.error("Failed to fetch transfers", error);
        } finally {
            setTransfersLoading(false);
        }
    }, []);

    const fetchData = useCallback(async () => {
        try {
            const [serversData, servicesData, addonsData] = await Promise.all([
                serversApi.list(),
                servicesApi.list(),
                addonsApi.list(),
            ]);
            setServers(serversData);
            setServices(servicesData);
            setAddons(addonsData);

            const addonsByService: Record<string, any[]> = {};
            addonsData.forEach((addon: any) => {
                const sId = addon.service;
                if (sId) {
                    if (!addonsByService[sId]) addonsByService[sId] = [];
                    addonsByService[sId].push(addon);
                }
            });

            const grouped: Record<string, any[]> = { local: [] };
            serversData
                .filter((srv: any) => !srv.is_primary && srv.allow_user_workloads !== false)
                .forEach((srv: any) => {
                    grouped[srv.id] = [];
                });

            const linkedAddonIds = new Set();

            servicesData.forEach((srv: any) => {
                const serverId = grouped[srv.server] ? srv.server : 'local';
                const linkedAddons = addonsByService[srv.id] || [];
                linkedAddons.forEach(a => linkedAddonIds.add(a.id));
                grouped[serverId].push({
                    ...srv,
                    type: 'service',
                    addons: linkedAddons
                });
            });

            addonsData.forEach((addon: any) => {
                if (!linkedAddonIds.has(addon.id)) {
                    const serverId = grouped[addon.server] ? addon.server : 'local';
                    grouped[serverId].push({ ...addon, type: 'addon', addons: [] });
                }
            });

            setGroupedServices(grouped);
        } catch (error) {
            console.error("Failed to fetch transfer data", error);
            toast.error("Failed to load connected servers or services");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchData();
        fetchTransfers();
        const interval = setInterval(fetchTransfers, 5000);
        return () => clearInterval(interval);
    }, [fetchData, fetchTransfers]);

    // ─────────────────────────────────────────────────────────────────
    // DnD Handlers
    // ─────────────────────────────────────────────────────────────────
    const handleDragStart = (e: React.DragEvent, itemId: string, itemType: string, sourceServerId: string) => {
        e.dataTransfer.setData('itemId', itemId);
        e.dataTransfer.setData('itemType', itemType);
        e.dataTransfer.setData('sourceServerId', sourceServerId);
    };

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault(); // Necessary to allow dropping
    };

    const handleDrop = async (e: React.DragEvent, targetServerId: string) => {
        e.preventDefault();
        const itemId = e.dataTransfer.getData('itemId');
        const itemType = e.dataTransfer.getData('itemType');
        const sourceServerId = e.dataTransfer.getData('sourceServerId');

        if (!itemId || sourceServerId === targetServerId) return;

        const targetServer = servers.find(server => server.id === targetServerId);
        if (targetServerId !== 'local' && (!targetServer || targetServer.allow_user_workloads === false)) {
            toast.error("Select a workload-enabled server.");
            return;
        }

        // Optimistic UI update
        const itemToMove = groupedServices[sourceServerId].find(item => item.id === itemId);
        if (!itemToMove) return;

        setGroupedServices(prev => {
            const next = { ...prev };
            next[sourceServerId] = next[sourceServerId].filter(i => i.id !== itemId);
            next[targetServerId] = [...(next[targetServerId] || []), itemToMove];
            return next;
        });

            // Trigger API transfer request
        try {
            const endpoint = `/transfers/`;
            const payload: any = {
                transfer_type: 'SERVICE',
            };
            if (sourceServerId !== 'local') payload.source_server_id = sourceServerId;
            if (targetServerId !== 'local') payload.target_server_id = targetServerId;

            if (itemType === 'service') {
                payload.service_id = itemId;
            } else if (itemType === 'addon') {
                const addon = addons.find((a: any) => a.id === itemId);
                payload.service_id = addon?.service;
            }

            // If cross-platform migration, prompt for target domain
            if (targetDomain) payload.target_public_domain = targetDomain;

            await api.post(endpoint, payload);
            toast.success(`Transfer initiated to ${getServerName(targetServerId)}`);
            fetchTransfers();
        } catch (error: any) {
            console.error("Transfer failed", error);
            toast.error(parseApiError(error, "Transfer request failed"));

            // Revert UI on failure
            setGroupedServices(prev => {
                const next = { ...prev };
                next[targetServerId] = next[targetServerId].filter(i => i.id !== itemId);
                next[sourceServerId] = [...(next[sourceServerId] || []), itemToMove];
                return next;
            });
        }
    };

    const getServerName = (id: string) => {
        if (id === 'local') return 'Local Server (This Node)';
        const srv = servers.find(s => s.id === id);
        return srv ? srv.name : 'Unknown Server';
    };

    const renderItemIcon = (type: string, itemType: string) => {
        if (itemType === 'addon') return <Database className="w-4 h-4 text-blue-500" />;
        if (type === 'template') return <LayoutTemplate className="w-4 h-4 text-emerald-500" />;
        return <Box className="w-4 h-4 text-emerald-500" />;
    };

    // ─────────────────────────────────────────────────────────────────
    // Render
    // ─────────────────────────────────────────────────────────────────
    return (
        <main className="h-screen min-h-0 flex flex-col premium-bg transition-colors duration-500 overflow-hidden">
            {/* Top Bar */}
            <div className="z-20 border-b border-zinc-800/60 bg-[#070a12]/85 backdrop-blur-xl">
                <div className="mx-auto w-full max-w-[1440px] px-6 py-4 flex items-center justify-between">
                    <div className="space-y-1">
                        <div className="flex items-center gap-3">
                            <h1 className="text-xl font-bold tracking-tight text-white">Grid Transfer Hub</h1>
                            <span className="rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider text-emerald-400">
                                Live Migration
                            </span>
                        </div>
                        <p className="text-xs text-zinc-400">
                            Orchestrate seamless workload migration between your nodes via secure P2P channels.
                        </p>
                    </div>
                    
                    <div className="flex items-center gap-3">
                        <div className="hidden md:flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-zinc-900/50 border border-zinc-800">
                            <Server className="w-3.5 h-3.5 text-zinc-500" />
                            <span className="text-[11px] font-medium text-zinc-400">Transfer Targets: {workloadServers.length}</span>
                        </div>
                        <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-900/20 border border-violet-500/20">
                            <Globe className="w-3.5 h-3.5 text-violet-400" />
                            <input
                                value={targetDomain}
                                onChange={e => setTargetDomain(e.target.value)}
                                placeholder="New domain (optional)"
                                className="bg-transparent text-[11px] text-violet-300 placeholder:text-zinc-600 outline-none w-40"
                            />
                        </div>
                        <Button 
                            variant="outline" 
                            size="sm"
                            className="h-8 rounded-full border-zinc-700 bg-zinc-900/80 text-xs font-semibold text-zinc-200 hover:bg-zinc-800"
                            onClick={() => window.location.reload()}
                        >
                            Refresh Fleet
                        </Button>
                    </div>
                </div>
            </div>

            {/* Main Content Area */}
            <div className="relative flex-1 min-h-0 overflow-hidden bg-dot-pattern p-6">
                {loading ? (
                    <div className="flex flex-col items-center justify-center h-full gap-4">
                        <div className="h-10 w-10 animate-spin rounded-full border-2 border-emerald-500 border-t-transparent shadow-[0_0_15px_rgba(16,185,129,0.3)]"></div>
                        <p className="text-sm font-medium text-emerald-500/80 animate-pulse">Scanning Neural Mesh...</p>
                    </div>
                ) : (
                    <div className="h-full flex gap-6 max-w-[1600px] mx-auto">
                        {/* Server Grid */}
                        <div className="flex-1 overflow-x-auto">
                            <div className="inline-flex gap-6 h-full min-w-full pb-4">
                                {/* Local Server */}
                                <ServerColumn
                                    id="local"
                                    name="Primary Node (Local)"
                                    items={groupedServices['local'] || []}
                                    isLocal={true}
                                    onDragStart={handleDragStart}
                                    onDragOver={handleDragOver}
                                    onDrop={handleDrop}
                                    renderItemIcon={renderItemIcon}
                                />

                                {/* Remote Servers */}
                                {workloadServers.map(server => (
                                    <ServerColumn
                                        key={server.id}
                                        id={server.id}
                                        name={server.name}
                                        items={groupedServices[server.id] || []}
                                        isLocal={false}
                                        onDragStart={handleDragStart}
                                        onDragOver={handleDragOver}
                                        onDrop={handleDrop}
                                        renderItemIcon={renderItemIcon}
                                    />
                                ))}
                            </div>
                        </div>

                        {/* Status Sidebar */}
                        <div className="w-80 flex flex-col gap-6 shrink-0">
                            <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 backdrop-blur-md p-5 shadow-2xl">
                                <div className="flex items-center gap-2 mb-4">
                                    <MessagesSquare className="w-4 h-4 text-emerald-400" />
                                    <h3 className="text-sm font-bold text-white uppercase tracking-wider">Active Stream</h3>
                                </div>
                                {transfersLoading && transfers.length === 0 ? (
                                    <div className="rounded-xl border border-dashed border-zinc-800 bg-black/20 p-8 flex flex-col items-center justify-center text-center">
                                        <p className="text-xs text-zinc-500 italic">Loading transfer state...</p>
                                    </div>
                                ) : transfers.length === 0 ? (
                                    <div className="rounded-xl border border-dashed border-zinc-800 bg-black/20 p-8 flex flex-col items-center justify-center text-center">
                                        <p className="text-xs text-zinc-500 italic">No transfer jobs are running.</p>
                                    </div>
                                ) : (
                                    <div className="space-y-3 max-h-72 overflow-y-auto pr-1 custom-scrollbar">
                                        {transfers.slice(0, 6).map((transfer: any) => (
                                            <div key={transfer.id} className="rounded-xl border border-zinc-800 bg-black/30 p-3">
                                                <div className="flex items-center justify-between gap-2">
                                                    <span className="text-[10px] font-bold text-zinc-400 uppercase">{transfer.transfer_type}</span>
                                                    <span className={`text-[10px] font-bold uppercase ${
                                                        transfer.status === 'COMPLETED'
                                                            ? 'text-emerald-400'
                                                            : transfer.status === 'FAILED'
                                                              ? 'text-red-400'
                                                              : 'text-amber-400'
                                                    }`}>
                                                        {transfer.status}
                                                    </span>
                                                </div>
                                                <div className="mt-2 h-1.5 rounded-full bg-zinc-800 overflow-hidden">
                                                    <div
                                                        className="h-full bg-emerald-500 transition-all"
                                                        style={{ width: `${Math.max(0, Math.min(100, transfer.progress_percent || 0))}%` }}
                                                    />
                                                </div>
                                                <p className="mt-2 text-[11px] text-zinc-400 leading-relaxed">
                                                    {transfer.current_step || transfer.error_message || `${transfer.source_server_ip} -> ${transfer.target_server_ip}`}
                                                </p>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            <div className="flex-1 rounded-2xl border border-zinc-800 bg-zinc-900/40 backdrop-blur-md p-5 flex flex-col shadow-2xl overflow-hidden">
                                <div className="flex items-center gap-2 mb-4">
                                    <ServerCog className="w-4 h-4 text-zinc-500" />
                                    <h3 className="text-sm font-bold text-white uppercase tracking-wider">Telemetry Log</h3>
                                </div>
                                <div className="flex-1 overflow-y-auto space-y-3 pr-2 scrollbar-hide">
                                    {transfers.slice(0, 8).map((transfer: any) => (
                                        <div key={`log-${transfer.id}`} className="p-3 rounded-lg bg-black/30 border border-zinc-800/50">
                                            <div className="flex justify-between items-center mb-1">
                                                <span className="text-[10px] font-bold text-zinc-600 uppercase">{transfer.status}</span>
                                                <span className="text-[9px] text-zinc-700">
                                                    {transfer.created_at ? new Date(transfer.created_at).toLocaleTimeString() : ''}
                                                </span>
                                            </div>
                                            <p className="text-[11px] text-zinc-400 leading-relaxed">
                                                {transfer.error_message || transfer.current_step || 'Transfer queued.'}
                                            </p>
                                        </div>
                                    ))}
                                    {transfers.length === 0 && (
                                        <p className="text-center py-10 text-[10px] text-zinc-600 font-medium">Transfer events will appear here.</p>
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        </main>
    );
}

// ─────────────────────────────────────────────────────────────────
// Server Column Component
// ─────────────────────────────────────────────────────────────────
function ServerColumn({
    id, name, items, isLocal,
    onDragStart, onDragOver, onDrop, renderItemIcon
}: any) {
    return (
        <div
            className={`flex flex-col w-[360px] h-full rounded-2xl border backdrop-blur-md shadow-2xl transition-all duration-300 ${
                isLocal 
                ? "bg-emerald-950/10 border-emerald-500/20 shadow-emerald-900/5" 
                : "bg-zinc-900/40 border-zinc-800"
            }`}
            onDragOver={onDragOver}
            onDrop={(e) => onDrop(e, id)}
        >
            {/* Header */}
            <div className={`px-5 py-4 border-b flex items-center justify-between rounded-t-2xl ${
                isLocal ? "border-emerald-500/20 bg-emerald-500/5" : "border-zinc-800 bg-zinc-900/50"
            }`}>
                <div className="flex items-center gap-2.5">
                    <div className={`p-2 rounded-lg ${isLocal ? "bg-emerald-500/20" : "bg-zinc-800"}`}>
                        <Server className={`w-4 h-4 ${isLocal ? "text-emerald-400" : "text-zinc-400"}`} />
                    </div>
                    <div className="min-w-0">
                        <h3 className="text-sm font-bold text-white truncate">{name}</h3>
                        <p className="text-[10px] text-zinc-500 font-medium uppercase tracking-tighter">
                            {isLocal ? "Authoritative Source" : "Target Node"}
                        </p>
                    </div>
                </div>
                {isLocal ? (
                    <div className="flex items-center gap-1.5">
                        <span className="relative flex h-2 w-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                        </span>
                        <span className="text-[10px] font-bold text-emerald-400 uppercase tracking-widest">Live</span>
                    </div>
                ) : (
                    <span className="text-[10px] font-bold text-zinc-600 uppercase">Ready</span>
                )}
            </div>

            {/* Body */}
            <div className="p-4 flex-1 overflow-y-auto space-y-3 bg-transparent custom-scrollbar">
                {items.length === 0 ? (
                    <div className="h-full flex flex-col items-center justify-center text-center gap-3 opacity-40">
                        <div className="p-4 rounded-full border-2 border-dashed border-zinc-800">
                           <Box className="w-8 h-8 text-zinc-700" />
                        </div>
                        <p className="text-xs text-zinc-500 font-medium italic">Empty Node</p>
                    </div>
                ) : (
                    items.map((item: any) => (
                        <div
                            key={item.id}
                            draggable={true}
                            onDragStart={(e) => onDragStart(e, item.id, item.type, id)}
                            className={`group relative border rounded-xl p-4 flex items-center justify-between transition-all duration-300 cursor-grab active:cursor-grabbing ${
                                isLocal 
                                ? "bg-zinc-900/60 border-zinc-800 hover:border-emerald-500/40 hover:bg-zinc-800/80 hover:shadow-[0_0_20px_rgba(16,185,129,0.1)]" 
                                : "bg-zinc-900/60 border-zinc-800 hover:border-violet-500/40 hover:bg-zinc-800/80 hover:shadow-[0_0_20px_rgba(139,92,246,0.1)]"
                            }`}
                        >
                            <div className="flex items-center gap-3.5">
                                <div className={`p-2 rounded-lg transition-colors ${
                                    isLocal ? 'bg-zinc-800 group-hover:bg-emerald-500/20' : 'bg-zinc-900'
                                }`}>
                                    {renderItemIcon(item.source_type, item.type)}
                                </div>
                                <div className="min-w-0">
                                    <p className={`text-sm font-bold truncate ${isLocal ? 'text-white' : 'text-zinc-400'}`}>
                                        {item.name}
                                    </p>
                                    <div className="flex items-center gap-1.5 mt-0.5">
                                        <span className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${
                                            item.type === 'addon' ? 'bg-blue-500/10 text-blue-400' : 'bg-emerald-500/10 text-emerald-400'
                                        }`}>
                                            {item.type}
                                        </span>
                                        {item.addons?.length > 0 && (
                                            <span className="text-[9px] font-bold text-blue-400 uppercase tracking-tight">
                                                + {item.addons.length} Addons
                                            </span>
                                        )}
                                        {item.is_public && (
                                            <span className="text-[9px] text-zinc-500">Public</span>
                                        )}
                                    </div>
                                </div>
                            </div>
                            
                            {item.addons?.length > 0 && (
                                <div className="flex -space-x-1.5 ml-2 overflow-hidden">
                                    {item.addons.slice(0, 3).map((a: any) => (
                                        <div key={a.id} className="w-5 h-5 rounded-md bg-zinc-800 border border-zinc-700 flex items-center justify-center shadow-lg" title={a.name}>
                                            <Database className="w-2.5 h-2.5 text-blue-500" />
                                        </div>
                                    ))}
                                    {item.addons.length > 3 && (
                                        <div className="w-5 h-5 rounded-md bg-zinc-900 border border-zinc-800 flex items-center justify-center text-[8px] font-bold text-zinc-500">
                                            +{item.addons.length - 3}
                                        </div>
                                    )}
                                </div>
                            )}

                            {isLocal && (
                                <div className="opacity-0 group-hover:opacity-100 transition-opacity ml-2 shrink-0">
                                    <Orbit className="w-4 h-4 text-emerald-500 animate-spin-slow" />
                                </div>
                            )}
                        </div>
                    ))
                )}
            </div>
            
            {/* Footer */}
            <div className="px-5 py-3 border-t border-zinc-800/50 bg-black/10 flex items-center justify-between">
                <span className="text-[10px] font-bold text-zinc-500 uppercase">{items.length} Workloads</span>
                <p className="text-[9px] text-zinc-600 font-medium">DRAG TO MOVE</p>
            </div>
        </div>
    );
}
