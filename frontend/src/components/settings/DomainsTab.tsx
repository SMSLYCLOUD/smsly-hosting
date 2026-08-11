'use client';

import React, { useState, useEffect, useCallback } from 'react';
import api, { servicesApi, systemApi, Service } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Globe, Plus, Trash2, CheckCircle, XCircle, ExternalLink, RefreshCw, Copy, Loader2, ArrowRight } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';

// CNAME target is derived per service from its public domain

interface DomainStatus {
    domain: string;
    verified: boolean | null; // null = not checked yet
    checking: boolean;
}

export function DomainsTab({ service: initialService }: { service: Service }) {
    const confirm = useConfirm();
    const [service, setService] = useState(initialService);
    const [domains, setDomains] = useState<DomainStatus[]>([]);
    const [newDomain, setNewDomain] = useState('');
    const [loading, setLoading] = useState(true);
    const [adding, setAdding] = useState(false);
    const [serverIp, setServerIp] = useState<string>('');
    const [stagingDomain, setStagingDomain] = useState(service.staging_domain || '');
    const [savingStaging, setSavingStaging] = useState(false);

    const defaultDomain = service.public_domain || `${service.name}.cloud.smsly.cloud`;

    // Fetch server IP from system config
    useEffect(() => {
        systemApi.getDomainConfig()
            .then((cfg: any) => setServerIp(cfg.server_ip || ''))
            .catch(() => {});
    }, []);

    const loadDomains = useCallback(async () => {
        try {
            setLoading(true);
            const latest = await servicesApi.get(service.id);
            setService(latest);
            const domainList = Array.isArray(latest.custom_domains)
                ? latest.custom_domains.map(d => String(d || '').trim().toLowerCase()).filter(Boolean)
                : [];
            setDomains(domainList.map(d => {
                const instance = (latest.domain_instances || []).find((inst: any) => inst.domain_name === d);
                // If instance exists, we use its verified status. Otherwise it's null (Pending)
                return { 
                    domain: d, 
                    verified: instance && instance.verified !== undefined ? instance.verified : null, 
                    checking: false 
                };
            }));
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [service.id]);

    useEffect(() => {
        void loadDomains();
    }, [loadDomains]);

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
        try {
            const res = await api.post(`/services/${service.id}/add-domain/`, { domain });
            toast({
                title: "Domain added",
                description: res.data?.message || "Custom domain saved and routing sync triggered.",
            });
            // Signal the rest of the UI that a domain change occurred
            window.dispatchEvent(new CustomEvent('DOMAIN_SYNC_TRIGGER', { detail: { domain, serviceId: service.id } }));
            setNewDomain('');
            await loadDomains();
        } catch (err: any) {
            console.error(err);
            toast({
                title: "Failed to add domain",
                description: err?.response?.data?.error || "Could not save custom domain.",
                variant: "destructive",
            });
        } finally {
            setAdding(false);
        }
    };

    const handleDelete = async (domain: string) => {
        if (!await confirm({ title: 'Remove domain?', message: `Are you sure you want to remove ${domain}?`, variant: 'destructive', confirmText: 'Remove' })) return;
        try {
            const res = await api.post(`/services/${service.id}/delete-domain/`, { domain });
            toast({
                title: "Domain removed",
                description: res.data?.message || "Custom domain removed and routing sync triggered.",
            });
            // Signal the rest of the UI that a domain change occurred
            window.dispatchEvent(new CustomEvent('DOMAIN_SYNC_TRIGGER', { detail: { domain, serviceId: service.id } }));
            await loadDomains();
        } catch (err: any) {
            console.error(err);
            toast({
                title: "Failed to remove domain",
                description: err?.response?.data?.error || "Could not remove custom domain.",
                variant: "destructive",
            });
        }
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
                toast({ title: "✅ DNS Verified", description: `${domain} is correctly pointing to Grid.` });
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

    const handleSaveStagingDomain = async () => {
        setSavingStaging(true);
        try {
            const updated = await servicesApi.update(service.id, { staging_domain: stagingDomain.trim() || null });
            setService(updated);
            toast({ title: 'Staging domain saved', description: stagingDomain.trim() ? `Staging URL: https://${stagingDomain.trim()}` : 'Staging URL will be auto-generated.' });
        } catch (err: any) {
            toast({ title: 'Failed to save', description: err?.response?.data?.error || 'Could not save staging domain.', variant: 'destructive' });
        } finally {
            setSavingStaging(false);
        }
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
                    <div className="flex items-center justify-between mb-3">
                        <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Default Domain</h4>
                        {domains.length > 0 && (
                            <div className="flex items-center gap-2">
                                <span className="text-xs text-muted-foreground">Public Default Domain</span>
                                <Switch
                                    checked={!service.public_domain_hidden}
                                    onCheckedChange={async (checked) => {
                                        try {
                                            const newVal = !checked; // hidden is inverse of checked (visible)
                                            const updated = await servicesApi.update(service.id, { public_domain_hidden: newVal });
                                            setService(updated);
                                            toast({ title: 'Success', description: `Default domain is now ${newVal ? 'hidden' : 'visible'}. Redeploy to apply.` });
                                        } catch (err) {
                                            toast({ title: 'Error', description: 'Failed to update visibility', variant: 'destructive' });
                                        }
                                    }}
                                />
                            </div>
                        )}
                    </div>
                    <div className={`flex items-center gap-3 p-3 border rounded-lg transition-colors ${service.public_domain_hidden ? 'bg-muted/10 border-border/50 opacity-60' : 'bg-muted/30 border-border'}`}>
                        <div className={`h-2 w-2 rounded-full ${service.public_domain_hidden ? 'bg-zinc-500' : 'bg-emerald-500 animate-pulse'}`} />
                        <span className={`font-mono text-sm flex-1 ${service.public_domain_hidden ? 'line-through text-muted-foreground' : ''}`}>{defaultDomain}</span>
                        {!service.public_domain_hidden && (
                            <a
                                href={`https://${defaultDomain}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-primary hover:text-primary/80"
                            >
                                <ExternalLink size={16} />
                            </a>
                        )}
                    </div>
                    {service.public_domain_hidden && (
                        <p className="text-xs text-muted-foreground mt-2">
                            The default domain will not serve traffic. Only custom domains are active.
                        </p>
                    )}
                </div>

                {/* Staging Domain */}
                <div className="mb-8">
                    <div className="flex items-center justify-between mb-3">
                        <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Staging Domain</h4>
                    </div>
                    <p className="text-xs text-muted-foreground mb-3">
                        Custom domain for webhook deployments. Pushes will deploy to this URL for review before going live.
                        If blank, auto-generated as <code>staging-{service.name}-{'<hash>'}.{service.public_domain?.split('.').slice(1).join('.') || 'cloud.smsly.cloud'}</code>.
                    </p>
                    <div className="flex gap-2">
                        <Input
                            placeholder={`staging-${service.name}.example.com`}
                            value={stagingDomain}
                            onChange={(e) => setStagingDomain(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleSaveStagingDomain()}
                            disabled={savingStaging}
                        />
                        <Button onClick={handleSaveStagingDomain} disabled={savingStaging}>
                            {savingStaging ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Globe className="w-4 h-4 mr-2" />}
                            Save
                        </Button>
                    </div>
                </div>

                {/* Custom Domains */}
                <div>
                    <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-3">Custom Domains</h4>

                    {/* DNS Setup Instructions */}
                    <div className="mb-4 p-4 bg-blue-500/5 border border-blue-500/20 rounded-lg">
                        <p className="text-sm font-medium text-blue-400 mb-2">📋 DNS Setup</p>
                        
                        {/* Option 1: CNAME (subdomains) */}
                        <p className="text-xs font-semibold text-muted-foreground mb-1">
                            Option 1: <strong>CNAME</strong> — for subdomains (e.g., app.yourdomain.com)
                        </p>
                        <div className="flex items-center gap-2 p-2 bg-background/60 rounded border border-border mb-3">
                            <code className="text-sm font-mono text-primary flex-1">{defaultDomain}</code>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={() => copyToClipboard(defaultDomain)}
                            >
                                <Copy size={14} />
                            </Button>
                        </div>
                        <div className="flex items-center gap-2 mb-4 text-xs text-muted-foreground">
                            <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">app.example.com</span>
                            <ArrowRight size={12} />
                            <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">CNAME</span>
                            <ArrowRight size={12} />
                            <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">{defaultDomain}</span>
                        </div>

                        {/* Option 2: A Record (apex domains) */}
                        <p className="text-xs font-semibold text-muted-foreground mb-1">
                            Option 2: <strong>A Record</strong> — for root/apex domains (e.g., yourdomain.com)
                        </p>
                        <div className="flex items-center gap-2 p-2 bg-background/60 rounded border border-border mb-2">
                            <code className="text-sm font-mono text-primary flex-1">{serverIp || '(see Settings → Infra)'}</code>
                            {serverIp && (
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-7 w-7"
                                    onClick={() => copyToClipboard(serverIp)}
                                >
                                    <Copy size={14} />
                                </Button>
                            )}
                        </div>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">example.com</span>
                            <ArrowRight size={12} />
                            <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">A</span>
                            <ArrowRight size={12} />
                            <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">{serverIp || 'your.server.ip'}</span>
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
                        Routing and SSL sync are applied immediately after add/remove.
                        SSL certificates are provisioned automatically when DNS is correct.
                    </p>
                </div>
            </Card>
        </div>
    );
}
