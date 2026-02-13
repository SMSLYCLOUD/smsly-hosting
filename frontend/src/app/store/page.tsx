'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Database, Layout, Box, Cpu, Search, Cloud, Activity } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { useToast } from '@/components/ui/use-toast';
import api, { templatesApi } from '@/lib/api';

const categories = [
    { id: 'all', label: 'All Apps' },
    { id: 'smsly-ecosystem', label: 'SMSLY Ecosystem' },
    { id: 'database', label: 'Databases' },
    { id: 'cms', label: 'CMS' },
    { id: 'analytics', label: 'Analytics' },
    { id: 'dev-tools', label: 'Dev Tools' },
];

const getIconForCategory = (category: string) => {
    switch (category) {
        case 'database': return Database;
        case 'cms': return Layout;
        case 'analytics': return Activity;
        case 'dev-tools': return Cpu;
        case 'smsly-ecosystem': return Cloud;
        default: return Box;
    }
};

const getColorForCategory = (category: string) => {
    switch (category) {
        case 'database': return 'bg-blue-600';
        case 'cms': return 'bg-purple-600';
        case 'analytics': return 'bg-green-600';
        case 'dev-tools': return 'bg-orange-600';
        case 'smsly-ecosystem': return 'bg-primary';
        default: return 'bg-gray-600';
    }
};

export default function AppStorePage() {
    const router = useRouter();
    const { toast } = useToast();
    const [activeCategory, setActiveCategory] = useState('all');
    const [search, setSearch] = useState('');
    const [apps, setApps] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [deployingId, setDeployingId] = useState<string | null>(null);

    useEffect(() => {
        async function loadTemplates() {
            try {
                const data = await templatesApi.list();
                // Defensive: ensure data is always an array
                setApps(Array.isArray(data) ? data : []);
            } catch (error) {
                console.error("Failed to fetch templates:", error);
                setApps([]);
            } finally {
                setLoading(false);
            }
        }
        loadTemplates();
    }, []);

    const normalizeCategory = (app: any): string => {
        const raw = app?.category;
        if (typeof raw === "string") return raw;
        if (raw && typeof raw === "object") {
            if (typeof raw.id === "string") return raw.id;
            if (typeof raw.slug === "string") return raw.slug;
            if (typeof raw.name === "string") return raw.name;
        }
        return "";
    };

    const handleOneClickDeploy = async (tpl: any) => {
        if (!tpl?.id) {
            toast({ title: "Template missing fields", description: "This template can't be deployed.", variant: "destructive" });
            return;
        }

        const token = typeof window !== "undefined" ? localStorage.getItem("auth_token") : null;
        if (!token) {
            toast({ title: "Login required", description: "Please login to deploy templates.", variant: "destructive" });
            router.push("/login");
            return;
        }

        setDeployingId(String(tpl.id));
        try {
            // Backend endpoint provisions required addons first, then triggers deployment via Celery.
            const res = await api.post(`/templates/${tpl.id}/one_click_deploy/`, {});
            const serviceId = res?.data?.service_id;
            const serviceName = res?.data?.service_name || "service";

            if (!serviceId) {
                throw new Error("Missing service_id from one_click_deploy response");
            }

            toast({
                title: "Deployment queued",
                description: `${tpl.name} is provisioning dependencies and deploying as ${serviceName}.`,
            });
            router.push(`/services/${serviceId}`);
        } catch (err: any) {
            console.error(err);
            const msg =
                err?.response?.data?.error ||
                err?.response?.data?.detail ||
                err?.message ||
                "Deployment failed.";
            toast({ title: "Deploy failed", description: msg, variant: "destructive" });
        } finally {
            setDeployingId(null);
        }
    };

    const filteredApps = apps.filter((app) => {
        const category = normalizeCategory(app);
        const name = String(app?.name || "");
        return (
            (activeCategory === "all" || category === activeCategory) &&
            name.toLowerCase().includes(search.toLowerCase())
        );
    });

    return (
        <DashboardShell>

            {/* Hero / Search */}
            <section className="border-b bg-muted/40 py-12">
                <div className="container max-w-6xl">
                    <h1 className="text-3xl font-bold tracking-tight mb-4">Templates</h1>
                    <p className="text-muted-foreground mb-8 text-lg">Browse and deploy production-ready application templates.</p>
                    <p className="text-xs text-muted-foreground mb-6">Total templates: {apps.length}</p>

                    <div className="relative max-w-md">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" size={18} />
                        <Input
                            placeholder="Search templates..."
                            className="pl-10 h-12 text-base bg-background text-foreground"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                        />
                    </div>
                </div>
            </section>

            {/* Grid */}
            <section className="container max-w-6xl py-12">
                {/* Categories */}
                <div className="flex gap-2 mb-8 overflow-x-auto pb-4">
                    {categories.map(cat => (
                        <Button
                            key={cat.id}
                            variant={activeCategory === cat.id ? "default" : "outline"}
                            onClick={() => setActiveCategory(cat.id)}
                            className="rounded-full"
                        >
                            {cat.label}
                        </Button>
                    ))}
                </div>

                {loading ? (
                    <div className="text-center py-20 text-muted-foreground">Loading marketplace...</div>
                ) : filteredApps.length === 0 ? (
                    <div className="text-center py-20 text-muted-foreground">No templates found.</div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {filteredApps.map((app) => {
                            const category = normalizeCategory(app);
                            const Icon = getIconForCategory(category);
                            const color = getColorForCategory(category);
                            const isDeploying = deployingId === String(app.id);

                            return (
                                <Card key={app.id} className="group hover:border-primary/50 transition-all hover:shadow-md cursor-pointer" onClick={() => router.push(`/new?template=${app.id}`)}>
                                    <CardHeader className="flex flex-row items-start gap-4 space-y-0">
                                        <div className={`p-3 rounded-xl text-white shadow-sm ${color}`}>
                                            <Icon size={24} />
                                        </div>
                                        <div className="flex-1">
                                            <CardTitle className="text-lg group-hover:text-primary transition-colors">{app.name}</CardTitle>
                                            <span className="text-xs text-muted-foreground font-medium uppercase tracking-wider">{category}</span>
                                        </div>
                                    </CardHeader>
                                    <CardContent>
                                        <CardDescription className="line-clamp-2 h-10">
                                            {app.description}
                                        </CardDescription>
                                    </CardContent>
                                    <CardFooter>
                                        <Button
                                            className="w-full bg-secondary text-secondary-foreground hover:bg-secondary/80 font-semibold group-hover:bg-primary group-hover:text-primary-foreground transition-all"
                                            disabled={isDeploying}
                                            onClick={(e) => {
                                                e.preventDefault();
                                                e.stopPropagation();
                                                handleOneClickDeploy(app);
                                            }}
                                        >
                                            {isDeploying ? "Deploying..." : "1-Click Deploy"}
                                        </Button>
                                    </CardFooter>
                                </Card>
                            );
                        })}
                    </div>
                )}
            </section>
        </DashboardShell>
    );
}
