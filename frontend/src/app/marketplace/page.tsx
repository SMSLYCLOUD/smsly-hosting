'use client';

import React, { useState, useEffect } from 'react';
import { templatesApi, servicesApi } from '@/lib/api';
import { Navbar } from '@/components/layout/Navbar';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Search, Server, Database, BarChart, FileText, ArrowRight, Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { toast } from '@/components/ui/use-toast';

export default function MarketplacePage() {
    const [templates, setTemplates] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [category, setCategory] = useState<string | null>(null);
    const [deployingId, setDeployingId] = useState<string | null>(null);
    const router = useRouter();

    useEffect(() => {
        const fetchTemplates = async () => {
            try {
                // Assuming list supports search/category params in client or we filter client side
                const data = await templatesApi.list();
                setTemplates(data);
            } catch (err) {
                console.error(err);
            } finally {
                setLoading(false);
            }
        };
        fetchTemplates();
    }, []);

    const categories = Array.from(new Set(templates.map(t => t.category))).filter(Boolean);

    const filteredTemplates = templates.filter(t => {
        const matchesSearch = t.name.toLowerCase().includes(search.toLowerCase()) ||
                              t.description.toLowerCase().includes(search.toLowerCase());
        const matchesCategory = category ? t.category === category : true;
        return matchesSearch && matchesCategory;
    });

    const handleDeploy = async (template: any) => {
        setDeployingId(template.id);
        try {
            // Create Service from Template
            const service = await servicesApi.create({
                name: `${template.id}-${Math.floor(Math.random() * 1000)}`,
                deploy_type: 'TEMPLATE',
                template_id: template.id,
                docker_image: template.docker_image,
                internal_port: template.default_port,
                env_vars: template.env_vars || []
            });

            toast({ title: "Service Created", description: `Deploying ${template.name}...` });

            // Trigger deployment
            await servicesApi.deploy(service.id);

            router.push(`/services/${service.id}`);
        } catch (err) {
            console.error(err);
            toast({ title: "Failed to deploy", variant: "destructive" });
            setDeployingId(null);
        }
    };

    return (
        <main className="min-h-screen bg-background flex flex-col">
            <Navbar />

            <div className="flex-1 container mx-auto py-12 px-4">
                <div className="text-center max-w-2xl mx-auto mb-16">
                    <h1 className="text-4xl font-bold tracking-tight mb-4">Template Marketplace</h1>
                    <p className="text-muted-foreground text-lg mb-8">
                        One-click deployments for databases, starters, and open-source applications.
                    </p>

                    <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground w-5 h-5" />
                        <Input
                            placeholder="Search templates..."
                            className="pl-10 h-12 text-lg"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                    </div>
                </div>

                {/* Categories */}
                <div className="flex gap-2 justify-center mb-12 flex-wrap">
                    <Button
                        variant={category === null ? "default" : "outline"}
                        onClick={() => setCategory(null)}
                        className="rounded-full"
                    >
                        All
                    </Button>
                    {categories.map(c => (
                        <Button
                            key={c}
                            variant={category === c ? "default" : "outline"}
                            onClick={() => setCategory(c)}
                            className="rounded-full"
                        >
                            {c}
                        </Button>
                    ))}
                </div>

                {/* Grid */}
                {loading ? (
                    <div className="flex justify-center p-12">
                        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {filteredTemplates.map((template) => (
                            <Card key={template.id} className="p-6 hover:border-primary/50 transition-all cursor-default flex flex-col h-full group">
                                <div className="flex items-start justify-between mb-4">
                                    <div className="w-12 h-12 bg-muted rounded-lg p-2 flex items-center justify-center">
                                        {template.icon ? (
                                            <img src={template.icon} alt={template.name} className="w-full h-full object-contain" />
                                        ) : (
                                            <Server className="w-6 h-6 text-muted-foreground" />
                                        )}
                                    </div>
                                    <span className="text-xs font-bold px-2 py-1 bg-muted rounded uppercase tracking-wider text-muted-foreground">
                                        {template.category}
                                    </span>
                                </div>

                                <h3 className="font-bold text-xl mb-2 group-hover:text-primary transition-colors">
                                    {template.name}
                                </h3>
                                <p className="text-sm text-muted-foreground mb-6 flex-1">
                                    {template.description}
                                </p>

                                <Button
                                    className="w-full group-hover:bg-primary group-hover:text-primary-foreground transition-colors"
                                    onClick={() => handleDeploy(template)}
                                    disabled={!!deployingId}
                                >
                                    {deployingId === template.id ? (
                                        <Loader2 className="w-4 h-4 animate-spin" />
                                    ) : (
                                        <>
                                            Deploy <ArrowRight className="w-4 h-4 ml-2" />
                                        </>
                                    )}
                                </Button>
                            </Card>
                        ))}
                    </div>
                )}
            </div>
        </main>
    );
}
