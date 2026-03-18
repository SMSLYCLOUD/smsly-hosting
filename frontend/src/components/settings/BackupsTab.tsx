'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Loader2, Download, RotateCcw, Trash2, Plus, Clock, Save } from 'lucide-react';
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
        try {
            await api.post(`/backups/${id}/restore/`);
            toast({ title: "Restore Started", description: "Service will restart once restored." });
        } catch (err) {
            toast({ title: "Error", description: "Failed to trigger restore.", variant: "destructive" });
        }
    };

    const handleDeleteBackup = async (id: string) => {
        if (!await confirm({ title: 'Delete backup?', message: 'This backup will be permanently deleted.', variant: 'destructive', confirmText: 'Delete' })) return;
        try {
            await api.delete(`/backups/${id}/`);
            toast({ title: "Backup deleted" });
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
                                                <Button variant="ghost" size="sm" onClick={() => handleRestore(backup.id)} title="Restore">
                                                    <RotateCcw className="w-4 h-4" />
                                                </Button>
                                                <Button variant="ghost" size="sm" onClick={() => {
                                                    const token = localStorage.getItem('auth_token') || localStorage.getItem('token');
                                                    window.location.href = `/api/v1/backups/${backup.id}/download/?token=${token}`;
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
