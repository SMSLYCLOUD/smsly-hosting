'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Database, Layout, Box, Globe, Cpu, Search, Server, Cloud, Activity } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Navbar } from '@/components/layout/Navbar';
import { templatesApi } from '@/lib/api';

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
    const [activeCategory, setActiveCategory] = useState('all');
    const [search, setSearch] = useState('');
    const [apps, setApps] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function loadTemplates() {
            try {
                const data = await templatesApi.list();
                setApps(data);
            } catch (error) {
                console.error("Failed to fetch templates:", error);
            } finally {
                setLoading(false);
            }
        }
        loadTemplates();
    }, []);

    const filteredApps = apps.filter(app =>
        (activeCategory === 'all' || app.category === activeCategory) &&
        app.name.toLowerCase().includes(search.toLowerCase())
    );

    return (
        <main className="min-h-screen bg-background text-foreground font-sans">
            <Navbar />

            {/* Hero / Search */}
            <section className="border-b bg-muted/40 py-12">
                <div className="container max-w-6xl">
                    <h1 className="text-3xl font-bold tracking-tight mb-4">Marketplace</h1>
                    <p className="text-muted-foreground mb-8 text-lg">One-click deploy production-ready applications.</p>

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
                            const Icon = getIconForCategory(app.category);
                            const color = getColorForCategory(app.category);

                            return (
                                <Card key={app.id} className="group hover:border-primary/50 transition-all hover:shadow-md cursor-pointer" onClick={() => router.push(`/new?template=${app.id}`)}>
                                    <CardHeader className="flex flex-row items-start gap-4 space-y-0">
                                        <div className={`p-3 rounded-xl text-white shadow-sm ${color}`}>
                                            <Icon size={24} />
                                        </div>
                                        <div className="flex-1">
                                            <CardTitle className="text-lg group-hover:text-primary transition-colors">{app.name}</CardTitle>
                                            <span className="text-xs text-muted-foreground font-medium uppercase tracking-wider">{app.category}</span>
                                        </div>
                                    </CardHeader>
                                    <CardContent>
                                        <CardDescription className="line-clamp-2 h-10">
                                            {app.description}
                                        </CardDescription>
                                    </CardContent>
                                    <CardFooter>
                                        <Button className="w-full bg-secondary text-secondary-foreground hover:bg-secondary/80 font-semibold group-hover:bg-primary group-hover:text-primary-foreground transition-all">
                                            Deploy
                                        </Button>
                                    </CardFooter>
                                </Card>
                            );
                        })}
                    </div>
                )}
            </section>
        </main>
    );
}
