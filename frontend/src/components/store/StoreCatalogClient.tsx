'use client';

import React, { useState, useEffect, useCallback } from 'react';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Database, Layout, Box, Cpu, Search, Cloud, Activity, Brain, AlertCircle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useToast } from '@/components/ui/use-toast';
import api, { templatesApi } from '@/lib/api';

const categories = [
  { id: 'all', label: 'All Apps' },
  { id: 'smsly-ecosystem', label: 'Grid Ecosystem' },
  { id: 'intelligence', label: 'Intelligence' },
  { id: 'database', label: 'Databases' },
  { id: 'cms', label: 'CMS' },
  { id: 'analytics', label: 'Analytics' },
  { id: 'dev-tools', label: 'Dev Tools' },
];

const getIconForCategory = (category: string) => {
  switch (category) {
    case 'database':
      return Database;
    case 'cms':
      return Layout;
    case 'analytics':
      return Activity;
    case 'dev-tools':
      return Cpu;
    case 'intelligence':
      return Brain;
    case 'smsly-ecosystem':
      return Cloud;
    default:
      return Box;
  }
};

const getColorForCategory = (category: string) => {
  switch (category) {
    case 'database':
      return 'bg-blue-700';
    case 'cms':
      return 'bg-purple-700';
    case 'analytics':
      return 'bg-emerald-700';
    case 'intelligence':
      return 'bg-cyan-700';
    case 'dev-tools':
      return 'bg-orange-700';
    case 'smsly-ecosystem':
      return 'bg-primary';
    default:
      return 'bg-slate-700';
  }
};

interface TemplateItem {
  id: string | number;
  name?: string;
  description?: string;
  category?: string | { id?: string; slug?: string; name?: string };
  logo_url?: string;
  icon?: string;
}

