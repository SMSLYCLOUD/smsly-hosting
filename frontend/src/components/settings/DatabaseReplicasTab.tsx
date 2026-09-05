'use client';

import { useEffect, useState, useCallback, memo } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import {
    Dialog, DialogContent, DialogDescription, DialogFooter,
    DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { useToast } from '@/components/ui/use-toast';
import {
    Database, RefreshCw, Loader2, Trash2, Plus, Power, CheckCircle2,
    AlertCircle, XCircle, Server, Network, Save, Lock, ExternalLink,
} from 'lucide-react';
import {
    default as api,
    databaseReplicasApi,
    DatabaseReplica,
    DatabaseReplicaKind,
    DatabaseReplicaSslMode,
    DatabaseReplicaStatus,
    systemApi,
} from '@/lib/api';

// ─── Status badge ────────────────────────────────────────────────────────────

export const StatusBadge = memo(function StatusBadge({ status, lastError }: { status: DatabaseReplicaStatus; lastError?: string }) {
    const map: Record<DatabaseReplicaStatus, { color: string; icon: React.ReactNode; label: string }> = {
        ok:     { color: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', icon: <CheckCircle2 className="h-3 w-3" />, label: 'Healthy' },
        warn:   { color: 'bg-amber-500/15 text-amber-400 border-amber-500/30',     icon: <AlertCircle className="h-3 w-3" />,  label: 'Lag' },
        error:  { color: 'bg-red-500/15 text-red-400 border-red-500/30',           icon: <XCircle className="h-3 w-3" />,      label: 'Error' },
        unknown:{ color: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',         icon: <RefreshCw className="h-3 w-3" />,    label: 'Unknown' },
    };
    const m = map[status];
    return (
        <span
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${m.color}`}
            title={lastError || undefined}
        >
            {m.icon}{m.label}
        </span>
    );
});

// ─── Add-replica form ────────────────────────────────────────────────────────

export function AddReplicaCard({ onAdded, defaultOpen = false }: { onAdded: (r: DatabaseReplica) => void; defaultOpen?: boolean }) {
    const { toast } = useToast();
    const [open, setOpen] = useState(defaultOpen);
    const [submitting, setSubmitting] = useState(false);
    const [urlValue, setUrlValue] = useState('');
    const [form, setForm] = useState({
        name: '',
        kind: 'remote' as DatabaseReplicaKind,
        host: '',
        port: 5432,
        database: 'smsly_hosting',
        username: '',
        password: '',
        ssl_mode: 'prefer' as DatabaseReplicaSslMode,
        notes: '',
    });

    const set = (k: keyof typeof form, v: string | number) =>
        setForm((f) => ({ ...f, [k]: v }));

    const applyUrl = () => {
        const raw = urlValue.trim();
        if (!raw) return;
        let u: URL;
        try {
            u = new URL(raw);
        } catch {
            toast({
                title: 'Invalid connection URL',
                description: 'Expected format: postgresql://user:password@host:5432/dbname',
                variant: 'destructive',
            });
            return;
        }
        if (!/^postgres(ql)?:$/.test(u.protocol)) {
            toast({
                title: 'Unsupported scheme',
                description: `"${u.protocol.replace(/:$/, '')}" is not a PostgreSQL URL — expected postgres:// or postgresql://`,
                variant: 'destructive',
            });
            return;
        }
        if (!u.hostname) {
            toast({ title: 'Missing host', description: 'The URL must include a host.', variant: 'destructive' });
            return;
        }
        let username = u.username;
        let password = u.password;
        try {
            if (username) username = decodeURIComponent(username);
            if (password) password = decodeURIComponent(password);
        } catch { /* keep raw values if malformed escapes */ }
        const portNum = u.port ? Number(u.port) : NaN;
        const dbname = u.pathname.replace(/^\//, '');
        const validSsl: string[] = ['disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'];
        const sslParam = u.searchParams.get('sslmode');
        const resolvedPort = Number.isFinite(portNum) && portNum >= 1 && portNum <= 65535 ? portNum : form.port;
        setForm((f) => ({
            ...f,
            host: u.hostname,
            port: resolvedPort,
            database: dbname || f.database,
            username,
            password,
            ssl_mode: sslParam && validSsl.includes(sslParam) ? (sslParam as DatabaseReplicaSslMode) : f.ssl_mode,
        }));
        toast({
            title: 'Connection details applied',
            description: `${username || '(no user)'}@${u.hostname}:${resolvedPort}/${dbname || form.database}`,
        });
    };

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            const created = await databaseReplicasApi.create({
                name: form.name.trim(),
                kind: form.kind,
                host: form.host.trim(),
                port: Number(form.port) || 5432,
                database: form.database.trim() || 'smsly_hosting',
                username: form.username.trim(),
                password: form.password,
                ssl_mode: form.ssl_mode,
                notes: form.notes,
            });
            toast({ title: 'Replica added', description: `${created.name} (${created.host}:${created.port})` });
            onAdded(created);
            setOpen(false);
            setForm({ name: '', kind: 'remote', host: '', port: 5432, database: 'smsly_hosting', username: '', password: '', ssl_mode: 'prefer', notes: '' });
            setUrlValue('');
        } catch (err: any) {
            const detail = err?.response?.data || err?.message || 'Unknown error';
            toast({ title: 'Failed to add replica', description: JSON.stringify(detail), variant: 'destructive' });
        } finally {
            setSubmitting(false);
        }
    };

    if (!open) {
        return (
            <Button onClick={() => setOpen(true)} className="w-full" variant="outline">
                <Plus className="h-4 w-4 mr-2" />Add a database replica
            </Button>
        );
    }

    return (
        <Card>
            <CardHeader>
                <CardTitle className="text-lg">Add a database replica</CardTitle>
                <CardDescription>
                    Pgcat will route SELECTs to active replicas. The password is encrypted at rest and never returned
                    in API responses; rotate it via PATCH.
                </CardDescription>
            </CardHeader>
            <CardContent>
                <form onSubmit={submit} className="space-y-4">
                    <div className="space-y-2 rounded-md border border-dashed p-3">
                        <Label htmlFor="rurl">Quick fill — connection string</Label>
                        <div className="flex gap-2">
                            <Input
                                id="rurl"
                                placeholder="postgresql://user:password@host:5432/dbname"
                                value={urlValue}
                                onChange={(e) => setUrlValue(e.target.value)}
                                className="font-mono text-xs"
                                autoComplete="off"
                                spellCheck={false}
                            />
                            <Button type="button" variant="secondary" onClick={applyUrl} disabled={!urlValue.trim()}>
                                Parse
                            </Button>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Paste a full postgres:// URL and hit Parse to fill the fields below. SSL mode is
                            picked up from ?sslmode= when present. The URL is never stored.
                        </p>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label htmlFor="rname">Name</Label>
                            <Input id="rname" required placeholder="europe-rds" value={form.name} onChange={(e) => set('name', e.target.value)} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="rkind">Kind</Label>
                            <select id="rkind" className="w-full rounded border border-input bg-background px-3 py-1.5 text-sm" value={form.kind} onChange={(e) => set('kind', e.target.value as DatabaseReplicaKind)}>
                                <option value="remote">Remote (managed DB or separate host)</option>
                                <option value="local">Local (docker container on this host)</option>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="rhost">Host</Label>
                            <Input id="rhost" required placeholder="replica.example.com" value={form.host} onChange={(e) => set('host', e.target.value)} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="rport">Port</Label>
                            <Input id="rport" type="number" min={1} max={65535} value={form.port} onChange={(e) => set('port', e.target.value)} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="rdb">Database</Label>
                            <Input id="rdb" value={form.database} onChange={(e) => set('database', e.target.value)} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="ruser">Username</Label>
                            <Input id="ruser" required placeholder="smsly_replica" value={form.username} onChange={(e) => set('username', e.target.value)} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="rpw">Password</Label>
                            <Input id="rpw" type="password" required placeholder="••••••••" value={form.password} onChange={(e) => set('password', e.target.value)} />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="rssl">SSL mode</Label>
                            <select id="rssl" className="w-full rounded border border-input bg-background px-3 py-1.5 text-sm" value={form.ssl_mode} onChange={(e) => set('ssl_mode', e.target.value as DatabaseReplicaSslMode)}>
                                <option value="disable">disable (LAN only)</option>
                                <option value="allow">allow (prefer TLS, fall back)</option>
                                <option value="prefer">prefer (try TLS first)</option>
                                <option value="require">require (TLS, no cert check)</option>
                                <option value="verify-ca">verify-ca (TLS + CA)</option>
                                <option value="verify-full">verify-full (TLS + CA + hostname)</option>
                            </select>
                        </div>
                    </div>
                    <div className="space-y-2">
                        <Label htmlFor="rnotes">Notes (optional)</Label>
                        <Input id="rnotes" placeholder="AWS RDS eu-west-1, paid tier..." value={form.notes} onChange={(e) => set('notes', e.target.value)} />
                    </div>
                    <div className="flex gap-2 pt-2">
                        <Button type="submit" disabled={submitting}>
                            {submitting ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Save className="h-4 w-4 mr-2" />}
                            Save replica
                        </Button>
                        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
                    </div>
                </form>
            </CardContent>
        </Card>
    );
}

