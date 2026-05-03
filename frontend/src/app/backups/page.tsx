'use client';

import React, { useState, useEffect, useRef } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Loader2, Download, RotateCcw, Archive, Trash2, Upload } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import api from '@/lib/api';

export default function ServerBackupsPage() {
    const { toast } = useToast();
    const confirm = useConfirm();
    const [backups, setBackups] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [creating, setCreating] = useState(false);
    const [restoringId, setRestoringId] = useState<string | null>(null);
    const [uploading, setUploading] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

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
        } catch (err: any) {
            const msg = err?.response?.data?.error || err?.response?.data?.detail || "Failed to start server backup.";
            toast({ title: "Backup Failed", description: msg, variant: "destructive" });
        } finally {
            setCreating(false);
        }
    };

    const handleRestore = async (backupId: string) => {
        if (!await confirm({ title: 'Restore server backup?', message: 'This will overwrite current state. Are you sure?', variant: 'destructive', confirmText: 'Restore' })) return;
        setRestoringId(backupId);
        try {
            await api.post(`/server/backups/${backupId}/restore/`, { confirm: true });
            toast({ title: "Restore Started", description: "Server will restart once restore is complete." });
        } catch (err: any) {
            const msg = err?.response?.data?.error || "Failed to trigger restore.";
            toast({ title: "Error", description: msg, variant: "destructive" });
        } finally {
            setRestoringId(null);
        }
    };

    const handleDeleteBackup = async (id: string) => {
        if (!await confirm({ title: 'Delete server backup?', message: 'This backup will be permanently deleted.', variant: 'destructive', confirmText: 'Delete' })) return;
        try {
            await api.delete(`/server/backups/${id}/`);
            toast({ title: "Backup deleted" });
            loadBackups();
        } catch (err) {
            toast({ title: "Error", description: "Failed to delete backup.", variant: "destructive" });
        }
    };

    const handleUploadRestore = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        if (!file.name.endsWith('.tar.gz') && !file.name.endsWith('.tgz')) {
            toast({ title: "Invalid File", description: "Please select a .tar.gz or .tgz backup file.", variant: "destructive" });
            return;
        }
        if (!await confirm({ title: 'Restore from file?', message: `Restore from "${file.name}"? This will overwrite current server state.`, variant: 'destructive', confirmText: 'Restore' })) {
            e.target.value = '';
            return;
        }
        setUploading(true);
        try {
            const formData = new FormData();
            formData.append('file', file);
            await api.post('/server/backups/upload-restore/', formData, {
                headers: { 'Content-Type': 'multipart/form-data' },
                timeout: 600000, // 10 minute timeout for large files
            });
            toast({ title: "Restore Started", description: `Restoring from uploaded file "${file.name}". This may take several minutes.` });
            loadBackups();
        } catch (err: any) {
            const msg = err?.response?.data?.error || "Failed to upload and restore backup.";
            toast({ title: "Upload Failed", description: msg, variant: "destructive" });
        } finally {
            setUploading(false);
            e.target.value = '';
        }
    };

    const getStatusBadge = (status: string) => {
        const styles: Record<string, string> = {
            COMPLETED: 'bg-emerald-500/10 text-emerald-500',
            FAILED: 'bg-red-500/10 text-red-500',
            IN_PROGRESS: 'bg-blue-500/10 text-blue-500',
            PENDING: 'bg-yellow-500/10 text-yellow-500',
        };
        return styles[status] || 'bg-muted text-muted-foreground';
    };

    return (
        <DashboardShell>
            <div className="container p-6 space-y-6">
                <div className="flex justify-between items-center">
                    <div>
                        <h1 className="text-3xl font-bold">Server Backups</h1>
                        <p className="text-muted-foreground">Full platform snapshots for disaster recovery or migration.</p>
                    </div>
                    <div className="flex gap-2">
                        <input
                            type="file"
                            ref={fileInputRef}
                            accept=".tar.gz,.tgz"
                            className="hidden"
                            onChange={handleUploadRestore}
                        />
                        <Button
                            variant="outline"
                            onClick={() => fileInputRef.current?.click()}
                            disabled={uploading}
                        >
                            {uploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
                            Restore from File
                        </Button>
                        <Button onClick={handleCreateBackup} disabled={creating}>
                            {creating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Archive className="mr-2 h-4 w-4" />}
                            Create Full Backup
                        </Button>
                    </div>
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
                                            <div className="space-y-1">
                                                <span className={`text-xs font-semibold px-2 py-1 rounded ${getStatusBadge(backup.status)}`}>
                                                    {backup.status}
                                                </span>
                                                {backup.status === 'FAILED' && backup.error_message && (
                                                    <p className="text-[11px] text-red-400 max-w-[260px] truncate" title={backup.error_message}>
                                                        {backup.error_message}
                                                    </p>
                                                )}
                                            </div>
                                        </TableCell>
                                        <TableCell className="text-right space-x-2">
                                            {backup.status === 'COMPLETED' && (
                                                <>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => handleRestore(backup.id)}
                                                        disabled={restoringId === backup.id}
                                                        title="Restore this backup"
                                                    >
                                                        {restoringId === backup.id
                                                            ? <Loader2 className="w-4 h-4 animate-spin" />
                                                            : <RotateCcw className="w-4 h-4" />}
                                                    </Button>
                                                    <Button variant="ghost" size="sm" onClick={() => {
                                                        const token = localStorage.getItem('auth_token') || localStorage.getItem('token');
                                                        if (!token) {
                                                            toast({ title: "Download failed", description: "Authentication token is missing.", variant: "destructive" });
                                                            return;
                                                        }
                                                        window.location.href = `/api/v1/server/backups/${backup.id}/download/?token=${encodeURIComponent(token)}`;
                                                    }} title="Download">
                                                        <Download className="w-4 h-4" />
                                                    </Button>
                                                </>
                                            )}
                                            {backup.status === 'FAILED' && (
                                                <Button variant="ghost" size="sm" onClick={() => handleDeleteBackup(backup.id)} title="Delete" className="text-red-400 hover:text-red-500">
                                                    <Trash2 className="w-4 h-4" />
                                                </Button>
                                            )}
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
