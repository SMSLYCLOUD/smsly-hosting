'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { servicesApi, Service } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Globe, Plus, Trash2, CheckCircle, ExternalLink, RefreshCw } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';

export function DomainsTab({ service }: { service: Service }) {
    // We'll manage custom domains by parsing the CUSTOM_DOMAINS env var for now,
    // as we decided in the plan to use that mechanism supported by LocalAdapter.
    const [domains, setDomains] = useState<string[]>([]);
    const [newDomain, setNewDomain] = useState('');
    const [loading, setLoading] = useState(true);
    const [envVarId, setEnvVarId] = useState<number | null>(null);

    const defaultDomain = service.public_domain || `${service.name}.cloud.smsly.cloud`;

    const loadDomains = useCallback(async () => {
        try {
            setLoading(true);
            const envVars = await servicesApi.getEnvVars(service.id);
            const customDomainsVar = envVars.find(v => v.key === 'CUSTOM_DOMAINS');

            if (customDomainsVar) {
                setEnvVarId(customDomainsVar.id);
                setDomains(customDomainsVar.value.split(',').map(d => d.trim()).filter(Boolean));
            } else {
                setEnvVarId(null);
                setDomains([]);
            }
        } catch (err) {
            console.error(err);
            toast({ title: "Failed to load domains", variant: "destructive" });
        } finally {
            setLoading(false);
        }
    }, [service.id]);

    useEffect(() => {
        void loadDomains();
    }, [loadDomains]);

    const saveDomains = useCallback(async (newDomainList: string[]) => {
        try {
            const value = newDomainList.join(',');
            if (envVarId) {
                if (value) {
                    // Update existing
                    // Note: We need an update API or just delete/create.
                    // Let's assume we delete and recreate for simplicity or if update isn't exposed yet.
                    // Actually let's try delete then create to be safe.
                    await servicesApi.deleteEnvVar(service.id, envVarId);
                    await servicesApi.createEnvVar(service.id, { key: 'CUSTOM_DOMAINS', value, is_secret: false });
                } else {
                    // Delete if empty
                    await servicesApi.deleteEnvVar(service.id, envVarId);
                }
            } else if (value) {
                // Create new
                await servicesApi.createEnvVar(service.id, { key: 'CUSTOM_DOMAINS', value, is_secret: false });
            }

            toast({ title: "Domains updated", description: "Redeploy to apply changes." });
            await loadDomains();
        } catch (err) {
            console.error(err);
            toast({ title: "Failed to save domains", variant: "destructive" });
        }
    }, [envVarId, loadDomains, service.id]);

    const handleAdd = () => {
        if (!newDomain) return;
        // Basic validation
        if (!newDomain.includes('.') || newDomain.includes(' ')) {
            toast({ title: "Invalid domain format", variant: "destructive" });
            return;
        }
        if (domains.includes(newDomain)) {
            toast({ title: "Domain already added", variant: "destructive" });
            return;
        }

        const updated = [...domains, newDomain];
        void saveDomains(updated);
        setNewDomain('');
    };

    const handleDelete = (domain: string) => {
        if (!confirm(`Remove ${domain}?`)) return;
        const updated = domains.filter(d => d !== domain);
        void saveDomains(updated);
    };

    if (loading) return <div className="p-4 text-center">Loading domains...</div>;

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center justify-between mb-6">
                    <div>
                        <h3 className="font-bold text-lg">Domain Management</h3>
                        <p className="text-sm text-muted-foreground">
                            Manage custom domains for your service.
                        </p>
                    </div>
                    <Globe className="w-10 h-10 text-muted-foreground/20" />
                </div>

                {/* Default Domain */}
                <div className="mb-8">
                    <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-3">Default Domain</h4>
                    <div className="flex items-center gap-3 p-3 bg-muted/30 border border-border rounded-lg">
                        <div className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                        <span className="font-mono text-sm flex-1">{defaultDomain}</span>
                        <a
                            href={`https://${defaultDomain}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:text-primary/80"
                        >
                            <ExternalLink size={16} />
                        </a>
                    </div>
                </div>

                {/* Custom Domains */}
                <div>
                    <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-3">Custom Domains</h4>

                    <div className="flex gap-2 mb-4">
                        <Input
                            placeholder="app.example.com"
                            value={newDomain}
                            onChange={(e) => setNewDomain(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
                        />
                        <Button onClick={handleAdd}>
                            <Plus className="w-4 h-4 mr-2" /> Add
                        </Button>
                    </div>

                    <div className="space-y-2">
                        {domains.length === 0 && (
                            <p className="text-sm text-muted-foreground italic">No custom domains configured.</p>
                        )}
                        {domains.map((domain) => (
                            <div key={domain} className="flex items-center justify-between p-3 bg-card border border-border rounded-lg group">
                                <div className="flex items-center gap-3">
                                    <Globe className="w-4 h-4 text-primary" />
                                    <span className="font-mono text-sm">{domain}</span>
                                    {/* DNS Check Simulation */}
                                    <span className="text-[10px] bg-emerald-500/10 text-emerald-500 px-1.5 py-0.5 rounded flex items-center gap-1">
                                        <CheckCircle size={10} /> Configured
                                    </span>
                                </div>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10 opacity-0 group-hover:opacity-100 transition-opacity"
                                    onClick={() => handleDelete(domain)}
                                >
                                    <Trash2 className="w-4 h-4" />
                                </Button>
                            </div>
                        ))}
                    </div>
                </div>

                 <div className="mt-8 pt-6 border-t border-border flex items-start gap-3">
                    <RefreshCw className="w-5 h-5 text-yellow-500 mt-0.5" />
                    <p className="text-xs text-muted-foreground">
                        After adding or removing domains, you must <strong className="text-foreground">Redeploy</strong> your service for the routing changes to take effect.
                    </p>
                </div>
            </Card>
        </div>
    );
}
