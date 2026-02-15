'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { servicesApi, EnvVar } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Plus, Trash2, Eye, EyeOff, Lock, Save, RotateCcw } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';

export function EnvVarsTab({ serviceId }: { serviceId: string }) {
    const [vars, setVars] = useState<EnvVar[]>([]);
    const [loading, setLoading] = useState(true);
    const [newKey, setNewKey] = useState('');
    const [newValue, setNewValue] = useState('');
    const [newIsSecret, setNewIsSecret] = useState(false);
    const [visibleValues, setVisibleValues] = useState<Record<string, boolean>>({});

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
            toast({ title: "Variable deleted" });
        } catch (err) {
            toast({ title: "Failed to delete variable", variant: "destructive" });
        }
    };

    const toggleVisibility = (id: number) => {
        setVisibleValues(prev => ({ ...prev, [id]: !prev[id] }));
    };

    if (loading) return <div className="p-4 text-center">Loading environment variables...</div>;

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
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
                            <div key={v.id} className="flex items-center gap-4 p-3 bg-card border border-border rounded-lg group hover:border-primary/50 transition-colors">
                                <div className="flex-1 font-mono font-bold text-sm text-primary">
                                    {v.key}
                                </div>
                                <div className="flex-1 font-mono text-sm relative">
                                    {v.is_secret && !visibleValues[v.id] ? (
                                        <span className="text-muted-foreground flex items-center gap-2">
                                            <Lock className="w-3 h-3" /> ••••••••••••••••
                                        </span>
                                    ) : (
                                        <span className="break-all">{v.value}</span>
                                    )}
                                </div>
                                <div className="flex items-center gap-2 opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity">
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
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10"
                                        onClick={() => handleDelete(v.id)}
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </Button>
                                </div>
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
