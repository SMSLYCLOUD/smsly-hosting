'use client';

import React, { useState, useEffect } from 'react';
import { servicesApi, Deployment } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { GitCommit, RotateCcw, Clock, CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';
import { formatDistanceToNow } from 'date-fns';

export function DeploymentsTab({ serviceId }: { serviceId: string }) {
    const [deployments, setDeployments] = useState<Deployment[]>([]);
    const [loading, setLoading] = useState(true);
    const [rollingBackId, setRollingBackId] = useState<string | null>(null);

    useEffect(() => {
        loadDeployments();
    }, [serviceId]);

    const loadDeployments = async () => {
        try {
            const data = await servicesApi.getDeployments(serviceId);
            setDeployments(data);
        } catch (err) {
            console.error(err);
            toast({ title: "Failed to load deployments", variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    const handleRollback = async (deployment: Deployment) => {
        if (!confirm(`Rollback to commit ${deployment.commit_hash.substring(0, 7)}? This will trigger a new deployment.`)) return;

        try {
            setRollingBackId(deployment.id);
            // We need a backend endpoint for this.
            // Assuming POST /api/v1/deployments/{id}/rollback/ exists or we trigger a new deploy with manual commit.
            // For now, let's call the generic trigger endpoint but pass the commit hash if supported,
            // OR use a new method we'll add to api.ts: servicesApi.rollback(deploymentId)

            await servicesApi.rollback(deployment.id);

            toast({ title: "Rollback initiated", description: "A new deployment has started." });
            // Wait a bit then refresh
            setTimeout(loadDeployments, 2000);
        } catch (err) {
            console.error(err);
            toast({ title: "Rollback failed", variant: "destructive" });
        } finally {
            setRollingBackId(null);
        }
    };

    if (loading) return <div className="p-4 text-center">Loading deployments...</div>;

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'ACTIVE': return <CheckCircle2 className="w-5 h-5 text-emerald-500" />;
            case 'FAILED': return <XCircle className="w-5 h-5 text-red-500" />;
            case 'BUILDING':
            case 'DEPLOYING': return <Loader2 className="w-5 h-5 text-blue-500 animate-spin" />;
            default: return <Clock className="w-5 h-5 text-muted-foreground" />;
        }
    };

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            <Card className="border-border shadow-md overflow-hidden">
                <div className="p-6 border-b border-border flex justify-between items-center">
                    <div>
                        <h3 className="font-bold text-lg">Deployment History</h3>
                        <p className="text-sm text-muted-foreground">
                            View past deployments and rollback if necessary.
                        </p>
                    </div>
                    <Button variant="outline" size="sm" onClick={loadDeployments}>
                        Refresh
                    </Button>
                </div>

                <div className="divide-y divide-border">
                    {deployments.length === 0 ? (
                        <div className="p-8 text-center text-muted-foreground">
                            No deployments found.
                        </div>
                    ) : (
                        deployments.map((d) => (
                            <div key={d.id} className="p-4 flex items-center justify-between hover:bg-muted/20 transition-colors">
                                <div className="flex items-center gap-4">
                                    <div className="bg-muted p-2 rounded-full">
                                        {getStatusIcon(d.status)}
                                    </div>
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <span className={`text-sm font-bold uppercase px-2 py-0.5 rounded text-[10px]
                                                ${d.status === 'ACTIVE' ? 'bg-emerald-500/10 text-emerald-500' :
                                                  d.status === 'FAILED' ? 'bg-red-500/10 text-red-500' : 'bg-blue-500/10 text-blue-500'}`}>
                                                {d.status}
                                            </span>
                                            <span className="text-xs text-muted-foreground">
                                                {d.created_at ? formatDistanceToNow(new Date(d.created_at), { addSuffix: true }) : 'Unknown time'}
                                            </span>
                                        </div>
                                        <div className="flex items-center gap-2 mt-1">
                                            <GitCommit className="w-4 h-4 text-muted-foreground" />
                                            <span className="font-mono text-sm">{d.commit_hash.substring(0, 7)}</span>
                                            <span className="text-sm text-muted-foreground truncate max-w-[200px] md:max-w-[400px]">
                                                {d.commit_message || 'No commit message'}
                                            </span>
                                        </div>
                                    </div>
                                </div>

                                <div className="flex items-center gap-2">
                                    {/* Only allow rollback if it's not currently building/deploying */}
                                    {['ACTIVE', 'FAILED', 'CANCELLED'].includes(d.status) && (
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            className="text-muted-foreground hover:text-foreground"
                                            onClick={() => handleRollback(d)}
                                            disabled={!!rollingBackId}
                                        >
                                            {rollingBackId === d.id ? (
                                                <Loader2 className="w-4 h-4 animate-spin" />
                                            ) : (
                                                <>
                                                    <RotateCcw className="w-4 h-4 mr-2" /> Rollback
                                                </>
                                            )}
                                        </Button>
                                    )}
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </Card>
        </div>
    );
}
