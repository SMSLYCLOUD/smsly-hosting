'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Loader2, Download, RotateCcw, Trash2, Plus, Clock, Save, AlertCircle, CheckCircle, FileKey, Key, GitCompare, Cloud, ShieldCheck, Upload, History } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import api, { servicesApi } from '@/lib/api';
import { backupsApi } from '@/lib/api';
import { getWsUrl } from '@/lib/websocket';
import { useConfirm } from '@/components/ui/confirm-dialog';

interface Schedule {
    id: number;
    cron_expression: string;
    retention_days: number;
    enabled: boolean;
    last_run: string | null;
    next_run: string | null;
    storage_backend: string;
    s3_bucket?: string;
    cloud_destination_id?: string;
}

interface CloudDestination {
    id: string;
    name: string;
    provider_display: string;
    bucket: string;
}

export default function BackupsTab({ serviceId }: { serviceId: string }) {
    const { toast } = useToast();
    const confirm = useConfirm();
    const [backups, setBackups] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [creating, setCreating] = useState(false);
    
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
    const [cloudBackupList, setCloudBackupList] = useState<any[]>([]);
    const [cloudBackupListLoading, setCloudBackupListLoading] = useState(false);
    const [cloudBackupPrefix, setCloudBackupPrefix] = useState('smsly-backups/');

    // Fetch cloud backup list when destination changes
    useEffect(() => {
        if (cloudRestorePromptOpen && cloudRestoreForm.cloud_storage_id && cloudRestoreForm.cloud_storage_id !== 'custom') {
            setCloudBackupListLoading(true);
            api.post('/backups/list-backups/', {
                cloud_storage_id: cloudRestoreForm.cloud_storage_id,
                prefix: cloudBackupPrefix,
                service_id: serviceId,
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
    }, [cloudRestorePromptOpen, cloudRestoreForm.cloud_storage_id, cloudBackupPrefix, serviceId]);

    // Encryption key prompt (shown when restoring a cross-master backup)
    const [keyPromptOpen, setKeyPromptOpen] = useState(false);
    const [keyPromptValue, setKeyPromptValue] = useState('');
    const [keyPromptBackupId, setKeyPromptBackupId] = useState<string | null>(null);
    const [keyPromptError, setKeyPromptError] = useState<string>('');
    const [keyPromptSubmitting, setKeyPromptSubmitting] = useState(false);
    const [keyPromptSaveForFuture, setKeyPromptSaveForFuture] = useState(false);
    const [keyPromptKeyId, setKeyPromptKeyId] = useState('');
    const [storedKeys, setStoredKeys] = useState<any[]>([]);

    // Pre-restore snapshot override dialog
    const [snapOverrideOpen, setSnapOverrideOpen] = useState(false);
    const [snapOverrideBackupId, setSnapOverrideBackupId] = useState<string | null>(null);
    const [snapOverrideError, setSnapOverrideError] = useState('');
    const [snapOverrideRemediation, setSnapOverrideRemediation] = useState('');
    const [snapOverrideSubmitting, setSnapOverrideSubmitting] = useState(false);
    const [snapOverrideIsCloud, setSnapOverrideIsCloud] = useState(false);
    const snapOverrideCloudFormRef = useRef<any>(null);
    const snapOverrideOpenRef = useRef(false);

    // Restore progress tracking
    const [restoringId, setRestoringId] = useState<string | null>(null);
    const [restoreStatus, setRestoreStatus] = useState<string>('');
    const deploymentStatusRef = useRef<string>('');
    const deployPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const deployTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [deploymentStatus, setDeploymentStatus] = useState<string>('');
    const [deploymentProgress, setDeploymentProgress] = useState<number>(0);
    const [deploymentLogs, setDeploymentLogs] = useState<string>('');
    const [isLiveDeploying, setIsLiveDeploying] = useState(false);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimer = useRef<NodeJS.Timeout | null>(null);

    // Backup progress WebSocket
    const progressWsRef = useRef<WebSocket | null>(null);
    const [backupProgress, setBackupProgress] = useState<{
        stage: string; percent: number; message: string;
        bytes_transferred?: number; total_bytes?: number;
    } | null>(null);
    const [progressLog, setProgressLog] = useState<string[]>([]);

    // Verify state
    const [verifying, setVerifying] = useState<string | null>(null);

    // Restoration history
    const [restoreHistory, setRestoreHistory] = useState<any[]>([]);
    const [showRestoreHistory, setShowRestoreHistory] = useState(false);
    const [restoreHistoryLoading, setRestoreHistoryLoading] = useState(false);

    // Local file upload restore
    const [uploadRestoreOpen, setUploadRestoreOpen] = useState(false);
    const [uploadRestoreFile, setUploadRestoreFile] = useState<File | null>(null);
    const [uploadRestoreLoading, setUploadRestoreLoading] = useState(false);

    // Schedule state
    const [schedule, setSchedule] = useState<Schedule | null>(null);
    const [scheduleLoading, setScheduleLoading] = useState(true);
    const [cronExpression, setCronExpression] = useState('0 3 * * *');
    const [retentionDays, setRetentionDays] = useState(7);
    const [scheduleEnabled, setScheduleEnabled] = useState(true);
    const [scheduleDbOnly, setScheduleDbOnly] = useState(false);
    const [scheduleCloudUpload, setScheduleCloudUpload] = useState(true);
    const [savingSchedule, setSavingSchedule] = useState(false);
    const [dbOnly, setDbOnly] = useState(false);
    const [backupLabel, setBackupLabel] = useState('');
    
    // Cloud storage state
    const [destinations, setDestinations] = useState<CloudDestination[]>([]);
    const [selectedDestination, setSelectedDestination] = useState<string>('local');

    // Snapshot state
    const [snapshots, setSnapshots] = useState<any[]>([]);
    const [snapshotsLoading, setSnapshotsLoading] = useState(true);
    const [creatingSnapshot, setCreatingSnapshot] = useState(false);
    const [showCreateSnapshotDialog, setShowCreateSnapshotDialog] = useState(false);
    const [snapshotLabel, setSnapshotLabel] = useState('');
    const [diffingSnapshot, setDiffingSnapshot] = useState<any | null>(null);
    const [diffResults, setDiffResults] = useState<any | null>(null);
    const [diffLoading, setDiffLoading] = useState(false);
    const [compareSnapshotId, setCompareSnapshotId] = useState('');

    // Snapshot Schedule state
    const [snapCronExpression, setSnapCronExpression] = useState('0 3 * * *');
    const [snapRetentionDays, setSnapRetentionDays] = useState(30);
    const [snapScheduleCloudDest, setSnapScheduleCloudDest] = useState('');
    const [snapScheduleEnabled, setSnapScheduleEnabled] = useState(true);
    const [snapScheduleCloudUpload, setSnapScheduleCloudUpload] = useState(true);
    const [savingSnapSchedule, setSavingSnapSchedule] = useState(false);

    const loadBackups = useCallback(async () => {
        try {
            const res = await api.get(`/services/${serviceId}/backups/`);
            setBackups(Array.isArray(res.data) ? res.data : res.data.results || []);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    const loadSnapshots = useCallback(async () => {
        try {
            const res = await api.get(`/services/${serviceId}/snapshots/`);
            setSnapshots(Array.isArray(res.data) ? res.data : res.data.results || []);
        } catch (err) {
            console.error(err);
        } finally {
            setSnapshotsLoading(false);
        }
    }, [serviceId]);

    const loadSchedule = useCallback(async () => {
        try {
            const res = await api.get('/backup-schedules/', { params: { service: serviceId } });
            const schedules = Array.isArray(res.data) ? res.data : res.data.results || [];
            const sched = schedules.find((s: any) => String(s.service) === serviceId);
            if (sched) {
                setSchedule(sched);
                setCronExpression(sched.cron_expression);
                setRetentionDays(sched.retention_days);
                setScheduleEnabled(sched.enabled);
                setScheduleDbOnly(sched.db_only || false);
                setScheduleCloudUpload(sched.cloud_upload_enabled !== false);
                if (sched.cloud_destination) {
                    setSelectedDestination(sched.cloud_destination);
                }
            }
        } catch (err) {
            console.error(err);
        } finally {
            setScheduleLoading(false);
        }
    }, [serviceId]);

    const loadSnapSchedule = useCallback(async () => {
        try {
            const res = await api.get('/snapshot-schedules/', { params: { service: serviceId } });
            const schedules = Array.isArray(res.data) ? res.data : res.data.results || [];
            const sched = schedules.find((s: any) => String(s.service) === serviceId);
            if (sched) {
                setSnapCronExpression(sched.cron_expression);
                setSnapRetentionDays(sched.retention_days);
                setSnapScheduleEnabled(sched.enabled);
                setSnapScheduleCloudUpload(sched.cloud_upload_enabled !== false);
            }
        } catch (err) {
            console.error('Failed to load snapshot schedule', err);
        }
    }, [serviceId]);

    const loadDestinations = useCallback(async () => {
        try {
            const res = await api.get(`/cloud-storage/?service=${serviceId}`);
            setDestinations(Array.isArray(res.data) ? res.data : res.data.results || []);
        } catch (err) {
            console.error('Failed to load cloud destinations', err);
        }
    }, [serviceId]);

    useEffect(() => {
        void loadBackups();
        void loadSchedule();
        void loadSnapSchedule();
        void loadSnapshots();
        void loadDestinations();
    }, [loadBackups, loadSchedule, loadSnapSchedule, loadSnapshots, loadDestinations]);

    const handleCreateBackup = async () => {
        setCreating(true);
        try {
            const res = await api.post('/backups/', { service: serviceId, db_only: dbOnly, label: backupLabel || undefined });
            toast({ title: "Backup Started", description: "This may take a few minutes." });
            setBackupLabel('');
            if (res.data?.id) {
                connectBackupProgressWebSocket(res.data.id);
            }
        } catch (err) {
            toast({ title: "Error", description: "Failed to start backup.", variant: "destructive" });
        } finally {
            setCreating(false);
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

        if (!await confirm({ title: 'Restore from Cloud?', message: `Restore from ${bucketStr}/${cloudRestoreForm.s3_key}? This will overwrite the current service state.`, variant: 'destructive', confirmText: 'Restore' })) {
            return;
        }

        setCreating(true);
        setCloudRestorePromptOpen(false);
        try {
            const res = await api.post('/backups/restore-from-cloud/', { ...cloudRestoreForm, service_id: serviceId });
            toast({ title: "Restore Started", description: `Restoring from cloud backup.` });
            
            // Connect WebSocket for real-time updates and monitor status
            if (res.data?.backup_id) {
                setRestoringId(res.data.backup_id);
                setRestoreStatus('RESTORING');
                monitorDeploymentAfterRestore(res.data.backup_id);
                connectWebSocket(res.data.backup_id);
            } else {
                loadBackups();
            }
        } catch (err: any) {
            const data = err?.response?.data;
            const status = err?.response?.status;
            if (status === 422 && data?.snapshot_error) {
                snapOverrideCloudFormRef.current = { ...cloudRestoreForm, service_id: serviceId };
                snapOverrideOpenRef.current = true;
                setSnapOverrideBackupId(data.backup_id || null);
                setSnapOverrideError(data.snapshot_error);
                setSnapOverrideRemediation(data.remediation || '');
                setSnapOverrideOpen(true);
                setSnapOverrideIsCloud(true);
                setCreating(false);
                return;
            }
            const msg = data?.error || "Failed to download and restore backup.";
            toast({ title: "Restore Failed", description: msg, variant: "destructive" });
        } finally {
            setCreating(false);
            if (!snapOverrideOpenRef.current) {
                setCloudRestoreForm({
                    cloud_storage_id: '', s3_bucket: '', s3_key: '', s3_endpoint: '', s3_region: 'us-east-1',
                    s3_access_key: '', s3_secret_key: '', encryption_key: ''
                });
            }
        }
    };

    const loadStoredKeys = async () => {
        try {
            const keys = await backupsApi.listKeys('service');
            setStoredKeys(keys);
        } catch {
            setStoredKeys([]);
        }
    };

    const handleRestore = async (id: string, encryptionKey?: string, keyId?: string) => {
        if (!await confirm({ title: 'Restore backup?', message: 'Are you sure? This will overwrite the current service state.', variant: 'destructive', confirmText: 'Restore' })) return;

        setRestoringId(id);
        setRestoreStatus('RESTORING');
        setDeploymentStatus('');
        setDeploymentProgress(0);
        setDeploymentLogs('');

        try {
            const body: any = { confirm: true };
            if (keyId) {
                body.key_id = keyId;
            } else if (encryptionKey) {
                body.encryption_key = encryptionKey;
            }
            await api.post(`/backups/${id}/restore/`, body);
            toast({ title: "Restore Started", description: "Service will restart once restored. Monitoring deployment progress..." });

            // Start monitoring deployment status
            monitorDeploymentAfterRestore(id);

            // Connect WebSocket for real-time updates
            connectWebSocket(id);
            connectBackupProgressWebSocket(id);

        } catch (err: any) {
            const data = err?.response?.data;
            const status = err?.response?.status;
            // If the backup needs an encryption key, show the key prompt
            if (data?.error_code === 'ENCRYPTION_KEY_REQUIRED') {
                setKeyPromptBackupId(id);
                setKeyPromptValue('');
                setKeyPromptKeyId(data?.key_id || '');
                setKeyPromptError(data?.error || 'Encryption key required');
                setKeyPromptSaveForFuture(false);
                setKeyPromptOpen(true);
                setRestoringId(null);
                setRestoreStatus('');
                loadStoredKeys();
                return;
            }
            // If pre-restore snapshot failed, show override dialog
            if (status === 422 && data?.snapshot_error) {
                setSnapOverrideBackupId(id);
                setSnapOverrideError(data.snapshot_error);
                setSnapOverrideRemediation(data.remediation || '');
                setSnapOverrideOpen(true);
                setSnapOverrideIsCloud(false);
                setRestoringId(null);
                setRestoreStatus('');
                return;
            }
            const msg = data?.error || 'Failed to trigger restore.';
            toast({ title: "Error", description: msg, variant: "destructive" });
            setRestoringId(null);
            setRestoreStatus('');
        }
    };

    const submitEncryptionKey = async () => {
        const usingStoredKey = keyPromptKeyId && keyPromptValue === '__imported__';
        if (!keyPromptBackupId || (!keyPromptValue.trim() && !usingStoredKey)) {
            setKeyPromptError('Please select a stored key or enter the encryption key.');
            return;
        }
        setKeyPromptSubmitting(true);
        setKeyPromptError('');
        try {
            if (keyPromptSaveForFuture && keyPromptKeyId && !usingStoredKey) {
                try {
                    await backupsApi.importKey('service', {
                        key_id: keyPromptKeyId,
                        key_material: keyPromptValue.trim(),
                        label: 'Imported from cross-master restore',
                    });
                    toast({ title: 'Key saved', description: 'Encryption key imported.' });
                } catch (importErr: any) {
                    if (importErr?.response?.status === 403) {
                        toast({ title: 'Admin only', description: 'Key import requires superuser.', variant: 'default' });
                    } else {
                        toast({ title: 'Import failed', description: 'Key will be used for this restore only.', variant: 'default' });
                    }
                }
            }
            if (usingStoredKey) {
                await handleRestore(keyPromptBackupId, undefined, keyPromptKeyId);
            } else {
                await handleRestore(keyPromptBackupId, keyPromptValue.trim());
            }
            setKeyPromptOpen(false);
        } catch {
            // handleRestore already shows the toast on error
        } finally {
            setKeyPromptSubmitting(false);
        }
    };

    const submitSnapOverride = async () => {
        if (!snapOverrideBackupId) return;
        setSnapOverrideSubmitting(true);
        try {
            if (snapOverrideIsCloud && snapOverrideCloudFormRef.current) {
                const res = await api.post('/backups/restore-from-cloud/', {
                    ...snapOverrideCloudFormRef.current, force: true,
                });
                if (res.data?.backup_id) {
                    setRestoringId(res.data.backup_id);
                    connectBackupProgressWebSocket(res.data.backup_id);
                    monitorDeploymentAfterRestore(res.data.backup_id);
                }
            } else {
                await api.post(`/backups/${snapOverrideBackupId}/restore/`, {
                    confirm: true, force: true,
                });
                setRestoringId(snapOverrideBackupId);
                setRestoreStatus('RESTORING');
                connectBackupProgressWebSocket(snapOverrideBackupId);
                connectWebSocket(snapOverrideBackupId);
                monitorDeploymentAfterRestore(snapOverrideBackupId);
            }
            toast({ title: "Restore Started", description: "Proceeding without safety snapshot." });
            setSnapOverrideOpen(false);
            snapOverrideOpenRef.current = false;
            snapOverrideCloudFormRef.current = null;
        } catch (err: any) {
            toast({ title: "Restore Failed", description: err?.response?.data?.error || 'Failed to trigger restore.', variant: "destructive" });
        } finally {
            setSnapOverrideSubmitting(false);
        }
    };

    const monitorDeploymentAfterRestore = async (backupId: string) => {
        if (deployPollRef.current) clearInterval(deployPollRef.current);
        if (deployTimeoutRef.current) clearTimeout(deployTimeoutRef.current);

        deploymentStatusRef.current = '';
        const pollInterval = setInterval(async () => {
            try {
                const res = await api.get(`/services/${serviceId}/`);
                const service = res.data;
                
                if (service.latest_deployment) {
                    const deployment = service.latest_deployment;
                    
                    if (deployment.status === 'BUILDING' || deployment.status === 'DEPLOYING') {
                        deploymentStatusRef.current = 'DEPLOYING';
                        setDeploymentStatus('DEPLOYING');
                        setDeploymentProgress(calculateProgress(deployment.status));
                        setRestoreStatus('RESTORED');
                        setIsLiveDeploying(true);
                    } else if (deployment.status === 'ACTIVE') {
                        deploymentStatusRef.current = 'COMPLETED';
                        setDeploymentStatus('COMPLETED');
                        setDeploymentProgress(100);
                        setIsLiveDeploying(false);
                        clearInterval(pollInterval);
                        deployPollRef.current = null;
                        toast({ title: "Restore Completed", description: "Service has been successfully restored and deployed." });
                        loadBackups();
                    } else if (deployment.status === 'FAILED') {
                        deploymentStatusRef.current = 'FAILED';
                        setDeploymentStatus('FAILED');
                        setIsLiveDeploying(false);
                        clearInterval(pollInterval);
                        deployPollRef.current = null;
                        toast({ title: "Restore Failed", description: "Deployment failed. Check service logs for details.", variant: "destructive" });
                    }
                }
            } catch (err) {
                console.error('Error monitoring deployment:', err);
            }
        }, 3000);
        deployPollRef.current = pollInterval;
        
        const timeoutId = setTimeout(() => {
            if (deployPollRef.current) {
                clearInterval(deployPollRef.current);
                deployPollRef.current = null;
            }
            if (deploymentStatusRef.current !== 'COMPLETED' && deploymentStatusRef.current !== 'FAILED') {
                setRestoreStatus('TIMEOUT');
                setDeploymentStatus('TIMEOUT');
                setIsLiveDeploying(false);
                toast({ title: "Restore Monitoring Timeout", description: "Restore process may still be running. Check service status manually.", variant: "destructive" });
            }
        }, 300000);
        deployTimeoutRef.current = timeoutId;
    };

    const calculateProgress = (status: string): number => {
        switch (status) {
            case 'QUEUED':
            case 'PENDING':
                return 10;
            case 'BUILDING':
                return 30;
            case 'DEPLOYING':
                return 70;
            case 'ACTIVE':
                return 100;
            default:
                return 0;
        }
    };

    const connectWebSocket = (deploymentId: string) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        // Auth for the build-logs WebSocket is provided by the
        // HttpOnly auth cookie that the browser attaches to the
        // WebSocket upgrade request. The server's
        // QueryStringAuthMiddleware reads the cookie directly from
        // the Cookie header (no token in the query string) — see
        // backend/apps/deployments/middleware.py for the matching
        // server-side change.

        const wsUrl = getWsUrl(`/ws/build-logs/${deploymentId}/`);

        try {
            const ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                // Connection established — server will start streaming build logs.
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'build_log') {
                        setDeploymentLogs(prev => prev + (data.log || ''));
                    } else if (data.type === 'status_change') {
                        setDeploymentStatus(data.status);
                        if (data.status === 'ACTIVE' || data.status === 'FAILED') {
                            setIsLiveDeploying(false);
                        }
                    }
                } catch {
                    // Non-JSON message, ignore
                }
            };

            ws.onclose = () => {
                // Don't reconnect automatically, let the polling handle it
            };

            ws.onerror = () => {
                ws.close();
            };

            wsRef.current = ws;
        } catch (error) {
            console.error('WebSocket connection failed:', error);
        }
    };

    const cleanupWebSocket = () => {
        if (wsRef.current) {
            wsRef.current.close();
            wsRef.current = null;
        }
        if (reconnectTimer.current) {
            clearTimeout(reconnectTimer.current);
            reconnectTimer.current = null;
        }
    };

    const connectBackupProgressWebSocket = (backupId: string) => {
        if (progressWsRef.current?.readyState === WebSocket.OPEN) return;

        const wsUrl = getWsUrl(`/ws/backup-progress/${backupId}/`);

        try {
            const ws = new WebSocket(wsUrl);
            setProgressLog([]);
            setBackupProgress({ stage: 'connecting', percent: 0, message: 'Connecting...' });

            ws.onopen = () => {
                setBackupProgress({ stage: 'connected', percent: 0, message: 'Waiting for progress...' });
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'backup_progress') {
                        setBackupProgress({
                            stage: data.stage,
                            percent: data.percent ?? 0,
                            message: data.message ?? '',
                            bytes_transferred: data.bytes_transferred,
                            total_bytes: data.total_bytes,
                        });
                        const timeStr = data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : '';
                        const logLine = `[${timeStr}] ${data.stage}: ${data.message || ''}`;
                        setProgressLog(prev => [...prev.slice(-200), logLine]);
                    }
                } catch {
                    // Ignore non-JSON messages
                }
            };

            ws.onerror = () => {
                ws.close();
            };

            ws.onclose = () => {
                progressWsRef.current = null;
            };

            progressWsRef.current = ws;
        } catch (error) {
            console.error('Backup progress WebSocket failed:', error);
        }
    };

    const disconnectBackupProgressWebSocket = () => {
        if (progressWsRef.current) {
            progressWsRef.current.close();
            progressWsRef.current = null;
        }
    };

    useEffect(() => {
        // Cleanup on unmount
        return () => {
            cleanupWebSocket();
            if (deployPollRef.current) {
                clearInterval(deployPollRef.current);
                deployPollRef.current = null;
            }
            if (deployTimeoutRef.current) {
                clearTimeout(deployTimeoutRef.current);
                deployTimeoutRef.current = null;
            }
        };
    }, []);

    const handleDeleteBackup = async (id: string) => {
        if (!await confirm({ title: 'Delete backup?', message: 'This backup will be permanently deleted.', variant: 'destructive', confirmText: 'Delete' })) return;
        try {
            await api.delete(`/backups/${id}/`);
            toast({ title: "Backup deleted" });
            // Clear restore state if deleting the backup being restored
            if (restoringId === id) {
                setRestoringId(null);
                setRestoreStatus('');
                setDeploymentStatus('');
                setDeploymentProgress(0);
                setDeploymentLogs('');
                setIsLiveDeploying(false);
                cleanupWebSocket();
            }
            loadBackups();
        } catch (err) {
            toast({ title: "Error", description: "Failed to delete backup.", variant: "destructive" });
        }
    };

    const handleVerifyBackup = async (id: string) => {
        setVerifying(id);
        try {
            const res = await api.post(`/backups/${id}/verify/`);
            toast({ title: "Verification Started", description: `Task ID: ${res.data?.task_id?.slice(0, 8)}...` });
        } catch (err: any) {
            toast({ title: "Verification Failed", description: err?.response?.data?.error || 'Could not start verification.', variant: "destructive" });
        } finally {
            setVerifying(null);
        }
    };

    const handleDownloadSnapshot = async (snapshot: any) => {
        try {
            const res = await api.get(`/snapshots/${snapshot.id}/download/`);
            const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `snapshot-${snapshot.id?.slice(0, 8) || 'config'}-${new Date(snapshot.created_at).toISOString().split('T')[0]}.json`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            toast({ title: "Snapshot downloaded" });
        } catch (err: any) {
            toast({ title: "Download failed", description: err?.response?.data?.error || 'Could not download snapshot.', variant: "destructive" });
        }
    };

    const handleUploadRestoreSubmit = async () => {
        if (!uploadRestoreFile) return;
        setUploadRestoreLoading(true);
        try {
            const formData = new FormData();
            formData.append('file', uploadRestoreFile);
            formData.append('service_id', serviceId);
            const res = await api.post('/backups/upload-restore/', formData);
            toast({ title: "Restore Started", description: `Restoring from ${uploadRestoreFile.name}.` });
            setUploadRestoreOpen(false);
            setUploadRestoreFile(null);
            if (res.data?.backup_id) {
                setRestoringId(res.data.backup_id);
                setRestoreStatus('RESTORING');
                monitorDeploymentAfterRestore(res.data.backup_id);
                connectWebSocket(res.data.backup_id);
            }
        } catch (err: any) {
            toast({ title: "Upload Restore Failed", description: err?.response?.data?.error || 'Failed to process uploaded backup.', variant: "destructive" });
        } finally {
            setUploadRestoreLoading(false);
        }
    };

    const loadRestoreHistory = async () => {
        setRestoreHistoryLoading(true);
        setShowRestoreHistory(true);
        try {
            const res = await api.get('/backups/restore-history/', { params: { limit: 20 } });
            setRestoreHistory(Array.isArray(res.data) ? res.data : res.data?.results || []);
        } catch {
            setRestoreHistory([]);
        } finally {
            setRestoreHistoryLoading(false);
        }
    };

    const handleSaveSchedule = async () => {
        setSavingSchedule(true);
        try {
            const payload: any = {
                service: serviceId,
                cron_expression: cronExpression,
                retention_days: retentionDays,
                enabled: scheduleEnabled,
                db_only: scheduleDbOnly,
                cloud_upload_enabled: scheduleCloudUpload,
            };
            
            if (selectedDestination !== 'local') {
                payload.cloud_destination_id = selectedDestination;
            } else {
                payload.storage_backend = 'local';
                payload.cloud_destination_id = null;
            }
            if (schedule?.id) {
                await api.patch(`/backup-schedules/${schedule.id}/`, payload);
            } else {
                await api.post('/backup-schedules/', payload);
            }
            toast({ title: "Schedule saved", description: "Backup schedule has been updated." });
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
            const payload: any = {
                service: serviceId,
                cron_expression: snapCronExpression,
                retention_days: snapRetentionDays,
                enabled: snapScheduleEnabled,
                cloud_upload_enabled: snapScheduleCloudUpload,
            };
            if (snapScheduleCloudDest) {
                payload.cloud_destination_id = snapScheduleCloudDest;
            } else {
                payload.cloud_destination_id = null;
            }
            // check if schedule exists
            const res = await api.get('/snapshot-schedules/', { params: { service: serviceId } });
            const schedules = Array.isArray(res.data) ? res.data : res.data.results || [];
            const sched = schedules.find((s: any) => String(s.service) === serviceId);

            if (sched?.id) {
                await api.patch(`/snapshot-schedules/${sched.id}/`, payload);
            } else {
                await api.post('/snapshot-schedules/', payload);
            }
            toast({ title: "Schedule saved", description: "Snapshot schedule has been updated." });
            loadSnapSchedule();
        } catch (err) {
            toast({ title: "Error", description: "Failed to save snapshot schedule.", variant: "destructive" });
        } finally {
            setSavingSnapSchedule(false);
        }
    };

    const handleCreateSnapshot = async () => {
        setCreatingSnapshot(true);
        try {
            await api.post(`/services/${serviceId}/snapshots/`, {
                service: serviceId,
                label: snapshotLabel,
                trigger: 'MANUAL'
            });
            toast({ title: "Snapshot Created", description: "Config snapshot captured successfully." });
            setSnapshotLabel('');
            setShowCreateSnapshotDialog(false);
            loadSnapshots();
        } catch (err) {
            toast({ title: "Error", description: "Failed to create snapshot.", variant: "destructive" });
        } finally {
            setCreatingSnapshot(false);
        }
    };

    const handleDeleteSnapshot = async (id: string) => {
        if (!await confirm({ title: 'Delete snapshot?', message: 'This snapshot will be permanently deleted.', variant: 'destructive', confirmText: 'Delete' })) return;
        try {
            await api.delete(`/snapshots/${id}/`);
            toast({ title: "Snapshot deleted" });
            loadSnapshots();
        } catch (err) {
            toast({ title: "Error", description: "Failed to delete snapshot.", variant: "destructive" });
        }
    };

    const handleRestoreSnapshot = async (id: string) => {
        if (!await confirm({
            title: 'Restore Snapshot?',
            message: 'Restore this configuration snapshot? This will overwrite the current configuration settings.',
            variant: 'destructive',
            confirmText: 'Restore'
        })) return;

        const redeploy = await confirm({
            title: 'Redeploy Service?',
            message: 'Would you like to trigger an immediate redeployment of the service with this restored configuration?',
            variant: 'default',
            confirmText: 'Yes, redeploy',
            cancelText: 'No, just restore config'
        });

        try {
            const res = await api.post(`/snapshots/${id}/restore/`, {
                confirm: true,
                redeploy: redeploy
            });
            toast({
                title: "Snapshot Restored",
                description: `Successfully restored configuration.${redeploy ? ' Redeployment triggered.' : ''}`
            });
            if (redeploy) {
                monitorDeploymentAfterRestore(id);
                setRestoringId(id);
                setRestoreStatus('RESTORED');
                setIsLiveDeploying(true);
            }
        } catch (err: any) {
            const msg = err?.response?.data?.error || 'Failed to restore snapshot.';
            toast({ title: "Error", description: msg, variant: "destructive" });
        }
    };

    const handleDiffSnapshot = async (snapshotA: any, snapshotBId: string) => {
        setDiffLoading(true);
        try {
            const res = await api.post(`/snapshots/${snapshotA.id}/diff/`, {
                compare_with_id: snapshotBId
            });
            setDiffResults(res.data);
        } catch (err: any) {
            toast({
                title: "Error",
                description: err?.response?.data?.error || "Failed to compare snapshots.",
                variant: "destructive"
            });
        } finally {
            setDiffLoading(false);
        }
    };

    const formatBytes = (bytes: number) => {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
    };

    if (loading) return <div className="flex justify-center p-8"><Loader2 className="animate-spin" /></div>;

    return (
        <>
        <div className="space-y-6">
            <Card>
                <CardHeader>
                    <div className="flex justify-between items-center">
                        <div>
                            <CardTitle>Backups</CardTitle>
                            <CardDescription>Snapshots of your service container, volumes, and configuration.</CardDescription>
                        </div>
                        <div className="flex items-center gap-4">
                            <label className="flex items-center space-x-2 text-sm font-medium cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={dbOnly}
                                    onChange={(e) => setDbOnly(e.target.checked)}
                                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                    disabled={creating}
                                />
                                <span>Database Only</span>
                            </label>
                            <Button variant="outline" onClick={() => setCloudRestorePromptOpen(true)} disabled={creating}>
                                <Cloud className="mr-2 h-4 w-4" />
                                Restore from Cloud
                            </Button>
                            <Button variant="outline" onClick={() => setUploadRestoreOpen(true)} disabled={creating}>
                                <Upload className="mr-2 h-4 w-4" />
                                Restore from Local
                            </Button>
                            <Button variant="outline" onClick={loadRestoreHistory} disabled={restoreHistoryLoading}>
                                {restoreHistoryLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <History className="mr-2 h-4 w-4" />}
                                Restore History
                            </Button>
                            <Input
                                placeholder="Label (optional)"
                                value={backupLabel}
                                onChange={(e) => setBackupLabel(e.target.value)}
                                className="w-40 h-9 text-xs"
                                disabled={creating}
                            />
                            <Button onClick={handleCreateBackup} disabled={creating}>
                                {creating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                                Create Backup
                            </Button>
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Date</TableHead>
                                <TableHead>Type</TableHead>
                                <TableHead>Label</TableHead>
                                <TableHead>Size</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {backups.map(backup => (
                                <TableRow key={backup.id}>
                                    <TableCell>{new Date(backup.created_at).toLocaleString()}</TableCell>
                                    <TableCell>
                                        <div className="flex items-center gap-2">
                                            <span className="text-xs font-mono bg-muted px-2 py-1 rounded">{backup.backup_type}</span>
                                            {backup.db_only && <span className="text-xs bg-blue-100 text-blue-800 px-2 py-1 rounded border border-blue-200">DB Only</span>}
                                        </div>
                                    </TableCell>
                                    <TableCell className="max-w-[160px]">
                                        {backup.label ? (
                                            <span className="text-xs truncate block" title={backup.label}>{backup.label}</span>
                                        ) : (
                                            <span className="text-[10px] text-muted-foreground italic">—</span>
                                        )}
                                    </TableCell>
                                    <TableCell>{formatBytes(backup.size_bytes)}</TableCell>
                                    <TableCell>
                                        <div className="space-y-1">
                                            <span className={`text-xs font-semibold px-2 py-1 rounded ${
                                                backup.status === 'COMPLETED' ? 'bg-emerald-500/10 text-emerald-500' :
                                                backup.status === 'FAILED' ? 'bg-red-500/10 text-red-500' :
                                                backup.status === 'IN_PROGRESS' ? 'bg-blue-500/10 text-blue-500' :
                                                'bg-yellow-500/10 text-yellow-500'
                                            }`}>
                                                {backup.status}
                                            </span>
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
                                    <TableCell className="text-right space-x-1">
                                        {backup.status === 'COMPLETED' && (
                                            <>
                                                <Button 
                                                    variant="ghost" 
                                                    size="sm" 
                                                    onClick={() => handleRestore(backup.id)} 
                                                    title="Restore"
                                                    disabled={restoringId === backup.id}
                                                >
                                                    {restoringId === backup.id ? (
                                                        <Loader2 className="w-4 h-4 animate-spin" />
                                                    ) : (
                                                        <RotateCcw className="w-4 h-4" />
                                                    )}
                                                </Button>
                                                <Button variant="ghost" size="sm" onClick={async () => {
                                                    try {
                                                        const res = await api.get(`/backups/${backup.id}/header/`);
                                                        if (res.data?.key_id && res.data?.fingerprint) {
                                                            const text = `key_id=${res.data.key_id}\nfingerprint=${res.data.fingerprint}`;
                                                            await navigator.clipboard.writeText(text);
                                                            toast({ title: 'V2 header copied', description: text });
                                                        } else {
                                                            toast({
                                                                title: 'Not a V2 backup',
                                                                description: res.data?.error || 'This backup is in an older format and has no key_id.',
                                                                variant: 'destructive',
                                                            });
                                                        }
                                                    } catch (err: any) {
                                                        const msg = err?.response?.data?.error || 'Could not read backup header.';
                                                        toast({ title: 'Header read failed', description: msg, variant: 'destructive' });
                                                    }
                                                }} title="Copy V2 header (key_id + fingerprint) for cross-master restore">
                                                    <FileKey className="w-4 h-4" />
                                                </Button>
                                                <Button variant="ghost" size="sm" onClick={async () => {
                                                    try {
                                                        const res = await api.get(`/backups/${backup.id}/download-url/`);
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
                                                        const res = await api.get(`/backups/${backup.id}/download-key/`, { responseType: 'blob' });
                                                        const blob = new Blob([res.data], { type: 'application/json' });
                                                        const url = window.URL.createObjectURL(blob);
                                                        const a = document.createElement('a');
                                                        a.href = url;
                                                        a.download = `backup-${backup.id}-key.json`;
                                                        document.body.appendChild(a);
                                                        a.click();
                                                        a.remove();
                                                        window.URL.revokeObjectURL(url);
                                                        toast({ title: "Key downloaded", description: "Store this file with your backup. You'll need it to restore on another master." });
                                                    } catch (err: any) {
                                                        const msg = err?.response?.data?.error || 'Could not download key.';
                                                        toast({ title: "Key download failed", description: msg, variant: "destructive" });
                                                    }
                                                }} title="Download encryption key info (for cross-master restore)">
                                                    <Key className="w-4 h-4" />
                                                </Button>
                                                <Button variant="ghost" size="sm" onClick={() => handleVerifyBackup(backup.id)} title="Verify integrity" disabled={verifying === backup.id}>
                                                    {verifying === backup.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
                                                </Button>
                                                <Button variant="ghost" size="sm" onClick={() => handleDeleteBackup(backup.id)} title="Delete" className="text-red-400 hover:text-red-500">
                                                    <Trash2 className="w-4 h-4" />
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
                                    <TableCell colSpan={6} className="text-center py-6 text-muted-foreground">No backups found. Create your first backup to get started.</TableCell>
                                </TableRow>
                            )}
                         </TableBody>
                     </Table>
                 </CardContent>
             </Card>

             <Card>
                  <CardHeader>
                      <div className="flex justify-between items-center">
                          <div>
                              <CardTitle>Configuration Snapshots</CardTitle>
                              <CardDescription>Lightweight config-only captures of env vars, resources, domains, and settings.</CardDescription>
                          </div>
                          <Button onClick={() => setShowCreateSnapshotDialog(true)} disabled={creatingSnapshot}>
                              <Plus className="mr-2 h-4 w-4" />
                              Create Snapshot
                          </Button>
                      </div>
                  </CardHeader>
                  <CardContent>
                      {snapshotsLoading ? (
                          <div className="flex justify-center p-4"><Loader2 className="animate-spin" /></div>
                      ) : (
                          <Table>
                              <TableHeader>
                                  <TableRow>
                                      <TableHead>Date</TableHead>
                                      <TableHead>Trigger</TableHead>
                                      <TableHead>Label</TableHead>
                                      <TableHead className="text-right">Actions</TableHead>
                                  </TableRow>
                              </TableHeader>
                              <TableBody>
                                  {snapshots.map(snapshot => (
                                      <TableRow key={snapshot.id}>
                                          <TableCell>{new Date(snapshot.created_at).toLocaleString()}</TableCell>
                                          <TableCell>
                                              <span className={`text-xs font-semibold px-2 py-1 rounded ${
                                                  snapshot.trigger === 'MANUAL' ? 'bg-blue-500/10 text-blue-500' :
                                                  snapshot.trigger === 'PRE_DEPLOY' ? 'bg-purple-500/10 text-purple-500' :
                                                  snapshot.trigger === 'PRE_ENV_CHANGE' ? 'bg-amber-500/10 text-amber-500' :
                                                  'bg-slate-500/10 text-slate-500'
                                              }`}>
                                                  {snapshot.trigger}
                                              </span>
                                          </TableCell>
                                          <TableCell className="max-w-[200px] truncate" title={snapshot.label || "No label"}>
                                              {snapshot.label || <span className="text-muted-foreground italic">No label</span>}
                                          </TableCell>
                                          <TableCell className="text-right space-x-1">
                                              <Button 
                                                  variant="ghost" 
                                                  size="sm" 
                                                  onClick={() => handleRestoreSnapshot(snapshot.id)} 
                                                  title="Restore config from snapshot"
                                                  disabled={restoringId === snapshot.id}
                                              >
                                                  <RotateCcw className="w-4 h-4" />
                                              </Button>
                                              {snapshots.length > 1 && (
                                                  <Button 
                                                      variant="ghost" 
                                                      size="sm" 
                                                      onClick={() => { setDiffingSnapshot(snapshot); setCompareSnapshotId(''); }} 
                                                      title="Compare with another snapshot"
                                                  >
                                                      <GitCompare className="w-4 h-4" />
                                                  </Button>
                                              )}
                                              <Button 
                                                  variant="ghost" 
                                                  size="sm" 
                                                  onClick={() => handleDownloadSnapshot(snapshot)} 
                                                  title="Download snapshot as JSON"
                                              >
                                                  <Download className="w-4 h-4" />
                                              </Button>
                                              <Button 
                                                  variant="ghost" 
                                                  size="sm" 
                                                  onClick={() => handleDeleteSnapshot(snapshot.id)} 
                                                  title="Delete snapshot" 
                                                  className="text-red-400 hover:text-red-500"
                                              >
                                                  <Trash2 className="w-4 h-4" />
                                              </Button>
                                          </TableCell>
                                      </TableRow>
                                  ))}
                                  {snapshots.length === 0 && (
                                      <TableRow>
                                          <TableCell colSpan={4} className="text-center py-6 text-muted-foreground">
                                              No snapshots found. Create your first snapshot to start tracking config history.
                                          </TableCell>
                                      </TableRow>
                                  )}
                              </TableBody>
                          </Table>
                      )}
                  </CardContent>
              </Card>

             {/* Live Backup/Restore Progress Terminal */}
             {backupProgress && (
                 <Card className="border-zinc-800">
                     <CardHeader className="pb-2">
                         <CardTitle className="flex items-center gap-2 text-sm">
                             {backupProgress.stage === 'connecting' || backupProgress.stage === 'connected' ? (
                                 <Loader2 className="w-4 h-4 animate-spin text-blue-500" />
                             ) : backupProgress.stage === 'completed' ? (
                                 <CheckCircle className="w-4 h-4 text-green-500" />
                             ) : backupProgress.stage === 'failed' ? (
                                 <AlertCircle className="w-4 h-4 text-red-500" />
                             ) : (
                                 <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                             )}
                             {backupProgress.stage === 'completed' ? 'Complete' : 'Live Progress'}
                             <span className="text-xs text-zinc-400 ml-auto">
                                 {backupProgress.percent.toFixed(0)}%
                             </span>
                         </CardTitle>
                     </CardHeader>
                     <CardContent className="space-y-2">
                         {/* Progress bar */}
                         <div className="w-full bg-zinc-800 rounded-full h-1.5 overflow-hidden">
                             <div
                                 className={`h-full rounded-full transition-all duration-300 ${
                                     backupProgress.stage === 'completed' ? 'bg-green-500' :
                                     backupProgress.stage === 'failed' ? 'bg-red-500' : 'bg-blue-500'
                                 }`}
                                 style={{ width: `${Math.min(100, backupProgress.percent)}%` }}
                             />
                         </div>

                         {/* Current stage message */}
                         <div className="text-sm text-zinc-300 font-medium">
                             {backupProgress.message}
                         </div>

                         {/* Transfer metrics */}
                         {backupProgress.bytes_transferred != null && backupProgress.total_bytes != null && backupProgress.total_bytes > 0 && (
                             <div className="text-xs text-zinc-500">
                                 {backupProgress.bytes_transferred >= 1048576
                                     ? `${(backupProgress.bytes_transferred / (1024 * 1024)).toFixed(1)} MB`
                                     : `${(backupProgress.bytes_transferred / 1024).toFixed(1)} KB`} / {backupProgress.total_bytes >= 1048576
                                     ? `${(backupProgress.total_bytes / (1024 * 1024)).toFixed(1)} MB`
                                     : `${(backupProgress.total_bytes / 1024).toFixed(1)} KB`} transferred
                             </div>
                         )}

                         {/* Log feed */}
                         {progressLog.length > 0 && (
                             <div className="bg-zinc-950 border border-zinc-800 rounded p-2 max-h-32 overflow-y-auto font-mono text-xs">
                                 {progressLog.slice(-50).map((line, i) => (
                                     <div key={i} className="text-green-400/80">
                                         {line}
                                     </div>
                                 ))}
                             </div>
                         )}

                         <Button
                             variant="ghost"
                             size="sm"
                             onClick={() => {
                                 disconnectBackupProgressWebSocket();
                                 setBackupProgress(null);
                                 setProgressLog([]);
                             }}
                             className="text-xs"
                         >
                             Dismiss
                         </Button>
                     </CardContent>
                 </Card>
             )}

             {/* Restore Progress Section */}
             {restoringId && (
                 <Card>
                     <CardHeader>
                         <CardTitle className="flex items-center gap-2">
                             {restoreStatus === 'RESTORING' && <Loader2 className="w-5 h-5 animate-spin text-blue-500" />}
                             {restoreStatus === 'RESTORED' && <Clock className="w-5 h-5 text-yellow-500" />}
                             {deploymentStatus === 'DEPLOYING' && <Loader2 className="w-5 h-5 animate-spin text-yellow-500" />}
                             {deploymentStatus === 'COMPLETED' && <CheckCircle className="w-5 h-5 text-green-500" />}
                             {deploymentStatus === 'FAILED' && <AlertCircle className="w-5 h-5 text-red-500" />}
                             {deploymentStatus === 'TIMEOUT' && <AlertCircle className="w-5 h-5 text-orange-500" />}
                             Restore Progress
                         </CardTitle>
                         <CardDescription>
                             Monitoring the restore and deployment process for backup {restoringId}
                         </CardDescription>
                     </CardHeader>
                     <CardContent className="space-y-4">
                         {/* Restore Status */}
                         <div className="flex items-center justify-between p-3 bg-blue-50/50 rounded-lg">
                             <div className="flex items-center gap-2">
                                <Clock className="w-4 h-4 text-blue-500" />
                                <span className="font-medium">Restore Phase</span>
                             </div>
                             <span className={`px-2 py-1 rounded text-xs font-semibold ${
                                 restoreStatus === 'RESTORING' ? 'bg-blue-500/10 text-blue-600' : 
                                 restoreStatus === 'RESTORED' ? 'bg-green-500/10 text-green-600' :
                                 restoreStatus === 'TIMEOUT' ? 'bg-orange-500/10 text-orange-600' :
                                 'bg-gray-500/10 text-gray-600'
                             }`}>
                                 {restoreStatus === 'RESTORING' ? 'Restoring backup...' : 
                                  restoreStatus === 'RESTORED' ? 'Backup restored, deploying...' :
                                  restoreStatus === 'TIMEOUT' ? 'Restore timeout' : 'Unknown'}
                             </span>
                         </div>

                         {/* Deployment Progress */}
                         {(deploymentStatus === 'DEPLOYING' || deploymentStatus === 'COMPLETED' || deploymentStatus === 'FAILED') && (
                             <div className="space-y-2">
                                 <div className="flex items-center justify-between">
                                     <span className="font-medium">Deployment Phase</span>
                                     <span className={`text-sm font-semibold ${
                                         deploymentStatus === 'DEPLOYING' ? 'text-yellow-600' :
                                         deploymentStatus === 'COMPLETED' ? 'text-green-600' :
                                         'text-red-600'
                                     }`}>
                                         {deploymentStatus === 'DEPLOYING' ? 'Deploying service...' : 
                                          deploymentStatus === 'COMPLETED' ? 'Completed successfully' : 
                                          'Failed'}
                                     </span>
                                 </div>
                                 <div className="w-full bg-gray-200 rounded-full h-2">
                                     <div 
                                         className={`h-2 rounded-full transition-all duration-300 ${
                                             deploymentStatus === 'COMPLETED' ? 'bg-green-500' :
                                             deploymentStatus === 'FAILED' ? 'bg-red-500' :
                                             'bg-yellow-500'
                                         }`}
                                         style={{ width: `${deploymentProgress}%` }}
                                     ></div>
                                 </div>
                                 <div className="text-xs text-gray-500">
                                     Progress: {deploymentProgress}%
                                 </div>
                             </div>
                         )}

                         {/* Live Status Indicator */}
                         {isLiveDeploying && (
                             <div className="flex items-center gap-2 text-yellow-600 bg-yellow-50 px-3 py-2 rounded-lg">
                                 <div className="w-2 h-2 bg-yellow-500 rounded-full animate-pulse"></div>
                                 <span className="text-sm font-medium">Live deployment in progress...</span>
                             </div>
                         )}

                         {/* Deployment Logs */}
                         {deploymentLogs && (
                             <div className="space-y-2">
                                 <div className="flex items-center justify-between">
                                     <span className="font-medium">Deployment Logs</span>
                                     <Button 
                                         variant="ghost" 
                                         size="sm" 
                                         onClick={() => setDeploymentLogs('')}
                                         className="text-xs"
                                     >
                                         Clear
                                     </Button>
                                 </div>
                                 <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 max-h-40 overflow-y-auto font-mono text-xs">
                                     <pre className="text-green-400 whitespace-pre-wrap">{deploymentLogs}</pre>
                                 </div>
                             </div>
                         )}

                         {/* Action Buttons */}
                         <div className="flex gap-2 pt-2">
                             <Button 
                                 variant="outline" 
                                 onClick={() => {
                                     setRestoringId(null);
                                     setRestoreStatus('');
                                     setDeploymentStatus('');
                                     setDeploymentProgress(0);
                                     setDeploymentLogs('');
                                     setIsLiveDeploying(false);
                                     cleanupWebSocket();
                                 }}
                             >
                                 Cancel Monitoring
                             </Button>
                              {deploymentStatus === 'FAILED' && (
                                  <Button 
                                      variant="destructive" 
                                      onClick={() => {
                                          // Try to restart the service
                                          servicesApi.restart(serviceId)
                                              .then(() => {
                                                  toast({ title: "Service restart initiated", description: "Attempting to restore service functionality." });
                                                  setDeploymentStatus('');
                                                  setDeploymentProgress(0);
                                              })
                                              .catch(err => {
                                                  toast({ title: "Restart failed", description: "Could not restart service.", variant: "destructive" });
                                              });
                                      }}
                                  >
                                      Restart Service
                                 </Button>
                             )}
                         </div>
                     </CardContent>
                 </Card>
             )}

            <Card>
                <CardHeader>
                    <CardTitle>Backup Schedule</CardTitle>
                    <CardDescription>Configure automated backup frequency and retention.</CardDescription>
                </CardHeader>
                <CardContent>
                    {scheduleLoading ? (
                        <div className="flex justify-center p-4"><Loader2 className="animate-spin h-5 w-5 text-muted-foreground" /></div>
                    ) : (
                        <div className="space-y-4">
                            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                                <div className="space-y-1">
                                    <label className="flex items-center space-x-2 text-sm font-medium mb-2 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={scheduleDbOnly}
                                            onChange={(e) => setScheduleDbOnly(e.target.checked)}
                                            className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                            disabled={savingSchedule}
                                        />
                                        <span>Database Only Backup</span>
                                    </label>
                                    <label className="text-sm font-medium">Cron Expression</label>
                                    <input
                                        type="text"
                                        value={cronExpression}
                                        onChange={(e) => setCronExpression(e.target.value)}
                                        className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm font-mono"
                                        placeholder="0 3 * * *"
                                    />
                                    <div className="flex flex-wrap gap-1.5 mt-1.5">
                                        {[
                                            { label: 'Every 5 min', expr: '*/5 * * * *' },
                                            { label: 'Every 30 min', expr: '*/30 * * * *' },
                                            { label: 'Hourly', expr: '0 * * * *' },
                                            { label: 'Every 6h', expr: '0 */6 * * *' },
                                            { label: 'Every 12h', expr: '0 */12 * * *' },
                                            { label: 'Daily 12 AM', expr: '0 0 * * *' },
                                            { label: 'Daily 3 AM', expr: '0 3 * * *' },
                                            { label: 'Twice daily', expr: '0 8,20 * * *' },
                                            { label: 'Weekly Sun', expr: '0 3 * * 0' },
                                        ].map(p => (
                                            <button
                                                key={p.expr}
                                                type="button"
                                                onClick={() => setCronExpression(p.expr)}
                                                className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
                                                    cronExpression === p.expr
                                                        ? 'bg-primary/10 border-primary text-primary'
                                                        : 'bg-background border-border text-muted-foreground hover:border-primary/50'
                                                }`}
                                            >
                                                {p.label}
                                            </button>
                                        ))}
                                    </div>
                                    <p className="text-[10px] text-muted-foreground">Click a preset or type a custom cron expression</p>
                                </div>
                                <div className="space-y-1">
                                    <label className="text-sm font-medium">Retention (days)</label>
                                    <input
                                        type="number"
                                        value={retentionDays}
                                        onChange={(e) => setRetentionDays(parseInt(e.target.value) || 7)}
                                        min={1}
                                        max={365}
                                        className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                    />
                                    <p className="text-[10px] text-muted-foreground">Backups older than this are auto-deleted</p>
                                </div>
                                <div className="space-y-1">
                                      <label className="text-sm font-medium">Status</label>
                                      <label className="flex items-center gap-3 rounded-lg border border-border p-2.5 cursor-pointer">
                                          <input
                                              type="checkbox"
                                              checked={scheduleEnabled}
                                              onChange={(e) => setScheduleEnabled(e.target.checked)}
                                              className="w-4 h-4 rounded border-border text-foreground focus:ring-foreground"
                                          />
                                          <span className="text-sm font-medium">{scheduleEnabled ? 'Enabled' : 'Paused'}</span>
                                      </label>
                                  </div>
                                  <div className="space-y-1">
                                      <label className="text-sm font-medium">Storage Destination</label>
                                      <select
                                          value={selectedDestination}
                                          onChange={(e) => setSelectedDestination(e.target.value)}
                                          className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm h-[42px]"
                                      >
                                          <option value="local">Local Server</option>
                                          {destinations.map(d => (
                                              <option key={d.id} value={d.id}>
                                                  {d.name} ({d.provider_display})
                                              </option>
                                          ))}
                                      </select>
                                      <p className="text-[10px] text-muted-foreground">Offload backups to S3/MinIO</p>
                                  </div>
                            </div>

                            <div className="flex items-center gap-2 pt-2 border-t border-border">
                                <label className="flex items-center gap-2 text-sm cursor-pointer">
                                    <input type="checkbox" checked={scheduleCloudUpload} onChange={(e) => setScheduleCloudUpload(e.target.checked)}
                                        className="rounded border-gray-300" />
                                    <span>Auto-upload to cloud storage</span>
                                </label>
                            </div>

                            {schedule && (
                                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                                    <Clock className="w-3 h-3" />
                                    <span>Last run: {schedule.last_run ? new Date(schedule.last_run).toLocaleString() : 'Never'}</span>
                                    <span>|</span>
                                    <span>Next run: {schedule.next_run ? new Date(schedule.next_run).toLocaleString() : 'TBD'}</span>
                                </div>
                            )}

                            <Button onClick={handleSaveSchedule} disabled={savingSchedule}>
                                {savingSchedule ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                                {schedule ? 'Update Schedule' : 'Create Schedule'}
                            </Button>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
            <Card>
                <CardHeader>
                    <CardTitle>Snapshot Schedule</CardTitle>
                    <CardDescription>Configure automated configuration snapshot frequency.</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="space-y-4">
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                            <div className="space-y-1">
                                <label className="text-sm font-medium">Cron Expression</label>
                                <input
                                    type="text"
                                    value={snapCronExpression}
                                    onChange={(e) => setSnapCronExpression(e.target.value)}
                                    className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm font-mono"
                                    placeholder="0 3 * * *"
                                />
                                <div className="flex flex-wrap gap-1.5 mt-1.5">
                                    {[
                                        { label: 'Every 5 min', expr: '*/5 * * * *' },
                                        { label: 'Hourly', expr: '0 * * * *' },
                                        { label: 'Every 6h', expr: '0 */6 * * *' },
                                        { label: 'Daily 12 AM', expr: '0 0 * * *' },
                                        { label: 'Weekly', expr: '0 3 * * 0' },
                                    ].map(p => (
                                        <button
                                            key={p.expr}
                                            type="button"
                                            onClick={() => setSnapCronExpression(p.expr)}
                                            className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
                                                snapCronExpression === p.expr
                                                    ? 'bg-primary/10 border-primary text-primary'
                                                    : 'bg-background border-border text-muted-foreground hover:border-primary/50'
                                            }`}
                                        >
                                            {p.label}
                                        </button>
                                    ))}
                                </div>
                                <p className="text-[10px] text-muted-foreground">Click a preset or type a custom cron expression</p>
                            </div>
                            <div className="space-y-1">
                                <label className="text-sm font-medium">Retention (days)</label>
                                <input
                                    type="number"
                                    value={snapRetentionDays}
                                    onChange={(e) => setSnapRetentionDays(parseInt(e.target.value) || 30)}
                                    min={1}
                                    max={365}
                                    className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm"
                                />
                                <p className="text-[10px] text-muted-foreground">Snapshots older than this are auto-deleted</p>
                            </div>
                            <div className="space-y-1">
                                  <label className="text-sm font-medium">Status</label>
                                  <label className="flex items-center gap-3 rounded-lg border border-border p-2.5 cursor-pointer">
                                      <input
                                          type="checkbox"
                                          checked={snapScheduleEnabled}
                                          onChange={(e) => setSnapScheduleEnabled(e.target.checked)}
                                          className="w-4 h-4 rounded border-border text-foreground focus:ring-foreground"
                                      />
                                      <span className="text-sm font-medium">{snapScheduleEnabled ? 'Enabled' : 'Paused'}</span>
                                  </label>
                              </div>
                              <div className="space-y-1">
                                  <label className="text-sm font-medium">Cloud Destination</label>
                                  <select
                                      value={snapScheduleCloudDest}
                                      onChange={(e) => setSnapScheduleCloudDest(e.target.value)}
                                      className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm h-[42px]"
                                  >
                                      <option value="">Do not upload</option>
                                      {destinations.map(d => (
                                          <option key={d.id} value={d.id}>
                                              {d.name} ({d.provider_display})
                                          </option>
                                      ))}
                                  </select>
                                  <p className="text-[10px] text-muted-foreground">Upload snapshots to S3/MinIO</p>
                              </div>
                        </div>

                        <div className="flex items-center gap-2 pt-2 border-t border-border">
                            <label className="flex items-center gap-2 text-sm cursor-pointer">
                                <input type="checkbox" checked={snapScheduleCloudUpload} onChange={(e) => setSnapScheduleCloudUpload(e.target.checked)} className="rounded border-gray-300" />
                                <span>Auto-upload snapshots to cloud</span>
                            </label>
                        </div>

                        <Button onClick={handleSaveSnapSchedule} disabled={savingSnapSchedule}>
                            {savingSnapSchedule ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                            Update Snapshot Schedule
                        </Button>
                    </div>
                </CardContent>
            </Card>

        {keyPromptOpen && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                <div className="bg-background border border-border rounded-lg p-6 max-w-md w-full mx-4 shadow-lg">
                    <div className="flex items-center gap-2 mb-4">
                        <Key className="h-5 w-5 text-amber-500" />
                        <h3 className="text-lg font-semibold">Encryption Key Required</h3>
                    </div>
                    <p className="text-sm text-muted-foreground mb-4">
                        {keyPromptError}
                    </p>
                    <p className="text-xs text-muted-foreground mb-4">
                        This backup was encrypted on a different master. Select a stored key below
                        or enter the source master&apos;s encryption key manually.
                    </p>
                    {storedKeys.length > 0 && (
                        <div className="mb-4">
                            <label className="text-xs text-muted-foreground block mb-1">Stored keys:</label>
                            <select
                                className="w-full h-9 px-3 border border-border rounded-md bg-background text-sm"
                                value={keyPromptKeyId || ""}
                                onChange={(e) => {
                                    const val = e.target.value;
                                    if (val === '__manual__') {
                                        setKeyPromptValue('');
                                        return;
                                    }
                                    const selected = storedKeys.find(k => k.key_id === val);
                                    if (selected) {
                                        setKeyPromptValue('__imported__');
                                        setKeyPromptKeyId(selected.key_id);
                                    }
                                }}
                            >
                                <option value="">-- Enter key manually --</option>
                                {storedKeys.filter(k => !k.is_active).map(k => (
                                    <option key={k.key_id} value={k.key_id}>
                                        {k.label || k.key_id} ({k.source === 'IMPORTED' ? 'imported' : 'auto'})
                                    </option>
                                ))}
                            </select>
                        </div>
                    )}
                    <Input
                        type="password"
                        placeholder="Or enter encryption key manually"
                        value={keyPromptValue}
                        onChange={(e) => { setKeyPromptValue(e.target.value); setKeyPromptKeyId(''); }}
                        onKeyDown={(e) => { if (e.key === 'Enter') submitEncryptionKey(); }}
                        autoFocus
                        className="mb-4"
                    />
                    {keyPromptKeyId && (
                        <div className="flex items-center gap-2 mb-4">
                            <input
                                type="checkbox"
                                id="save-key-checkbox"
                                checked={keyPromptSaveForFuture}
                                onChange={(e) => setKeyPromptSaveForFuture(e.target.checked)}
                                className="rounded border-gray-300"
                            />
                            <label htmlFor="save-key-checkbox" className="text-xs text-muted-foreground cursor-pointer">
                                Save this key for future restores from this master (imports via <code className="text-[10px] bg-muted rounded px-1">POST /backups/import-key/</code> — admin required).
                                Will not overwrite your local <code className="text-[10px] bg-muted rounded px-1">BACKUP_ENCRYPTION_KEY</code>.
                            </label>
                        </div>
                    )}
                    <div className="flex justify-end gap-2">
                        <Button
                            variant="outline"
                            onClick={() => setKeyPromptOpen(false)}
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

         {snapOverrideOpen && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                <div className="bg-background border border-red-900/50 rounded-lg p-6 max-w-lg w-full mx-4 shadow-lg">
                    <div className="flex items-center gap-2 mb-4">
                        <AlertCircle className="h-5 w-5 text-red-500" />
                        <h3 className="text-lg font-semibold">Safety Snapshot Failed</h3>
                    </div>
                    <p className="text-sm text-muted-foreground mb-2">
                        The pre-restore safety snapshot could not be created. Without it, a corrupt
                        restore archive would permanently destroy the current running state.
                    </p>
                    <div className="bg-red-950/30 border border-red-900/30 rounded p-3 mb-4">
                        <p className="text-xs font-mono text-red-400 break-all">
                            {snapOverrideError}
                        </p>
                    </div>
                    <p className="text-xs text-amber-400 mb-4">
                        {snapOverrideRemediation || 'Fix the error and retry, or override to proceed without a safety net.'}
                    </p>
                    <div className="flex justify-end gap-2">
                        <Button
                            variant="outline"
                            onClick={() => { setSnapOverrideOpen(false); snapOverrideOpenRef.current = false; snapOverrideCloudFormRef.current = null; }}
                            disabled={snapOverrideSubmitting}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={submitSnapOverride}
                            disabled={snapOverrideSubmitting}
                        >
                            {snapOverrideSubmitting ? (
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            ) : (
                                <AlertCircle className="mr-2 h-4 w-4" />
                            )}
                            Proceed Without Snapshot
                        </Button>
                    </div>
                </div>
            </div>
         )}

          {showCreateSnapshotDialog && (
              <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 animate-in fade-in duration-200">
                  <div className="bg-background border border-border rounded-lg p-6 max-w-md w-full mx-4 shadow-lg">
                      <h3 className="text-lg font-semibold mb-2">Create Config Snapshot</h3>
                      <p className="text-xs text-muted-foreground mb-4">
                          Capture the current configuration settings (env vars, domains, resources, etc.) as a lightweight rollback point.
                      </p>
                      <div className="space-y-2 mb-4">
                          <label className="text-xs font-medium text-muted-foreground">Optional Label</label>
                          <Input
                              placeholder='e.g., "Before env var update"'
                              value={snapshotLabel}
                              onChange={(e) => setSnapshotLabel(e.target.value)}
                              onKeyDown={(e) => { if (e.key === 'Enter') handleCreateSnapshot(); }}
                              autoFocus
                          />
                      </div>
                      <div className="flex justify-end gap-2">
                          <Button variant="outline" onClick={() => { setShowCreateSnapshotDialog(false); setSnapshotLabel(''); }}>
                              Cancel
                          </Button>
                          <Button onClick={handleCreateSnapshot} disabled={creatingSnapshot}>
                              {creatingSnapshot ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                              Create Snapshot
                          </Button>
                      </div>
                  </div>
              </div>
          )}

          {diffingSnapshot && !diffResults && (
              <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 animate-in fade-in duration-200">
                  <div className="bg-background border border-border rounded-lg p-6 max-w-md w-full mx-4 shadow-lg">
                      <div className="flex items-center gap-2 mb-4">
                          <GitCompare className="h-5 w-5 text-blue-500" />
                          <h3 className="text-lg font-semibold">Compare Snapshot</h3>
                      </div>
                      <p className="text-sm text-muted-foreground mb-4">
                          Select a snapshot to compare with the one from {new Date(diffingSnapshot.created_at).toLocaleString()}.
                      </p>
                      <div className="space-y-1 mb-4">
                          <label className="text-xs font-medium text-muted-foreground">Compare against:</label>
                          <select
                              value={compareSnapshotId}
                              onChange={(e) => setCompareSnapshotId(e.target.value)}
                              className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm"
                          >
                              <option value="">-- Select a snapshot --</option>
                              {snapshots
                                  .filter(s => s.id !== diffingSnapshot.id)
                                  .map(s => (
                                      <option key={s.id} value={s.id}>
                                          {new Date(s.created_at).toLocaleString()} ({s.trigger}) {s.label ? `- ${s.label}` : ''}
                                      </option>
                                  ))}
                          </select>
                      </div>
                      <div className="flex justify-end gap-2">
                          <Button variant="outline" onClick={() => { setDiffingSnapshot(null); setCompareSnapshotId(''); }}>
                              Cancel
                          </Button>
                          <Button
                              onClick={() => handleDiffSnapshot(diffingSnapshot, compareSnapshotId)}
                              disabled={!compareSnapshotId || diffLoading}
                          >
                              {diffLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                              Compare
                          </Button>
                      </div>
                  </div>
              </div>
          )}

          {diffResults && (
              <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 animate-in fade-in duration-200">
                  <div className="bg-background border border-border rounded-lg p-6 max-w-2xl w-full mx-4 shadow-lg max-h-[85vh] flex flex-col">
                      <div className="flex items-center gap-2 mb-2">
                          <GitCompare className="h-5 w-5 text-blue-500" />
                          <h3 className="text-lg font-semibold">Compare Configuration Snapshots</h3>
                      </div>
                      <p className="text-xs text-muted-foreground mb-4">
                          Comparing snapshot A ({new Date(diffResults.snapshot_a.created_at).toLocaleString()}, {diffResults.snapshot_a.trigger}) 
                          with snapshot B ({new Date(diffResults.snapshot_b.created_at).toLocaleString()}, {diffResults.snapshot_b.trigger})
                      </p>
                      
                      <div className="flex-1 overflow-y-auto space-y-4 pr-1">
                          {diffResults.diff.total_changes === 0 ? (
                              <div className="text-center py-8 text-muted-foreground text-sm">
                                  No changes detected. The configurations are identical.
                              </div>
                          ) : (
                              <>
                                  {Object.keys(diffResults.diff.added).length > 0 && (
                                      <div className="space-y-1.5">
                                          <h4 className="text-xs font-semibold text-emerald-500 uppercase tracking-wider">Added Keys</h4>
                                          <div className="border border-emerald-500/20 bg-emerald-500/5 rounded-lg p-3 font-mono text-xs space-y-1">
                                              {Object.entries(diffResults.diff.added).map(([key, val]) => (
                                                  <div key={key} className="break-all">
                                                      <span className="text-emerald-400 font-semibold">+ {key}:</span> <span className="text-muted-foreground">{JSON.stringify(val)}</span>
                                                  </div>
                                              ))}
                                          </div>
                                      </div>
                                  )}
                                  
                                  {Object.keys(diffResults.diff.removed).length > 0 && (
                                      <div className="space-y-1.5">
                                          <h4 className="text-xs font-semibold text-red-500 uppercase tracking-wider">Removed Keys</h4>
                                          <div className="border border-red-500/20 bg-red-500/5 rounded-lg p-3 font-mono text-xs space-y-1">
                                              {Object.entries(diffResults.diff.removed).map(([key, val]) => (
                                                  <div key={key} className="break-all">
                                                      <span className="text-red-400 font-semibold">- {key}:</span> <span className="text-muted-foreground">{JSON.stringify(val)}</span>
                                                  </div>
                                              ))}
                                          </div>
                                      </div>
                                  )}
                                  
                                  {Object.keys(diffResults.diff.changed).length > 0 && (
                                      <div className="space-y-1.5">
                                          <h4 className="text-xs font-semibold text-blue-500 uppercase tracking-wider">Modified Keys</h4>
                                          <div className="border border-blue-500/20 bg-blue-500/5 rounded-lg p-3 font-mono text-xs space-y-2">
                                              {Object.entries(diffResults.diff.changed).map(([key, diff]: [string, any]) => (
                                                  <div key={key} className="border-b border-blue-500/10 last:border-0 pb-2 last:pb-0 break-all">
                                                      <div className="font-semibold text-blue-400">{key}:</div>
                                                      <div className="grid grid-cols-2 gap-2 mt-1">
                                                          <div className="bg-red-500/5 p-2 rounded border border-red-500/10">
                                                              <span className="text-red-400 font-semibold">Old:</span> <span className="text-muted-foreground text-[11px]">{JSON.stringify(diff.old)}</span>
                                                          </div>
                                                          <div className="bg-emerald-500/5 p-2 rounded border border-emerald-500/10">
                                                              <span className="text-emerald-400 font-semibold">New:</span> <span className="text-muted-foreground text-[11px]">{JSON.stringify(diff.new)}</span>
                                                          </div>
                                                      </div>
                                                  </div>
                                              ))}
                                          </div>
                                      </div>
                                  )}
                              </>
                          )}
                      </div>
                      
                      <div className="flex justify-end gap-2 pt-4 border-t border-border mt-4">
                          <Button variant="outline" onClick={() => { setDiffResults(null); setDiffingSnapshot(null); setCompareSnapshotId(''); }}>
                              Close
                          </Button>
                      </div>
                  </div>
              </div>
          )}

          {cloudRestorePromptOpen && (
              <div className="fixed inset-0 bg-black/50 z-50 flex flex-col items-center justify-center p-4">
                  <div className="bg-background max-w-md w-full rounded-xl border border-border shadow-2xl overflow-hidden flex flex-col max-h-screen">
                      <div className="p-6 pb-0 mb-4 shrink-0">
                          <h3 className="text-lg font-bold">Restore Backup from Cloud</h3>
                          <p className="text-sm text-muted-foreground mt-1">
                              Download a tarball from an S3-compatible bucket and restore it to this service.
                          </p>
                      </div>

                      <div className="p-6 pt-0 space-y-3 overflow-y-auto">
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
                                          placeholder="Object Key (e.g., smsly-backups/service_123/backup.tar.gz)"
                                          value={cloudRestoreForm.s3_key}
                                          onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, s3_key: e.target.value })}
                                      />
                                  )}

                                  <Input
                                      placeholder="Prefix filter (e.g., smsly-backups/)"
                                      value={cloudBackupPrefix}
                                      onChange={(e) => setCloudBackupPrefix(e.target.value)}
                                  />
                              </>
                          ) : (
                              <>
                                  <Input
                                      placeholder="Object Key (e.g., smsly-backups/service_123/backup.tar.gz)"
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
                                  placeholder="Leave blank if backup is not encrypted"
                                  value={cloudRestoreForm.encryption_key}
                                  onChange={(e) => setCloudRestoreForm({ ...cloudRestoreForm, encryption_key: e.target.value })}
                              />
                          </div>
                      </div>

                      <div className="p-6 pt-4 border-t border-border flex justify-end gap-2 shrink-0">
                          <Button variant="outline" onClick={() => setCloudRestorePromptOpen(false)}>Cancel</Button>
                          <Button
                              onClick={handleCloudRestoreSubmit}
                              disabled={
                                  creating ||
                                  !cloudRestoreForm.s3_key ||
                                  (!cloudRestoreForm.cloud_storage_id || cloudRestoreForm.cloud_storage_id === 'custom'
                                      ? (!cloudRestoreForm.s3_bucket || !cloudRestoreForm.s3_access_key || !cloudRestoreForm.s3_secret_key)
                                      : false)
                              }
                          >
                              {creating ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                              Restore from Cloud
                          </Button>
                      </div>
                  </div>
              </div>
          )}
           {uploadRestoreOpen && (
              <div className="fixed inset-0 bg-black/50 z-50 flex flex-col items-center justify-center p-4">
                  <div className="bg-background max-w-md w-full rounded-xl border border-border shadow-2xl p-6">
                      <h3 className="text-lg font-bold mb-2">Restore Backup from Local File</h3>
                      <p className="text-sm text-muted-foreground mb-4">
                          Upload a backup .tar.gz or .tgz file to restore this service to a previous state.
                      </p>
                      <div className="space-y-3">
                          <div className="border-2 border-dashed border-border rounded-lg p-6 text-center hover:border-blue-500/50 transition-colors cursor-pointer"
                              onClick={() => document.getElementById('restore-file-input')?.click()}
                              onDragOver={(e) => e.preventDefault()}
                              onDrop={(e) => {
                                  e.preventDefault();
                                  const file = e.dataTransfer.files?.[0];
                                  if (file) setUploadRestoreFile(file);
                              }}
                          >
                              {uploadRestoreFile ? (
                                  <div className="text-sm">
                                      <CheckCircle className="h-8 w-8 mx-auto mb-2 text-emerald-500" />
                                      <p className="font-medium">{uploadRestoreFile.name}</p>
                                      <p className="text-muted-foreground text-xs mt-1">{(uploadRestoreFile.size / 1024 / 1024).toFixed(1)} MB</p>
                                  </div>
                              ) : (
                                  <div className="text-sm text-muted-foreground">
                                      <Upload className="h-8 w-8 mx-auto mb-2" />
                                      <p>Click or drag a backup file here</p>
                                      <p className="text-xs mt-1">.tar.gz or .tgz</p>
                                  </div>
                              )}
                              <input id="restore-file-input" type="file" accept=".tar.gz,.tgz,.gz,.enc" className="hidden"
                                  onChange={(e) => setUploadRestoreFile(e.target.files?.[0] || null)} />
                          </div>
                      </div>
                      <div className="flex justify-end gap-2 mt-4">
                          <Button variant="outline" onClick={() => { setUploadRestoreOpen(false); setUploadRestoreFile(null); }}>
                              Cancel
                          </Button>
                          <Button onClick={handleUploadRestoreSubmit} disabled={!uploadRestoreFile || uploadRestoreLoading}>
                              {uploadRestoreLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Upload className="mr-2 h-4 w-4" />}
                              Upload & Restore
                          </Button>
                      </div>
                  </div>
              </div>
          )}

          {showRestoreHistory && (
              <Card>
                  <CardHeader className="pb-3">
                      <div className="flex justify-between items-center">
                          <div>
                              <CardTitle>Restoration Activity</CardTitle>
                              <CardDescription>Recent backup restores and their deployment status.</CardDescription>
                          </div>
                          <Button variant="ghost" size="sm" onClick={() => setShowRestoreHistory(false)}>
                              <span className="text-muted-foreground">Close</span>
                          </Button>
                      </div>
                  </CardHeader>
                  <CardContent>
                      {restoreHistoryLoading ? (
                          <div className="flex justify-center p-4"><Loader2 className="animate-spin h-5 w-5 text-muted-foreground" /></div>
                      ) : restoreHistory.length === 0 ? (
                          <div className="text-center py-6 text-muted-foreground text-sm">
                              <History className="h-8 w-8 mx-auto mb-2 opacity-40" />
                              No restoration activity found.
                          </div>
                      ) : (
                          <Table>
                              <TableHeader>
                                  <TableRow>
                                      <TableHead>Date</TableHead>
                                      <TableHead>Service</TableHead>
                                      <TableHead>Source</TableHead>
                                      <TableHead>Deploy Status</TableHead>
                                  </TableRow>
                              </TableHeader>
                              <TableBody>
                                  {restoreHistory.map((r: any) => (
                                      <TableRow key={r.backup_id}>
                                          <TableCell className="text-xs">{r.restored_at ? new Date(r.restored_at).toLocaleString() : '-'}</TableCell>
                                          <TableCell className="text-xs font-medium">{r.service_name || r.service_id?.slice(0, 8) || '-'}</TableCell>
                                          <TableCell className="text-xs max-w-[200px] truncate" title={r.restore_type}>{r.restore_type}</TableCell>
                                          <TableCell>
                                              <span className={`text-xs font-semibold px-2 py-1 rounded ${
                                                  r.deployment_status === 'ACTIVE' ? 'bg-emerald-500/10 text-emerald-500' :
                                                  r.deployment_status === 'FAILED' ? 'bg-red-500/10 text-red-500' :
                                                  r.deployment_status ? 'bg-blue-500/10 text-blue-500' :
                                                  'bg-slate-500/10 text-slate-500'
                                              }`}>
                                                  {r.deployment_status || 'Pending'}
                                              </span>
                                          </TableCell>
                                      </TableRow>
                                  ))}
                              </TableBody>
                          </Table>
                      )}
                  </CardContent>
              </Card>
          )}
        </>
    );
}
