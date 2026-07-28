'use client';

import React, { useState, useEffect, useRef } from 'react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Loader2, Download, RotateCcw, Archive, Trash2, Upload, Key, FileKey, Cloud, Clock, Save, Camera, X } from 'lucide-react';
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
    service?: string | null;
}

interface BackupSchedule {
    id?: string;
    is_server_wide?: boolean;
    cron_expression: string;
    retention_days: number;
    enabled: boolean;
    db_only?: boolean;
    cloud_upload_enabled?: boolean;
    cloud_destination?: string | null;
    last_run?: string;
    next_run?: string;
}

interface ServerBackup {
    id: string;
    status: string;
    size_bytes: number;
    created_at: string;
    completed_at?: string;
    error_message?: string;
    services_included?: string[];
    db_only?: boolean;
    cloud_uploaded?: boolean;
}

interface SnapshotSchedule {
    id?: string;
    is_server_wide?: boolean;
    cron_expression: string;
    retention_days: number;
    enabled: boolean;
    cloud_upload_enabled?: boolean;
    cloud_destination?: string | null;
}

export default function ServerBackupsPage() {
    const { toast } = useToast();
    const confirm = useConfirm();
    const [backups, setBackups] = useState<ServerBackup[]>([]);
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
    const [cloudBackupList, setCloudBackupList] = useState<Record<string, unknown>[]>([]);
    const [cloudBackupListLoading, setCloudBackupListLoading] = useState(false);
    const [cloudBackupPrefix, setCloudBackupPrefix] = useState('smsly-backups/');

    // Fetch cloud backup list when destination changes
    useEffect(() => {
        if (cloudRestorePromptOpen && cloudRestoreForm.cloud_storage_id && cloudRestoreForm.cloud_storage_id !== 'custom') {
            setCloudBackupListLoading(true);
            api.post('/server/backups/list-backups/', {
                cloud_storage_id: cloudRestoreForm.cloud_storage_id,
                prefix: cloudBackupPrefix,
            }).then(res => {
                setCloudBackupList(res.data?.objects || []);
            }).catch(() => {
                setCloudBackupList([]);
            }).finally(() => {
                setCloudBackupListLoading(false);
            });
        } else {
            setCloudBackupList([]);
        }
    }, [cloudRestorePromptOpen, cloudRestoreForm.cloud_storage_id, cloudBackupPrefix]);

    // Schedule state
    const [schedule, setSchedule] = useState<BackupSchedule | null>(null);
    const [cronExpression, setCronExpression] = useState('0 3 * * *');
    const [retentionDays, setRetentionDays] = useState(7);
    const [scheduleEnabled, setScheduleEnabled] = useState(true);
    const [scheduleCloudDest, setScheduleCloudDest] = useState('');
    const [scheduleCloudUpload, setScheduleCloudUpload] = useState(true);
    const [savingSchedule, setSavingSchedule] = useState(false);
    const [scheduleLoading, setScheduleLoading] = useState(true);
    const [scheduleDbOnly, setScheduleDbOnly] = useState(false);
    const [dbOnly, setDbOnly] = useState(false);

    // Snapshot schedule state (server-wide)
    const [snapSchedule, setSnapSchedule] = useState<SnapshotSchedule | null>(null);
    const [snapCronExpression, setSnapCronExpression] = useState('0 3 * * *');
    const [snapRetentionDays, setSnapRetentionDays] = useState(7);
    const [snapScheduleEnabled, setSnapScheduleEnabled] = useState(true);
    const [snapScheduleCloudDest, setSnapScheduleCloudDest] = useState('');
    const [snapScheduleCloudUpload, setSnapScheduleCloudUpload] = useState(true);
    const [savingSnapSchedule, setSavingSnapSchedule] = useState(false);
    const [snapScheduleLoading, setSnapScheduleLoading] = useState(true);

    useEffect(() => {
        loadBackups();
        loadSchedule();
        loadSnapSchedule();
    }, []);

    const loadSchedule = async () => {
        try {
            const res = await api.get('/backup-schedules/', { params: { is_server_wide: true } });
            const schedules = Array.isArray(res.data) ? res.data : res.data.results || [];
            const sched = schedules.find((s: BackupSchedule) => s.is_server_wide);
            if (sched) {
                setSchedule(sched);
                setCronExpression(sched.cron_expression);
                setRetentionDays(sched.retention_days);
                setScheduleEnabled(sched.enabled);
                setScheduleDbOnly(sched.db_only || false);
                setScheduleCloudUpload(sched.cloud_upload_enabled !== false);
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

    const loadSnapSchedule = async () => {
        try {
            const res = await api.get('/snapshot-schedules/', { params: { is_server_wide: true } });
            const schedules = Array.isArray(res.data) ? res.data : res.data.results || [];
            const sched = schedules.find((s: SnapshotSchedule) => s.is_server_wide);
            if (sched) {
                setSnapSchedule(sched);
                setSnapCronExpression(sched.cron_expression);
                setSnapRetentionDays(sched.retention_days);
                setSnapScheduleEnabled(sched.enabled);
                setSnapScheduleCloudUpload(sched.cloud_upload_enabled !== false);
            }
        } catch (err) {
            console.error('Failed to load server snapshot schedule', err);
        } finally {
            setSnapScheduleLoading(false);
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
            const relevant = allDestinations.filter((d: CloudDestination) => !d.service);
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
            const payload: Record<string, unknown> = {
                is_server_wide: true,
                cron_expression: cronExpression,
                retention_days: retentionDays,
                enabled: scheduleEnabled,
                db_only: scheduleDbOnly,
                cloud_upload_enabled: scheduleCloudUpload,
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

    const handleSaveSnapSchedule = async () => {
        setSavingSnapSchedule(true);
        try {
            const payload: Record<string, unknown> = {
                is_server_wide: true,
                cron_expression: snapCronExpression,
                retention_days: snapRetentionDays,
                enabled: snapScheduleEnabled,
                cloud_upload_enabled: snapScheduleCloudUpload,
            };

            if (snapScheduleCloudDest) {
                payload.cloud_destination_id = snapScheduleCloudDest;
            } else {
                payload.cloud_destination_id = null;
                payload.storage_backend = 'local';
            }
            if (snapSchedule?.id) {
                await api.patch(`/snapshot-schedules/${snapSchedule.id}/`, payload);
            } else {
                await api.post('/snapshot-schedules/', payload);
            }
            toast({ title: "Snapshot schedule saved", description: "Server snapshot schedule has been updated." });
            loadSnapSchedule();
        } catch (err) {
            toast({ title: "Error", description: "Failed to save snapshot schedule.", variant: "destructive" });
        } finally {
            setSavingSnapSchedule(false);
        }
    };

    const handleCreateBackup = async () => {
        setCreating(true);
        try {
            const payload = selectedDestination ? { cloud_destination: selectedDestination, db_only: dbOnly } : { db_only: dbOnly };
            await api.post('/server/backups/', payload);
            toast({ title: "Server Backup Started", description: "This captures all services and configuration." });
            loadBackups();
        } catch (err: unknown) {
            const axiosErr = err as { response?: { data?: { error?: string; detail?: string } } };
            const msg = axiosErr?.response?.data?.error || axiosErr?.response?.data?.detail || "Failed to start server backup.";
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
                } else if (json.encryption?.key_id || json.backup_id) {
                    toast({ title: "Metadata File Uploaded", description: "This is a backup header metadata file. It does not contain the encryption key material for security reasons. Please provide a JSON file containing the actual 'key_material'.", variant: "destructive" });
                    setUploadKeyFile(null);
                } else {
                    toast({ title: "Invalid Key File", description: "JSON file must contain a 'key_material', 'key', or 'encryption_key' field.", variant: "destructive" });
                    setUploadKeyFile(null);
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

                {/* Backup Schedule */}
                <Card>
                    <CardContent className="p-6">
                        <h2 className="text-lg font-semibold mb-1">Backup Schedule</h2>
                        <p className="text-sm text-muted-foreground mb-4">Configure automated server-wide backup frequency and retention.</p>
                        {scheduleLoading ? (
                            <div className="flex justify-center p-4"><Loader2 className="animate-spin h-5 w-5 text-muted-foreground" /></div>
                        ) : (
                            <div className="space-y-4">
                                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                                    <div className="space-y-1">
                                        <label className="flex items-center space-x-2 text-sm font-medium mb-2 cursor-pointer">
                                            <input type="checkbox" checked={scheduleDbOnly} onChange={(e) => setScheduleDbOnly(e.target.checked)} className="rounded border-gray-300 text-blue-600" disabled={savingSchedule} />
                                            <span>Database Only Backup</span>
                                        </label>
                                        <label className="text-sm font-medium">Cron Expression</label>
                                        <input type="text" value={cronExpression} onChange={(e) => setCronExpression(e.target.value)} className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm font-mono" placeholder="0 3 * * *" />
                                        <div className="flex flex-wrap gap-1.5 mt-1.5">
                                            {[
                                                { label: 'Every 30 min', expr: '*/30 * * * *' },
                                                { label: 'Hourly', expr: '0 * * * *' },
                                                { label: 'Every 6h', expr: '0 */6 * * *' },
                                                { label: 'Daily 12 AM', expr: '0 0 * * *' },
                                                { label: 'Daily 3 AM', expr: '0 3 * * *' },
                                                { label: 'Weekly Sun', expr: '0 3 * * 0' },
                                            ].map(p => (
                                                <button key={p.expr} type="button" onClick={() => setCronExpression(p.expr)}
                                                    className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${cronExpression === p.expr ? 'bg-primary/10 border-primary text-primary' : 'bg-background border-border text-muted-foreground hover:border-primary/50'}`}>
                                                    {p.label}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">Retention (days)</label>
                                        <input type="number" value={retentionDays} onChange={(e) => setRetentionDays(parseInt(e.target.value) || 7)} min={1} max={365} className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm" />
                                        <p className="text-[10px] text-muted-foreground">Backups older than this are auto-deleted</p>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">Status</label>
                                        <label className="flex items-center gap-3 rounded-lg border border-border p-2.5 cursor-pointer">
                                            <input type="checkbox" checked={scheduleEnabled} onChange={(e) => setScheduleEnabled(e.target.checked)} className="w-4 h-4 rounded border-border" />
                                            <span className="text-sm font-medium">{scheduleEnabled ? 'Enabled' : 'Paused'}</span>
                                        </label>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">Storage Destination</label>
                                        <select value={scheduleCloudDest} onChange={(e) => setScheduleCloudDest(e.target.value)} className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm h-[42px]">
                                            <option value="">Local Server</option>
                                            {destinations.map(d => (
                                                <option key={d.id} value={d.id}>{d.name} ({d.provider_display})</option>
                                            ))}
                                        </select>
                                    </div>
                                </div>
                                <div className="flex items-center gap-4 pt-2 border-t border-border">
                                    <label className="flex items-center gap-2 text-sm cursor-pointer">
                                        <input type="checkbox" checked={scheduleCloudUpload} onChange={(e) => setScheduleCloudUpload(e.target.checked)} className="rounded border-gray-300" />
                                        <span>Auto-upload to cloud storage</span>
                                    </label>
                                    {schedule && (
                                        <span className="text-xs text-muted-foreground">
                                            Last run: {schedule.last_run ? new Date(schedule.last_run).toLocaleString() : 'Never'} &middot;
                                            Next run: {schedule.next_run ? new Date(schedule.next_run).toLocaleString() : 'TBD'}
                                        </span>
                                    )}
                                    <Button onClick={handleSaveSchedule} disabled={savingSchedule} className="ml-auto">
                                        {savingSchedule ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                        {schedule ? 'Update Schedule' : 'Create Schedule'}
                                    </Button>
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>

                {/* Snapshot Schedule */}
                <Card>
                    <CardContent className="p-6">
                        <h2 className="text-lg font-semibold mb-1">Snapshot Schedule</h2>
                        <p className="text-sm text-muted-foreground mb-4">Automated configuration snapshots for fast config rollback.</p>
                        {snapScheduleLoading ? (
                            <div className="flex justify-center p-4"><Loader2 className="animate-spin h-5 w-5 text-muted-foreground" /></div>
                        ) : (
                            <div className="space-y-4">
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">Cron Expression</label>
                                        <input type="text" value={snapCronExpression} onChange={(e) => setSnapCronExpression(e.target.value)} className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm font-mono" placeholder="0 3 * * *" />
                                        <div className="flex flex-wrap gap-1.5 mt-1.5">
                                            {[
                                                { label: 'Hourly', expr: '0 * * * *' },
                                                { label: 'Every 6h', expr: '0 */6 * * *' },
                                                { label: 'Daily 3 AM', expr: '0 3 * * *' },
                                                { label: 'Weekly Sun', expr: '0 3 * * 0' },
                                            ].map(p => (
                                                <button key={p.expr} type="button" onClick={() => setSnapCronExpression(p.expr)}
                                                    className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${snapCronExpression === p.expr ? 'bg-primary/10 border-primary text-primary' : 'bg-background border-border text-muted-foreground hover:border-primary/50'}`}>
                                                    {p.label}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">Retention (days)</label>
                                        <input type="number" value={snapRetentionDays} onChange={(e) => setSnapRetentionDays(parseInt(e.target.value) || 7)} min={1} max={365} className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm" />
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-sm font-medium">Status</label>
                                        <label className="flex items-center gap-3 rounded-lg border border-border p-2.5 cursor-pointer">
                                            <input type="checkbox" checked={snapScheduleEnabled} onChange={(e) => setSnapScheduleEnabled(e.target.checked)} className="w-4 h-4 rounded border-border" />
                                            <span className="text-sm font-medium">{snapScheduleEnabled ? 'Enabled' : 'Paused'}</span>
                                        </label>
                                    </div>
                                </div>
                                <div className="flex items-center gap-4 pt-2 border-t border-border">
                                    <label className="flex items-center gap-2 text-sm cursor-pointer">
                                        <input type="checkbox" checked={snapScheduleCloudUpload} onChange={(e) => setSnapScheduleCloudUpload(e.target.checked)} className="rounded border-gray-300" />
                                        <span>Auto-upload snapshots to cloud</span>
                                    </label>
                                    <Button onClick={handleSaveSnapSchedule} disabled={savingSnapSchedule} className="ml-auto">
                                        {savingSnapSchedule ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                        {snapSchedule ? 'Update Schedule' : 'Create Schedule'}
                                    </Button>
                                </div>
                            </div>
                        )}
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
                            JSON format: {`{ "key_material": "<your-fernet-key>" }`}
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

                            {cloudRestoreForm.cloud_storage_id && cloudRestoreForm.cloud_storage_id !== 'custom' ? (
                                <>
                                    <div className="space-y-1">
                                        <label className="text-xs font-medium text-muted-foreground">Backup File</label>
                                        {cloudBackupListLoading ? (
                                            <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                                                <Loader2 className="h-4 w-4 animate-spin" /> Loading backups...
                                            </div>
                                        ) : cloudBackupList.length > 0 ? (
                                            <>
                                                <Select
                                                    value={cloudRestoreForm.s3_key}
                                                    onValueChange={(val) => setCloudRestoreForm({ ...cloudRestoreForm, s3_key: val })}
                                                >
                                                    <SelectTrigger>
                                                        <SelectValue placeholder="Select a backup file..." />
                                                    </SelectTrigger>
                                                    <SelectContent className="max-h-[300px]">
                                                        {cloudBackupList.map((obj: any) => (
                                                            <SelectItem key={obj.key} value={obj.key}>
                                                                <span className="font-mono text-xs">{obj.key}</span>
                                                                <span className="ml-2 text-[10px] text-muted-foreground">
                                                                    ({(obj.size / 1024 / 1024).toFixed(1)} MB)
                                                                </span>
                                                            </SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                                <button
                                                    type="button"
                                                    onClick={() => setCloudBackupList([])}
                                                    className="text-xs text-blue-500 hover:underline"
                                                >
                                                    Or type the key manually
                                                </button>
                                            </>
                                        ) : (
                                            <p className="text-xs text-amber-500">No backups found. Try a different prefix or type manually below.</p>
                                        )}
                                    </div>

                                    {cloudBackupList.length === 0 && (
                                        <Input
                                            placeholder="Object Key (e.g., smsly-backups/server/backup.tar.gz)"
                                            value={cloudRestoreForm.s3_key}
                                            onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, s3_key: e.target.value })}
                                        />
                                    )}

                                    <div className="flex gap-2">
                                        <Input
                                            placeholder="Prefix filter (e.g., smsly-backups/)"
                                            value={cloudBackupPrefix}
                                            onChange={(e) => setCloudBackupPrefix(e.target.value)}
                                            className="flex-1"
                                        />
                                    </div>
                                </>
                            ) : (
                                <>
                                    <Input
                                        placeholder="Object Key (e.g., smsly-backups/server/backup.tar.gz)"
                                        value={cloudRestoreForm.s3_key}
                                        onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, s3_key: e.target.value })}
                                    />
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
                                disabled={
                                    uploading ||
                                    !cloudRestoreForm.s3_key ||
                                    (!cloudRestoreForm.cloud_storage_id || cloudRestoreForm.cloud_storage_id === 'custom'
                                        ? (!cloudRestoreForm.s3_bucket || !cloudRestoreForm.s3_access_key || !cloudRestoreForm.s3_secret_key)
                                        : false)
                                }
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
