'use client';

import React, { useState, useEffect } from 'react';
import { PageHeader } from '@/components/ui/page-header';
import api, { servicesApi, addonsApi, serversApi } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Database, LayoutTemplate, Box, Server, CheckCircle2, ServerCog, MessagesSquare } from 'lucide-react';
import { toast } from 'sonner';
import { featureFlags, featureDisabledReason } from '@/lib/featureFlags';
import { parseApiError } from '@/lib/apiError';

export default function TransfersPage() {
    const [servers, setServers] = useState<any[]>([]);
    const [services, setServices] = useState<any[]>([]);
    const [addons, setAddons] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    // Grouping structure for DnD
    const [groupedServices, setGroupedServices] = useState<Record<string, any[]>>({});
    const transferDisabled = !featureFlags.transfers;
    const transferBlockedNoTarget = servers.length === 0;

    useEffect(() => {
        const fetchData = async () => {
            try {
                // Fetch connected servers
                const serversRes = await serversApi.list();
                const serversData = serversRes;
                setServers(serversData);

                // Fetch services
                const servicesRes = await servicesApi.list();
                const servicesData = servicesRes;
                setServices(servicesData);

                // Fetch addons
                const addonsRes = await addonsApi.list();
                const addonsData = addonsRes;
                setAddons(addonsData);

                // Group by server
                const grouped: Record<string, any[]> = {};
                // Initialize with all servers including local node
                grouped['local'] = []; // Assume local has id 'local' or we use empty for default
                serversData.forEach((srv: any) => {
                    grouped[srv.id] = [];
                });

                // Add services
                servicesData.forEach((srv: any) => {
                    const serverId = srv.server || 'local';
                    if (!grouped[serverId]) grouped[serverId] = [];
                    grouped[serverId].push({ ...srv, type: 'service' });
                });

                // Add addons
                addonsData.forEach((addon: any) => {
                    const serverId = addon.server || 'local';
                    if (!grouped[serverId]) grouped[serverId] = [];
                    grouped[serverId].push({ ...addon, type: 'addon' });
                });

                setGroupedServices(grouped);
            } catch (error) {
                console.error("Failed to fetch transfer data", error);
                toast.error("Failed to load connected servers or services");
            } finally {
                setLoading(false);
            }
        };
        fetchData();
    }, []);

    // ─────────────────────────────────────────────────────────────────
    // DnD Handlers
    // ─────────────────────────────────────────────────────────────────
    const handleDragStart = (e: React.DragEvent, itemId: string, itemType: string, sourceServerId: string) => {
        // Only allow dragging from the local server
        if (sourceServerId !== 'local') {
            e.preventDefault();
            toast.error("Transfers must originate from the Local Server.");
            return;
        }
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
        if (transferDisabled) { toast.error(featureDisabledReason.transfers); return; }
        if (transferBlockedNoTarget) { toast.error('Transfer requires at least one connected target server.'); return; }

        if (sourceServerId !== 'local') {
            toast.error("Transfers must originate from the Local Server.");
            return;
        }

        if (targetServerId === 'local') {
            toast.error("Cannot transfer to the Local Server. Select a remote server.");
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
                // For addons, we transfer the parent service
                const addon = addons.find((a: any) => a.id === itemId);
                payload.service_id = addon?.service;
            }

            await api.post(endpoint, payload);
            toast.success(`Transfer initiated to ${getServerName(targetServerId)}`);
        } catch (error: any) {
            console.error("Transfer failed", error);
            toast.error(parseApiError(error));

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
        if (itemType === 'addon') return <Database className="w-4 h-4 text-gray-500" />;
        if (type === 'template') return <LayoutTemplate className="w-4 h-4 text-gray-500" />;
        return <Box className="w-4 h-4 text-gray-500" />;
    };

    // ─────────────────────────────────────────────────────────────────
    // Render
    // ─────────────────────────────────────────────────────────────────
    return (
        <div className="max-w-6xl mx-auto space-y-8 p-6">
            <div>
                <h1 className="text-2xl font-bold text-gray-900 mb-2">Server Transfers</h1>
                <p className="text-gray-500 text-sm">
                    Drag and drop services between connected servers to seamlessly migrate data and traffic.
                </p>
                {(transferDisabled || transferBlockedNoTarget) && (
                  <div className="mt-3 rounded-md border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                    {transferDisabled ? featureDisabledReason.transfers : 'Transfers disabled: connect at least one remote server first.'}
                  </div>
                )}
            </div>

            {loading ? (
                <div className="flex justify-center items-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
                </div>
            ) : (
                <div className="flex gap-6 relative">
                    {/* Left/Main Column - Servers map */}
                    <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-6">
                        {/* Always show local server */}
                        <ServerColumn
                            id="local"
                            name="Local Server (This Node)"
                            items={groupedServices['local'] || []}
                            isLocal={true}
                            onDragStart={handleDragStart}
                            onDragOver={handleDragOver}
                            onDrop={handleDrop}
                            renderItemIcon={renderItemIcon}
                        />

                        {/* Connected Servers */}
                        {servers.map(server => (
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

                    {/* Right Sidebar - Status */}
                    <div className="w-80 space-y-6">
                        <div>
                            <h3 className="text-lg font-semibold text-gray-900 mb-4">Active Transfers</h3>
                            <div className="bg-gray-50 rounded-xl p-8 border border-gray-100 flex flex-col items-center justify-center text-center">
                                <p className="text-sm text-gray-500">No active transfers.</p>
                            </div>
                        </div>

                        <div>
                            <h3 className="text-lg font-semibold text-gray-900 mb-4">History</h3>
                            <p className="text-sm text-gray-500">No past transfers.</p>
                        </div>
                    </div>
                </div>
            )}
        </div>
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
            className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden flex flex-col min-h-[400px]"
            onDragOver={onDragOver}
            onDrop={(e) => onDrop(e, id)}
        >
            {/* Header */}
            <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between bg-gray-50/50">
                <div className="flex items-center gap-2">
                    <Server className="w-5 h-5 text-gray-500" />
                    <h3 className="font-semibold text-gray-900">{name}</h3>
                </div>
                {isLocal ? (
                    <span className="px-2 py-0.5 rounded text-[10px] font-bold tracking-wide uppercase bg-emerald-100 text-emerald-700">Online</span>
                ) : (
                    <span className="w-4 h-1 rounded-full bg-gray-200" />
                )}
            </div>

            {/* Body */}
            <div className="p-4 flex-1 overflow-y-auto space-y-3 bg-gray-50/20">
                {items.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-sm text-gray-400 italic">
                        No services
                    </div>
                ) : (
                    items.map((item: any) => (
                        <div
                            key={item.id}
                            draggable={isLocal}
                            onDragStart={(e) => isLocal ? onDragStart(e, item.id, item.type, id) : e.preventDefault()}
                            className={`border rounded-lg p-4 flex items-center justify-between transition-all group ${
                                isLocal ? "bg-gray-900 border-gray-800 text-white cursor-grab active:cursor-grabbing hover:border-gray-700 hover:shadow-md" : "bg-white border-gray-200 opacity-75 cursor-not-allowed"
                            }`}
                        >
                            <div className="flex items-center gap-3">
                                <div className={`p-1.5 rounded-md transition-colors ${isLocal ? 'bg-gray-800 group-hover:bg-gray-700' : 'bg-gray-50 group-hover:bg-gray-100'}`}>
                                    {renderItemIcon(item.source_type, item.type, isLocal)}
                                </div>
                                <div>
                                    <p className={`text-sm font-medium ${isLocal ? 'text-white' : 'text-gray-900'}`}>{item.name}</p>
                                </div>
                            </div>
                            <ServerCog className={`w-4 h-4 ${isLocal ? 'text-gray-400 group-hover:text-gray-300' : 'text-gray-200'}`} />
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}
