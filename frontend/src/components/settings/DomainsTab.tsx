'use client';

import React, { useState, useEffect, useCallback } from 'react';
import api, { servicesApi, systemApi, Service, Deployment } from '@/lib/api';
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
    const [stagingVerified, setStagingVerified] = useState<boolean | null>(() => {
        if (service.staging_domain_verified === true) return true;
        if (service.staging_domain_verified === false) return false;
        return null;
    });
    const [stagingChecking, setStagingChecking] = useState(false);
    const [stagedDeployment, setStagedDeployment] = useState<Deployment | null>(null);
    const [platformDomain, setPlatformDomain] = useState('');
    const [newAliasHost, setNewAliasHost] = useState('');
    const [newAliasRoot, setNewAliasRoot] = useState('/login');
    const [newPath, setNewPath] = useState('');
    const [newTarget, setNewTarget] = useState('');

    const defaultDomain = service.public_domain || `${service.name}.cloud.Trulay.co`;

    // Compute auto-generated staging domain: staging-{slug}.{platformDomain}
    const autoStagingDomain = platformDomain
        ? `staging-${(service.slug || service.name).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 30)}.${platformDomain}`
        : '';

    // Fetch server IP and platform domain from system config
    useEffect(() => {
        systemApi.getDomainConfig()
            .then((cfg: any) => {
                setServerIp(cfg.server_ip || '');
                setPlatformDomain(cfg.domain || '');
            })
            .catch(() => {});
    }, []);

    // Fetch active staged deployment
    useEffect(() => {
        servicesApi.getDeployments(service.id)
            .then((deps: Deployment[]) => {
                const staged = deps.find((d: Deployment) => d.status === 'STAGED' && d.staging_url);
                setStagedDeployment(staged || null);
            })
            .catch(() => {});
    }, [service.id]);

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

    const updateHostAliases = async (next: { host: string; rewrite_root: string }[]) => {
        try {
            const updated = await servicesApi.update(service.id, { host_aliases: next });
            setService(prev => ({ ...prev, ...updated }));
            toast({ title: 'Host alias saved', description: 'Routing sync dispatched. SSL is issued automatically once DNS resolves.' });
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Failed to save host alias.', variant: 'destructive' });
        }
    };

    const handleAddAlias = () => {
        const host = newAliasHost.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/+$/, '');
        if (!host || !host.includes('.') || host.includes(' ')) {
            toast({ title: 'Invalid hostname', description: 'Enter a host like account.example.com', variant: 'destructive' });
            return;
        }
        if ((Array.isArray(service.host_aliases) ? service.host_aliases : []).some(a => a.host === host)) {
            toast({ title: 'Alias already added', variant: 'destructive' });
            return;
        }
        const rewriteRoot = newAliasRoot.trim() || '';
        void updateHostAliases([...(Array.isArray(service.host_aliases) ? service.host_aliases : []), { host, rewrite_root: rewriteRoot }]);
        setNewAliasHost('');
    };

    const updatePathRedirects = async (next: { path: string; target: string }[]) => {
        try {
            const updated = await servicesApi.update(service.id, { path_redirects: next });
            setService(prev => ({ ...prev, ...updated }));
            toast({ title: 'Path redirects saved', description: 'Routing sync dispatched.' });
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Failed to save path redirect.', variant: 'destructive' });
        }
    };

    const handleAddPathRedirect = () => {
        const path = newPath.trim().toLowerCase();
        const target = newTarget.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/+$/, '');
        if (!/^\/[a-z0-9_-]{1,63}$/.test(path)) {
            toast({ title: 'Invalid path', description: 'Use a single segment like /account', variant: 'destructive' });
            return;
        }
        if (!target || !target.includes('.') || target.includes(' ')) {
            toast({ title: 'Invalid target', description: 'Enter a host like account.example.com', variant: 'destructive' });
            return;
        }
        if ((service.path_redirects ?? []).some(r => r.path === path)) {
            toast({ title: 'Path already redirected', variant: 'destructive' });
            return;
        }
        void updatePathRedirects([...(service.path_redirects ?? []), { path, target }]);
        setNewPath('');
        setNewTarget('');
    };

    const handleSaveStagingDomain = async () => {
        setSavingStaging(true);
        try {
            const oldDomain = service.staging_domain || '';
            const newDomain = stagingDomain.trim() || '';
            const updated = await servicesApi.update(service.id, { staging_domain: newDomain || undefined });
            setService(prev => ({ ...prev, ...updated }));
            if (oldDomain !== newDomain) {
                setStagingVerified(null);
            }
            toast({ title: 'Staging domain saved', description: newDomain ? `Staging URL: https://${newDomain}` : 'Staging URL will be auto-generated.' });
        } catch (err: any) {
            toast({ title: 'Failed to save', description: err?.response?.data?.error || 'Could not save staging domain.', variant: 'destructive' });
        } finally {
            setSavingStaging(false);
        }
    };

    const handleVerifyStaging = async () => {
        if (!stagingDomain.trim()) return;
        setStagingChecking(true);
        try {
            const result = await servicesApi.verifyDomain(service.id, stagingDomain.trim());
            setStagingVerified(result.verified);
            setService({ ...service, staging_domain_verified: result.verified });
            if (result.verified) {
                toast({ title: "DNS Verified", description: `${stagingDomain.trim()} is correctly pointing to Grid.` });
            } else {
                toast({ title: "DNS Not Found", description: result.message, variant: "destructive" });
            }
        } catch {
            setStagingVerified(false);
            toast({ title: "Verification failed", variant: "destructive" });
        } finally {
            setStagingChecking(false);
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

                {/* How routing works */}
                <div className="mb-8 p-4 bg-emerald-500/5 border border-emerald-500/20 rounded-lg">
                    <p className="text-sm font-medium text-emerald-400 mb-2">How traffic reaches this service</p>
                    <ul className="text-xs text-muted-foreground space-y-1.5 list-disc pl-4">
                        <li><span className="font-semibold text-foreground">Default domain</span> — auto-generated and always ready. Set it to Public, Internal-only (hidden from the internet, mesh traffic still routes), or Hidden. Custom domains are never affected.</li>
                        <li><span className="font-semibold text-foreground">Custom domains</span> — add yours below and point DNS at Grid. SSL certificates are issued automatically once DNS resolves.</li>
                        <li><span className="font-semibold text-foreground">Host aliases</span> — extra hostnames that serve this app directly (the accounts.google.com pattern). Great for account.example.com showing your login page.</li>
                        <li><span className="font-semibold text-foreground">Path redirects</span> — permanently forward paths like /account on your domains to another host.</li>
                    </ul>
                    <p className="text-xs text-emerald-400/80 mt-2">
                        Every change applies instantly — routing sync runs automatically in the background. No redeploy required.
                    </p>
                </div>

                {/* DNS Setup Instructions — at the top */}
                <div className="mb-8 p-4 bg-blue-500/5 border border-blue-500/20 rounded-lg">
                    <p className="text-sm font-medium text-blue-400 mb-3">DNS Setup</p>

                    {/* Default Domain */}
                    <p className="text-xs font-semibold text-muted-foreground mb-1">
                        Default Domain — auto-configured, no DNS needed
                    </p>
                    <div className="flex items-center gap-2 p-2 bg-background/60 rounded border border-border mb-3">
                        <code className="text-sm font-mono text-primary flex-1">{defaultDomain}</code>
                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => copyToClipboard(defaultDomain)}>
                            <Copy size={14} />
                        </Button>
                    </div>

                    {/* Custom Domains */}
                    <p className="text-xs font-semibold text-muted-foreground mb-1 mt-4">
                        Custom Domains — CNAME for subdomains
                    </p>
                    <div className="flex items-center gap-2 p-2 bg-background/60 rounded border border-border mb-2">
                        <code className="text-sm font-mono text-primary flex-1">{defaultDomain}</code>
                        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => copyToClipboard(defaultDomain)}>
                            <Copy size={14} />
                        </Button>
                    </div>
                    <div className="flex items-center gap-2 mb-3 text-xs text-muted-foreground">
                        <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">app.example.com</span>
                        <ArrowRight size={12} />
                        <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">CNAME</span>
                        <ArrowRight size={12} />
                        <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">{defaultDomain}</span>
                    </div>
                    <p className="text-xs font-semibold text-muted-foreground mb-1">
                        Custom Domains — A Record for apex domains
                    </p>
                    <div className="flex items-center gap-2 p-2 bg-background/60 rounded border border-border mb-2">
                        <code className="text-sm font-mono text-primary flex-1">{serverIp || '(see Settings → Infra)'}</code>
                        {serverIp && (
                            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => copyToClipboard(serverIp)}>
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

                    {/* Staging Domain */}
                    {stagingDomain.trim() && (
                        <>
                            <p className="text-xs font-semibold text-muted-foreground mb-1 mt-4">
                                Staging Domain — CNAME to staging domain
                            </p>
                            <div className="flex items-center gap-2 p-2 bg-background/60 rounded border border-border mb-2">
                                <code className="text-sm font-mono text-primary flex-1">{stagingDomain.trim()}</code>
                                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => copyToClipboard(stagingDomain.trim())}>
                                    <Copy size={14} />
                                </Button>
                            </div>
                            <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">{stagingDomain.trim()}</span>
                                <ArrowRight size={12} />
                                <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">CNAME</span>
                                <ArrowRight size={12} />
                                <span className="font-mono bg-muted/50 px-2 py-0.5 rounded">{autoStagingDomain || 'staging-{service}.platform.domain'}</span>
                            </div>
                        </>
                    )}
                </div>

                {/* Default Domain */}
                <div className="mb-8">
                    <div className="flex items-center justify-between mb-3">
                        <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Default Domain</h4>
                        {domains.length > 0 && (
                            <div className="flex items-center gap-2">
                                <span className="text-xs text-muted-foreground">Access</span>
                                <div className="flex items-center rounded-lg border border-border overflow-hidden">
                                    {([
                                        { key: 'public', label: 'Public', cls: 'bg-emerald-500/15 text-emerald-500' },
                                        { key: 'internal', label: 'Internal', cls: 'bg-blue-500/15 text-blue-500' },
                                        { key: 'hidden', label: 'Hidden', cls: 'bg-amber-500/15 text-amber-500' },
                                    ] as const).map(({ key, label, cls }) => {
                                        const active =
                                            (key === 'public' && !service.public_domain_hidden && !service.wildcard_internal_only) ||
                                            (key === 'internal' && !service.public_domain_hidden && service.wildcard_internal_only === true) ||
                                            (key === 'hidden' && service.public_domain_hidden);
                                        return (
                                            <button
                                                key={key}
                                                onClick={async () => {
                                                    const payload = {
                                                        public_domain_hidden: key === 'hidden',
                                                        wildcard_internal_only: key === 'internal',
                                                    };
                                                    try {
                                                        const updated = await servicesApi.update(service.id, payload);
                                                        setService(prev => ({ ...prev, ...updated }));
                                                        toast({
                                                            title:
                                                                key === 'public'
                                                                    ? 'Default domain is Public'
                                                                    : key === 'internal'
                                                                      ? 'Default domain is Internal-only'
                                                                      : 'Default domain is Hidden',
                                                            description:
                                                                key === 'public'
                                                                    ? 'Anyone can reach it. Applied instantly.'
                                                                    : key === 'internal'
                                                                      ? 'Hidden from the public internet; internal/mesh traffic still routes. Custom domains unaffected.'
                                                                      : 'Visitors see an unavailable page. Custom domains stay active.',
                                                        });
                                                    } catch (err) {
                                                        toast({ title: 'Error', description: 'Failed to update default domain access.', variant: 'destructive' });
                                                    }
                                                }}
                                                className={`px-3 py-1.5 text-xs font-bold transition-colors ${active ? cls : 'text-muted-foreground hover:bg-muted/50'}`}
                                            >
                                                {label}
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                    </div>
                    <div className={`flex items-center gap-3 p-3 border rounded-lg transition-colors ${service.public_domain_hidden ? 'bg-muted/10 border-border/50 opacity-60' : 'bg-muted/30 border-border'}`}>
                        <div className={`h-2 w-2 rounded-full ${service.public_domain_hidden ? 'bg-zinc-500' : service.wildcard_internal_only ? 'bg-blue-500' : 'bg-emerald-500 animate-pulse'}`} />
                        <span className={`font-mono text-sm flex-1 ${service.public_domain_hidden ? 'line-through text-muted-foreground' : ''}`}>{defaultDomain}</span>
                        {service.wildcard_internal_only === true && !service.public_domain_hidden && (
                            <span className="text-[10px] font-bold text-blue-500 bg-blue-500/10 px-1.5 py-0.5 rounded">INTERNAL ONLY</span>
                        )}
                        {!service.public_domain_hidden && (
                            <a href={`https://${defaultDomain}`} target="_blank" rel="noopener noreferrer" className="text-primary hover:text-primary/80">
                                <ExternalLink size={16} />
                            </a>
                        )}
                    </div>
                    {service.public_domain_hidden && (
                        <p className="text-xs text-muted-foreground mt-2">
                            The default domain now serves an unavailable (503) page instead of your app. Custom domains and host aliases stay active.
                        </p>
                    )}
                    {!service.public_domain_hidden && service.wildcard_internal_only === true && (
                        <p className="text-xs text-muted-foreground mt-2">
                            Hidden from the public internet — public visitors see an unavailable (503) page. Internal/mesh traffic still routes, and custom domains work normally.
                        </p>
                    )}

                    {/* Wildcard -> Custom Domain redirect */}
                    {(service.custom_domains?.length ?? 0) > 0 && !service.public_domain_hidden && (
                        <div className="flex items-center justify-between gap-3 p-3 bg-muted/30 border border-border rounded-lg mt-2">
                            <div className="min-w-0">
                                <p className="text-xs font-semibold text-muted-foreground mb-0.5">Redirect to Custom Domain</p>
                                <p className="text-xs text-muted-foreground truncate">
                                    Visitors of the default domain go straight to <span className="font-mono">{(service.custom_domains ?? [])[0]}</span> (301, path preserved)
                                </p>
                            </div>
                            <Switch
                                checked={service.wildcard_redirect_custom_domain === true}
                                onCheckedChange={async (checked) => {
                                    try {
                                        const updated = await servicesApi.update(service.id, { wildcard_redirect_custom_domain: checked });
                                        setService(prev => ({ ...prev, ...updated }));
                                        toast({
                                            title: checked ? 'Redirect enabled' : 'Redirect disabled',
                                            description: checked
                                                ? `Default domain now redirects to ${(service.custom_domains ?? [])[0]}. Routing updated.`
                                                : 'Default domain serves the service directly again.',
                                        });
                                    } catch (err) {
                                        toast({ title: 'Error', description: 'Failed to update redirect setting', variant: 'destructive' });
                                    }
                                }}
                            />
                        </div>
                    )}
                </div>

                {/* Host Aliases (accounts.google.com pattern) */}
                <div className="mb-8">
                    <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-1">Host Aliases</h4>
                    <p className="text-xs text-muted-foreground mb-3">
                        Extra hostnames that serve this app directly — like accounts.google.com.
                        Visiting the alias shows the rewrite target page (e.g. /login); all other paths load unchanged.
                    </p>
                    <ol className="text-xs text-muted-foreground space-y-1 list-decimal pl-4 mb-3">
                        <li>Add the alias hostname below.</li>
                        <li>Point its DNS at Grid — CNAME to your default domain, or an A record to your server IP.</li>
                        <li>SSL is issued automatically once DNS resolves. That&apos;s it — no config, no redeploy.</li>
                    </ol>

                    {(Array.isArray(service.host_aliases) ? service.host_aliases : []).map(({ host, rewrite_root }) => (
                        <div key={host} className="flex items-center gap-3 p-3 bg-card border border-border rounded-lg mb-2">
                            <div className="h-2 w-2 rounded-full bg-emerald-500" />
                            <span className="font-mono text-sm flex-1 truncate">{host}</span>
                            {rewrite_root && (
                                <span className="text-[10px] font-mono text-muted-foreground bg-muted/50 px-1.5 py-0.5 rounded">/ → {rewrite_root}</span>
                            )}
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10"
                                onClick={() => updateHostAliases((Array.isArray(service.host_aliases) ? service.host_aliases : []).filter(a => a.host !== host))}
                            >
                                <Trash2 className="w-4 h-4" />
                            </Button>
                        </div>
                    ))}

                    <div className="flex gap-2 mt-3">
                        <Input
                            placeholder="account.example.com"
                            value={newAliasHost}
                            onChange={(e) => setNewAliasHost(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleAddAlias()}
                            className="flex-1"
                        />
                        <Input
                            placeholder="/login (root rewrites to)"
                            value={newAliasRoot}
                            onChange={(e) => setNewAliasRoot(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleAddAlias()}
                            className="w-44"
                        />
                        <Button onClick={handleAddAlias}>
                            <Plus className="w-4 h-4 mr-2" /> Add
                        </Button>
                    </div>
                    <p className="text-xs text-muted-foreground mt-2">
                        Sessions are host-scoped — to keep users logged in across your main domain and the alias, set a shared cookie domain (e.g. <code className="font-mono">.example.com</code>) in your app.
                    </p>
                </div>

                {/* Path Redirects */}
                <div className="mb-8">
                    <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-1">Path Redirects</h4>
                    <p className="text-xs text-muted-foreground mb-3">
                        Permanently forward paths on this service&apos;s domains to another host, e.g. /account → account.example.com.
                    </p>

                    {(service.path_redirects ?? []).map(({ path, target }) => (
                        <div key={path} className="flex items-center gap-3 p-3 bg-card border border-border rounded-lg mb-2">
                            <span className="font-mono text-sm">{path}</span>
                            <ArrowRight size={14} className="text-muted-foreground shrink-0" />
                            <span className="font-mono text-sm flex-1 truncate">{target}</span>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10"
                                onClick={() => updatePathRedirects((service.path_redirects ?? []).filter(r => r.path !== path))}
                            >
                                <Trash2 className="w-4 h-4" />
                            </Button>
                        </div>
                    ))}

                    <div className="flex gap-2 mt-3">
                        <Input
                            placeholder="/account"
                            value={newPath}
                            onChange={(e) => setNewPath(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleAddPathRedirect()}
                            className="w-40"
                        />
                        <Input
                            placeholder="account.example.com"
                            value={newTarget}
                            onChange={(e) => setNewTarget(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleAddPathRedirect()}
                            className="flex-1"
                        />
                        <Button onClick={handleAddPathRedirect}>
                            <Plus className="w-4 h-4 mr-2" /> Add
                        </Button>
                    </div>
                    <p className="text-xs text-muted-foreground mt-2">
                        Redirects are permanent (301). The rest of the path and query string are preserved — /account/settings?q=1 lands on account.example.com/settings?q=1.
                    </p>
                </div>

                {/* Node URL Entry Points (full nodes only) */}
                {service.node_url && (
                    <div className="mb-8">
                        <div className="mb-3">
                            <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Node Entry Points</h4>
                            <p className="text-xs text-muted-foreground mt-1">
                                Control which URLs route traffic to this service. Both use the same backend.
                            </p>
                        </div>

                        {/* Wildcard URL (master-proxied) */}
                        <div className="mb-3">
                            <div className="flex items-center justify-between p-3 bg-muted/30 border border-border rounded-lg">
                                <div className="flex items-center gap-3 flex-1 min-w-0">
                                    <div className={`h-2 w-2 rounded-full ${service.wildcard_url_enabled !== false ? 'bg-emerald-500' : 'bg-zinc-500'}`} />
                                    <div className="min-w-0 flex-1">
                                        <p className="text-xs font-semibold text-muted-foreground mb-0.5">Wildcard URL (master-proxied)</p>
                                        <span className={`font-mono text-sm ${service.wildcard_url_enabled === false ? 'line-through text-muted-foreground' : ''}`}>
                                            {service.public_domain || `${service.name}.cloud.Trulay.co`}
                                        </span>
                                    </div>
                                </div>
                                <div className="flex items-center gap-2 ml-3">
                                    {service.wildcard_url_enabled !== false && (
                                        <a href={`https://${service.public_domain || `${service.name}.cloud.Trulay.co`}`} target="_blank" rel="noopener noreferrer" className="text-primary hover:text-primary/80">
                                            <ExternalLink size={16} />
                                        </a>
                                    )}
                                    <Switch
                                        checked={service.wildcard_url_enabled !== false}
                                        onCheckedChange={async (checked) => {
                                            try {
                                                const result = await servicesApi.toggleWildcardUrl(service.id, checked);
                                                setService(prev => ({ ...prev, wildcard_url_enabled: result.wildcard_url_enabled }));
                                                toast({ title: 'Success', description: `Wildcard URL ${checked ? 'enabled' : 'disabled'}. Routing updated.` });
                                            } catch (err) {
                                                toast({ title: 'Error', description: 'Failed to toggle wildcard URL', variant: 'destructive' });
                                            }
                                        }}
                                    />
                                </div>
                            </div>
                        </div>

                        {/* Direct Node URL */}
                        <div>
                            <div className="flex items-center justify-between p-3 bg-muted/30 border border-border rounded-lg">
                                <div className="flex items-center gap-3 flex-1 min-w-0">
                                    <div className={`h-2 w-2 rounded-full ${service.node_url_enabled !== false ? 'bg-emerald-500' : 'bg-zinc-500'}`} />
                                    <div className="min-w-0 flex-1">
                                        <p className="text-xs font-semibold text-muted-foreground mb-0.5">Direct Node URL</p>
                                        <span className={`font-mono text-sm ${service.node_url_enabled === false ? 'line-through text-muted-foreground' : ''}`}>
                                            {service.node_url.replace('https://', '')}
                                        </span>
                                    </div>
                                </div>
                                <div className="flex items-center gap-2 ml-3">
                                    {service.node_url_enabled !== false && (
                                        <a href={service.node_url!} target="_blank" rel="noopener noreferrer" className="text-primary hover:text-primary/80">
                                            <ExternalLink size={16} />
                                        </a>
                                    )}
                                    <Switch
                                        checked={service.node_url_enabled !== false}
                                        onCheckedChange={async (checked) => {
                                            try {
                                                const result = await servicesApi.toggleNodeUrl(service.id, checked);
                                                setService(prev => ({ ...prev, node_url_enabled: result.node_url_enabled }));
                                                toast({ title: 'Success', description: `Node URL ${checked ? 'enabled' : 'disabled'}. Routing updated.` });
                                            } catch (err) {
                                                toast({ title: 'Error', description: 'Failed to toggle node URL', variant: 'destructive' });
                                            }
                                        }}
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                )}

                {/* Staging Domain */}
                <div className="mb-8">
                    <div className="flex items-center justify-between mb-3">
                        <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Staging Domain</h4>
                    </div>
                    <p className="text-xs text-muted-foreground mb-3">
                        Custom domain for webhook deployments. Pushes will deploy to this URL for review before going live.
                        If blank, auto-generated as <code>staging-{service.name}.{service.public_domain?.split('.').slice(1).join('.') || 'cloud.Trulay.co'}</code>.
                    </p>
                    <div className="flex gap-2 mb-3">
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

                    {/* Staging domain DNS status + verify */}
                    {stagingDomain.trim() && (
                        <div className="flex items-center justify-between p-3 bg-card border border-border rounded-lg">
                            <div className="flex items-center gap-3">
                                <Globe className="w-4 h-4 text-amber-500" />
                                <span className="font-mono text-sm">{stagingDomain.trim()}</span>
                                {stagingVerified === true && (
                                    <span className="text-[10px] bg-emerald-500/10 text-emerald-500 px-1.5 py-0.5 rounded flex items-center gap-1">
                                        <CheckCircle size={10} /> Verified
                                    </span>
                                )}
                                {stagingVerified === false && (
                                    <span className="text-[10px] bg-red-500/10 text-red-400 px-1.5 py-0.5 rounded flex items-center gap-1">
                                        <XCircle size={10} /> DNS Not Found
                                    </span>
                                )}
                                {stagingVerified === null && !stagingChecking && (
                                    <span className="text-[10px] bg-yellow-500/10 text-yellow-500 px-1.5 py-0.5 rounded">
                                        Pending
                                    </span>
                                )}
                            </div>
                            <div className="flex items-center gap-1">
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-8 w-8 text-primary hover:text-primary/80"
                                    onClick={handleVerifyStaging}
                                    disabled={stagingChecking}
                                    title="Verify DNS"
                                >
                                    {stagingChecking ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                                </Button>
                                {stagingVerified === true && (
                                    <a
                                        href={`https://${stagingDomain.trim()}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center justify-center h-8 w-8 text-primary hover:text-primary/80"
                                    >
                                        <ExternalLink size={16} />
                                    </a>
                                )}
                            </div>
                        </div>
                    )}
                </div>

                {/* Active Staging Deployment or No Staged Container */}
                {stagedDeployment && stagedDeployment.staging_url ? (
                    <div className="mb-8">
                        <div className="flex items-center justify-between mb-3">
                            <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Active Staged Deployment</h4>
                            <span className="text-[10px] bg-amber-500/10 text-amber-500 px-1.5 py-0.5 rounded flex items-center gap-1">
                                STAGED
                            </span>
                        </div>
                        <p className="text-xs text-muted-foreground mb-3">
                            This staging URL is live and ready for review. Promote it to make it the production deployment.
                        </p>
                        <div className="flex items-center gap-3 p-3 bg-amber-500/5 border border-amber-500/20 rounded-lg">
                            <div className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
                            <span className="font-mono text-sm flex-1 text-amber-400">{stagedDeployment.staging_url.replace('https://', '')}</span>
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={() => copyToClipboard(stagedDeployment.staging_url!.replace('https://', ''))}
                            >
                                <Copy size={14} />
                            </Button>
                            <a
                                href={stagedDeployment.staging_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-primary hover:text-primary/80"
                            >
                                <ExternalLink size={16} />
                            </a>
                        </div>
                    </div>
                ) : stagingDomain.trim() ? (
                    <div className="mb-8">
                        <div className="flex items-center justify-between mb-3">
                            <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Staging Deployment</h4>
                            <span className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded flex items-center gap-1">
                                NO STAGED CONTAINER
                            </span>
                        </div>
                        <p className="text-xs text-muted-foreground mb-3">
                            No active staged deployment. Push to this service to deploy a staged container for review before going live.
                        </p>
                        <div className="flex items-center gap-3 p-3 bg-muted/30 border border-border rounded-lg">
                            <div className="h-2 w-2 rounded-full bg-muted-foreground/40" />
                            <span className="font-mono text-sm flex-1 text-muted-foreground">{stagingDomain.trim()}</span>
                            <span className="text-[10px] text-muted-foreground">Waiting for staged deploy</span>
                        </div>
                    </div>
                ) : null}

                {/* Custom Domains */}
                <div>
                    <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-3">Custom Domains</h4>

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
