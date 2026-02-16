'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { servicesApi, EnvVar } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Plus, Trash2, Eye, EyeOff, Lock, RotateCcw, Pencil, Check, X, Rocket, Loader2 } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';

export function EnvVarsTab({ serviceId }: { serviceId: string }) {
    const [vars, setVars] = useState<EnvVar[]>([]);
    const [loading, setLoading] = useState(true);
    const [newKey, setNewKey] = useState('');
    const [newValue, setNewValue] = useState('');
    const [newIsSecret, setNewIsSecret] = useState(false);
    const [visibleValues, setVisibleValues] = useState<Record<string, boolean>>({});
    const [editingId, setEditingId] = useState<number | null>(null);
    const [editValue, setEditValue] = useState('');
    const [saving, setSaving] = useState(false);
    const [hasChanges, setHasChanges] = useState(false);
    const [deploying, setDeploying] = useState(false);

    const loadVars = useCallback(async () => {
        try {
            const data = await servicesApi.getEnvVars(serviceId);
            setVars(data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    useEffect(() => {
        void loadVars();
        const interval = setInterval(loadVars, 10000);
        return () => clearInterval(interval);
    }, [loadVars]);

    const handleAdd = async () => {
        if (!newKey || !newValue) return;
        try {
            await servicesApi.createEnvVar(serviceId, {
                key: newKey,
                value: newValue,
                is_secret: newIsSecret
            });
            setNewKey('');
            setNewValue('');
            setNewIsSecret(false);
            await loadVars();
            setHasChanges(true);
            toast({ title: "Variable added" });
        } catch (err) {
            toast({ title: "Failed to add variable", variant: "destructive" });
        }
    };

    const handleDelete = async (id: number) => {
        if (!confirm('Are you sure?')) return;
        try {
            await servicesApi.deleteEnvVar(serviceId, id);
            await loadVars();
            setHasChanges(true);
            toast({ title: "Variable deleted" });
        } catch (err) {
            toast({ title: "Failed to delete variable", variant: "destructive" });
        }
    };

    const startEdit = (v: EnvVar) => {
        setEditingId(v.id);
        setEditValue(v.value);
        // Make sure the value is visible while editing
        setVisibleValues(prev => ({ ...prev, [v.id]: true }));
    };

    const cancelEdit = () => {
        setEditingId(null);
        setEditValue('');
    };

    const handleSaveEdit = async (v: EnvVar) => {
        if (editValue === v.value) {
            cancelEdit();
            return;
        }
        setSaving(true);
        try {
            // Delete old, create new with same key (no PATCH endpoint)
            await servicesApi.deleteEnvVar(serviceId, v.id);
            await servicesApi.createEnvVar(serviceId, {
                key: v.key,
                value: editValue,
                is_secret: v.is_secret
            });
            await loadVars();
            setEditingId(null);
            setEditValue('');
            setHasChanges(true);
            toast({ title: "Variable updated" });
        } catch (err) {
            toast({ title: "Failed to update variable", variant: "destructive" });
        } finally {
            setSaving(false);
        }
    };

    const handleRedeploy = async () => {
        setDeploying(true);
        try {
            await servicesApi.deploy(serviceId, 'HEAD');
            setHasChanges(false);
            toast({ title: "🚀 Deployment started", description: "Your service is redeploying with the updated variables." });
        } catch (err) {
            toast({ title: "Failed to deploy", variant: "destructive" });
        } finally {
            setDeploying(false);
        }
    };

    const toggleVisibility = (id: number) => {
        setVisibleValues(prev => ({ ...prev, [id]: !prev[id] }));
    };

    if (loading) return <div className="p-4 text-center">Loading environment variables...</div>;

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            {/* Redeploy Banner */}
            {hasChanges && (
                <div className="sticky top-0 z-50 animate-in slide-in-from-top-2 fade-in">
                    <div className="flex items-center justify-between p-4 bg-gradient-to-r from-red-600/95 to-rose-600/95 backdrop-blur rounded-xl border border-red-500/30 shadow-lg shadow-red-500/20">
                        <div className="flex items-center gap-3">
                            <div className="w-2 h-2 rounded-full bg-white animate-pulse" />
                            <p className="text-sm font-medium text-white">
                                ⚠ Variables changed — redeploy to apply
                            </p>
                        </div>
                        <Button
                            onClick={handleRedeploy}
                            disabled={deploying}
                            className="bg-white text-red-700 hover:bg-white/90 font-semibold shadow-sm"
                            size="sm"
                        >
                            {deploying ? (
                                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                            ) : (
                                <Rocket className="w-4 h-4 mr-2" />
                            )}
                            {deploying ? 'Deploying...' : 'Redeploy Now'}
                        </Button>
                    </div>
                </div>
            )}

            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center justify-between mb-6">
                    <div>
                        <h3 className="font-bold text-lg">Environment Variables</h3>
                        <p className="text-sm text-muted-foreground">
                            Configured for the build and runtime environments.
                        </p>
                    </div>
                    <Button variant="outline" onClick={loadVars} size="sm">
                        <RotateCcw className="w-4 h-4 mr-2" /> Refresh
                    </Button>
                </div>

                {/* Add New Variable Form */}
                <div className="flex gap-4 mb-8 bg-muted/30 p-4 rounded-lg border border-border">
                    <div className="flex-1">
                        <Input
                            placeholder="KEY_NAME"
                            className="font-mono uppercase"
                            value={newKey}
                            onChange={(e) => setNewKey(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ''))}
                        />
                    </div>
                    <div className="flex-1">
                        <Input
                            placeholder="Value"
                            type={newIsSecret ? "password" : "text"}
                            value={newValue}
                            onChange={(e) => setNewValue(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
                        />
                    </div>
                    <div className="flex items-center gap-2">
                        <Button
                            variant={newIsSecret ? "default" : "outline"}
                            size="icon"
                            onClick={() => setNewIsSecret(!newIsSecret)}
                            title="Toggle Secret"
                        >
                            <Lock className={`w-4 h-4 ${newIsSecret ? 'text-primary-foreground' : 'text-muted-foreground'}`} />
                        </Button>
                        <Button onClick={handleAdd}>
                            <Plus className="w-4 h-4 mr-2" /> Add
                        </Button>
                    </div>
                </div>

                {/* List Variables */}
                <div className="space-y-2">
                    {vars.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground border-2 border-dashed border-border rounded-lg">
                            No environment variables configured.
                        </div>
                    ) : (
                        vars.map((v) => (
                            <div key={v.id} className={`flex items-center gap-4 p-3 bg-card border rounded-lg group transition-colors ${v.value?.startsWith('CHANGE_ME') ? 'border-red-500/50 bg-red-500/5' : 'border-border hover:border-primary/50'}`}>
                                <div className={`flex-1 font-mono font-bold text-sm min-w-[120px] ${v.value?.startsWith('CHANGE_ME') ? 'text-red-500' : 'text-primary'}`}>
                                    {v.key}
                                    {v.value?.startsWith('CHANGE_ME') && (
                                        <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-400 font-medium normal-case">needs value</span>
                                    )}
                                </div>

                                {/* Value: display or edit mode */}
                                <div className="flex-[2] font-mono text-sm relative">
                                    {editingId === v.id ? (
                                        <div className="flex items-center gap-2">
                                            <Input
                                                value={editValue}
                                                onChange={(e) => setEditValue(e.target.value)}
                                                className="font-mono text-sm h-8"
                                                autoFocus
                                                onKeyDown={(e) => {
                                                    if (e.key === 'Enter') handleSaveEdit(v);
                                                    if (e.key === 'Escape') cancelEdit();
                                                }}
                                            />
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8 text-emerald-500 hover:text-emerald-400 hover:bg-emerald-500/10"
                                                onClick={() => handleSaveEdit(v)}
                                                disabled={saving}
                                            >
                                                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                                            </Button>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8 text-muted-foreground"
                                                onClick={cancelEdit}
                                            >
                                                <X className="w-4 h-4" />
                                            </Button>
                                        </div>
                                    ) : (
                                        <>
                                            {v.is_secret && !visibleValues[v.id] ? (
                                                <span className="text-muted-foreground flex items-center gap-2">
                                                    <Lock className="w-3 h-3" /> ••••••••••••••••
                                                </span>
                                            ) : (
                                                <span className="break-all">{v.value}</span>
                                            )}
                                        </>
                                    )}
                                </div>

                                {/* Actions */}
                                {editingId !== v.id && (
                                    <div className="flex items-center gap-1 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
                                        {/* Edit Button */}
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            className="h-8 w-8 text-muted-foreground hover:text-primary"
                                            onClick={() => startEdit(v)}
                                            title="Edit value"
                                        >
                                            <Pencil className="w-4 h-4" />
                                        </Button>

                                        {/* Show/Hide Secret */}
                                        {v.is_secret && (
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8"
                                                onClick={() => toggleVisibility(v.id)}
                                            >
                                                {visibleValues[v.id] ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                            </Button>
                                        )}

                                        {/* Delete */}
                                        <Button
                                            variant="ghost"
                                            size="icon"
                                            className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10"
                                            onClick={() => handleDelete(v.id)}
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </Button>
                                    </div>
                                )}
                            </div>
                        ))
                    )}
                </div>

                <div className="mt-8 pt-6 border-t border-border">
                    <p className="text-xs text-muted-foreground">
                        <strong className="text-foreground">Note:</strong> Changes to environment variables require a redeployment to take effect.
                    </p>
                </div>
            </Card>
        </div>
    );
}
