'use client';

import React, { useState, useEffect, useRef } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Loader2, Download, RotateCcw, Archive, Trash2, Upload, Key, FileKey, Cloud, Clock } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api from '@/lib/api';

interface CloudDestination {
    id: string;
    name: string;
    provider_display: string;
    bucket: string;
}

export default function ServerBackupsPage() {
    const { toast } = useToast();
    const confirm = useConfirm();
    const [backups, setBackups] = useState<any[]>([]);
    const [destinations, setDestinations] = useState<CloudDestination[]>([]);
    const [selectedDestination, setSelectedDestination] = useState<string>('');
    const [loading, setLoading] = useState(true);
    const [creating, setCreating] = useState(false);
    const [restoringId, setRestoringId] = useState<string | null>(null);
    const [uploading, setUploading] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const jsonKeyInputRef = useRef<HTMLInputElement>(null);

    // Encryption key prompt modal state
    const [keyPromptOpen, setKeyPromptOpen] = useState(false);
    const [keyPromptValue, setKeyPromptValue] = useState('');
    const [keyPromptBackupId, setKeyPromptBackupId] = useState<string | null>(null);
    const [keyPromptError, setKeyPromptError] = useState('');
    const [keyPromptSubmitting, setKeyPromptSubmitting] = useState(false);

    // Upload key prompt modal state
    const [uploadKeyPromptOpen, setUploadKeyPromptOpen] = useState(false);
    const [uploadKeyValue, setUploadKeyValue] = useState('');
    const [uploadKeyFile, setUploadKeyFile] = useState<File | null>(null);
    const [pendingUploadFile, setPendingUploadFile] = useState<File | null>(null);

    // Cloud Restore Modal State
    const [cloudRestorePromptOpen, setCloudRestorePromptOpen] = useState(false);
    const [cloudRestoreForm, setCloudRestoreForm] = useState({
        cloud_storage_id: '',
        s3_bucket: '',
        s3_key: '',
        s3_endpoint: '',
        s3_region: 'us-east-1',
        s3_access_key: '',
        s3_secret_key: '',
        encryption_key: ''
    });

    // Schedule state
    const [schedule, setSchedule] = useState<any | null>(null);
    const [cronExpression, setCronExpression] = useState('0 3 * * *');
    const [retentionDays, setRetentionDays] = useState(7);
    const [scheduleEnabled, setScheduleEnabled] = useState(true);
    const [scheduleCloudDest, setScheduleCloudDest] = useState('');
    const [savingSchedule, setSavingSchedule] = useState(false);
    const [scheduleLoading, setScheduleLoading] = useState(true);
    const [scheduleDbOnly, setScheduleDbOnly] = useState(false);
    const [dbOnly, setDbOnly] = useState(false);

    useEffect(() => {
        loadBackups();
        loadSchedule();
    }, []);

    const loadSchedule = async () => {
        try {
            const res = await api.get('/backup-schedules/', { params: { is_server_wide: true } });
            const schedules = Array.isArray(res.data) ? res.data : res.data.results || [];
            const sched = schedules.find((s: any) => s.is_server_wide);
            if (sched) {
                setSchedule(sched);
                setCronExpression(sched.cron_expression);
                setRetentionDays(sched.retention_days);
                setScheduleEnabled(sched.enabled);
                setScheduleDbOnly(sched.db_only || false);
                if (sched.cloud_destination) {
                    setScheduleCloudDest(sched.cloud_destination);
                }
            }
        } catch (err) {
            console.error('Failed to load server backup schedule', err);
        } finally {
            setScheduleLoading(false);
        }
    };

    const loadBackups = async () => {
        try {
            const [backupsRes, destsRes] = await Promise.all([
                api.get('/server/backups/'),
                api.get('/cloud-storage/')
            ]);
            setBackups(Array.isArray(backupsRes.data) ? backupsRes.data : backupsRes.data.results || []);
            
            const allDestinations = Array.isArray(destsRes.data) ? destsRes.data : destsRes.data.results || [];
            // For server backups, we want platform-wide destinations (service=null)
            const relevant = allDestinations.filter((d: any) => !d.service);
            setDestinations(relevant);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleSaveSchedule = async () => {
        setSavingSchedule(true);
        try {
            const payload: any = {
                is_server_wide: true,
                cron_expression: cronExpression,
                retention_days: retentionDays,
                enabled: scheduleEnabled,
                db_only: scheduleDbOnly,
            };
            
            if (scheduleCloudDest) {
                payload.cloud_destination_id = scheduleCloudDest;
            } else {
                payload.cloud_destination_id = null;
                payload.storage_backend = 'local';
            }
            if (schedule?.id) {
                await api.patch(`/backup-schedules/${schedule.id}/`, payload);
            } else {
                await api.post('/backup-schedules/', payload);
            }
            toast({ title: "Schedule saved", description: "Server backup schedule has been updated." });
            loadSchedule();
        } catch (err) {
            toast({ title: "Error", description: "Failed to save schedule.", variant: "destructive" });
        } finally {
            setSavingSchedule(false);
        }
    };

    const handleCreateBackup = async () => {
        setCreating(true);
        try {
            const payload = selectedDestination ? { cloud_destination: selectedDestination, db_only: dbOnly } : { db_only: dbOnly };
            await api.post('/server/backups/', payload);
            toast({ title: "Server Backup Started", description: "This captures all services and configuration." });
            loadBackups();
        } catch (err: any) {
            const msg = err?.response?.data?.error || err?.response?.data?.detail || "Failed to start server backup.";
            toast({ title: "Backup Failed", description: msg, variant: "destructive" });
        } finally {
            setCreating(false);
        }
    };

    const handleRestore = async (backupId: string, encryptionKey?: string) => {
        if (!await confirm({ title: 'Restore server backup?', message: 'This will overwrite current state. Are you sure?', variant: 'destructive', confirmText: 'Restore' })) return;
        setRestoringId(backupId);
        try {
            await api.post(`/server/backups/${backupId}/restore/`, { confirm: true, ...(encryptionKey ? { encryption_key: encryptionKey } : {}) });
            toast({ title: "Restore Started", description: "Server will restart once restore is complete." });
        } catch (err: any) {
            const data = err?.response?.data;
            if (data?.error_code === 'ENCRYPTION_KEY_REQUIRED') {
                setKeyPromptBackupId(backupId);
                setKeyPromptValue('');
                setKeyPromptError(data?.error || 'Encryption key required');
                setKeyPromptOpen(true);
                setRestoringId(null);
                return;
            }
            const msg = data?.error || "Failed to trigger restore.";
            toast({ title: "Error", description: msg, variant: "destructive" });
        } finally {
            setRestoringId(null);
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
            await handleRestore(keyPromptBackupId, keyPromptValue.trim());
            setKeyPromptOpen(false);
            setKeyPromptSubmitting(false);
        } catch {
            setKeyPromptSubmitting(false);
        }
    };

    const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        if (!file.name.endsWith('.tar.gz') && !file.name.endsWith('.tgz')) {
            toast({ title: "Invalid File", description: "Please select a .tar.gz or .tgz backup file.", variant: "destructive" });
            e.target.value = '';
            return;
        }
        setPendingUploadFile(file);
        setUploadKeyValue('');
        setUploadKeyFile(null);
        setUploadKeyPromptOpen(true);
        e.target.value = '';
    };

    const handleUploadRestore = async (encryptionKey?: string) => {
        const file = pendingUploadFile;
        if (!file) return;

        if (!await confirm({ title: 'Restore from file?', message: `Restore from "${file.name}"? This will overwrite current server state.`, variant: 'destructive', confirmText: 'Restore' })) {
            setPendingUploadFile(null);
            setUploadKeyPromptOpen(false);
            return;
        }

        setUploading(true);
        setUploadKeyPromptOpen(false);
        try {
            const formData = new FormData();
            formData.append('file', file);
            if (encryptionKey) {
                formData.append('encryption_key', encryptionKey);
            }
            await api.post('/server/backups/upload-restore/', formData, {
                headers: { 'Content-Type': 'multipart/form-data' },
                timeout: 600000,
            });
            toast({ title: "Restore Started", description: `Restoring from uploaded file "${file.name}". This may take several minutes.` });
            loadBackups();
        } catch (err: any) {
            const msg = err?.response?.data?.error || "Failed to upload and restore backup.";
            toast({ title: "Upload Failed", description: msg, variant: "destructive" });
        } finally {
            setUploading(false);
            setPendingUploadFile(null);
        }
    };

    const handleCloudRestoreSubmit = async () => {
        const isCustom = !cloudRestoreForm.cloud_storage_id || cloudRestoreForm.cloud_storage_id === 'custom';
        if (isCustom && (!cloudRestoreForm.s3_bucket || !cloudRestoreForm.s3_key || !cloudRestoreForm.s3_access_key || !cloudRestoreForm.s3_secret_key)) {
            toast({ title: 'Missing Fields', description: 'Please fill in all required S3 fields.', variant: 'destructive' });
            return;
        }
        if (!isCustom && !cloudRestoreForm.s3_key) {
            toast({ title: 'Missing Fields', description: 'Please provide the Object Key.', variant: 'destructive' });
            return;
        }

        const bucketStr = isCustom ? cloudRestoreForm.s3_bucket : (destinations.find(d => d.id === cloudRestoreForm.cloud_storage_id)?.bucket || 'Cloud Storage');

        if (!await confirm({ title: 'Restore from Cloud?', message: `Restore from ${bucketStr}/${cloudRestoreForm.s3_key}? This will overwrite current server state.`, variant: 'destructive', confirmText: 'Restore' })) {
            return;
        }

        setUploading(true);
        setCloudRestorePromptOpen(false);
        try {
            await api.post('/server/backups/restore-from-cloud/', cloudRestoreForm);
            toast({ title: "Restore Started", description: `Restoring from cloud backup.` });
            loadBackups();
        } catch (err: any) {
            const msg = err?.response?.data?.error || "Failed to download and restore backup.";
            toast({ title: "Restore Failed", description: msg, variant: "destructive" });
        } finally {
            setUploading(false);
            setCloudRestoreForm({
                cloud_storage_id: '', s3_bucket: '', s3_key: '', s3_endpoint: '', s3_region: 'us-east-1',
                s3_access_key: '', s3_secret_key: '', encryption_key: ''
            });
        }
    };

    const handleJsonKeyFile = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        setUploadKeyFile(file);
        const reader = new FileReader();
        reader.onload = (ev) => {
            try {
                const json = JSON.parse(ev.target?.result as string);
                const key = json.key_material || json.key || json.encryption_key || json.BACKUP_ENCRYPTION_KEY || '';
                if (key) {
                    setUploadKeyValue(key);
                } else {
                    toast({ title: "Invalid Key File", description: "JSON file must contain a 'key_material', 'key', or 'encryption_key' field.", variant: "destructive" });
                }
            } catch {
                toast({ title: "Invalid JSON", description: "Could not parse the selected file as JSON.", variant: "destructive" });
            }
        };
        reader.readAsText(file);
        e.target.value = '';
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
                            onChange={handleFileSelected}
                        />
                        <div className="flex gap-2">
                            <Button
                                variant="outline"
                                onClick={() => fileInputRef.current?.click()}
                                disabled={uploading}
                            >
                                {uploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
                                Restore from File
                            </Button>
                            <Button
                                variant="outline"
                                onClick={() => setCloudRestorePromptOpen(true)}
                                disabled={uploading}
                            >
                                {uploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Cloud className="mr-2 h-4 w-4" />}
                                Restore from Cloud
                            </Button>
                        </div>
                        <select
                            value={selectedDestination}
                            onChange={(e) => setSelectedDestination(e.target.value)}
                            className="px-3 py-2 rounded-lg bg-background border border-border text-sm h-[40px]"
                        >
                            <option value="">Store Locally</option>
                            {destinations.map(d => (
                                <option key={d.id} value={d.id}>
                                    Save to {d.name} ({d.provider_display})
                                </option>
                            ))}
                        </select>
                        <div className="flex items-center gap-4">
                            <label className="flex items-center space-x-2 text-sm font-medium cursor-pointer bg-background border px-3 py-2 rounded-lg h-[40px]">
                                <input
                                    type="checkbox"
                                    checked={dbOnly}
                                    onChange={(e) => setDbOnly(e.target.checked)}
                                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                    disabled={creating}
                                />
                                <span>DB Only</span>
                            </label>
                            <Button onClick={handleCreateBackup} disabled={creating}>
                                {creating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Archive className="mr-2 h-4 w-4" />}
                                {dbOnly ? 'Create DB Backup' : 'Create Full Backup'}
                            </Button>
                        </div>
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
                                            <div className="space-y-1 flex flex-col">
                                                <span className={`text-xs font-semibold px-2 py-1 rounded w-fit ${getStatusBadge(backup.status)}`}>
                                                    {backup.status}
                                                </span>
                                                {backup.db_only && <span className="text-[11px] bg-blue-100 text-blue-800 px-2 py-0.5 rounded border border-blue-200 w-fit">DB Only</span>}
                                                {backup.cloud_uploaded && (
                                                    <span className="flex items-center gap-1 text-[11px] text-blue-400 font-medium ml-2" title="Backed up to Cloud Storage">
                                                        <Cloud className="w-3 h-3" />
                                                        Cloud
                                                    </span>
                                                )}
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
                                                    <Button variant="ghost" size="sm" onClick={async () => {
                                                        try {
                                                            const res = await api.get(`/server/backups/${backup.id}/download-url/`);
                                                            if (res.data?.url) {
                                                                window.location.href = res.data.url;
                                                            } else {
                                                                toast({ title: "Download failed", description: "Could not generate signed download link.", variant: "destructive" });
                                                            }
                                                        } catch (err) {
                                                            toast({ title: "Download failed", description: "Could not generate signed download link.", variant: "destructive" });
                                                        }
                                                    }} title="Download">
                                                        <Download className="w-4 h-4" />
                                                    </Button>
                                                    <Button variant="ghost" size="sm" onClick={async () => {
                                                        try {
                                                            const res = await api.get(`/server/backups/${backup.id}/download-key/`, { responseType: 'blob' });
                                                            const blob = new Blob([res.data], { type: 'application/json' });
                                                            const url = window.URL.createObjectURL(blob);
                                                            const a = document.createElement('a');
                                                            a.href = url;
                                                            a.download = `backup-${backup.id}-key.json`;
                                                            document.body.appendChild(a);
                                                            a.click();
                                                            a.remove();
                                                            window.URL.revokeObjectURL(url);
                                                            toast({ title: "Key downloaded", description: "Store this file with your backup." });
                                                        } catch (err: any) {
                                                            const msg = err?.response?.data?.error || 'Could not download key.';
                                                            toast({ title: "Key download failed", description: msg, variant: "destructive" });
                                                        }
                                                    }} title="Download encryption key info">
                                                        <Key className="w-4 h-4" />
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

            {/* Encryption key prompt modal (for existing backup restore) */}
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
                            This backup was encrypted on a different master. Enter the source master&apos;s
                            backup encryption key to decrypt and restore it.
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

            {/* Upload key prompt modal (for file upload restore) */}
            {uploadKeyPromptOpen && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 animate-in fade-in duration-200">
                    <div className="bg-background border border-border rounded-lg p-6 max-w-md w-full mx-4 shadow-lg">
                        <div className="flex items-center gap-2 mb-4">
                            <FileKey className="h-5 w-5 text-amber-500" />
                            <h3 className="text-lg font-semibold">Encryption Key (Optional)</h3>
                        </div>
                        <p className="text-sm text-muted-foreground mb-4">
                            If this backup was encrypted on a different server, enter or upload
                            the source backup encryption key. Skip if the backup is from the same server.
                        </p>

                        {/* Text input */}
                        <Input
                            type="password"
                            placeholder="Backup encryption key (or leave blank to skip)"
                            value={uploadKeyValue}
                            onChange={(e) => setUploadKeyValue(e.target.value)}
                            className="mb-3"
                        />

                        {/* JSON file upload */}
                        <div className="flex items-center gap-2 mb-4">
                            <input
                                type="file"
                                ref={jsonKeyInputRef}
                                accept=".json"
                                className="hidden"
                                onChange={handleJsonKeyFile}
                            />
                            <Button
                                variant="outline"
                                size="sm"
                                onClick={() => jsonKeyInputRef.current?.click()}
                            >
                                <FileKey className="mr-2 h-4 w-4" />
                                {uploadKeyFile ? uploadKeyFile.name : 'Upload Key JSON'}
                            </Button>
                            {uploadKeyFile && (
                                <span className="text-xs text-muted-foreground truncate max-w-[180px]">
                                    {uploadKeyFile.name}
                                </span>
                            )}
                        </div>

                        <p className="text-xs text-muted-foreground mb-4">
                            JSON format: {`{ "key_material": "...", "key_id": "..." }`}
                        </p>

                        <div className="flex justify-end gap-2">
                            <Button
                                variant="outline"
                                onClick={() => {
                                    setUploadKeyPromptOpen(false);
                                    setPendingUploadFile(null);
                                }}
                                disabled={uploading}
                            >
                                Cancel
                            </Button>
                            <Button
                                variant="secondary"
                                onClick={() => handleUploadRestore(undefined)}
                                disabled={uploading}
                            >
                                Skip — Use Server Key
                            </Button>
                            <Button
                                onClick={() => handleUploadRestore(uploadKeyValue.trim() || undefined)}
                                disabled={uploading || !uploadKeyValue.trim()}
                            >
                                {uploading ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    <Upload className="mr-2 h-4 w-4" />
                                )}
                                {uploadKeyValue.trim() ? 'Upload & Restore' : 'Skip & Restore'}
                            </Button>
                        </div>
                    </div>
                </div>
            )}

            {/* Cloud Restore Modal */}
            {cloudRestorePromptOpen && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 animate-in fade-in duration-200">
                    <div className="bg-background border border-border rounded-lg p-6 max-w-md w-full mx-4 shadow-lg">
                        <div className="flex items-center gap-2 mb-4">
                            <Cloud className="h-5 w-5 text-blue-500" />
                            <h3 className="text-lg font-semibold">Restore from Cloud</h3>
                        </div>
                        <p className="text-sm text-muted-foreground mb-4">
                            Enter the details of the S3-compatible cloud storage where your backup is located.
                        </p>

                        <div className="space-y-3 mb-4">
                            <Select
                                value={cloudRestoreForm.cloud_storage_id || 'custom'}
                                onValueChange={(val) => setCloudRestoreForm({ ...cloudRestoreForm, cloud_storage_id: val })}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="Select Cloud Storage" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="custom">Custom Credentials</SelectItem>
                                    {destinations.map(d => (
                                        <SelectItem key={d.id} value={d.id}>
                                            {d.name} ({d.provider_display})
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>

                            <Input
                                placeholder="Object Key (e.g., smsly-backups/server/backup.tar.gz)"
                                value={cloudRestoreForm.s3_key}
                                onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, s3_key: e.target.value })}
                            />

                            {(!cloudRestoreForm.cloud_storage_id || cloudRestoreForm.cloud_storage_id === 'custom') && (
                                <>
                                    <Input
                                        placeholder="Bucket Name"
                                        value={cloudRestoreForm.s3_bucket}
                                        onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, s3_bucket: e.target.value })}
                                    />
                                    <Input
                                        placeholder="Endpoint URL (e.g., https://s3.eu-west-1.amazonaws.com)"
                                        value={cloudRestoreForm.s3_endpoint}
                                        onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, s3_endpoint: e.target.value })}
                                    />
                                    <Input
                                        placeholder="Region (default: us-east-1)"
                                        value={cloudRestoreForm.s3_region}
                                        onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, s3_region: e.target.value })}
                                    />
                                    <Input
                                        placeholder="Access Key ID"
                                        value={cloudRestoreForm.s3_access_key}
                                        onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, s3_access_key: e.target.value })}
                                    />
                                    <Input
                                        type="password"
                                        placeholder="Secret Access Key"
                                        value={cloudRestoreForm.s3_secret_key}
                                        onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, s3_secret_key: e.target.value })}
                                    />
                                </>
                            )}
                            <div className="pt-2 border-t border-border mt-2">
                                <label className="text-xs font-semibold mb-1 block">Backup Encryption Key (Optional)</label>
                                <Input
                                    type="password"
                                    placeholder="Leave blank if backup isn't encrypted"
                                    value={cloudRestoreForm.encryption_key}
                                    onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, encryption_key: e.target.value })}
                                />
                            </div>
                        </div>

                        <div className="flex justify-end gap-2">
                            <Button
                                variant="outline"
                                onClick={() => setCloudRestorePromptOpen(false)}
                                disabled={uploading}
                            >
                                Cancel
                            </Button>
                            <Button
                                onClick={handleCloudRestoreSubmit}
                                disabled={uploading || !cloudRestoreForm.s3_bucket || !cloudRestoreForm.s3_key || !cloudRestoreForm.s3_access_key || !cloudRestoreForm.s3_secret_key}
                            >
                                {uploading ? (
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                ) : (
                                    <Cloud className="mr-2 h-4 w-4" />
                                )}
                                Start Restore
                            </Button>
                        </div>
                    </div>
                </div>
            )}
        </DashboardShell>
    );
}
