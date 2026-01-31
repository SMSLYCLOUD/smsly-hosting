'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import axios from 'axios';
import { Database, Layout, Box, Globe, Cpu, Search, Plus, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Navbar } from '@/components/layout/Navbar';

const categories = [
    { id: 'all', label: 'All Apps' },
    { id: 'database', label: 'Databases' },
    { id: 'cms', label: 'CMS' },
    { id: 'analytics', label: 'Analytics' },
    { id: 'dev-tools', label: 'Dev Tools' },
];

// Helper to map category to icon
const getIcon = (category: string) => {
    switch (category) {
        case 'database': return Database;
        case 'cms': return Layout;
        case 'analytics': return Globe;
        case 'dev-tools': return Cpu;
        default: return Box;
    }
};

// Helper to map category to color
const getColor = (category: string) => {
    switch (category) {
        case 'database': return 'bg-blue-600';
        case 'cms': return 'bg-purple-600';
        case 'analytics': return 'bg-emerald-600';
        case 'dev-tools': return 'bg-orange-500';
        default: return 'bg-gray-600';
    }
};

interface Template {
    id: string;
    name: string;
    description: string;
    category: string;
}

export default function AppStorePage() {
    const router = useRouter();
    const [activeCategory, setActiveCategory] = useState('all');
    const [search, setSearch] = useState('');
    const [apps, setApps] = useState<Template[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchTemplates = async () => {
            try {
                const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
                const res = await axios.get(`${API_URL}/templates/`);
                if (res.data && Array.isArray(res.data.results)) {
                    setApps(res.data.results);
                } else if (Array.isArray(res.data)) {
                    setApps(res.data);
                } else {
                    setApps([]);
                }
            } catch (e) {
                console.error("Failed to load templates", e);
            } finally {
                setLoading(false);
            }
        };
        fetchTemplates();
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
                    <div className="flex justify-center py-24">
                        <Loader2 className="h-8 w-8 animate-spin text-primary" />
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {filteredApps.map((app) => {
                            const Icon = getIcon(app.category);
                            const colorClass = getColor(app.category);

                            return (
                                <Card key={app.id} className="group hover:border-primary/50 transition-all hover:shadow-md cursor-pointer" onClick={() => router.push(`/new?template=${app.id}`)}>
                                    <CardHeader className="flex flex-row items-start gap-4 space-y-0">
                                        <div className={`p-3 rounded-xl text-white shadow-sm ${colorClass}`}>
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
