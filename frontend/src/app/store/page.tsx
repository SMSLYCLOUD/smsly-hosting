'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Database, Layout, Box, Globe, Cpu, Search, Plus } from 'lucide-react';
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

const apps = [
  { id: 'postgres', name: 'PostgreSQL', description: 'The world\'s most advanced open source relational database.', category: 'database', icon: Database, color: 'bg-blue-500' },
  { id: 'redis', name: 'Redis', description: 'In-memory data structure store, used as a database, cache, and message broker.', category: 'database', icon: Box, color: 'bg-red-500' },
  { id: 'wordpress', name: 'WordPress', description: 'The most popular CMS for building websites and blogs.', category: 'cms', icon: Layout, color: 'bg-cyan-600' },
  { id: 'n8n', name: 'n8n', description: 'Workflow automation tool for technical people.', category: 'dev-tools', icon: Cpu, color: 'bg-orange-500' },
  { id: 'metabase', name: 'Metabase', description: 'The simplest, fastest way to get business intelligence and analytics.', category: 'analytics', icon: Globe, color: 'bg-indigo-500' },
  { id: 'mongodb', name: 'MongoDB', description: 'The most popular database for modern apps.', category: 'database', icon: Database, color: 'bg-green-600' },
];

export default function AppStorePage() {
  const router = useRouter();
  const [activeCategory, setActiveCategory] = useState('all');
  const [search, setSearch] = useState('');

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

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredApps.map((app) => (
                <Card key={app.id} className="group hover:border-primary/50 transition-all hover:shadow-md cursor-pointer" onClick={() => router.push(`/new?template=${app.id}`)}>
                    <CardHeader className="flex flex-row items-start gap-4 space-y-0">
                        <div className={`p-3 rounded-xl text-white shadow-sm ${app.color}`}>
                            <app.icon size={24} />
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
            ))}
        </div>
      </section>
    </main>
  );
}
