'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { servicesApi, serversApi, Deployment, ManagedServer } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { GitCommit, RotateCcw, Clock, CheckCircle2, XCircle, Loader2, ChevronDown, ChevronRight, Rocket, Brain, Timer, Ban, Eye, CheckCheck, Trash2, ArrowUpCircle, Server, UploadCloud } from 'lucide-react';
import { toast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { formatDistanceToNow } from 'date-fns';

const LOCAL_DEPLOY_TARGET = 'local';

export function DeploymentsTab({ serviceId }: { serviceId: string }) {
    const confirm = useConfirm();
    const [deployments, setDeployments] = useState<Deployment[]>([]);
    const [loading, setLoading] = useState(true);
    const [rollingBackId, setRollingBackId] = useState<string | null>(null);
    const [expandedId, setExpandedId] = useState<string | null>(null);
    const [expandedDetails, setExpandedDetails] = useState<Record<string, any>>({});
    const [redeploying, setRedeploying] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [uploadProgress, setUploadProgress] = useState(0);
    const [cancellingId, setCancellingId] = useState<string | null>(null);
    const [approvingId, setApprovingId] = useState<string | null>(null);
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
    const [bulkCancelling, setBulkCancelling] = useState(false);
    const [promotingId, setPromotingId] = useState<string | null>(null);
    const [servers, setServers] = useState<ManagedServer[]>([]);
    const [targetServerId, setTargetServerId] = useState<string>(LOCAL_DEPLOY_TARGET);

    const loadDeployments = useCallback(async () => {
        try {
            const data = await servicesApi.getDeployments(serviceId);
            setDeployments(data);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    }, [serviceId]);

    useEffect(() => {
        void loadDeployments();
        const interval = setInterval(loadDeployments, 5000);
        return () => clearInterval(interval);
    }, [loadDeployments]);

    useEffect(() => {
        const init = async () => {
            try {
                const [srv, sList] = await Promise.all([
                    servicesApi.get(serviceId),
                    serversApi.list(),
                ]);
                setServers(sList);
                setTargetServerId((srv.node_metadata?.id) || srv.server_id || LOCAL_DEPLOY_TARGET);
            } catch (err) { console.error(err); }
        };
        init();
    }, [serviceId]);

    const handleRedeploy = async () => {
        try {
            setRedeploying(true);
            const deployResult = await servicesApi.deploy(serviceId, 'HEAD', targetServerId);
            if (deployResult?.existing_deployment) {
                const statusLabel = deployResult?.existing_deployment?.status || 'in progress';
                toast({
                    title: "Deployment already in progress",
                    description: `Current deployment status: ${statusLabel}.`,
                });
                setRedeploying(false);
                return;
            }
            toast({ title: "Redeployment triggered", description: "A new deployment has started." });
            setTimeout(() => { void loadDeployments(); setRedeploying(false); }, 2000);
        } catch (err) {
            console.error(err);
            toast({ title: "Redeploy failed", variant: "destructive" });
            setRedeploying(false);
        }
    };

    const handleUploadDeploy = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;
        try {
            setUploading(true);
            setUploadProgress(0);
            await servicesApi.uploadDeploy(serviceId, file, (progressEvent: any) => {
                if (progressEvent.total) {
                    const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
                    setUploadProgress(percent);
                }
            });
            toast({ title: "Upload deploy triggered", description: `Uploaded ${file.name} (100%) and started deployment.` });
            setTimeout(() => { void loadDeployments(); setUploading(false); setUploadProgress(0); }, 2000);
        } catch (err: any) {
            console.error(err);
            const msg = err?.response?.data?.error || err?.message || "Upload deploy failed";
            toast({ title: msg, variant: "destructive" });
            setUploading(false);
            setUploadProgress(0);
        } finally {
            e.target.value = '';
        }
    };

    const getDeploymentProgress = (status: string) => {
        switch (status) {
            case 'QUEUED': return 15;
            case 'REVIEW': return 35;
            case 'BUILDING': return 65;
            case 'DEPLOYING': return 85;
            case 'ACTIVE': return 100;
            case 'SUCCESS': return 100;
            default: return 0;
        }
    };

    const handleRollback = async (deployment: Deployment) => {
        const shortCommit = deployment.commit_hash?.substring(0, 7) ?? 'unknown';
        if (!await confirm({
            title: 'Rollback deployment?',
            message: `Roll back to commit ${shortCommit}? A new deployment will be created from this successful release.`,
            confirmText: 'Rollback',
            variant: 'destructive',
        })) return;

        try {
            setRollingBackId(deployment.id);
            await servicesApi.rollback(deployment.id);
            toast({ title: "Rollback initiated", description: `Rolling back to ${shortCommit}.` });
            setTimeout(() => { void loadDeployments(); }, 2000);
        } catch (err: any) {
            const code = err?.response?.data?.code;
            const detail =
                err?.response?.data?.user_action
                || err?.response?.data?.error
                || err?.response?.data?.message
                || err?.message
                || 'Rollback request failed';
            toast({
                title: code ? `Rollback blocked (${code})` : 'Rollback failed',
                description: detail,
                variant: 'destructive',
            });
            console.error(err);
        } finally {
            setRollingBackId(null);
        }
    };

    const handleCancel = async (deployment: Deployment) => {
        if (!await confirm({ title: 'Cancel deployment?', message: `Cancel deployment ${deployment.commit_hash.substring(0, 7)}?`, variant: 'destructive', confirmText: 'Cancel Deployment' })) return;
        try {
            setCancellingId(deployment.id);
            await servicesApi.cancelDeployment(deployment.id);
            toast({ title: "Deployment cancelled" });
            setTimeout(() => { void loadDeployments(); }, 1000);
        } catch (err: any) {
            console.error(err);
            const msg = err?.response?.data?.error?.message || err?.response?.data?.message || err?.message || "Cancel failed";
            toast({ title: msg, variant: "destructive" });
        } finally {
            setCancellingId(null);
        }
    };

    const handleApprove = async (deployment: Deployment) => {
        try {
            setApprovingId(deployment.id);
            await servicesApi.approveDeployment(deployment.id);
            toast({ title: "Deployment approved", description: "Build phase has started." });
            setTimeout(() => { void loadDeployments(); }, 2000);
        } catch (err: any) {
            console.error(err);
            const msg = err?.response?.data?.error?.message || err?.response?.data?.message || err?.message || 'Approve failed';
            toast({ title: msg, variant: "destructive" });
        } finally {
            setApprovingId(null);
        }
    };

    const handlePromote = async (deployment: Deployment) => {
        try {
            setPromotingId(deployment.id);
            await servicesApi.promoteDeployment(deployment.id);
            toast({ title: "Promotion triggered", description: "Routing will swap to the new container momentarily." });
            setTimeout(() => { void loadDeployments(); }, 2000);
        } catch (err: any) {
            console.error(err);
            const msg = err?.response?.data?.error || 'Promote failed';
            toast({ title: msg, variant: "destructive" });
        } finally {
            setPromotingId(null);
        }
    };

    const toggleSelect = (id: string) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
        });
    };

    const cancellableStatuses = ['QUEUED', 'REVIEW', 'BUILDING', 'FAILED', 'STAGED'];
    const cancellableIds = deployments.filter(d => cancellableStatuses.includes(d.status)).map(d => d.id);
    const allSelected = cancellableIds.length > 0 && cancellableIds.every(id => selectedIds.has(id));

    const toggleSelectAll = () => {
        if (allSelected) {
            setSelectedIds(new Set());
        } else {
            setSelectedIds(new Set(cancellableIds));
        }
    };

    const handleBulkCancel = async () => {
        const ids = Array.from(selectedIds);
        if (ids.length === 0) return;
        if (!await confirm({ title: `Cancel ${ids.length} deployment(s)?`, message: `This will cancel ${ids.length} selected deployment(s).`, variant: 'destructive', confirmText: 'Cancel All Selected' })) return;
        try {
            setBulkCancelling(true);
            const result = await servicesApi.bulkCancelDeployments(ids);
            toast({ title: result.message || `${result.cancelled} deployment(s) cancelled.` });
            setSelectedIds(new Set());
            setTimeout(() => { void loadDeployments(); }, 1000);
        } catch (err: any) {
            console.error(err);
            const msg = err?.response?.data?.error?.message || err?.response?.data?.message || err?.message || "Bulk cancel failed";
            toast({ title: msg, variant: "destructive" });
        } finally {
            setBulkCancelling(false);
        }
    };

    const toggleExpand = async (d: Deployment) => {
        if (expandedId === d.id) {
            setExpandedId(null);
            return;
        }
        setExpandedId(d.id);
        // Fetch full details if not cached
        if (!expandedDetails[d.id]) {
            try {
                const details = await servicesApi.getDeployment(d.id);
                setExpandedDetails(prev => ({ ...prev, [d.id]: details }));
            } catch (err) {
                console.error(err);
            }
        }
    };

    if (loading) return <div className="p-4 text-center">Loading deployments...</div>;

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'ACTIVE': return <CheckCircle2 className="w-5 h-5 text-emerald-500" />;
            case 'STAGED': return <ArrowUpCircle className="w-5 h-5 text-amber-500 animate-pulse" />;
            case 'FAILED': return <XCircle className="w-5 h-5 text-red-500" />;
            case 'CANCELLED': return <Ban className="w-5 h-5 text-muted-foreground" />;
            case 'REVIEW': return <Eye className="w-5 h-5 text-amber-500 animate-pulse" />;
            case 'BUILDING':
            case 'DEPLOYING': return <Loader2 className="w-5 h-5 text-blue-500 animate-spin" />;
            default: return <Clock className="w-5 h-5 text-muted-foreground" />;
        }
    };

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'ACTIVE': return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30';
            case 'STAGED': return 'bg-amber-500/10 text-amber-500 border-amber-500/30';
            case 'FAILED': return 'bg-red-500/10 text-red-500 border-red-500/30';
            case 'REVIEW': return 'bg-amber-500/10 text-amber-500 border-amber-500/30';
            case 'BUILDING':
            case 'DEPLOYING': return 'bg-blue-500/10 text-blue-500 border-blue-500/30';
            default: return 'bg-muted text-muted-foreground border-border';
        }
    };

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
            <Card className="border-border shadow-md overflow-hidden">
                <div className="p-6 border-b border-border flex justify-between items-center">
                    <div>
                        <h3 className="font-bold text-lg">Deployment History</h3>
                        <p className="text-sm text-muted-foreground">
                            Click a deployment to see details. Rollback or redeploy as needed.
                        </p>
                    </div>
                    <div className="flex gap-2 items-center">
                        {cancellableIds.length > 0 && (
                            <label className="flex items-center gap-1.5 text-xs cursor-pointer text-muted-foreground hover:text-foreground">
                                <input
                                    type="checkbox"
                                    checked={allSelected}
                                    onChange={toggleSelectAll}
                                    className="rounded border-border"
                                />
                                Select All
                            </label>
                        )}
                        {selectedIds.size > 0 && (
                            <Button
                                variant="destructive"
                                size="sm"
                                onClick={handleBulkCancel}
                                disabled={bulkCancelling}
                                className="gap-2"
                            >
                                {bulkCancelling ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                                Cancel Selected ({selectedIds.size})
                            </Button>
                        )}
                        <Button variant="outline" size="sm" onClick={loadDeployments}>
                            Refresh
                        </Button>
                        <div className="flex items-center gap-2">
                            <select
                                value={targetServerId}
                                onChange={(e) => setTargetServerId(e.target.value || LOCAL_DEPLOY_TARGET)}
                                className="bg-card border border-border rounded-md px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                            >
                                <option value={LOCAL_DEPLOY_TARGET}>Local Server</option>
                                {servers.filter((s) => !s.is_primary).map((s) => (
                                    <option key={s.id} value={s.id}>
                                        {s.name}
                                    </option>
                                ))}
                            </select>
                            <Button
                                size="sm"
                                onClick={handleRedeploy}
                                disabled={redeploying || uploading}
                                className="gap-2"
                            >
                                {redeploying ? <Loader2 className="w-4 h-4 animate-spin" /> : <Rocket className="w-4 h-4" />}
                                {redeploying ? 'Deploying...' : 'Redeploy'}
                            </Button>
                            <label className={`inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-9 px-3 cursor-pointer ${uploading ? 'opacity-50 pointer-events-none' : ''}`}>
                                <input
                                    type="file"
                                    accept=".zip,.tar.gz,.tgz"
                                    onChange={handleUploadDeploy}
                                    disabled={uploading}
                                    className="hidden"
                                />
                                {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <UploadCloud className="w-4 h-4 text-primary" />}
                                {uploading ? 'Uploading...' : 'Upload & Deploy'}
                            </label>
                        </div>
                    </div>
                </div>

                {uploading && (
                    <div className="p-4 bg-muted/20 border-b border-border space-y-2 animate-in fade-in">
                        <div className="flex justify-between text-sm font-medium">
                            <span className="flex items-center gap-2">
                                <Loader2 className="w-4 h-4 animate-spin text-primary" />
                                Uploading & Deploying Service Archive...
                            </span>
                            <span className="font-bold text-primary">{uploadProgress}%</span>
                        </div>
                        <Progress value={uploadProgress} className="h-2 w-full" />
                    </div>
                )}

                <div className="divide-y divide-border">
                    {deployments.length === 0 ? (
                        <div className="p-8 text-center text-muted-foreground">
                            No deployments found.
                        </div>
                    ) : (
                        deployments.map((d, idx) => {
                            const isExpanded = expandedId === d.id;
                            const isLive = d.status === 'ACTIVE' && idx === deployments.findIndex(x => x.status === 'ACTIVE');
                            const details = expandedDetails[d.id] || d;
                            const deployPercent = getDeploymentProgress(d.status);
                            const isDeploying = ['QUEUED', 'REVIEW', 'BUILDING', 'DEPLOYING'].includes(d.status);
                            return (
                                <div key={d.id}>
                                    {/* Clickable Row */}
                                    <div
                                        className={`p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4 hover:bg-muted/30 transition-colors cursor-pointer ${isLive ? 'bg-emerald-500/5 border-l-2 border-l-emerald-500' : ''}`}
                                        onClick={() => toggleExpand(d)}
                                    >
                                        <div className="flex items-center gap-4 w-full sm:w-auto">
                                            {/* Checkbox for bulk cancel */}
                                            {cancellableStatuses.includes(d.status) && (
                                                <input
                                                    type="checkbox"
                                                    checked={selectedIds.has(d.id)}
                                                    onChange={(e) => { e.stopPropagation(); toggleSelect(d.id); }}
                                                    onClick={(e) => e.stopPropagation()}
                                                    className="rounded border-border"
                                                />
                                            )}
                                            <div className="bg-muted p-2 rounded-full">
                                                {getStatusIcon(d.status)}
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-center gap-2">
                                                    {isLive ? (
                                                        <span className="text-sm font-bold uppercase px-2.5 py-0.5 rounded text-[10px] border bg-emerald-500 text-white border-emerald-600 flex items-center gap-1.5">
                                                            <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse" />
                                                            LIVE
                                                        </span>
                                                    ) : (
                                                        <span className={`text-sm font-bold uppercase px-2 py-0.5 rounded text-[10px] border ${getStatusColor(d.status)}`}>
                                                            {d.status}
                                                        </span>
                                                    )}
                                                    <span className="text-xs text-muted-foreground">
                                                        {d.created_at ? formatDistanceToNow(new Date(d.created_at), { addSuffix: true }) : 'Unknown time'}
                                                    </span>
                                                </div>
                                                <div className="flex items-center gap-2 mt-1">
                                                    {d.commit_message?.includes('Upload') || d.commit_hash?.startsWith('upload-') ? <UploadCloud className="w-4 h-4 text-primary" /> : <GitCommit className="w-4 h-4 text-muted-foreground" />}
                                                    <span className="font-mono text-sm">{d.commit_hash.substring(0, 7)}</span>
                                                    <span className="text-sm text-muted-foreground truncate max-w-[200px] md:max-w-[400px]">
                                                        {d.commit_message || 'No commit message'}
                                                    </span>
                                                </div>
                                                {isDeploying && (
                                                    <div className="w-full mt-2.5 space-y-1 pr-4">
                                                        <div className="flex justify-between text-[11px] font-medium text-muted-foreground">
                                                            <span>Deploying ({d.status})...</span>
                                                            <span className="text-primary font-bold">{deployPercent}%</span>
                                                        </div>
                                                        <Progress value={deployPercent} className="h-1.5 w-full bg-secondary" />
                                                    </div>
                                                )}
                                            </div>
                                        </div>

                                        <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap mt-2 sm:mt-0 w-full sm:w-auto justify-end sm:justify-start">
                                            {/* Approve button for REVIEW */}
                                            {d.status === 'REVIEW' && (
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="text-emerald-500 hover:text-emerald-400 hover:bg-emerald-500/10"
                                                    onClick={(e) => { e.stopPropagation(); handleApprove(d); }}
                                                    disabled={!!approvingId}
                                                >
                                                    {approvingId === d.id ? (
                                                        <Loader2 className="w-4 h-4 animate-spin" />
                                                    ) : (
                                                        <><CheckCheck className="w-4 h-4 mr-1" /> Approve</>
                                                    )}
                                                </Button>
                                            )}
                                            {/* Promote Now button for STAGED */}
                                            {d.status === 'STAGED' && (
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="text-amber-500 hover:text-amber-400 hover:bg-amber-500/10"
                                                    onClick={(e) => { e.stopPropagation(); handlePromote(d); }}
                                                    disabled={!!promotingId}
                                                >
                                                    {promotingId === d.id ? (
                                                        <Loader2 className="w-4 h-4 animate-spin" />
                                                    ) : (
                                                        <><ArrowUpCircle className="w-4 h-4 mr-1" /> Promote Now</>
                                                    )}
                                                </Button>
                                            )}
                                            {/* Cancel button for QUEUED / REVIEW / BUILDING / STAGED */}
                                            {['QUEUED', 'REVIEW', 'BUILDING', 'STAGED'].includes(d.status) && (
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="text-red-400 hover:text-red-300 hover:bg-red-500/10"
                                                    onClick={(e) => { e.stopPropagation(); handleCancel(d); }}
                                                    disabled={!!cancellingId}
                                                >
                                                    {cancellingId === d.id ? (
                                                        <Loader2 className="w-4 h-4 animate-spin" />
                                                    ) : (
                                                        <><Ban className="w-4 h-4 mr-1" /> Cancel</>
                                                    )}
                                                </Button>
                                            )}
                                            {/* Rollback is only available on SUCCESSFUL deployments. Rolling back to
                                    a FAILED or CANCELLED release would just re-run the broken code,
                                    which is never what the user wants. The backend also enforces
                                    this with ROLLBACK_TARGET_NOT_SUCCESSFUL. */}
                                            {(['ACTIVE', 'INACTIVE'].includes(d.status)) && d.commit_hash && (
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="text-muted-foreground hover:text-foreground"
                                                    onClick={(e) => { e.stopPropagation(); handleRollback(d); }}
                                                    disabled={!!rollingBackId}
                                                >
                                                    {rollingBackId === d.id ? (
                                                        <Loader2 className="w-4 h-4 animate-spin" />
                                                    ) : (
                                                        <><RotateCcw className="w-4 h-4 mr-2" /> Rollback</>
                                                    )}
                                                </Button>
                                            )}
                                            {isExpanded ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
                                        </div>
                                    </div>

                                    {/* Expanded Details */}
                                    {isExpanded && (
                                        <div className="px-6 pb-6 bg-muted/10 border-t border-border animate-in fade-in slide-in-from-top-2 duration-200">
                                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
                                                <div className="bg-card border border-border rounded-lg p-4">
                                                    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                                                        <Timer className="w-3 h-3" /> Duration
                                                    </div>
                                                    <p className="font-bold text-lg">
                                                        {details.duration_seconds ? `${details.duration_seconds.toFixed(1)}s` : '—'}
                                                    </p>
                                                </div>
                                                <div className="bg-card border border-border rounded-lg p-4">
                                                    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                                                        <GitCommit className="w-3 h-3" /> Commit
                                                    </div>
                                                    <p className="font-mono text-sm font-bold">{details.commit_hash?.substring(0, 12)}</p>
                                                </div>
                                                <div className="bg-card border border-border rounded-lg p-4">
                                                    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                                                        <Clock className="w-3 h-3" /> Created
                                                    </div>
                                                    <p className="text-sm font-bold">
                                                        {details.created_at ? new Date(details.created_at).toLocaleString() : '—'}
                                                    </p>
                                                </div>
                                            </div>

                                            {/* Build Logs */}
                                            {details.build_logs && (
                                                <div className="mt-4">
                                                    <h4 className="text-sm font-bold mb-2 text-muted-foreground">Build Logs</h4>
                                                    <div className="bg-slate-950 text-emerald-400 font-mono text-xs p-4 rounded-lg max-h-64 overflow-y-auto whitespace-pre-wrap">
                                                        {details.build_logs}
                                                    </div>
                                                </div>
                                            )}

                                            {/* AI Diagnosis */}
                                            {details.ai_diagnosis && (
                                                <div className="mt-4">
                                                    <h4 className="text-sm font-bold mb-2 flex items-center gap-2 text-muted-foreground">
                                                        <Brain className="w-4 h-4 text-purple-500" /> AI Diagnosis
                                                    </h4>
                                                    <div className="bg-purple-500/5 border border-purple-500/20 text-sm p-4 rounded-lg whitespace-pre-wrap">
                                                        {details.ai_diagnosis}
                                                    </div>
                                                </div>
                                            )}

                                            {/* Error details for failed */}
                                            {details.error_message && (
                                                <div className="mt-4">
                                                    <h4 className="text-sm font-bold mb-2 text-red-500">Error</h4>
                                                    <div className="bg-red-500/5 border border-red-500/20 text-sm p-4 rounded-lg text-red-400 font-mono whitespace-pre-wrap">
                                                        {details.error_message}
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            );
                        })
                    )}
                </div>
            </Card>
        </div>
    );
}
