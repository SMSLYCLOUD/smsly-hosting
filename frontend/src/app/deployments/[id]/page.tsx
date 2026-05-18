"use client";

import React, { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { useSpaceOps } from "@/context/SpaceOpsContext";
import { Loader2, CheckCircle, XCircle, Rocket, Terminal, GitBranch, Clock, AlertTriangle, ArrowLeft } from "lucide-react";
import api from "@/lib/api";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { useConfirm } from "@/components/ui/confirm-dialog";

export default function DeploymentWatchPage() {
  const params = useParams();
  const id = params.id as string;
  const { setSpaceOpsState, resetSpaceOpsState } = useSpaceOps();
  const [deployment, setDeployment] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState<string>("Initializing deployment watch...\n");
  const confirm = useConfirm();

  const fetchDeployment = async () => {
    try {
        const res = await api.get(`/deployments/${id}/`);
        setDeployment(res.data);

        // Map status to SpaceOps Visual Layer
        const status = res.data.status;
        if (status === 'ACTIVE' || status === 'SUCCESS') {
            setSpaceOpsState({ mode: 'success', intensity: 'low' });
        } else if (status === 'FAILED') {
            setSpaceOpsState({ mode: 'failed', intensity: 'medium' });
        } else if (['PENDING', 'BUILDING', 'DEPLOYING'].includes(status)) {
            setSpaceOpsState({ mode: 'deploying', intensity: 'high' });
        } else {
            setSpaceOpsState({ mode: 'idle', intensity: 'low' });
        }
    } catch (err) {
        console.error("Failed to fetch deployment:", err);
    } finally {
        setLoading(false);
    }
  };

  useEffect(() => {
    fetchDeployment();
    const interval = setInterval(fetchDeployment, 5000);
    return () => {
        clearInterval(interval);
        resetSpaceOpsState(); // Reset background on unmount
    };
  }, [id]);

  useEffect(() => {
    // Simulate streaming logs if active
    if (!deployment || ['ACTIVE', 'SUCCESS', 'FAILED'].includes(deployment.status)) return;

    const interval = setInterval(() => {
        setLogs(prev => prev + `[${new Date().toISOString()}] Waiting for status update...\n`);
    }, 3000);
    return () => clearInterval(interval);
  }, [deployment?.status]);

  if (loading) {
    return (
      <DashboardShell>
         <div className="flex-1 flex items-center justify-center p-8 relative z-10">
            <Loader2 className="h-8 w-8 animate-spin text-emerald-500" />
         </div>
      </DashboardShell>
    );
  }

  if (!deployment) {
      return (
          <DashboardShell>
              <div className="flex-1 p-8 relative z-10">
                  <div className="max-w-4xl mx-auto text-center mt-20">
                      <h2 className="text-2xl font-bold mb-4">Deployment Not Found</h2>
                      <Link href="/deployments"><Button>Back to Deployments</Button></Link>
                  </div>
              </div>
          </DashboardShell>
      );
  }

  const isFailed = deployment.status === 'FAILED';
  const isActive = ['ACTIVE', 'SUCCESS'].includes(deployment.status);
  const isDeploying = ['PENDING', 'BUILDING', 'DEPLOYING'].includes(deployment.status);

  return (
    <DashboardShell>
        <div className="flex-1 p-4 pt-safe sm:p-8 relative z-10 w-full overflow-x-hidden">
            <div className="max-w-6xl mx-auto space-y-6">
                <div className="flex items-center gap-4 mb-6">
                    <Link href="/deployments">
                        <Button variant="ghost" size="icon" className="rounded-full bg-muted/50 hover:bg-muted">
                            <ArrowLeft size={18} />
                        </Button>
                    </Link>
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
                            Deployment Watch: {deployment.service_name || deployment.service}
                        </h1>
                        <p className="text-muted-foreground flex items-center gap-2 mt-1 text-sm">
                            <GitBranch size={14} /> {deployment.commit_hash?.slice(0, 7) || 'latest'}
                            <span className="mx-2 text-muted-foreground/30">•</span>
                            <Clock size={14} /> {new Date(deployment.created_at).toLocaleString()}
                        </p>
                    </div>
                    <div className="ml-auto flex items-center gap-3">
                        <Badge variant={isActive ? 'default' : isFailed ? 'destructive' : 'secondary'} className="text-sm px-3 py-1 font-bold tracking-wider">
                            {isDeploying && <Loader2 size={12} className="animate-spin mr-2 inline" />}
                            {deployment.status}
                        </Badge>
                    </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* Status Timeline (Left) */}
                    <div className="space-y-6">
                        <Card className="bg-card/80 backdrop-blur-sm border-border">
                            <CardHeader>
                                <CardTitle className="text-sm flex items-center gap-2 text-muted-foreground uppercase tracking-wider">
                                    <Rocket size={16} /> Mission Control
                                </CardTitle>
                            </CardHeader>
                            <CardContent>
                                <div className="space-y-4">
                                    <div className="flex items-center gap-3 text-sm">
                                        <CheckCircle size={16} className="text-emerald-500" />
                                        <span className="font-medium">Queued</span>
                                    </div>
                                    <div className="flex items-center gap-3 text-sm">
                                        {(isDeploying && deployment.status !== 'PENDING') || isActive || isFailed ? (
                                            <CheckCircle size={16} className="text-emerald-500" />
                                        ) : isDeploying ? (
                                            <Loader2 size={16} className="text-blue-500 animate-spin" />
                                        ) : (
                                            <div className="w-4 h-4 rounded-full border-2 border-muted" />
                                        )}
                                        <span className="font-medium text-foreground">Building</span>
                                    </div>
                                    <div className="flex items-center gap-3 text-sm">
                                        {isActive || isFailed ? (
                                            <CheckCircle size={16} className="text-emerald-500" />
                                        ) : deployment.status === 'DEPLOYING' ? (
                                            <Loader2 size={16} className="text-purple-500 animate-spin" />
                                        ) : (
                                            <div className="w-4 h-4 rounded-full border-2 border-muted" />
                                        )}
                                        <span className="font-medium text-foreground">Deploying</span>
                                    </div>
                                    <div className="flex items-center gap-3 text-sm">
                                        {isActive ? (
                                            <CheckCircle size={16} className="text-emerald-500" />
                                        ) : isFailed ? (
                                            <XCircle size={16} className="text-red-500" />
                                        ) : (
                                            <div className="w-4 h-4 rounded-full border-2 border-muted" />
                                        )}
                                        <span className="font-medium text-foreground">Health Check</span>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>

                        {/* Rollback CTA */}
                        <AnimatePresence>
                            {isFailed && (
                                <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
                                    <Card className="bg-red-500/10 border-red-500/20">
                                        <CardContent className="p-4 flex flex-col gap-3">
                                            <div className="flex items-center gap-2 text-red-500 font-bold text-sm">
                                                <AlertTriangle size={16} /> Deployment Failed
                                            </div>
                                            <p className="text-xs text-muted-foreground">
                                                The deployment failed to pass health checks or build constraints. The service has been kept on the previous healthy version.
                                            </p>
                                            <Button variant="outline" className="w-full text-red-500 hover:bg-red-500/20 hover:text-red-500 border-red-500/30" onClick={async () => {
                                                const confirmed = await confirm({
                                                    title: "Rollback to previous?",
                                                    message: "Are you sure you want to attempt a rollback to the previous version?",
                                                    confirmText: "Yes, Rollback",
                                                    variant: "destructive"
                                                });
                                                if (confirmed) {
                                                    setSpaceOpsState({ mode: 'recovering', intensity: 'high' });
                                                    // Placeholder: call API here in real implementation
                                                }
                                            }}>
                                                Initiate Rollback
                                            </Button>
                                        </CardContent>
                                    </Card>
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </div>

                    {/* Logs (Main) */}
                    <div className="lg:col-span-2">
                        <Card className="bg-zinc-950/80 backdrop-blur-md border-border h-[600px] flex flex-col overflow-hidden">
                            <CardHeader className="py-3 px-4 border-b border-border/50 bg-black/40 flex flex-row items-center justify-between">
                                <CardTitle className="text-sm font-mono text-muted-foreground flex items-center gap-2">
                                    <Terminal size={14} /> live_logs.log
                                </CardTitle>
                                {deployment.logs_url && (
                                    <a href={deployment.logs_url} target="_blank" rel="noreferrer" className="text-xs text-blue-400 hover:underline">
                                        View Raw Logs
                                    </a>
                                )}
                            </CardHeader>
                            <CardContent className="p-0 flex-1 relative">
                                <div className="absolute inset-0 overflow-y-auto p-4 font-mono text-[11px] leading-relaxed text-zinc-300">
                                    <pre className="whitespace-pre-wrap">{logs}</pre>
                                </div>
                            </CardContent>
                        </Card>
                    </div>
                </div>
            </div>
        </div>
    </DashboardShell>
  );
}
