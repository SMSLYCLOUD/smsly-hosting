'use client';

import React, { useState, useEffect, useCallback } from 'react';
import Image from 'next/image';
import { Database, RotateCcw, Plus, Trash2, RefreshCw, Download, Shield, Loader2, Server, Search, MessageSquare, Zap, HardDrive, Layers, Eye, Copy, Check, Globe } from 'lucide-react';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { addonsApi, Addon } from '@/lib/api';
import { ADDON_TYPES } from '@/lib/addonConstants';
import { AddonLogsViewer } from '@/components/addons/AddonLogsViewer';


interface Backup {
    id: string;
    addon: string;
    status: 'PENDING' | 'COMPLETED' | 'FAILED';
    size_bytes: number;
    created_at: string;
    completed_at?: string;
    error_message?: string;
}


export function AddonsTab({ serviceId }: { serviceId?: string }) {
    const confirm = useConfirm();
    const [addons, setAddons] = useState<Addon[]>([]);
    const [loading, setLoading] = useState(true);
    const [creating, setCreating] = useState(false);
    const [showCreate, setShowCreate] = useState(false);
    const [newType, setNewType] = useState('POSTGRES');
    const [newName, setNewName] = useState('');
    const [newServiceId, setNewServiceId] = useState(serviceId || '');
    const [backups, setBackups] = useState<Record<string, Backup[]>>({});
    const [expandedAddon, setExpandedAddon] = useState<string | null>(null);
    const [credentials, setCredentials] = useState<Record<string, Record<string, string>>>({});
    const [revealedCreds, setRevealedCreds] = useState<Record<string, boolean>>({});
    const [copied, setCopied] = useState<string | null>(null);

    const fetchAddons = useCallback(async () => {
        try {
            const list = await addonsApi.list();
            setAddons(serviceId ? list.filter((a: Addon) => a.service === serviceId) : list);
        } catch (e) {
            console.error('Failed to fetch addons:', e);
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    useEffect(() => {
        fetchAddons();
        const interval = setInterval(fetchAddons, 10000);
        return () => clearInterval(interval);
    }, [fetchAddons]);

    const handleCreate = async () => {
        setCreating(true);
        try {
            const svcId = serviceId || newServiceId;
            if (!svcId) { alert('Select a service first'); setCreating(false); return; }
            const name = newName || `${newType.toLowerCase()}-${Date.now().toString(36)}`;
            await addonsApi.create({ service: svcId, addon_type: newType, name } as any);
            setShowCreate(false);
            setNewName('');
            fetchAddons();
        } catch (e) {
            console.error('Failed to create addon:', e);
        } finally {
            setCreating(false);
        }
    };

    const handleDeprovision = async (addonId: string) => {
        if (!await confirm({ title: 'Delete addon?', message: 'This will permanently delete this addon and all its data. Continue?', variant: 'destructive', confirmText: 'Delete' })) return;
        try {
            await addonsApi.deprovision(addonId);
            fetchAddons();
        } catch (e) {
            console.error('Failed to deprovision:', e);
        }
    };

    const handleRetryDelete = async (addonId: string) => {
        try {
            await addonsApi.retryDelete(addonId);
            fetchAddons();
        } catch (e) {
            console.error('Failed to retry delete:', e);
        }
    };

    const handleClear = async (addonId: string) => {
        if (!await confirm({ title: 'Clear addon?', message: 'This will completely remove the addon record from the database. Continue?', variant: 'destructive', confirmText: 'Clear' })) return;
        try {
            await addonsApi.delete(addonId);
            fetchAddons();
        } catch (e) {
            console.error('Failed to clear addon:', e);
        }
    };

    const handleUpdatePublicDomain = async (addonId: string, currentDomain: string | null | undefined) => {
        if (!currentDomain) {
            if (!await confirm({ title: 'Expose Addon?', message: 'This will generate a public domain and expose this addon\'s UI to the public internet securely using its generated credentials. Continue?' })) return;
            try {
                await addonsApi.expose(addonId);
                fetchAddons();
            } catch (e) {
                console.error('Failed to expose addon:', e);
                alert("Failed to expose addon");
            }
            return;
        }

        const domain = window.prompt("Enter custom public domain (or leave empty to disable public access):", currentDomain);
        if (domain === null) return; // Cancelled

        try {
            await addonsApi.update(addonId, { public_domain: domain.trim() || null });
            fetchAddons();
        } catch (e) {
            console.error('Failed to update public domain:', e);
            alert("Failed to update public domain");
        }
    };

    const handleBackup = async (addonId: string) => {
        try {
            await addonsApi.backup(addonId);
            fetchBackups(addonId);
        } catch (e) {
            console.error('Failed to trigger backup:', e);
        }
    };

    const handleToggleBucketPublic = async (addonId: string, isPublic: boolean) => {
        try {
            await addonsApi.toggleBucketPublic(addonId, isPublic);
            setAddons(prev => prev.map(a => a.id === addonId ? { ...a, is_bucket_public: isPublic } : a));
        } catch (e) {
            console.error('Failed to toggle bucket access:', e);
            alert("Failed to change bucket access policy");
        }
    };

    const fetchBackups = async (addonId: string) => {
        try {
            const data = await addonsApi.backups(addonId);
            setBackups(prev => ({ ...prev, [addonId]: data }));
        } catch (e) {
            console.error('Failed to fetch backups:', e);
        }
    };

    const toggleExpand = async (addonId: string) => {
        if (expandedAddon === addonId) {
            setExpandedAddon(null);
        } else {
            setExpandedAddon(addonId);
            fetchBackups(addonId);
            const addon = addons.find(a => a.id === addonId);
            if (addon?.status === 'ACTIVE' && !credentials[addonId]) {
                try {
                    const creds = await addonsApi.addonCredentials(addonId);
                    setCredentials(prev => ({ ...prev, [addonId]: creds }));
                } catch (e) {
                    console.error('Failed to fetch credentials:', e);
                }
            }
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
                    <p className="text-sm text-muted-foreground mt-1">
                        {serviceId ? 'Databases and caches attached to this service' : 'All addons across your services'}
                    </p>
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
                    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
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
                                <span className="text-2xl block h-6 w-6 relative">
                                    {(type as any).logo ? (
                                        <Image src={(type as any).logo} alt={type.label} className="w-full h-full object-contain" unoptimized />
                                    ) : (
                                        <span className="text-sm font-mono text-muted-foreground">?</span>
                                    )}
                                </span>
                                <p className={`font-semibold mt-2 ${type.color}`}>{type.label}</p>
                                <p className="text-[10px] text-muted-foreground mt-0.5">{type.description}</p>
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
                        Add databases, caches, search engines, and message brokers to your services.
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
                                        <span className="text-3xl block h-8 w-8 relative">
                                            {(meta as any).logo ? (
                                                <Image src={(meta as any).logo} alt={meta.label} className="w-full h-full object-contain" unoptimized />
                                            ) : (
                                                <span className="text-sm font-mono text-muted-foreground">?</span>
                                            )}
                                        </span>
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
                                                onClick={async (e) => { 
                                                    e.stopPropagation(); 
                                                    if (!await confirm({ title: 'Rebuild Addon?', message: 'This will restart and re-provision the container to apply network and label updates. Existing data will be preserved. Continue?' })) return;
                                                    try {
                                                        await addonsApi.reprovision(addon.id);
                                                        fetchAddons();
                                                    } catch (err) {
                                                        console.error('Failed to rebuild addon:', err);
                                                        alert('Failed to trigger rebuild');
                                                    }
                                                }}
                                                className="flex items-center gap-2 px-3 py-2 bg-indigo-500/10 text-indigo-400 rounded-lg text-xs font-medium hover:bg-indigo-500/20 transition-colors"
                                            >
                                                <RefreshCw size={12} /> Rebuild
                                            </button>
                                            <button
                                                onClick={(e) => { e.stopPropagation(); fetchBackups(addon.id); }}
                                                className="flex items-center gap-2 px-3 py-2 bg-muted text-muted-foreground rounded-lg text-xs font-medium hover:bg-muted/80 transition-colors"
                                            >
                                                <RefreshCw size={12} /> Refresh
                                            </button>
                                            {addon.public_domain ? (
                                                <div className="flex items-center gap-2">
                                                    <div className="flex items-center rounded-lg overflow-hidden border border-emerald-500/20">
                                                        <span
                                                            className="flex items-center gap-2 px-3 py-2 bg-emerald-500/10 text-emerald-400 text-xs font-medium h-full"
                                                        >
                                                            <Globe size={12} /> {addon.public_domain}
                                                        </span>
                                                        <button
                                                            onClick={(e) => { e.stopPropagation(); handleUpdatePublicDomain(addon.id, addon.public_domain); }}
                                                            className="flex items-center px-2 py-2 bg-emerald-500/10 border-l border-emerald-500/20 text-emerald-400 text-xs font-medium hover:bg-emerald-500/30 transition-colors h-full"
                                                            title="Edit Domain"
                                                        >
                                                            Edit
                                                        </button>
                                                    </div>
                                                    {(meta as any).has_dashboard ? (
                                                        <a
                                                            href={`https://${addon.public_domain}`}
                                                            target="_blank"
                                                            rel="noreferrer"
                                                            onClick={(e) => e.stopPropagation()}
                                                            className="flex items-center gap-2 px-3 py-2 bg-emerald-500/10 text-emerald-400 rounded-lg text-xs font-medium hover:bg-emerald-500/20 transition-colors h-full"
                                                        >
                                                            <Eye size={12} /> View Dashboard
                                                        </a>
                                                    ) : (
                                                        <span className="flex items-center gap-2 px-3 py-2 bg-zinc-500/10 text-zinc-400 rounded-lg text-xs font-medium h-full cursor-not-allowed" title="This addon type does not have an HTTP dashboard. Use TCP clients to connect.">
                                                            <Server size={12} /> TCP Service
                                                        </span>
                                                    )}
                                                </div>
                                            ) : (
                                                <button
                                                    onClick={(e) => { e.stopPropagation(); handleUpdatePublicDomain(addon.id, addon.public_domain); }}
                                                    className="flex items-center gap-2 px-3 py-2 bg-muted text-muted-foreground rounded-lg text-xs font-medium hover:bg-muted/80 transition-colors"
                                                >
                                                    <Globe size={12} /> Expose Publicly
                                                </button>
                                            )}
                                            {addon.status === 'FAILED' || addon.status === 'DELETED' ? (
                                                <button
                                                    onClick={(e) => { e.stopPropagation(); handleClear(addon.id); }}
                                                    className="flex items-center gap-2 px-3 py-2 bg-red-500/10 text-red-400 rounded-lg text-xs font-medium hover:bg-red-500/20 transition-colors ml-auto"
                                                >
                                                    <Trash2 size={12} /> Clear Record
                                                </button>
                                            ) : (
                                                <button
                                                    onClick={(e) => { e.stopPropagation(); handleDeprovision(addon.id); }}
                                                    className="flex items-center gap-2 px-3 py-2 bg-red-500/10 text-red-400 rounded-lg text-xs font-medium hover:bg-red-500/20 transition-colors ml-auto"
                                                >
                                                    <Trash2 size={12} /> Delete
                                                </button>
                                            )}
                                        </div>

                                        {/* MinIO Bucket Access */}
                                        {addon.addon_type === 'MINIO' && (
                                            <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-4 flex items-center justify-between">
                                                <div className="flex items-center gap-3">
                                                    <div className={`p-2 rounded-full ${addon.is_bucket_public ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'}`}>
                                                        <Shield size={18} />
                                                    </div>
                                                    <div>
                                                        <p className="text-sm font-semibold text-foreground">Bucket Access Control</p>
                                                        <p className="text-xs text-muted-foreground">
                                                            {addon.is_bucket_public 
                                                                ? 'Public: Files can be accessed via direct S3 URLs without authentication.' 
                                                                : 'Private: Files require signed URLs or AWS credentials to access.'}
                                                        </p>
                                                    </div>
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <span className="text-[10px] font-bold uppercase text-muted-foreground">
                                                        {addon.is_bucket_public ? 'Public' : 'Private'}
                                                    </span>
                                                    <button
                                                        onClick={(e) => { e.stopPropagation(); handleToggleBucketPublic(addon.id, !addon.is_bucket_public); }}
                                                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${addon.is_bucket_public ? 'bg-emerald-500' : 'bg-zinc-700'}`}
                                                    >
                                                        <span
                                                            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${addon.is_bucket_public ? 'translate-x-6' : 'translate-x-1'}`}
                                                        />
                                                    </button>
                                                </div>
                                            </div>
                                        )}

                                        {/* Credentials */}
                                        {addon.status === 'ACTIVE' && credentials[addon.id] && (
                                            <div>
                                                <h5 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">Connection Details</h5>
                                                <div className="space-y-2">
                                                    {Object.entries(credentials[addon.id]).map(([key, value]) => {
                                                        const isRevealed = revealedCreds[`${addon.id}-${key}`];
                                                        const shortSuffix = key.split('_').slice(1).join('_');
                                                        const shortcode = `{{${addon.name}.${shortSuffix}}}`;

                                                        return (
                                                            <div key={key} className="bg-zinc-900/30 rounded-lg border border-zinc-800/50 p-3">
                                                                <div className="flex items-center justify-between mb-1">
                                                                    <span className="font-mono text-xs text-blue-300">{key}</span>
                                                                    <div className="flex items-center gap-2">
                                                                        <button
                                                                            onClick={(e) => { e.stopPropagation(); setRevealedCreds(prev => ({...prev, [`${addon.id}-${key}`]: !isRevealed})); }}
                                                                            className="text-muted-foreground hover:text-foreground transition-colors"
                                                                        >
                                                                            <Eye size={12} />
                                                                        </button>
                                                                        <button
                                                                            onClick={(e) => {
                                                                                e.stopPropagation();
                                                                                navigator.clipboard.writeText(value);
                                                                                setCopied(`${addon.id}-${key}`);
                                                                                setTimeout(() => setCopied(null), 2000);
                                                                            }}
                                                                            className="text-muted-foreground hover:text-foreground transition-colors"
                                                                        >
                                                                            {copied === `${addon.id}-${key}` ? <Check size={12} className="text-emerald-500" /> : <Copy size={12} />}
                                                                        </button>
                                                                    </div>
                                                                </div>
                                                                <div className="font-mono text-xs text-zinc-400 break-all mb-1">
                                                                    {isRevealed ? value : '•'.repeat(24)}
                                                                </div>
                                                                <div className="inline-flex items-center px-1.5 py-0.5 rounded bg-zinc-800/50 border border-zinc-700/50 text-[10px] text-zinc-500 font-mono">
                                                                    {shortcode}
                                                                </div>
                                                            </div>
                                                        );
                                                    })}
                                                </div>
                                            </div>
                                        )}

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

                                        {/* Logs */}
                                        <div>
                                            <h5 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">Logs</h5>
                                            <AddonLogsViewer
                                                addonId={addon.id}
                                                addonType={addon.addon_type}
                                                status={addon.status}
                                                compact
                                            />
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