export function StoreCatalogClient() {
  const router = useRouter();
  const { toast } = useToast();
  const [activeCategory, setActiveCategory] = useState('all');
  const [search, setSearch] = useState('');
  const [apps, setApps] = useState<TemplateItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [deployingId, setDeployingId] = useState<string | null>(null);

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const data = await templatesApi.list();
      setApps(Array.isArray(data) ? data : []);
    } catch {
      setLoadError('Unable to load application templates right now.');
      setApps([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  const normalizeCategory = (app: TemplateItem): string => {
    const raw = app?.category;
    if (typeof raw === 'string') return raw;
    if (raw && typeof raw === 'object') {
      if (typeof raw.id === 'string') return raw.id;
      if (typeof raw.slug === 'string') return raw.slug;
      if (typeof raw.name === 'string') return raw.name;
    }
    return '';
  };

  const handleOneClickDeploy = async (tpl: TemplateItem) => {
    if (!tpl?.id) {
      toast({
        title: 'Template missing fields',
        description: "This template can't be deployed.",
        variant: 'destructive',
      });
      return;
    }

    setDeployingId(String(tpl.id));
    try {
      const res = await api.post(`/templates/${tpl.id}/one_click_deploy/`, {});
      const serviceId = res?.data?.service_id;
      const serviceName = res?.data?.service_name || 'service';
      if (!serviceId) {
        throw new Error('Missing service_id from one_click_deploy response');
      }

      toast({
        title: 'Deployment queued',
        description: `${tpl.name || 'Template'} is provisioning dependencies and deploying as ${serviceName}.`,
      });
      router.push(`/services/${serviceId}`);
    } catch (err: unknown) {
      const errObj = err as {
        response?: { status?: number; data?: { error?: string; detail?: string } };
        message?: string;
      };
      if (errObj?.response?.status === 401) {
        router.push(`/login?next=${encodeURIComponent('/store')}`);
        return;
      }
      const msg =
        errObj?.response?.data?.error ||
        errObj?.response?.data?.detail ||
        errObj?.message ||
        'Deployment failed.';
      toast({ title: 'Deploy failed', description: msg, variant: 'destructive' });
    } finally {
      setDeployingId(null);
    }
  };

  const query = search.trim().toLowerCase();
  const filteredApps = apps.filter((app) => {
    const category = normalizeCategory(app);
    const name = String(app?.name || '').toLowerCase();
    const desc = String(app?.description || '').toLowerCase();
    const matchesCat = activeCategory === 'all' || category === activeCategory;
    const matchesSearch = !query || name.includes(query) || desc.includes(query);
    return matchesCat && matchesSearch;
  });

  return (
    <div className="flex-1 flex flex-col">
      {/* Hero / Search */}
      <section className="border-b border-border bg-muted/40 py-12 px-4">
        <div className="container max-w-6xl mx-auto">
          <h1 className="text-3xl md:text-4xl font-extrabold tracking-tight text-foreground mb-3">
            Templates
          </h1>
          <p className="text-muted-foreground mb-4 text-lg max-w-2xl">
            Browse and deploy pre-configured application stacks and services on your infrastructure.
          </p>
          <p className="text-xs text-muted-foreground mb-6 tabular-nums" aria-live="polite">
            {loading ? 'Loading templates...' : `Showing ${filteredApps.length} of ${apps.length} templates`}
          </p>

          <div className="relative max-w-md">
            <label htmlFor="template-search" className="sr-only">
              Search application templates
            </label>
            <Search
              className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
              size={18}
              aria-hidden="true"
            />
            <Input
              id="template-search"
              type="search"
              placeholder="Search templates by name or description..."
              className="pl-10 h-12 text-base bg-background text-foreground"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
      </section>

      {/* Grid */}
      <section className="container max-w-6xl mx-auto px-4 py-10">
        {/* Categories */}
        <div className="flex flex-wrap gap-2 mb-8" role="group" aria-label="Filter templates by category">
          {categories.map((cat) => {
            const active = activeCategory === cat.id;
            return (
              <Button
                key={cat.id}
                type="button"
                variant={active ? 'default' : 'outline'}
                aria-pressed={active}
                onClick={() => setActiveCategory(cat.id)}
                className="rounded-full min-h-[44px] px-4"
              >
                {cat.label}
              </Button>
            );
          })}
        </div>

        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6" role="status" aria-label="Loading templates">
            {[1, 2, 3, 4, 5, 6].map((n) => (
              <div key={n} className="h-52 rounded-xl border border-border bg-card animate-pulse p-6" />
            ))}
          </div>
        ) : loadError ? (
          <div role="alert" className="text-center py-16 px-4 rounded-2xl border border-border bg-card">
            <AlertCircle className="w-8 h-8 text-destructive mx-auto mb-3" aria-hidden="true" />
            <p className="text-foreground font-semibold mb-2">{loadError}</p>
            <Button type="button" variant="outline" onClick={loadTemplates} className="min-h-[44px]">
              <RefreshCw className="w-4 h-4 mr-2" aria-hidden="true" />
              Retry
            </Button>
          </div>
        ) : filteredApps.length === 0 ? (
          <div className="text-center py-16 px-4 rounded-2xl border border-border bg-card">
            <p className="text-muted-foreground mb-4">No templates match your current filters.</p>
            {(search || activeCategory !== 'all') && (
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setSearch('');
                  setActiveCategory('all');
                }}
                className="min-h-[44px]"
              >
                Clear Filters
              </Button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredApps.map((app) => {
              const category = normalizeCategory(app);
              const Icon = getIconForCategory(category);
              const color = getColorForCategory(category);
              const isDeploying = deployingId === String(app.id);
              const logoSrc = app.logo_url || app.icon;
              const categoryLabel =
                categories.find((c) => c.id === category)?.label || category || 'App';

              return (
                <Card key={app.id} className="flex flex-col hover:border-primary/50 transition-colors">
                  <CardHeader className="flex flex-row items-start gap-4 space-y-0">
                    <div
                      className={`p-3 rounded-xl flex items-center justify-center overflow-hidden shrink-0 ${
                        logoSrc ? 'bg-background border border-border' : `text-white ${color}`
                      }`}
                    >
                      {logoSrc ? (
                        <Image
                          src={logoSrc}
                          alt={app.name || 'Template icon'}
                          width={28}
                          height={28}
                          className="w-7 h-7 object-contain"
                          unoptimized
                        />
                      ) : (
                        <Icon size={24} aria-hidden="true" />
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <CardTitle className="text-lg">
                        <Link
                          href={`/new?template=${app.id}`}
                          className="hover:text-primary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
                        >
                          {app.name || 'Unnamed Template'}
                        </Link>
                      </CardTitle>
                      <span className="text-xs text-muted-foreground font-medium uppercase tracking-wider">
                        {categoryLabel}
                      </span>
                    </div>
                  </CardHeader>
                  <CardContent className="flex-1">
                    <CardDescription className="line-clamp-2 min-h-[2.5rem]">
                      {app.description}
                    </CardDescription>
                  </CardContent>
                  <CardFooter className="gap-2">
                    <Button
                      type="button"
                      className="w-full min-h-[44px] bg-primary text-primary-foreground hover:bg-primary/90 font-semibold"
                      disabled={isDeploying}
                      onClick={() => handleOneClickDeploy(app)}
                    >
                      {isDeploying ? 'Deploying...' : '1-Click Deploy'}
                    </Button>
                  </CardFooter>
                </Card>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
