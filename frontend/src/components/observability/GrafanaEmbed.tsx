'use client';

import { useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useToast } from '@/components/ui/use-toast';
import { ExternalLink, Loader2, RefreshCcw, AlertCircle } from 'lucide-react';

interface GrafanaEmbedProps {
    dashboard: string;
    service?: string;
    time?: string;
}

interface EmbedResponse {
    url: string;
    dashboard: { uid: string; title: string };
}

export function GrafanaEmbed({ dashboard, service, time }: GrafanaEmbedProps) {
    const { toast } = useToast();
    const [data, setData] = useState<EmbedResponse | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [resolvedService, setResolvedService] = useState<string | undefined>(undefined);

    // Resolve service UUID to service name for Grafana template variable
    useEffect(() => {
        if (!service) {
            setResolvedService(undefined);
            return;
        }
        const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        if (!uuidRegex.test(service)) {
            setResolvedService(service);
            return;
        }
        fetch(`/api/v1/services/${encodeURIComponent(service)}/`, {
            credentials: "include",
        })
            .then((r) => (r.ok ? r.json() : null))
            .then((svc) => {
                if (svc?.name) setResolvedService(svc.name);
            })
            .catch(() => {});
    }, [service]);

    const params = useMemo(() => {
        const p = new URLSearchParams();
        p.set('time', time || 'now-24h');
        const svc = resolvedService || service;
        if (svc) p.set('var-service', svc);
        return p.toString();
    }, [time, service, resolvedService]);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(null);

        fetch(`/api/v1/observability/grafana/embed/${encodeURIComponent(dashboard)}/?${params}`, {
            credentials: "include",
        })
            .then(async (res) => {
                if (!res.ok) {
                    const body = await res.json().catch(() => ({}));
                    throw new Error(body?.error || `Grafana embed failed (${res.status})`);
                }
                return res.json();
            })
            .then((json: EmbedResponse) => {
                if (cancelled) return;
                setData(json);
            })
            .catch((err) => {
                if (cancelled) return;
                setError(err.message || 'Failed to load Grafana embed');
            })
            .finally(() => {
                if (cancelled) return;
                setLoading(false);
            });

        return () => {
            cancelled = true;
        };
    }, [dashboard, params]);

    useEffect(() => {
        if (error) {
            toast({ title: 'Grafana unavailable', description: error, variant: 'destructive' });
        }
    }, [error, toast]);

    if (loading) {
        return (
            <div className="p-12 text-center text-muted-foreground flex flex-col items-center gap-3">
                <Loader2 className="w-6 h-6 animate-spin" />
                Loading Grafana…
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="p-8 text-center text-destructive flex flex-col items-center gap-3">
                <AlertCircle className="w-6 h-6" />
                <p>{error || 'Failed to load Grafana embed.'}</p>
                <button
                    onClick={() => window.location.reload()}
                    className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-md border border-border hover:bg-muted"
                >
                    <RefreshCcw className="w-3 h-3" /> Retry
                </button>
            </div>
        );
    }

    return (
        <div className="flex flex-col">
            <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-muted/30">
                <div>
                    <p className="text-sm font-medium">{data.dashboard.title}</p>
                    <p className="text-xs text-muted-foreground">UID: {data.dashboard.uid}</p>
                </div>
                <a
                    href={data.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                >
                    Open in Grafana <ExternalLink className="w-3 h-3" />
                </a>
            </div>
            <iframe
                title={`Grafana dashboard ${data.dashboard.uid}`}
                src={data.url}
                className="w-full h-[calc(100vh-12rem)] border-0 bg-background"
                allow="fullscreen"
            />
        </div>
    );
}
