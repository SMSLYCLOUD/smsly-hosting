'use client';

import { useEffect, useState, useCallback } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useToast } from '@/components/ui/use-toast';
import { Database, RefreshCw, Loader2, ExternalLink } from 'lucide-react';
import { databaseReplicasApi, DatabaseReplica } from '@/lib/api';
import { AddReplicaCard, ReplicaRow } from '@/components/settings/DatabaseReplicasTab';

// ─── Page ────────────────────────────────────────────────────────────────────

export default function ReplicationSettingsPage() {
    const { toast } = useToast();
    const [replicas, setReplicas] = useState<DatabaseReplica[]>([]);
    const [endpoints, setEndpoints] = useState<string>('');
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [list, eps] = await Promise.all([
                databaseReplicasApi.list(),
                databaseReplicasApi.endpoints(),
            ]);
            setReplicas(list);
            setEndpoints(eps.endpoints);
        } catch (err: any) {
            toast({ title: 'Failed to load replicas', description: err?.message, variant: 'destructive' });
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => { load(); }, [load]);

    const sync = async () => {
        setSyncing(true);
        try {
            const result = await databaseReplicasApi.sync();
            if (result.error) {
                toast({ title: 'Sync failed', description: result.error, variant: 'destructive' });
            } else {
                toast({
                    title: 'Pgcat config synced',
                    description: `Config written, ${result.replica_count} replica(s), ${result.reloaded ? 'reloaded' : 'reload pending'}.`,
                });
            }
        } catch (err: any) {
            toast({ title: 'Sync failed', description: err?.message, variant: 'destructive' });
        } finally {
            setSyncing(false);
        }
    };

    return (
        <DashboardShell>
            <div className="space-y-6 p-4 md:p-6 max-w-5xl">
                <header>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <Database className="h-6 w-6" />
                        Database Replicas
                    </h1>
                    <p className="text-muted-foreground text-sm mt-1">
                        Read-replica endpoints that pgcat routes SELECTs to. Writes always go to the primary.
                    </p>
                </header>

                <Card>
                    <CardHeader>
                        <CardTitle className="text-lg">Current pgcat endpoints</CardTitle>
                        <CardDescription>
                            The <code>DB_REPLICA_HOSTS</code> value rendered into pgcat.toml. After adding or
                            removing replicas, click <strong>Sync pgcat</strong> to push the new config and reload
                            the container.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="flex items-center gap-2 flex-wrap">
                            <code className="bg-muted px-2 py-1 rounded text-sm font-mono break-all">
                                {endpoints || '(no replicas configured)'}
                            </code>
                            <Button size="sm" onClick={sync} disabled={syncing}>
                                {syncing
                                    ? <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                                    : <RefreshCw className="h-3 w-3 mr-1" />}
                                Sync pgcat
                            </Button>
                        </div>
                    </CardContent>
                </Card>

                <AddReplicaCard
                    onAdded={(r) => setReplicas((cur) => [...cur, r])}
                />

                <section className="space-y-3">
                    {loading ? (
                        <div className="flex justify-center py-8 text-muted-foreground">
                            <Loader2 className="h-5 w-5 animate-spin mr-2" />Loading…
                        </div>
                    ) : replicas.length === 0 ? (
                        <Card>
                            <CardContent className="py-12 text-center text-muted-foreground">
                                <Database className="h-8 w-8 mx-auto mb-2 opacity-40" />
                                No replicas configured yet.
                                <p className="text-sm mt-1">
                                    You can also enable a local docker replica via{' '}
                                    <code className="text-xs">sudo bash install.sh --with-replica</code>.
                                </p>
                            </CardContent>
                        </Card>
                    ) : (
                        replicas.map((r) => (
                            <ReplicaRow
                                key={r.id}
                                replica={r}
                                onUpdated={(updated) =>
                                    setReplicas((cur) => cur.map((x) => (x.id === updated.id ? updated : x)))
                                }
                                onDeleted={(id) => setReplicas((cur) => cur.filter((x) => x.id !== id))}
                            />
                        ))
                    )}
                </section>

                <footer className="text-xs text-muted-foreground border-t pt-4">
                    <p>
                        <strong>Tip:</strong> pgcat uses read/write query splitting. SELECTs are routed to replicas
                        and writes always go to the primary. If a replica becomes unreachable, queries fall back to
                        the primary automatically (primary_reads_enabled=true).
                    </p>
                    <p className="mt-1">
                        <a
                            href="https://github.com/SMSLYCLOUD/smsly-hosting/blob/main/infrastructure/pgcat/README.md"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:underline inline-flex items-center gap-1"
                        >
                            pgcat documentation <ExternalLink className="h-3 w-3" />
                        </a>
                    </p>
                </footer>
            </div>
        </DashboardShell>
    );
}
