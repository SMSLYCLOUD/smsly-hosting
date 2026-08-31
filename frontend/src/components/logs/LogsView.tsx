'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Search, RefreshCcw, Loader2, Terminal, AlertCircle } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import {
    LiveLogViewer,
    LiveLogViewerHandle,
    LogLine,
} from '@/components/logs/LiveLogViewer';

interface LokiEvent {
    timestamp: string;
    line: string;
    labels: Record<string, string>;
}

/** Loki returns timestamps in nanoseconds; JS Date needs milliseconds. */
function lokiTsToMs(ts: string): number {
    const n = Number(ts);
    return Number.isFinite(n) ? (n > 1e12 ? Math.floor(n / 1_000_000) : n) : 0;
}

const TIME_RANGES = [
    { label: '15m', value: 'now-15m' },
    { label: '1h', value: 'now-1h' },
    { label: '6h', value: 'now-6h' },
    { label: '24h', value: 'now-24h' },
    { label: '7d', value: 'now-7d' },
];

const DEFAULT_QUERY = '{compose_service=~".+"}';
const POLL_INTERVAL_MS = 15000;

export function LogsView({
    searchParams,
    embed = false,
}: {
    searchParams?: { service?: string; query?: string };
    embed?: boolean;
}) {
    const { toast } = useToast();
    const [query, setQuery] = useState(searchParams?.query || DEFAULT_QUERY);
    const [draftQuery, setDraftQuery] = useState(searchParams?.query || DEFAULT_QUERY);
    const [serviceFilter, setServiceFilter] = useState<string | undefined>(searchParams?.service);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [range, setRange] = useState('now-24h');
    const [limit, setLimit] = useState(200);
    const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
    const [eventCount, setEventCount] = useState(0);

    const viewerRef = useRef<LiveLogViewerHandle>(null);
    // Monotonic counter for line IDs.
    const seqRef = useRef(0);
    // We track the *last* Loki timestamp we rendered so we can fetch only
    // new lines on subsequent polls. This keeps the append-only buffer
    // stable and the user's scroll position intact.
    const lastTsRef = useRef<number>(0);
    // Stable signature of (query, range, limit) — when this changes we
    // reset the buffer; otherwise we append.
    const filterKeyRef = useRef<string>('');

    // Resolve service UUID (or name) from URL param to compose service name
    // and set the query accordingly so the query box reflects reality.
    useEffect(() => {
        const raw = searchParams?.service;
        if (!raw) {
            setServiceFilter(undefined);
            return;
        }
        const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        const isUuid = uuidRegex.test(raw);

        const applyFilter = (svcName: string, deployMode?: string) => {
            setServiceFilter(svcName);
            const normalizedName = svcName.toLowerCase().replace(/ /g, '-');
            const filteredQuery = deployMode === 'COMPOSE'
                ? `{compose_project="${normalizedName}"}`
                : `{compose_service="${svcName}"}`;
            setQuery(filteredQuery);
            setDraftQuery(filteredQuery);
        };

        if (isUuid) {
            fetch(`/api/v1/services/${encodeURIComponent(raw)}/`, {
                credentials: "include",
            })
                .then((r) => (r.ok ? r.json() : null))
                .then((svc) => {
                    if (svc?.name) applyFilter(svc.name, svc.deploy_mode);
                })
                .catch(() => {});
        } else {
            applyFilter(raw);
        }
    }, [searchParams?.service]);

    const fetchLogs = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const filterKey = `${query}|${range}|${limit}`;
            const isFreshFetch = filterKeyRef.current !== filterKey;
            filterKeyRef.current = filterKey;

            const params = new URLSearchParams();
            params.set('query', query);
            params.set('start', range);
            params.set('end', 'now');
            params.set('limit', String(limit));
            const res = await fetch(`/api/v1/observability/loki/query/?${params.toString()}`, {
                credentials: "include",
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body?.error || `Loki query failed (${res.status})`);
            }
            const data = await res.json();
            const events: LokiEvent[] = data.events || [];
            setLastUpdated(new Date());

            if (isFreshFetch) {
                // Hard reset: replace buffer with the full set, sorted
                // ascending (oldest first) so the viewer renders top-down.
                const sorted = events
                    .slice()
                    .sort((a, b) => lokiTsToMs(a.timestamp) - lokiTsToMs(b.timestamp));
                const newLines: LogLine[] = sorted.map((e) => {
                    seqRef.current += 1;
                    return {
                        id: `loki-${lokiTsToMs(e.timestamp)}-${seqRef.current}`,
                        time: new Date(lokiTsToMs(e.timestamp)).toLocaleTimeString('en-US', { hour12: false }),
                        source: e.labels?.compose_service
                            || e.labels?.container_name
                            || e.labels?.container?.slice(0, 12)
                            || undefined,
                        text: e.line,
                    };
                });
                lastTsRef.current = newLines.length
                    ? lokiTsToMs(events[events.length - 1]?.timestamp || '0')
                    : 0;
                viewerRef.current?.clear();
                viewerRef.current?.append(newLines);
                setEventCount(newLines.length);
            } else {
                // Append only the lines newer than what we last rendered.
                const newOnes = events
                    .filter((e) => lokiTsToMs(e.timestamp) > lastTsRef.current)
                    .sort((a, b) => lokiTsToMs(a.timestamp) - lokiTsToMs(b.timestamp));
                if (newOnes.length) {
                    const lines: LogLine[] = newOnes.map((e) => {
                        seqRef.current += 1;
                        return {
                            id: `loki-${lokiTsToMs(e.timestamp)}-${seqRef.current}`,
                            time: new Date(lokiTsToMs(e.timestamp)).toLocaleTimeString('en-US', { hour12: false }),
                            source: e.labels?.compose_service
                                || e.labels?.container_name
                                || e.labels?.container?.slice(0, 12)
                                || undefined,
                            text: e.line,
                        };
                    });
                    lastTsRef.current = lokiTsToMs(newOnes[newOnes.length - 1].timestamp);
                    viewerRef.current?.append(lines);
                    setEventCount((c) => c + lines.length);
                }
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load logs');
        } finally {
            setLoading(false);
        }
    }, [query, range, limit]);

    useEffect(() => {
        fetchLogs();
        const interval = setInterval(fetchLogs, POLL_INTERVAL_MS);
        return () => clearInterval(interval);
    }, [fetchLogs]);

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setQuery(draftQuery);
    };

    const applyServiceFilter = useCallback((svcName: string) => {
        if (!svcName || !svcName.trim()) {
            setDraftQuery(DEFAULT_QUERY);
            setQuery(DEFAULT_QUERY);
            setServiceFilter(undefined);
            return;
        }
        const normalized = svcName.trim().toLowerCase().replace(/ /g, '-');
        const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
        if (uuidRegex.test(svcName.trim())) {
            fetch(`/api/v1/services/${encodeURIComponent(svcName.trim())}/`, {
                credentials: "include",
            })
                .then((r) => (r.ok ? r.json() : null))
                .then((svc) => {
                    if (svc) {
                        const svcNormalized = svc.name.toLowerCase().replace(/ /g, '-');
                        const q = svc.deploy_mode === 'COMPOSE'
                            ? `{compose_project="${svcNormalized}"}`
                            : `{compose_service="${svc.name}"}`;
                        setDraftQuery(q);
                        setQuery(q);
                        setServiceFilter(svc.name);
                    }
                })
                .catch(() => {});
            return;
        }
        const filteredQuery = `{compose_service="${normalized}"}`;
        setDraftQuery(filteredQuery);
        setQuery(filteredQuery);
    }, []);

    useEffect(() => {
        if (error) {
            toast({ title: 'Loki unavailable', description: error, variant: 'destructive' });
        }
    }, [error, toast]);

    const content = (
        <div className="space-y-6">
            {!embed && (
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Logs</h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        Live log search across all platform containers via Loki.
                    </p>
                </div>
            )}

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
                                onBlur={(e) => { if (e.target.value) applyServiceFilter(e.target.value); }}
                                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); applyServiceFilter(e.currentTarget.value); } }}
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
                            {eventCount} events
                            {lastUpdated && ` • updated ${lastUpdated.toLocaleTimeString()}`}
                        </span>
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {error ? (
                        <div className="text-sm text-destructive flex items-center gap-2 py-8 justify-center">
                            <AlertCircle className="w-4 h-4" /> {error}
                        </div>
                    ) : (
                        <LiveLogViewer
                            ref={viewerRef}
                            heightClass="h-[60vh]"
                            emptyMessage={
                                loading
                                    ? 'Querying Loki…'
                                    : 'No log events match the current query.'
                            }
                            shortcutsHint="Space: pause · End: jump live · c: clear"
                            initialLines={[]}
                        />
                    )}
                </CardContent>
            </Card>
        </div>
    );

    if (embed) return content;
    return <div className="container mx-auto py-8 space-y-6">{content}</div>;
}
