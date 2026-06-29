'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Loader2, RotateCcw, Clock, CheckCircle, AlertCircle, RefreshCw, Server, Archive, Key } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import api from '@/lib/api';
import Link from 'next/link';

interface BackupEntry {
    id: string;
    service: {
        id: string;
        name: string;
    };
    status: string;
    backup_type: string;
    created_at: string;
    size_bytes: number;
}

interface ServiceWithBackups {
    serviceId: string;
    serviceName: string;
    latestBackup: BackupEntry | null;
    totalBackups: number;
}

export default function RestorePage() {
    const { toast } = useToast();
    const confirm = useConfirm();
    const [loading, setLoading] = useState(true);
    const [serviceBackups, setServiceBackups] = useState<ServiceWithBackups[]>([]);
    const [restoringId, setRestoringId] = useState<string | null>(null);
    const [restoringName, setRestoringName] = useState<string | null>(null);
    const [restorePollInterval, setRestorePollInterval] = useState<number | null>(null);
    const [error, setError] = useState<string | null>(null);

    // Encryption key prompt modal state
    const [keyPromptOpen, setKeyPromptOpen] = useState(false);
    const [keyPromptValue, setKeyPromptValue] = useState('');
    const [keyPromptBackupId, setKeyPromptBackupId] = useState<string | null>(null);
    const [keyPromptServiceName, setKeyPromptServiceName] = useState('');
    const [keyPromptError, setKeyPromptError] = useState('');
    const [keyPromptSubmitting, setKeyPromptSubmitting] = useState(false);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const [backupsRes, servicesRes] = await Promise.all([
                api.get('/backups/'),
                api.get('/services/'),
            ]);

            const backups: BackupEntry[] = Array.isArray(backupsRes.data)
                ? backupsRes.data
                : backupsRes.data.results || [];

            const services: any[] = Array.isArray(servicesRes.data)
                ? servicesRes.data
                : servicesRes.data.results || [];

            const completedBackups = backups.filter(b => b.status === 'COMPLETED');

            const backupMap = new Map<string, BackupEntry[]>();
            for (const b of completedBackups) {
                const serviceId = b.service?.id;
                if (!serviceId) continue;
                if (!backupMap.has(serviceId)) backupMap.set(serviceId, []);
                backupMap.get(serviceId)!.push(b);
            }

            const result: ServiceWithBackups[] = services.map(s => {
                const svcBackups = backupMap.get(s.id) || [];
                const sorted = svcBackups.sort((a, b) =>
                    new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
                );
                return {
                    serviceId: s.id,
                    serviceName: s.name,
                    latestBackup: sorted[0] || null,
                    totalBackups: sorted.length,
                };
            });

            const withBackups = result.filter(s => s.latestBackup);
            const withoutBackups = result.filter(s => !s.latestBackup);

            setServiceBackups([...withBackups, ...withoutBackups]);
        } catch (err) {
            console.error('Failed to load restore data:', err);
            setError('Failed to load service backup information.');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        loadData();
    }, [loadData]);

    // Poll backup status while restore is in progress
    useEffect(() => {
        if (!restorePollInterval) return;
        const intervalId = window.setInterval(async () => {
            try {
                const res = await api.get(`/backups/${restoringId}/`);
                const status = res.data?.status;
                if (status === 'COMPLETED' || status === 'FAILED') {
                    setRestoringId(null);
                    setRestoringName(null);
                    setRestorePollInterval(null);
                    if (status === 'COMPLETED') {
                        toast({ title: 'Restore Complete', description: `"${restoringName}" has been restored.` });
                    }
                    loadData();
                }
            } catch {
                // Ignore polling errors — the backup might still be processing
            }
        }, 5000);
        return () => window.clearInterval(intervalId);
    }, [restorePollInterval, restoringId, restoringName, toast, loadData]);

    const doRestore = async (backupId: string, serviceName: string, encryptionKey?: string) => {
        setRestoringId(backupId);
        setRestoringName(serviceName);
        try {
            await api.post(`/backups/${backupId}/restore/`, {
                confirm: true,
                ...(encryptionKey ? { encryption_key: encryptionKey } : {}),
            });
            toast({
                title: 'Restore Started',
                description: `"${serviceName}" is being restored. This may take several minutes.`,
            });
            setRestorePollInterval(Date.now());
        } catch (err: any) {
            setRestoringId(null);
            setRestoringName(null);
            const data = err?.response?.data;
            if (data?.error_code === 'ENCRYPTION_KEY_REQUIRED') {
                setKeyPromptBackupId(backupId);
                setKeyPromptServiceName(serviceName);
                setKeyPromptValue('');
                setKeyPromptError(data?.error || 'Encryption key required');
                setKeyPromptOpen(true);
                return;
            }
            const msg = data?.error || 'Failed to trigger restore.';
            toast({ title: 'Restore Failed', description: msg, variant: 'destructive' });
        }
    };

    const submitEncryptionKey = async () => {
        if (!keyPromptBackupId || !keyPromptValue.trim()) {
            setKeyPromptError('Please enter the encryption key.');
            return;
        }
        setKeyPromptSubmitting(true);
        setKeyPromptError('');
        try {
            await doRestore(keyPromptBackupId, keyPromptServiceName, keyPromptValue.trim());
            setKeyPromptOpen(false);
        } catch {
            setKeyPromptError('Failed to restore with provided key.');
        } finally {
            setKeyPromptSubmitting(false);
        }
    };

    const handleRestore = async (serviceName: string, backupId: string) => {
        if (!await confirm({
            title: `Restore "${serviceName}"?`,
            message: 'This will overwrite the current service state with the backup. Are you sure?',
            variant: 'destructive',
            confirmText: 'Restore'
        })) return;
        doRestore(backupId, serviceName);
    };

    const formatBytes = (bytes: number) => {
        if (!bytes) return '-';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
    };

    return (
        <DashboardShell>
            <div className="container p-6 space-y-6">
                <div className="flex justify-between items-center">
                    <div>
                        <h1 className="text-3xl font-bold">Service Restore</h1>
                        <p className="text-muted-foreground">
                            Restore individual services from their latest backups.
                        </p>
                    </div>
                    <div className="flex gap-2">
                        <Button variant="outline" onClick={loadData} disabled={loading}>
                            <RefreshCw className={`mr-2 h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                            Refresh
                        </Button>
                        <Link href="/backups">
                            <Button variant="outline">
                                <Archive className="mr-2 h-4 w-4" />
                                Server Backups
                            </Button>
                        </Link>
                    </div>
                </div>

                <Card>
                    <CardHeader>
                        <CardTitle>Services</CardTitle>
                        <CardDescription>
                            Services with available backups can be restored to a previous state.
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="p-0">
                        {loading ? (
                            <div className="flex justify-center p-12">
                                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                            </div>
                        ) : error ? (
                            <div className="flex flex-col items-center gap-3 p-12 text-center">
                                <AlertCircle className="h-8 w-8 text-red-500" />
                                <p className="text-muted-foreground">{error}</p>
                                <Button variant="outline" onClick={loadData}>Retry</Button>
                            </div>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Service</TableHead>
                                        <TableHead>Latest Backup</TableHead>
                                        <TableHead>Type</TableHead>
                                        <TableHead>Size</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead className="text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {serviceBackups.map(sb => (
                                        <TableRow key={sb.serviceId}>
                                            <TableCell>
                                                <Link
                                                    href={`/services/${sb.serviceId}`}
                                                    className="font-medium hover:text-emerald-400 transition-colors"
                                                >
                                                    <Server className="inline h-3.5 w-3.5 mr-1.5 text-muted-foreground" />
                                                    {sb.serviceName}
                                                </Link>
                                            </TableCell>
                                            <TableCell className="text-sm text-muted-foreground">
                                                {sb.latestBackup
                                                    ? new Date(sb.latestBackup.created_at).toLocaleString()
                                                    : 'No backups'
                                                }
                                            </TableCell>
                                            <TableCell>
                                                {sb.latestBackup ? (
                                                    <Badge variant="outline" className="text-xs font-mono">
                                                        {sb.latestBackup.backup_type}
                                                    </Badge>
                                                ) : (
                                                    <span className="text-xs text-muted-foreground">-</span>
                                                )}
                                            </TableCell>
                                            <TableCell className="text-sm text-muted-foreground">
                                                {sb.latestBackup ? formatBytes(sb.latestBackup.size_bytes) : '-'}
                                            </TableCell>
                                            <TableCell>
                                                {sb.latestBackup ? (
                                                    <span className="text-xs font-semibold text-emerald-500 bg-emerald-500/10 px-2 py-1 rounded">
                                                        Available
                                                    </span>
                                                ) : (
                                                    <span className="text-xs text-muted-foreground bg-muted px-2 py-1 rounded">
                                                        No backup
                                                    </span>
                                                )}
                                            </TableCell>
                                            <TableCell className="text-right">
                                                {sb.latestBackup && (
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => handleRestore(sb.serviceName, sb.latestBackup!.id)}
                                                        disabled={restoringId === sb.latestBackup.id}
                                                        title={`Restore ${sb.serviceName} from backup`}
                                                    >
                                                        {restoringId === sb.latestBackup.id ? (
                                                            <Loader2 className="w-4 h-4 animate-spin mr-1" />
                                                        ) : (
                                                            <RotateCcw className="w-4 h-4 mr-1" />
                                                        )}
                                                        Restore
                                                    </Button>
                                                )}
                                                {sb.latestBackup && (
                                                    <Link
                                                        href={`/services/${sb.serviceId}`}
                                                        className="text-xs text-muted-foreground hover:text-emerald-400 ml-2 transition-colors"
                                                    >
                                                        Manage
                                                    </Link>
                                                )}
                                                {!sb.latestBackup && (
                                                    <Link href={`/services/${sb.serviceId}`}>
                                                        <Button variant="ghost" size="sm">
                                                            View Service
                                                        </Button>
                                                    </Link>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                    {serviceBackups.length === 0 && (
                                        <TableRow>
                                            <TableCell colSpan={6} className="text-center py-12 text-muted-foreground">
                                                <div className="flex flex-col items-center gap-2">
                                                    <Archive className="h-8 w-8 text-muted-foreground/50" />
                                                    <p>No services found.</p>
                                                    <Link href="/new">
                                                        <Button variant="outline" size="sm">
                                                            Create a Service
                                                        </Button>
                                                    </Link>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>About Service Restore</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-muted-foreground space-y-2">
                        <p>
                            Restoring a service from backup will overwrite its current state
                            including environment variables, volumes, and Docker image.
                        </p>
                        <p>
                            A pre-restore snapshot is automatically created before each restore
                            as a safety net. The service will be redeployed after the restore
                            completes.
                        </p>
                        <p>
                            For server-level disaster recovery, use the{' '}
                            <Link href="/backups" className="text-emerald-400 hover:underline">
                                Server Backups
                            </Link>{' '}
                            page.
                        </p>
                    </CardContent>
                </Card>
            </div>

            {/* Encryption key prompt modal (for cross-master restores) */}
            {keyPromptOpen && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 animate-in fade-in duration-200">
                    <div className="bg-background border border-border rounded-lg p-6 max-w-md w-full mx-4 shadow-lg">
                        <div className="flex items-center gap-2 mb-4">
                            <Key className="h-5 w-5 text-amber-500" />
                            <h3 className="text-lg font-semibold">Encryption Key Required</h3>
                        </div>
                        <p className="text-sm text-muted-foreground mb-2">
                            {keyPromptError}
                        </p>
                        <p className="text-xs text-muted-foreground mb-4">
                            This backup was encrypted on a different master. Enter the source backup
                            encryption key to decrypt and restore it.
                        </p>
                        <Input
                            type="password"
                            placeholder="Backup encryption key"
                            value={keyPromptValue}
                            onChange={(e) => setKeyPromptValue(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') submitEncryptionKey(); }}
                            autoFocus
                            className="mb-4"
                        />
                        <div className="flex justify-end gap-2">
                            <Button
                                variant="outline"
                                onClick={() => { setKeyPromptOpen(false); setKeyPromptValue(''); }}
                                disabled={keyPromptSubmitting}
                            >
                                Cancel
                            </Button>
                            <Button
                                onClick={submitEncryptionKey}
                                disabled={keyPromptSubmitting || !keyPromptValue.trim()}
                            >
                                {keyPromptSubmitting ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    <Key className="mr-2 h-4 w-4" />
                                )}
                                Restore
                            </Button>
                        </div>
                    </div>
                </div>
            )}
        </DashboardShell>
    );
}
