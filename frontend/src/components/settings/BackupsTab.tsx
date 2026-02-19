'use client';

import React, { useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Loader2, Download, RotateCcw, Trash2, Plus, Clock } from 'lucide-react';
import { useToast } from '@/components/ui/use-toast';
import api from '@/lib/api';

export default function BackupsTab({ serviceId }: { serviceId: string }) {
    const { toast } = useToast();
    const [backups, setBackups] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [creating, setCreating] = useState(false);

    useEffect(() => {
        loadBackups();
    }, [serviceId]);

    const loadBackups = async () => {
        try {
            const res = await api.get(`/services/${serviceId}/backups/`);
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
        if (!confirm("Are you sure? This will overwrite the current service state.")) return;
        try {
            await api.post(`/backups/${id}/restore/`);
            toast({ title: "Restore Started", description: "Service will restart once restored." });
        } catch (err) {
            toast({ title: "Error", description: "Failed to trigger restore.", variant: "destructive" });
        }
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
                                    <TableCell>{(backup.size_bytes / 1024 / 1024).toFixed(2)} MB</TableCell>
                                    <TableCell>{backup.status}</TableCell>
                                    <TableCell className="text-right space-x-2">
                                        <Button variant="ghost" size="sm" onClick={() => handleRestore(backup.id)} title="Restore">
                                            <RotateCcw className="w-4 h-4" />
                                        </Button>
                                        <Button variant="ghost" size="sm" asChild title="Download">
                                            <a href={`/api/v1/backups/${backup.id}/download/`} target="_blank" rel="noopener noreferrer">
                                                <Download className="w-4 h-4" />
                                            </a>
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {backups.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center py-6 text-muted-foreground">No backups found.</TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle>Schedule</CardTitle>
                    <CardDescription>Automated backup frequency.</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex items-center gap-4 p-4 border rounded bg-muted/20">
                        <Clock className="text-muted-foreground" />
                        <div>
                            <p className="font-medium">Daily at 3:00 AM UTC</p>
                            <p className="text-xs text-muted-foreground">Retention: 7 days</p>
                        </div>
                        <div className="ml-auto">
                            <Button variant="outline" size="sm">Configure</Button>
                        </div>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
