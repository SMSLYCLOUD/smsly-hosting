'use client';

import React, { useState, useEffect } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Loader2, Download, RotateCcw, Archive } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import api from '@/lib/api';

export default function ServerBackupsPage() {
    const { toast } = useToast();
    const [backups, setBackups] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [creating, setCreating] = useState(false);
    const [restoringId, setRestoringId] = useState<string | null>(null);

    useEffect(() => {
        loadBackups();
    }, []);

    const loadBackups = async () => {
        try {
            const res = await api.get('/server/backups/');
            setBackups(Array.isArray(res.data) ? res.data : res.data.results || []);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleCreateBackup = async () => {
        setCreating(true);
        try {
            await api.post('/server/backups/');
            toast({ title: "Server Backup Started", description: "This captures all services and configuration." });
            loadBackups();
        } catch (err) {
            toast({ title: "Error", description: "Failed to start server backup.", variant: "destructive" });
        } finally {
            setCreating(false);
        }
    };

    const handleRestore = async (backupId: string) => {
        if (!confirm('Are you sure you want to restore this server backup? This will overwrite current state.')) return;
        setRestoringId(backupId);
        try {
            await api.post(`/server/backups/${backupId}/restore/`);
            toast({ title: "Restore Started", description: "Server will restart once restore is complete." });
        } catch (err: any) {
            const msg = err?.response?.data?.error || "Failed to trigger restore.";
            toast({ title: "Error", description: msg, variant: "destructive" });
        } finally {
            setRestoringId(null);
        }
    };

    return (
        <DashboardShell>
            <div className="container p-6 space-y-6">
                <div className="flex justify-between items-center">
                    <div>
                        <h1 className="text-3xl font-bold">Server Backups</h1>
                        <p className="text-muted-foreground">Full platform snapshots for disaster recovery or migration.</p>
                    </div>
                    <Button onClick={handleCreateBackup} disabled={creating}>
                        {creating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Archive className="mr-2 h-4 w-4" />}
                        Create Full Backup
                    </Button>
                </div>

                <Card>
                    <CardContent className="p-0">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Date</TableHead>
                                    <TableHead>Services Included</TableHead>
                                    <TableHead>Size</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {backups.map(backup => (
                                    <TableRow key={backup.id}>
                                        <TableCell>{new Date(backup.created_at).toLocaleString()}</TableCell>
                                        <TableCell>{backup.services_included?.length || 0}</TableCell>
                                        <TableCell>{(backup.size_bytes / 1024 / 1024).toFixed(2)} MB</TableCell>
                                        <TableCell>
                                            <span className={`text-xs font-semibold px-2 py-1 rounded ${
                                                backup.status === 'COMPLETED' ? 'bg-emerald-500/10 text-emerald-500' :
                                                backup.status === 'FAILED' ? 'bg-red-500/10 text-red-500' :
                                                'bg-yellow-500/10 text-yellow-500'
                                            }`}>
                                                {backup.status}
                                            </span>
                                        </TableCell>
                                        <TableCell className="text-right space-x-2">
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                onClick={() => handleRestore(backup.id)}
                                                disabled={backup.status !== 'COMPLETED' || restoringId === backup.id}
                                                title="Restore this backup"
                                            >
                                                {restoringId === backup.id
                                                    ? <Loader2 className="w-4 h-4 animate-spin" />
                                                    : <RotateCcw className="w-4 h-4" />}
                                            </Button>
                                            <Button variant="ghost" size="sm" asChild>
                                                <a href={`/api/v1/backups/${backup.id}/download/`} target="_blank" rel="noopener noreferrer">
                                                    <Download className="w-4 h-4" />
                                                </a>
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                ))}
                                {backups.length === 0 && (
                                    <TableRow>
                                        <TableCell colSpan={5} className="text-center py-12 text-muted-foreground">
                                            No server backups found.
                                        </TableCell>
                                    </TableRow>
                                )}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            </div>
        </DashboardShell>
    );
}

