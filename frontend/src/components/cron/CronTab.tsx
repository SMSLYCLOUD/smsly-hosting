'use client';

import React, { useState, useEffect, useCallback } from 'react';
import api, { servicesApi, CronJob } from '@/lib/api';

interface CloudDestination {
    id: string;
    name: string;
    provider_display: string;
    bucket: string;
}
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Clock, Plus, Trash2, Play, CheckCircle2 } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { formatDistanceToNow } from 'date-fns';

interface CronTabProps {
    serviceId: string;
}

export const CronTab = React.memo(function CronTab({ serviceId }: CronTabProps) {
    const confirm = useConfirm();
    const [jobs, setJobs] = useState<CronJob[]>([]);
    const [loading, setLoading] = useState(true);
    const [newName, setNewName] = useState('');
    const [newSchedule, setNewSchedule] = useState('*/15 * * * *');
    const [newCommand, setNewCommand] = useState('');
    const [destinations, setDestinations] = useState<CloudDestination[]>([]);
    const [selectedDestination, setSelectedDestination] = useState<string>('');

    const loadDestinations = useCallback(async () => {
        try {
            const res = await api.get('/cloud-storage/');
            const allDestinations = Array.isArray(res.data) ? res.data : res.data.results || [];
            const relevant = allDestinations.filter((d: any) => !d.service || String(d.service) === serviceId);
            setDestinations(relevant);
        } catch (err) {
            console.error('Failed to load cloud destinations', err);
        }
    }, [serviceId]);

    const loadJobs = useCallback(async () => {
        try {
            const data = await servicesApi.getCronJobs(serviceId);
            setJobs(data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    useEffect(() => {
        void loadJobs();
        void loadDestinations();
        const interval = setInterval(loadJobs, 10000);
        return () => clearInterval(interval);
    }, [loadJobs, loadDestinations]);

    const handleAdd = async () => {
        if (!newName || !newSchedule || !newCommand) return;
        try {
            await servicesApi.createCronJob(serviceId, {
                name: newName,
                schedule: newSchedule,
                command: newCommand,
                ...(selectedDestination ? { cloud_destination: selectedDestination } : {})
            });
            setNewName('');
            setNewCommand('');
            await loadJobs();
            toast({ title: "Cron Job added" });
        } catch (err) {
            toast({ title: "Failed to add cron job", variant: "destructive" });
        }
    };

    const handleDelete = async (id: number) => {
        if (!await confirm({ title: 'Delete scheduled task?', message: 'Are you sure you want to delete this scheduled task?', variant: 'destructive', confirmText: 'Delete' })) return;
        try {
            await servicesApi.deleteCronJob(serviceId, id);
            await loadJobs();
            toast({ title: "Cron Job deleted" });
        } catch (err) {
            toast({ title: "Failed to delete", variant: "destructive" });
        }
    };

    if (loading) return <div className="p-4 text-center">Loading scheduled tasks...</div>;

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            <Card className="p-6 border-border shadow-md">
                <div className="flex items-center justify-between mb-6">
                    <div>
                        <h3 className="font-bold text-lg">Scheduled Tasks (Cron)</h3>
                        <p className="text-sm text-muted-foreground">
                            Run commands inside your container on a schedule.
                        </p>
                    </div>
                    <Clock className="w-10 h-10 text-muted-foreground/20" />
                </div>

                {/* Add Form */}
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8 bg-muted/30 p-4 rounded-lg border border-border">
                    <div className="col-span-1">
                        <Input
                            placeholder="Job Name"
                            value={newName}
                            onChange={(e) => setNewName(e.target.value)}
                        />
                    </div>
                    <div className="col-span-1">
                        <Input
                            placeholder="Schedule (e.g. */5 * * * *)"
                            className="font-mono"
                            value={newSchedule}
                            onChange={(e) => setNewSchedule(e.target.value)}
                        />
                    </div>
                    <div className="col-span-1 md:col-span-2 flex flex-col gap-2">
                        <Input
                            placeholder="Command (e.g. python manage.py clear_cache)"
                            className="font-mono flex-1"
                            value={newCommand}
                            onChange={(e) => setNewCommand(e.target.value)}
                        />
                        <div className="flex gap-2 w-full">
                            <select
                                value={selectedDestination}
                                onChange={(e) => setSelectedDestination(e.target.value)}
                                className="flex-1 px-3 py-2 rounded-lg bg-background border border-border text-sm h-[40px]"
                            >
                                <option value="">Log to Console Only</option>
                                {destinations.map(d => (
                                    <option key={d.id} value={d.id}>
                                        Save logs to {d.name} ({d.provider_display})
                                    </option>
                                ))}
                            </select>
                            <Button onClick={handleAdd}>
                                <Plus className="w-4 h-4 mr-2" /> Add
                            </Button>
                        </div>
                    </div>
                </div>

                {/* List */}
                <div className="space-y-3">
                    {jobs.length === 0 ? (
                        <p className="text-center text-muted-foreground italic py-8">No scheduled tasks.</p>
                    ) : (
                        jobs.map((job) => (
                            <div key={job.id} className="flex items-center justify-between p-4 bg-card border border-border rounded-lg group">
                                <div className="flex items-center gap-4">
                                    <div className="bg-primary/10 p-2 rounded-full">
                                        <Clock className="w-5 h-5 text-primary" />
                                    </div>
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <span className="font-bold">{job.name}</span>
                                            <span className="text-xs bg-muted px-2 py-0.5 rounded font-mono">{job.schedule}</span>
                                        </div>
                                        <code className="text-xs text-muted-foreground block mt-1 font-mono">
                                            {job.command}
                                        </code>
                                        {job.last_run_at && (
                                            <p className="text-[10px] text-emerald-500 mt-1 flex items-center gap-1">
                                                <CheckCircle2 size={10} /> Last run {formatDistanceToNow(new Date(job.last_run_at), { addSuffix: true })}
                                            </p>
                                        )}
                                    </div>
                                </div>
                                <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                    <Button variant="ghost" size="icon" className="hover:bg-primary/10 hover:text-primary">
                                        <Play className="w-4 h-4" /> {/* Trigger manually (future) */}
                                    </Button>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="text-destructive hover:text-destructive hover:bg-destructive/10"
                                        onClick={() => handleDelete(job.id)}
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </Button>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </Card>
        </div>
    );
});
