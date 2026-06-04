'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Search, RefreshCcw, Loader2, Terminal, AlertCircle } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';

interface LokiEvent {
    timestamp: string;
    line: string;
    labels: Record<string, string>;
}

const TIME_RANGES = [
    { label: '15m', value: 'now-15m' },
    { label: '1h', value: 'now-1h' },
    { label: '6h', value: 'now-6h' },
    { label: '24h', value: 'now-24h' },
];

const DEFAULT_QUERY = '{compose_service=~".+"}';

export default function LogsPage({
    searchParams,
}: {
    searchParams?: { service?: string; query?: string };
}) {
    const { toast } = useToast();
    const [query, setQuery] = useState(searchParams?.query || DEFAULT_QUERY);
    const [draftQuery, setDraftQuery] = useState(searchParams?.query || DEFAULT_QUERY);
    const [serviceFilter, setServiceFilter] = useState<string | undefined>(searchParams?.service);
    const [events, setEvents] = useState<LokiEvent[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [range, setRange] = useState('now-1h');
    const [limit, setLimit] = useState(200);
    const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

    const effectiveQuery = useMemo(() => {
        if (serviceFilter) {
            return `{compose_service=~"${serviceFilter}.*"}`;
        }
        return query;
    }, [query, serviceFilter]);

    const fetchLogs = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
            const params = new URLSearchParams();
            params.set('query', effectiveQuery);
            params.set('start', range);
            params.set('end', 'now');
            params.set('limit', String(limit));
            const res = await fetch(`/api/v1/observability/loki/query/?${params.toString()}`, {
                headers: token ? { 'Authorization': `Token ${token}` } : {},
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body?.error || `Loki query failed (${res.status})`);
            }
            const data = await res.json();
            setEvents(data.events || []);
            setLastUpdated(new Date());
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load logs');
            setEvents([]);
        } finally {
            setLoading(false);
        }
    }, [effectiveQuery, range, limit]);

    useEffect(() => {
        fetchLogs();
        const interval = setInterval(fetchLogs, 15000);
        return () => clearInterval(interval);
    }, [fetchLogs]);

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setQuery(draftQuery);
    };

    useEffect(() => {
        if (error) {
            toast({ title: 'Loki unavailable', description: error, variant: 'destructive' });
        }
    }, [error, toast]);

    return (
        <div className="container mx-auto py-8 space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Logs</h1>
                <p className="text-sm text-muted-foreground mt-1">
                    Live log search across all platform containers via Loki.
                </p>
            </div>

            <Card>
                <CardContent className="p-4 space-y-3">
                    <form onSubmit={handleSubmit} className="flex flex-col md:flex-row gap-2">
                        <div className="flex-1 flex items-center gap-2 px-3 py-1.5 rounded-md border border-border bg-background">
                            <Search className="w-3.5 h-3.5 text-muted-foreground" />
                            <input
                                value={draftQuery}
                                onChange={(e) => setDraftQuery(e.target.value)}
                                placeholder='{compose_service=~".+"} |= "error"'
                                className="flex-1 bg-transparent outline-none text-sm font-mono"
                            />
                        </div>
                        <button
                            type="submit"
                            className="px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
                        >
                            Run
                        </button>
                    </form>

                    <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                        <div className="flex items-center gap-1">
                            <span>Service:</span>
                            <input
                                value={serviceFilter || ''}
                                onChange={(e) => setServiceFilter(e.target.value || undefined)}
                                placeholder="(all)"
                                className="px-2 py-1 rounded-md border border-border bg-background text-foreground"
                            />
                        </div>
                        <div className="flex items-center gap-1">
                            <span>Range:</span>
                            <select
                                value={range}
                                onChange={(e) => setRange(e.target.value)}
                                className="px-2 py-1 rounded-md border border-border bg-background text-foreground"
                            >
                                {TIME_RANGES.map((r) => (
                                    <option key={r.value} value={r.value}>{r.label}</option>
                                ))}
                            </select>
                        </div>
                        <div className="flex items-center gap-1">
                            <span>Limit:</span>
                            <select
                                value={limit}
                                onChange={(e) => setLimit(Number(e.target.value))}
                                className="px-2 py-1 rounded-md border border-border bg-background text-foreground"
                            >
                                <option value={100}>100</option>
                                <option value={200}>200</option>
                                <option value={500}>500</option>
                            </select>
                        </div>
                        <button
                            onClick={fetchLogs}
                            className="ml-auto inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-border hover:bg-muted"
                        >
                            <RefreshCcw className="w-3 h-3" /> Refresh
                        </button>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-sm flex items-center justify-between">
                        <span className="flex items-center gap-2">
                            <Terminal className="w-4 h-4" />
                            Results
                            {loading && <Loader2 className="w-3 h-3 animate-spin" />}
                        </span>
                        <span className="text-xs text-muted-foreground font-normal">
                            {events.length} events
                            {lastUpdated && ` • updated ${lastUpdated.toLocaleTimeString()}`}
                        </span>
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {error ? (
                        <div className="text-sm text-destructive flex items-center gap-2 py-8 justify-center">
                            <AlertCircle className="w-4 h-4" /> {error}
                        </div>
                    ) : events.length === 0 ? (
                        <div className="text-sm text-muted-foreground py-8 text-center">
                            {loading ? 'Querying Loki…' : 'No log events match the current query.'}
                        </div>
                    ) : (
                        <div className="max-h-[60vh] overflow-y-auto rounded border border-border bg-zinc-950 text-zinc-200 font-mono text-xs">
                            {events.map((event, idx) => (
                                <div
                                    key={`${event.timestamp}-${idx}`}
                                    className="px-3 py-1 border-b border-zinc-900 hover:bg-zinc-900/60"
                                >
                                    <div className="flex items-center gap-3 text-zinc-500">
                                        <span className="shrink-0 w-32 truncate">
                                            {new Date(event.timestamp).toLocaleString()}
                                        </span>
                                        <span className="shrink-0 truncate">
                                            {event.labels?.compose_service || event.labels?.container_name || event.labels?.container?.slice(0, 12) || '—'}
                                        </span>
                                    </div>
                                    <pre className="whitespace-pre-wrap break-all text-zinc-200 mt-0.5">
                                        {event.line}
                                    </pre>
                                </div>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
