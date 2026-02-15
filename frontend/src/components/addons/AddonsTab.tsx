'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Database, Plus, Trash2, RefreshCw, Download, Shield, Loader2, Server } from 'lucide-react';

interface Addon {
    id: string;
    service: string;
    name: string;
    addon_type: 'POSTGRES' | 'REDIS' | 'MYSQL' | 'MONGODB';
    status: 'PROVISIONING' | 'ACTIVE' | 'FAILED' | 'DELETED';
    created_at: string;
}

interface Backup {
    id: string;
    addon: string;
    status: 'PENDING' | 'COMPLETED' | 'FAILED';
    size_bytes: number;
    created_at: string;
    completed_at?: string;
    error_message?: string;
}

const ADDON_TYPES = [
    { value: 'POSTGRES', label: 'PostgreSQL', icon: '🐘', color: 'text-blue-400' },
    { value: 'REDIS', label: 'Redis', icon: '🔴', color: 'text-red-400' },
    { value: 'MYSQL', label: 'MySQL', icon: '🐬', color: 'text-cyan-400' },
    { value: 'MONGODB', label: 'MongoDB', icon: '🍃', color: 'text-green-400' },
];

function getHeaders() {
    const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
    return token ? { 'Authorization': `Token ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
}

function apiUrl(path: string) {
    const base = typeof window !== 'undefined' ? `${window.location.origin}/api/v1` : '/api/v1';
    return `${base}${path}`;
}

export function AddonsTab({ serviceId }: { serviceId: string }) {
    const [addons, setAddons] = useState<Addon[]>([]);
    const [loading, setLoading] = useState(true);
    const [creating, setCreating] = useState(false);
    const [showCreate, setShowCreate] = useState(false);
    const [newType, setNewType] = useState('POSTGRES');
    const [newName, setNewName] = useState('');
    const [backups, setBackups] = useState<Record<string, Backup[]>>({});
    const [expandedAddon, setExpandedAddon] = useState<string | null>(null);

    const fetchAddons = useCallback(async () => {
        try {
            const res = await fetch(apiUrl('/addons/'), { headers: getHeaders() });
            if (res.ok) {
                const data = await res.json();
                const list = Array.isArray(data) ? data : (data?.results || []);
                // Filter to only this service's addons
                setAddons(list.filter((a: Addon) => a.service === serviceId));
            }
        } catch (e) {
            console.error('Failed to fetch addons:', e);
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    useEffect(() => { fetchAddons(); }, [fetchAddons]);

    const handleCreate = async () => {
        setCreating(true);
        try {
            const name = newName || `${newType.toLowerCase()}-${Date.now().toString(36)}`;
            const res = await fetch(apiUrl('/addons/'), {
                method: 'POST',
                headers: getHeaders(),
                body: JSON.stringify({ service: serviceId, addon_type: newType, name }),
            });
            if (res.ok) {
                setShowCreate(false);
                setNewName('');
                fetchAddons();
            }
        } catch (e) {
            console.error('Failed to create addon:', e);
        } finally {
            setCreating(false);
        }
    };

    const handleDeprovision = async (addonId: string) => {
        if (!confirm('This will permanently delete this addon and all its data. Continue?')) return;
        try {
            await fetch(apiUrl(`/addons/${addonId}/deprovision/`), {
                method: 'POST',
                headers: getHeaders(),
            });
            fetchAddons();
        } catch (e) {
            console.error('Failed to deprovision:', e);
        }
    };

    const handleBackup = async (addonId: string) => {
        try {
            await fetch(apiUrl(`/addons/${addonId}/backup/`), {
                method: 'POST',
                headers: getHeaders(),
            });
            // Refresh backups
            fetchBackups(addonId);
        } catch (e) {
            console.error('Failed to trigger backup:', e);
        }
    };

    const fetchBackups = async (addonId: string) => {
        try {
            const res = await fetch(apiUrl(`/addons/${addonId}/backups/`), { headers: getHeaders() });
            if (res.ok) {
                const data = await res.json();
                setBackups(prev => ({ ...prev, [addonId]: Array.isArray(data) ? data : [] }));
            }
        } catch (e) {
            console.error('Failed to fetch backups:', e);
        }
    };

    const toggleExpand = (addonId: string) => {
        if (expandedAddon === addonId) {
            setExpandedAddon(null);
        } else {
            setExpandedAddon(addonId);
            fetchBackups(addonId);
        }
    };

    const statusColor = (s: string) => {
        switch (s) {
            case 'ACTIVE': return 'bg-emerald-500/10 text-emerald-500';
            case 'PROVISIONING': return 'bg-yellow-500/10 text-yellow-500';
            case 'FAILED': return 'bg-red-500/10 text-red-500';
            default: return 'bg-zinc-500/10 text-zinc-500';
        }
    };

    const addonMeta = (type: string) => ADDON_TYPES.find(t => t.value === type) || ADDON_TYPES[0];

    if (loading) {
        return (
            <div className="flex items-center justify-center h-64 text-muted-foreground gap-2">
                <Loader2 className="w-5 h-5 animate-spin" /> Loading addons...
            </div>
        );
    }

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-xl font-bold text-foreground">Infrastructure Addons</h2>
                    <p className="text-sm text-muted-foreground mt-1">Databases and caches attached to this service</p>
                </div>
                <button
                    onClick={() => setShowCreate(!showCreate)}
                    className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg font-medium text-sm hover:bg-primary/90 transition-colors"
                >
                    <Plus size={16} /> Add Addon
                </button>
            </div>

            {/* Create Form */}
            {showCreate && (
                <div className="bg-card border border-border rounded-xl p-6 space-y-4">
                    <h3 className="font-semibold text-foreground">Provision New Addon</h3>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        {ADDON_TYPES.map(type => (
                            <button
                                key={type.value}
                                onClick={() => setNewType(type.value)}
                                className={`p-4 rounded-lg border-2 text-left transition-all ${
                                    newType === type.value
                                        ? 'border-primary bg-primary/5'
                                        : 'border-border hover:border-muted-foreground/30'
                                }`}
                            >
                                <span className="text-2xl">{type.icon}</span>
                                <p className={`font-semibold mt-2 ${type.color}`}>{type.label}</p>
                            </button>
                        ))}
                    </div>
                    <div className="flex gap-3">
                        <input
                            type="text"
                            placeholder="Addon name (optional)"
                            value={newName}
                            onChange={e => setNewName(e.target.value)}
                            className="flex-1 p-2.5 bg-background border border-border rounded-lg text-sm"
                        />
                        <button
                            onClick={handleCreate}
                            disabled={creating}
                            className="px-6 py-2.5 bg-primary text-primary-foreground rounded-lg font-medium text-sm hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2"
                        >
                            {creating ? <Loader2 size={14} className="animate-spin" /> : <Server size={14} />}
                            {creating ? 'Provisioning...' : 'Provision'}
                        </button>
                    </div>
                </div>
            )}

            {/* Addon List */}
            {addons.length === 0 && !showCreate ? (
                <div className="bg-card border border-border rounded-xl p-12 text-center">
                    <Database className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
                    <h3 className="font-semibold text-foreground mb-2">No Addons Yet</h3>
                    <p className="text-sm text-muted-foreground mb-4">
                        Add a PostgreSQL, Redis, MySQL, or MongoDB database to this service.
                    </p>
                    <button
                        onClick={() => setShowCreate(true)}
                        className="px-4 py-2 bg-primary text-primary-foreground rounded-lg font-medium text-sm hover:bg-primary/90"
                    >
                        Add Your First Addon
                    </button>
                </div>
            ) : (
                <div className="space-y-3">
                    {addons.map(addon => {
                        const meta = addonMeta(addon.addon_type);
                        const isExpanded = expandedAddon === addon.id;
                        const addonBackups = backups[addon.id] || [];

                        return (
                            <div key={addon.id} className="bg-card border border-border rounded-xl overflow-hidden">
                                <div
                                    className="p-5 flex items-center justify-between cursor-pointer hover:bg-muted/30 transition-colors"
                                    onClick={() => toggleExpand(addon.id)}
                                >
                                    <div className="flex items-center gap-4">
                                        <span className="text-3xl">{meta.icon}</span>
                                        <div>
                                            <h4 className="font-semibold text-foreground">{addon.name}</h4>
                                            <p className={`text-xs font-medium ${meta.color}`}>{meta.label}</p>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-3">
                                        <span className={`px-2.5 py-1 rounded text-xs font-bold uppercase ${statusColor(addon.status)}`}>
                                            {addon.status}
                                        </span>
                                        <span className="text-xs text-muted-foreground">
                                            {new Date(addon.created_at).toLocaleDateString()}
                                        </span>
                                    </div>
                                </div>

                                {isExpanded && (
                                    <div className="border-t border-border p-5 space-y-4 bg-muted/10">
                                        {/* Actions */}
                                        <div className="flex gap-2">
                                            <button
                                                onClick={(e) => { e.stopPropagation(); handleBackup(addon.id); }}
                                                className="flex items-center gap-2 px-3 py-2 bg-blue-500/10 text-blue-400 rounded-lg text-xs font-medium hover:bg-blue-500/20 transition-colors"
                                            >
                                                <Shield size={12} /> Create Backup
                                            </button>
                                            <button
                                                onClick={(e) => { e.stopPropagation(); fetchBackups(addon.id); }}
                                                className="flex items-center gap-2 px-3 py-2 bg-muted text-muted-foreground rounded-lg text-xs font-medium hover:bg-muted/80 transition-colors"
                                            >
                                                <RefreshCw size={12} /> Refresh
                                            </button>
                                            <button
                                                onClick={(e) => { e.stopPropagation(); handleDeprovision(addon.id); }}
                                                className="flex items-center gap-2 px-3 py-2 bg-red-500/10 text-red-400 rounded-lg text-xs font-medium hover:bg-red-500/20 transition-colors ml-auto"
                                            >
                                                <Trash2 size={12} /> Delete
                                            </button>
                                        </div>

                                        {/* Backups */}
                                        <div>
                                            <h5 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">Backups</h5>
                                            {addonBackups.length === 0 ? (
                                                <p className="text-xs text-muted-foreground">No backups yet. Create one above.</p>
                                            ) : (
                                                <div className="space-y-2">
                                                    {addonBackups.map(b => (
                                                        <div key={b.id} className="flex items-center justify-between p-3 bg-background rounded-lg border border-border">
                                                            <div className="flex items-center gap-3">
                                                                <Download size={14} className="text-muted-foreground" />
                                                                <div>
                                                                    <p className="text-xs font-medium text-foreground">
                                                                        {new Date(b.created_at).toLocaleString()}
                                                                    </p>
                                                                    <p className="text-xs text-muted-foreground">
                                                                        {(b.size_bytes / 1024 / 1024).toFixed(2)} MB
                                                                    </p>
                                                                </div>
                                                            </div>
                                                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                                                                b.status === 'COMPLETED' ? 'bg-emerald-500/10 text-emerald-500' :
                                                                b.status === 'FAILED' ? 'bg-red-500/10 text-red-500' :
                                                                'bg-yellow-500/10 text-yellow-500'
                                                            }`}>
                                                                {b.status}
                                                            </span>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
