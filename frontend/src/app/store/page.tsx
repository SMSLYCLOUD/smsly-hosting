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
    // ===== DATABASES =====
    { id: 'postgres', name: 'PostgreSQL', description: 'The world\'s most advanced open source relational database.', category: 'database', icon: Database, color: 'bg-blue-600' },
    { id: 'redis', name: 'Redis', description: 'In-memory data structure store, used as database, cache, and message broker.', category: 'database', icon: Box, color: 'bg-red-500' },
    { id: 'mongodb', name: 'MongoDB', description: 'Document database designed for ease of development and scaling.', category: 'database', icon: Database, color: 'bg-green-600' },
    { id: 'mysql', name: 'MySQL', description: 'The most popular open-source relational database in the world.', category: 'database', icon: Database, color: 'bg-orange-500' },
    { id: 'mariadb', name: 'MariaDB', description: 'Community-developed fork of MySQL with enhanced features.', category: 'database', icon: Database, color: 'bg-amber-600' },
    { id: 'clickhouse', name: 'ClickHouse', description: 'Fast open-source OLAP database for real-time analytics.', category: 'database', icon: Database, color: 'bg-yellow-500' },
    { id: 'influxdb', name: 'InfluxDB', description: 'Time series database for metrics, events, and real-time analytics.', category: 'database', icon: Database, color: 'bg-purple-500' },
    { id: 'cassandra', name: 'Cassandra', description: 'Highly-scalable partitioned row store NoSQL database.', category: 'database', icon: Database, color: 'bg-teal-600' },
    { id: 'neo4j', name: 'Neo4j', description: 'Graph database platform for connected data applications.', category: 'database', icon: Database, color: 'bg-sky-500' },
    { id: 'meilisearch', name: 'Meilisearch', description: 'Lightning-fast, open-source search engine alternative to Algolia.', category: 'database', icon: Database, color: 'bg-pink-500' },
    { id: 'elasticsearch', name: 'Elasticsearch', description: 'Distributed search and analytics engine for all types of data.', category: 'database', icon: Database, color: 'bg-lime-500' },

    // ===== CMS & WEBSITES =====
    { id: 'wordpress', name: 'WordPress', description: 'The most popular CMS for building websites and blogs.', category: 'cms', icon: Layout, color: 'bg-cyan-600' },
    { id: 'ghost', name: 'Ghost', description: 'Professional publishing platform for modern online publications.', category: 'cms', icon: Layout, color: 'bg-violet-600' },
    { id: 'strapi', name: 'Strapi', description: 'Leading open-source headless CMS with customizable API.', category: 'cms', icon: Layout, color: 'bg-indigo-500' },
    { id: 'directus', name: 'Directus', description: 'Open-source headless CMS and API for any SQL database.', category: 'cms', icon: Layout, color: 'bg-purple-600' },
    { id: 'payload', name: 'Payload CMS', description: 'Modern TypeScript headless CMS with beautiful admin UI.', category: 'cms', icon: Layout, color: 'bg-blue-500' },
    { id: 'umami', name: 'Umami', description: 'Simple, fast, privacy-focused alternative to Google Analytics.', category: 'analytics', icon: Globe, color: 'bg-emerald-600' },
    { id: 'plausible', name: 'Plausible', description: 'Lightweight privacy-friendly Google Analytics alternative.', category: 'analytics', icon: Globe, color: 'bg-violet-500' },
    { id: 'matomo', name: 'Matomo', description: 'Privacy-respecting web analytics platform.', category: 'analytics', icon: Globe, color: 'bg-blue-400' },

    // ===== DEV TOOLS =====
    { id: 'n8n', name: 'n8n', description: 'Fair-code workflow automation tool for technical people.', category: 'dev-tools', icon: Cpu, color: 'bg-orange-500' },
    { id: 'gitea', name: 'Gitea', description: 'Lightweight self-hosted Git service written in Go.', category: 'dev-tools', icon: Globe, color: 'bg-green-500' },
    { id: 'gitlab', name: 'GitLab', description: 'Complete DevOps platform with Git, CI/CD, and more.', category: 'dev-tools', icon: Globe, color: 'bg-orange-600' },
    { id: 'jenkins', name: 'Jenkins', description: 'The leading open source automation server for CI/CD.', category: 'dev-tools', icon: Cpu, color: 'bg-red-600' },
    { id: 'drone', name: 'Drone CI', description: 'Container-native CI/CD platform with simple YAML config.', category: 'dev-tools', icon: Cpu, color: 'bg-blue-700' },
    { id: 'sonarqube', name: 'SonarQube', description: 'Code quality and security scanning platform.', category: 'dev-tools', icon: Cpu, color: 'bg-sky-600' },
    { id: 'harbor', name: 'Harbor', description: 'Enterprise-class container registry with security features.', category: 'dev-tools', icon: Box, color: 'bg-teal-500' },
    { id: 'vault', name: 'HashiCorp Vault', description: 'Secrets management and data protection platform.', category: 'dev-tools', icon: Box, color: 'bg-gray-700' },
    { id: 'minio', name: 'MinIO', description: 'High-performance S3-compatible object storage.', category: 'dev-tools', icon: Box, color: 'bg-red-700' },
    { id: 'registry', name: 'Docker Registry', description: 'Private container image registry for Docker images.', category: 'dev-tools', icon: Box, color: 'bg-blue-500' },

    // ===== ANALYTICS & BI =====
    { id: 'metabase', name: 'Metabase', description: 'The simplest way to get business intelligence and analytics.', category: 'analytics', icon: Globe, color: 'bg-indigo-500' },
    { id: 'superset', name: 'Apache Superset', description: 'Modern data exploration and visualization platform.', category: 'analytics', icon: Globe, color: 'bg-cyan-500' },
    { id: 'redash', name: 'Redash', description: 'Connect and visualize all your data sources.', category: 'analytics', icon: Globe, color: 'bg-amber-500' },
    { id: 'grafana', name: 'Grafana', description: 'Observability and data visualization platform.', category: 'analytics', icon: Globe, color: 'bg-orange-400' },
    { id: 'prometheus', name: 'Prometheus', description: 'Systems monitoring and alerting toolkit.', category: 'analytics', icon: Cpu, color: 'bg-rose-600' },

    // ===== COMMUNICATION =====
    { id: 'mattermost', name: 'Mattermost', description: 'Open-source Slack alternative for secure team collaboration.', category: 'cms', icon: Layout, color: 'bg-blue-600' },
    { id: 'rocketchat', name: 'Rocket.Chat', description: 'Open-source team communication platform.', category: 'cms', icon: Layout, color: 'bg-red-500' },
    { id: 'jitsi', name: 'Jitsi Meet', description: 'Secure, flexible, and open-source video conferencing.', category: 'cms', icon: Layout, color: 'bg-blue-400' },
    { id: 'element', name: 'Element', description: 'Secure decentralized chat based on Matrix protocol.', category: 'cms', icon: Layout, color: 'bg-green-500' },

    // ===== PROJECT MANAGEMENT =====
    { id: 'nextcloud', name: 'Nextcloud', description: 'Self-hosted productivity platform (files, calendar, contacts).', category: 'cms', icon: Layout, color: 'bg-blue-500' },
    { id: 'outline', name: 'Outline', description: 'Modern team knowledge base and wiki.', category: 'cms', icon: Layout, color: 'bg-indigo-400' },
    { id: 'bookstack', name: 'BookStack', description: 'Simple, self-hosted wiki/documentation platform.', category: 'cms', icon: Layout, color: 'bg-cyan-700' },
    { id: 'focalboard', name: 'Focalboard', description: 'Open-source project management alternative to Notion/Trello.', category: 'dev-tools', icon: Layout, color: 'bg-violet-500' },
    { id: 'plane', name: 'Plane', description: 'Open-source project management alternative to Jira.', category: 'dev-tools', icon: Layout, color: 'bg-purple-500' },

    // ===== E-COMMERCE & MISC =====
    { id: 'nocodb', name: 'NocoDB', description: 'Open-source Airtable alternative, turns any database into a spreadsheet.', category: 'dev-tools', icon: Database, color: 'bg-blue-600' },
    { id: 'appwrite', name: 'Appwrite', description: 'Secure backend platform for web, mobile, and Flutter apps.', category: 'dev-tools', icon: Cpu, color: 'bg-pink-600' },
    { id: 'supabase', name: 'Supabase', description: 'Open-source Firebase alternative with Postgres database.', category: 'database', icon: Database, color: 'bg-emerald-500' },
    { id: 'pocketbase', name: 'PocketBase', description: 'Open-source backend in a single file (Go + SQLite).', category: 'dev-tools', icon: Database, color: 'bg-gray-600' },
    { id: 'uptime-kuma', name: 'Uptime Kuma', description: 'Self-hosted uptime monitoring tool with beautiful UI.', category: 'analytics', icon: Globe, color: 'bg-green-600' },
    { id: 'portainer', name: 'Portainer', description: 'Container management UI for Docker, Swarm, and Kubernetes.', category: 'dev-tools', icon: Box, color: 'bg-cyan-500' },
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