// ─── Replica row ─────────────────────────────────────────────────────────────

export function ReplicaRow({
    replica,
    onUpdated,
    onDeleted,
}: {
    replica: DatabaseReplica;
    onUpdated: (r: DatabaseReplica) => void;
    onDeleted: (id: string) => void;
}) {
    const { toast } = useToast();
    const [busy, setBusy] = useState<'test' | 'delete' | 'toggle' | null>(null);
    const [editingPassword, setEditingPassword] = useState(false);
    const [newPassword, setNewPassword] = useState('');

    const test = async () => {
        setBusy('test');
        try {
            const result = await databaseReplicasApi.test(replica.id);
            toast({
                title: result.ok ? 'Connection OK' : 'Connection failed',
                description: result.ok
                    ? `TCP reachable at ${result.endpoint}`
                    : result.error.slice(0, 200),
                variant: result.ok ? 'default' : 'destructive',
            });
        } catch (err: any) {
            toast({ title: 'Test failed', description: err?.message, variant: 'destructive' });
        } finally {
            setBusy(null);
        }
    };

    const remove = async () => {
        if (!confirm(`Remove replica "${replica.name}"?`)) return;
        setBusy('delete');
        try {
            await databaseReplicasApi.remove(replica.id);
            toast({ title: 'Replica removed', description: replica.name });
            onDeleted(replica.id);
        } catch (err: any) {
            toast({ title: 'Remove failed', description: err?.message, variant: 'destructive' });
        } finally {
            setBusy(null);
        }
    };

    const toggle = async () => {
        setBusy('toggle');
        try {
            const updated = await databaseReplicasApi.update(replica.id, { is_active: !replica.is_active });
            toast({ title: updated.is_active ? 'Replica enabled' : 'Replica disabled', description: updated.name });
            onUpdated(updated);
        } catch (err: any) {
            toast({ title: 'Toggle failed', description: err?.message, variant: 'destructive' });
        } finally {
            setBusy(null);
        }
    };

    const rotatePassword = async () => {
        if (!newPassword) return;
        try {
            await databaseReplicasApi.update(replica.id, { password: newPassword });
            toast({ title: 'Password rotated', description: replica.name });
            setNewPassword('');
            setEditingPassword(false);
        } catch (err: any) {
            toast({ title: 'Rotation failed', description: err?.message, variant: 'destructive' });
        }
    };

    return (
        <Card>
            <CardContent className="p-4">
                <div className="flex items-start justify-between gap-4 flex-wrap">
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                            {replica.kind === 'local'
                                ? <Server className="h-4 w-4 text-blue-400" />
                                : <Network className="h-4 w-4 text-purple-400" />}
                            <h3 className="font-semibold">{replica.name}</h3>
                            <StatusBadge status={replica.last_status} lastError={replica.last_error} />
                            {!replica.is_active && (
                                <span className="text-xs px-2 py-0.5 rounded border border-zinc-500/30 bg-zinc-500/10 text-zinc-400">
                                    Disabled
                                </span>
                            )}
                        </div>
                        <div className="text-sm text-muted-foreground mt-1 font-mono break-all">
                            {replica.username}@{replica.host}:{replica.port}/{replica.database}
                        </div>
                        <div className="text-xs text-muted-foreground mt-1 flex flex-wrap gap-x-3 gap-y-1">
                            <span>SSL: <code className="bg-muted px-1 rounded">{replica.ssl_mode}</code></span>
                            {replica.lag_seconds != null && (
                                <span>Lag: <code className="bg-muted px-1 rounded">{replica.lag_seconds.toFixed(1)}s</code></span>
                            )}
                            {replica.last_checked_at && (
                                <span>Checked: {new Date(replica.last_checked_at).toLocaleString()}</span>
                            )}
                        </div>
                        {replica.notes && (
                            <p className="text-xs text-muted-foreground mt-1 italic">{replica.notes}</p>
                        )}
                    </div>
                    <div className="flex items-center gap-2 flex-wrap">
                        <Button size="sm" variant="outline" onClick={test} disabled={busy !== null}>
                            {busy === 'test' ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
                            <span className="ml-1 hidden sm:inline">Test</span>
                        </Button>
                        <Button size="sm" variant="outline" onClick={toggle} disabled={busy !== null}>
                            {busy === 'toggle' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Power className="h-3 w-3" />}
                            <span className="ml-1 hidden sm:inline">{replica.is_active ? 'Disable' : 'Enable'}</span>
                        </Button>
                        <Button size="sm" variant="ghost" onClick={remove} disabled={busy !== null}>
                            {busy === 'delete' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3 text-red-400" />}
                        </Button>
                    </div>
                </div>
                {editingPassword && (
                    <div className="mt-3 flex gap-2 items-end">
                        <div className="flex-1">
                            <Label htmlFor={`pw-${replica.id}`} className="text-xs">New password</Label>
                            <Input id={`pw-${replica.id}`} type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
                        </div>
                        <Button size="sm" onClick={rotatePassword} disabled={!newPassword}>
                            <Lock className="h-3 w-3 mr-1" />Rotate
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => { setEditingPassword(false); setNewPassword(''); }}>
                            Cancel
                        </Button>
                    </div>
                )}
                {!editingPassword && (
                    <div className="mt-2">
                        <Button size="sm" variant="link" className="text-xs p-0 h-auto" onClick={() => setEditingPassword(true)}>
                            <Lock className="h-3 w-3 mr-1" />Rotate password
                        </Button>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}

// ─── Tab component ───────────────────────────────────────────────────────────

export function DatabaseReplicasTab() {
    const { toast } = useToast();
    const [replicas, setReplicas] = useState<DatabaseReplica[]>([]);
    const [endpoints, setEndpoints] = useState<string>('');
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [dbHaEnabled, setDbHaEnabled] = useState(true);
    const [localHealth, setLocalHealth] = useState<{
        primary: { name: string; status: string } | null;
        local_replicas: {
            name: string;
            host: string;
            port: number;
            status: string;
            lag_seconds: number | null;
        }[];
    } | null>(null);
    const [haLoading, setHaLoading] = useState(false);
    const [confirmOpen, setConfirmOpen] = useState(false);
    const [pendingHaValue, setPendingHaValue] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [list, eps, local] = await Promise.all([
                databaseReplicasApi.list(),
                databaseReplicasApi.endpoints(),
                api.get('/replication/local-health/').catch(() => ({ data: null })),
            ]);
            setReplicas(list);
            setEndpoints(eps.endpoints);
            setLocalHealth(local.data);
        } catch (err: any) {
            toast({ title: 'Failed to load replicas', description: err?.message, variant: 'destructive' });
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => {
        load();
        systemApi.getConfig().then((config: any) => {
            setDbHaEnabled(config.DB_HA_ENABLED ?? true);
        }).catch(() => {});
    }, [load]);

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

    const handleHaToggle = (checked: boolean) => {
        if (!checked) {
            setPendingHaValue(checked);
            setConfirmOpen(true);
        } else {
            doHaToggle(checked);
        }
    };

    const doHaToggle = async (enabled: boolean) => {
        setHaLoading(true);
        try {
            const result = await systemApi.toggleDbHa(enabled);
            setDbHaEnabled(enabled);
            toast({ title: 'PostgreSQL HA Updated', description: result.message });
        } catch (err: any) {
            toast({ title: 'Error', description: err?.response?.data?.error || 'Failed to toggle HA', variant: 'destructive' });
        } finally {
            setHaLoading(false);
            setConfirmOpen(false);
        }
    };

    return (
        <div className="space-y-6">
            {/* PostgreSQL HA Toggle */}
            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Database className="h-5 w-5 text-purple-500" />
                        PostgreSQL High Availability
                    </CardTitle>
                    <CardDescription>
                        Enable a local read replica for high availability and read scaling.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center justify-between">
                        <div>
                            <p className="text-sm font-medium">
                                {dbHaEnabled ? 'Replica Active' : 'Replica Disabled'}
                            </p>
                            <p className="text-xs text-muted-foreground">
                                {dbHaEnabled
                                    ? 'Read queries are routed to the replica. Writes go to primary.'
                                    : 'All queries route to the primary. No read scaling.'}
                            </p>
                        </div>
                        <Switch
                            checked={dbHaEnabled}
                            onCheckedChange={handleHaToggle}
                            disabled={haLoading}
                        />
                    </div>
                </CardContent>
            </Card>

            <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Disable PostgreSQL HA?</DialogTitle>
                        <DialogDescription>
                            This will stop the postgres-replica container and reconfigure pgcat.
                            All database queries will route to the primary.
                            The replica data is preserved and can be restored by re-enabling HA.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setConfirmOpen(false)}>Cancel</Button>
                        <Button variant="destructive" onClick={() => doHaToggle(pendingHaValue)}>
                            Disable HA
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
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

            <div className="space-y-3">
                {localHealth?.local_replicas?.map((replica) => (
                    <Card key={`local-${replica.host}-${replica.port}`}>
                        <CardContent className="p-4">
                            <div className="flex items-start justify-between gap-4 flex-wrap">
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <Server className="h-4 w-4 text-blue-400" />
                                        <h3 className="font-semibold">{replica.name}</h3>
                                        <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs ${
                                            replica.status === 'OK'
                                                ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                                                : 'bg-red-500/15 text-red-400 border-red-500/30'
                                        }`}>
                                            {replica.status === 'OK' ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
                                            {replica.status === 'OK' ? 'Healthy' : replica.status}
                                        </span>
                                        <span className="text-xs px-2 py-0.5 rounded border border-blue-500/30 bg-blue-500/10 text-blue-400">
                                            Local HA
                                        </span>
                                    </div>
                                    <div className="text-sm text-muted-foreground mt-1 font-mono break-all">
                                        {replica.host}:{replica.port}
                                    </div>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        Docker-managed read replica. Controlled by the PostgreSQL HA toggle,
                                        not editable as an external database endpoint.
                                    </p>
                                </div>
                                {replica.lag_seconds != null && (
                                    <div className="text-right">
                                        <p className="text-xs text-muted-foreground">Replication Lag</p>
                                        <p className="font-bold text-sm text-emerald-500">{replica.lag_seconds.toFixed(2)}s</p>
                                    </div>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                ))}
                {loading ? (
                    <div className="flex justify-center py-8 text-muted-foreground">
                        <Loader2 className="h-5 w-5 animate-spin mr-2" />Loading…
                    </div>
                ) : replicas.length === 0 && !localHealth?.local_replicas?.length ? (
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
            </div>

            <div className="text-xs text-muted-foreground border-t pt-4">
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
            </div>
        </div>
    );
}
