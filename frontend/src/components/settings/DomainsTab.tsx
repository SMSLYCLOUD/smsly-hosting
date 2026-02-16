'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { servicesApi, Service } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Globe, Plus, Trash2, CheckCircle, XCircle, ExternalLink, RefreshCw, Copy, Loader2, ArrowRight } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';

const CNAME_TARGET = 'cname.cloud.smsly.cloud';

interface DomainStatus {
    domain: string;
    verified: boolean | null; // null = not checked yet
    checking: boolean;
}

export function DomainsTab({ service }: { service: Service }) {
    const [domains, setDomains] = useState<DomainStatus[]>([]);
    const [newDomain, setNewDomain] = useState('');
    const [loading, setLoading] = useState(true);
    const [envVarId, setEnvVarId] = useState<number | null>(null);
    const [adding, setAdding] = useState(false);

    const defaultDomain = service.public_domain || `${service.name}.cloud.smsly.cloud`;

    const loadDomains = useCallback(async () => {
        try {
            setLoading(true);
            const envVars = await servicesApi.getEnvVars(service.id);
            const customDomainsVar = envVars.find(v => v.key === 'CUSTOM_DOMAINS');

            if (customDomainsVar) {
                setEnvVarId(customDomainsVar.id);
                const domainList = customDomainsVar.value.split(',').map(d => d.trim()).filter(Boolean);
                setDomains(domainList.map(d => ({ domain: d, verified: null, checking: false })));
            } else {
                setEnvVarId(null);
                setDomains([]);
            }
        } catch (err) {
            console.error(err);
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
                await servicesApi.deleteEnvVar(service.id, envVarId);
                if (value) {
                    await servicesApi.createEnvVar(service.id, { key: 'CUSTOM_DOMAINS', value, is_secret: false });
                }
            } else if (value) {
                await servicesApi.createEnvVar(service.id, { key: 'CUSTOM_DOMAINS', value, is_secret: false });
            }
            toast({ title: "Domains updated", description: "Redeploy to apply changes." });
            await loadDomains();
        } catch (err) {
            console.error(err);
            toast({ title: "Failed to save domains", variant: "destructive" });
        }
    }, [envVarId, loadDomains, service.id]);

    const handleAdd = async () => {
        const domain = newDomain.trim().toLowerCase();
        if (!domain) return;
        if (!domain.includes('.') || domain.includes(' ') || domain.includes('://')) {
            toast({ title: "Invalid domain", description: "Enter a domain like app.example.com (no http://)", variant: "destructive" });
            return;
        }
        if (domains.some(d => d.domain === domain)) {
            toast({ title: "Domain already added", variant: "destructive" });
            return;
        }

        setAdding(true);
        const updated = [...domains.map(d => d.domain), domain];
        await saveDomains(updated);
        setNewDomain('');
        setAdding(false);
    };

    const handleDelete = async (domain: string) => {
        if (!confirm(`Remove ${domain}?`)) return;
        const updated = domains.filter(d => d.domain !== domain).map(d => d.domain);
        await saveDomains(updated);
    };

    const handleVerify = async (domain: string) => {
        setDomains(prev => prev.map(d =>
            d.domain === domain ? { ...d, checking: true } : d
        ));
        try {
            const result = await servicesApi.verifyDomain(service.id, domain);
            setDomains(prev => prev.map(d =>
                d.domain === domain ? { ...d, verified: result.verified, checking: false } : d
            ));
            if (result.verified) {
                toast({ title: "✅ DNS Verified", description: `${domain} is correctly pointing to CloudNeuron.` });
            } else {
                toast({ title: "❌ DNS Not Found", description: result.message, variant: "destructive" });
            }
        } catch {
            setDomains(prev => prev.map(d =>
                d.domain === domain ? { ...d, verified: false, checking: false } : d
            ));
            toast({ title: "Verification failed", variant: "destructive" });
        }
    };

    const copyToClipboard = (text: string) => {
        navigator.clipboard.writeText(text);
        toast({ title: "Copied!", description: text });
    };

    if (loading) return <div className="p-4 text-center">Loading domains...</div>;

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center justify-between mb-6">
                    <div>
                        <h3 className="font-bold text-lg">Domain Management</h3>
                        <p className="text-sm text-muted-foreground">
                            Manage default and custom domains for your service.
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

                    {/* CNAME Setup Instructions */}
                    <div className="mb-4 p-4 bg-blue-500/5 border border-blue-500/20 rounded-lg">
                        <p className="text-sm font-medium text-blue-400 mb-2">📋 DNS Setup</p>
                        <p className="text-xs text-muted-foreground mb-3">
                            Add a <strong>CNAME</strong> record in your DNS provider pointing to:
                        </p>
                        <div className="flex items-center gap-2 p-2 bg-background/60 rounded border border-border">
                            <code className="text-sm font-mono text-primary flex-1">{CNAME_TARGET}</code>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={() => copyToClipboard(CNAME_TARGET)}
                            >
                                <Copy size={14} />
                            </Button>
                        </div>
                        <div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground">
                            <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">myapp.com</span>
                            <ArrowRight size={12} />
                            <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">CNAME</span>
                            <ArrowRight size={12} />
                            <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">{CNAME_TARGET}</span>
                        </div>
                    </div>

                    {/* Add Domain */}
                    <div className="flex gap-2 mb-4">
                        <Input
                            placeholder="app.example.com"
                            value={newDomain}
                            onChange={(e) => setNewDomain(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
                            disabled={adding}
                        />
                        <Button onClick={handleAdd} disabled={adding || !newDomain.trim()}>
                            {adding ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Plus className="w-4 h-4 mr-2" />}
                            Add
                        </Button>
                    </div>

                    {/* Domain List */}
                    <div className="space-y-2">
                        {domains.length === 0 && (
                            <p className="text-sm text-muted-foreground italic">No custom domains configured.</p>
                        )}
                        {domains.map(({ domain, verified, checking }) => (
                            <div key={domain} className="flex items-center justify-between p-3 bg-card border border-border rounded-lg group">
                                <div className="flex items-center gap-3">
                                    <Globe className="w-4 h-4 text-primary" />
                                    <span className="font-mono text-sm">{domain}</span>

                                    {/* DNS Status Badge */}
                                    {verified === true && (
                                        <span className="text-[10px] bg-emerald-500/10 text-emerald-500 px-1.5 py-0.5 rounded flex items-center gap-1">
                                            <CheckCircle size={10} /> Verified
                                        </span>
                                    )}
                                    {verified === false && (
                                        <span className="text-[10px] bg-red-500/10 text-red-400 px-1.5 py-0.5 rounded flex items-center gap-1">
                                            <XCircle size={10} /> DNS Not Found
                                        </span>
                                    )}
                                    {verified === null && !checking && (
                                        <span className="text-[10px] bg-yellow-500/10 text-yellow-500 px-1.5 py-0.5 rounded">
                                            Pending
                                        </span>
                                    )}
                                </div>

                                <div className="flex items-center gap-1">
                                    {/* Verify Button */}
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8 text-primary hover:text-primary/80"
                                        onClick={() => handleVerify(domain)}
                                        disabled={checking}
                                        title="Verify DNS"
                                    >
                                        {checking ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                                    </Button>

                                    {/* Visit Link */}
                                    {verified === true && (
                                        <a
                                            href={`https://${domain}`}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="inline-flex items-center justify-center h-8 w-8 text-primary hover:text-primary/80"
                                        >
                                            <ExternalLink size={16} />
                                        </a>
                                    )}

                                    {/* Delete Button */}
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10 opacity-0 group-hover:opacity-100 transition-opacity"
                                        onClick={() => handleDelete(domain)}
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </Button>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                <div className="mt-8 pt-6 border-t border-border flex items-start gap-3">
                    <RefreshCw className="w-5 h-5 text-yellow-500 mt-0.5" />
                    <p className="text-xs text-muted-foreground">
                        After adding or removing domains, you must <strong className="text-foreground">Redeploy</strong> your service for the routing changes to take effect.
                        SSL certificates are automatically provisioned on first visit.
                    </p>
                </div>
            </Card>
        </div>
    );
}
