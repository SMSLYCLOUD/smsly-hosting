'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Loader2, Download, RotateCcw, Trash2, Plus, Clock, Save, AlertCircle, CheckCircle, FileKey } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import api from '@/lib/api';
import { useConfirm } from '@/components/ui/confirm-dialog';

interface Schedule {
    id: number;
    cron_expression: string;
    retention_days: number;
    enabled: boolean;
    last_run: string | null;
    next_run: string | null;
}

export default function BackupsTab({ serviceId }: { serviceId: string }) {
    const { toast } = useToast();
    const confirm = useConfirm();
    const [backups, setBackups] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [creating, setCreating] = useState(false);
    
    // Restore progress tracking
    const [restoringId, setRestoringId] = useState<string | null>(null);
    const [restoreStatus, setRestoreStatus] = useState<string>('');
    const [deploymentStatus, setDeploymentStatus] = useState<string>('');
    const [deploymentProgress, setDeploymentProgress] = useState<number>(0);
    const [deploymentLogs, setDeploymentLogs] = useState<string>('');
    const [isLiveDeploying, setIsLiveDeploying] = useState(false);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimer = useRef<NodeJS.Timeout | null>(null);

    // Schedule state
    const [schedule, setSchedule] = useState<Schedule | null>(null);
    const [scheduleLoading, setScheduleLoading] = useState(true);
    const [cronExpression, setCronExpression] = useState('0 3 * * *');
    const [retentionDays, setRetentionDays] = useState(7);
    const [scheduleEnabled, setScheduleEnabled] = useState(true);
    const [savingSchedule, setSavingSchedule] = useState(false);

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
            }
        } catch (err) {
            console.error(err);
        } finally {
            setScheduleLoading(false);
        }
    }, [serviceId]);

    useEffect(() => {
        loadBackups();
        loadSchedule();
    }, [loadBackups, loadSchedule]);

    const handleCreateBackup = async () => {
        setCreating(true);
        try {
            await api.post('/backups/', { service: serviceId });
            toast({ title: "Backup Started", description: "This may take a few minutes." });
            loadBackups();
        } catch (err) {
            toast({ title: "Error", description: "Failed to start backup.", variant: "destructive" });
        } finally {
            setCreating(false);
        }
    };

    const handleRestore = async (id: string) => {
        if (!await confirm({ title: 'Restore backup?', message: 'Are you sure? This will overwrite the current service state.', variant: 'destructive', confirmText: 'Restore' })) return;
        
        setRestoringId(id);
        setRestoreStatus('RESTORING');
        setDeploymentStatus('');
        setDeploymentProgress(0);
        setDeploymentLogs('');
        
        try {
            await api.post(`/backups/${id}/restore/`, { confirm: true });
            toast({ title: "Restore Started", description: "Service will restart once restored. Monitoring deployment progress..." });
            
            // Start monitoring deployment status
            monitorDeploymentAfterRestore(id);
            
            // Connect WebSocket for real-time updates
            connectWebSocket(id);
            
        } catch (err) {
            toast({ title: "Error", description: "Failed to trigger restore.", variant: "destructive" });
            setRestoringId(null);
            setRestoreStatus('');
        }
    };

    const monitorDeploymentAfterRestore = async (backupId: string) => {
        const pollInterval = setInterval(async () => {
            try {
                const res = await api.get(`/services/${serviceId}/`);
                const service = res.data;
                
                // Check for active deployment
                if (service.latest_deployment) {
                    const deployment = service.latest_deployment;
                    
                    if (deployment.status === 'BUILDING' || deployment.status === 'DEPLOYING') {
                        setDeploymentStatus('DEPLOYING');
                        setDeploymentProgress(calculateProgress(deployment.status));
                        setRestoreStatus('RESTORED');
                        setIsLiveDeploying(true);
                    } else if (deployment.status === 'ACTIVE') {
                        setDeploymentStatus('COMPLETED');
                        setDeploymentProgress(100);
                        setIsLiveDeploying(false);
                        clearInterval(pollInterval);
                        toast({ title: "Restore Completed", description: "Service has been successfully restored and deployed." });
                        // Refresh backups to show any new status
                        loadBackups();
                    } else if (deployment.status === 'FAILED') {
                        setDeploymentStatus('FAILED');
                        setIsLiveDeploying(false);
                        clearInterval(pollInterval);
                        toast({ title: "Restore Failed", description: "Deployment failed. Check service logs for details.", variant: "destructive" });
                    }
                }
            } catch (err) {
                console.error('Error monitoring deployment:', err);
            }
        }, 3000); // Poll every 3 seconds
        
        // Stop monitoring after 5 minutes
        setTimeout(() => {
            clearInterval(pollInterval);
            if (deploymentStatus !== 'COMPLETED' && deploymentStatus !== 'FAILED') {
                setRestoreStatus('TIMEOUT');
                setDeploymentStatus('TIMEOUT');
                setIsLiveDeploying(false);
                toast({ title: "Restore Monitoring Timeout", description: "Restore process may still be running. Check service status manually.", variant: "destructive" });
            }
        }, 300000); // 5 minutes
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

        const proto = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss' : 'ws';
        const host = typeof window !== 'undefined' ? window.location.host : 'localhost';
        const wsUrl = `${proto}://${host}/ws/build-logs/${deploymentId}/`;

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

    useEffect(() => {
        // Cleanup WebSocket on unmount
        return () => {
            cleanupWebSocket();
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

    const handleSaveSchedule = async () => {
        setSavingSchedule(true);
        try {
            const payload = {
                service: serviceId,
                cron_expression: cronExpression,
                retention_days: retentionDays,
                enabled: scheduleEnabled,
            };
            if (schedule) {
                await api.put(`/backup-schedules/${schedule.id}/`, payload);
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

    const formatBytes = (bytes: number) => {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
    };

    if (loading) return <div className="flex justify-center p-8"><Loader2 className="animate-spin" /></div>;

    return (
        <div className="space-y-6">
            <Card>
                <CardHeader>
                    <div className="flex justify-between items-center">
                        <div>
                            <CardTitle>Backups</CardTitle>
                            <CardDescription>Snapshots of your service container, volumes, and configuration.</CardDescription>
                        </div>
                        <Button onClick={handleCreateBackup} disabled={creating}>
                            {creating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                            Create Backup
                        </Button>
                    </div>
                </CardHeader>
                <CardContent>
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Date</TableHead>
                                <TableHead>Type</TableHead>
                                <TableHead>Size</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {backups.map(backup => (
                                <TableRow key={backup.id}>
                                    <TableCell>{new Date(backup.created_at).toLocaleString()}</TableCell>
                                    <TableCell><span className="text-xs font-mono bg-muted px-2 py-1 rounded">{backup.backup_type}</span></TableCell>
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
                                    <TableCell colSpan={5} className="text-center py-6 text-muted-foreground">No backups found. Create your first backup to get started.</TableCell>
                                </TableRow>
                            )}
                         </TableBody>
                     </Table>
                 </CardContent>
             </Card>

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
                                         api.post(`/services/${serviceId}/restart/`)
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
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <div className="space-y-1">
                                    <label className="text-sm font-medium">Cron Expression</label>
                                    <input
                                        type="text"
                                        value={cronExpression}
                                        onChange={(e) => setCronExpression(e.target.value)}
                                        className="w-full px-3 py-2 rounded-lg bg-background border border-border text-sm font-mono"
                                        placeholder="0 3 * * *"
                                    />
                                    <p className="text-[10px] text-muted-foreground">e.g. &quot;0 3 * * *&quot; = daily at 3 AM</p>
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
                                        />
                                        <span className="text-sm">{scheduleEnabled ? 'Enabled' : 'Disabled'}</span>
                                    </label>
                                </div>
                            </div>

                            {schedule && (
                                <div className="flex items-center gap-4 text-xs text-muted-foreground border-t border-border pt-3">
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
    );
}
