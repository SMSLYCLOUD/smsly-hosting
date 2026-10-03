'use client';

import React, { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { Server, Loader2, Trash2, PauseCircle, PlayCircle, RefreshCw, ArrowLeft, Cpu, MemoryStick, HardDrive } from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { autoscalerApi, type AutoscalerNode } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/use-toast';
import { cn } from '@/lib/utils';

function Bar({ value, color }: { value: number; color: string }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className="h-1.5 rounded-full bg-muted/40 overflow-hidden">
      <div className="h-full rounded-full transition-all" style={{ width: `${v}%`, background: color }} />
    </div>
  );
}

function ResRow({ icon, label, freePct }: { icon: React.ReactNode; label: string; freePct: number }) {
  const color = freePct >= 30 ? '#10b981' : freePct >= 15 ? '#f59e0b' : '#ef4444';
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-[11px]">
        <span className="flex items-center gap-1 text-muted-foreground">{icon}{label}</span>
        <span className="font-mono font-bold">{freePct.toFixed(0)}% free</span>
      </div>
      <Bar value={freePct} color={color} />
    </div>
  );
}

export default function AutoscalerNodesPage() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [nodes, setNodes] = useState<AutoscalerNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const fetchNodes = useCallback(async () => {
    try {
      setNodes(await autoscalerApi.getNodes());
    } catch (err: any) {
      toast({ title: 'Failed to load nodes', description: err?.response?.data?.error || err.message, variant: 'destructive' });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    fetchNodes();
    const t = setInterval(fetchNodes, 30000);
    return () => clearInterval(t);
  }, [fetchNodes]);

  const handleDrain = async (node: AutoscalerNode) => {
    if (!await confirm({ title: `Drain node ${node.name}?`, message: 'Destroys all replicas on this node and stops new placements.', confirmText: 'Drain', variant: 'destructive' })) return;
    setBusy(node.id);
    try {
      const res = await autoscalerApi.drainNode(node.id);
      toast({ title: 'Node drained', description: `${res.drained_replicas} replica(s) removed from ${node.name}` });
      await fetchNodes();
    } catch (err: any) {
      toast({ title: 'Drain failed', description: err?.response?.data?.error || err.message, variant: 'destructive' });
    } finally {
      setBusy(null);
    }
  };

  const handleCordon = async (node: AutoscalerNode) => {
    const allow = !(node.allow_user_workloads !== false);
    if (!await confirm({ title: `${allow ? 'Uncordon' : 'Cordon'} node ${node.name}?`, message: allow ? 'Allow new replica placements on this node again.' : 'Stop new replica placements (existing replicas keep running).', confirmText: allow ? 'Uncordon' : 'Cordon' })) return;
    setBusy(node.id);
    try {
      await autoscalerApi.cordonNode(node.id, allow);
      toast({ title: allow ? 'Node uncordoned' : 'Node cordoned', description: `${node.name} ${allow ? 'accepts' : 'refuses'} new placements` });
      await fetchNodes();
    } catch (err: any) {
      toast({ title: 'Failed', description: err?.response?.data?.error || err.message, variant: 'destructive' });
    } finally {
      setBusy(null);
    }
  };

  return (
    <DashboardShell>
      <div className="space-y-6 p-4 md:p-6">
        <div className="flex items-center justify-between">
          <div>
            <Link href="/autoscaler" className="text-xs text-muted-foreground hover:text-primary flex items-center gap-1 mb-1">
              <ArrowLeft size={12} /> Autoscaler
            </Link>
            <h1 className="text-xl font-bold flex items-center gap-2">
              <Server size={20} className="text-emerald-500" /> Nodes
            </h1>
            <p className="text-xs text-muted-foreground mt-1 max-w-[560px]">
              How the autoscaler sees each remote server: placement score (free mem/CPU/disk vs minimum
              {nodes[0]?.min_score !== undefined ? ` ${nodes[0].min_score}` : ''}), live replicas, storage, and placement state.
              Horizontal scaling lands on the service&apos;s home server; vertical picks the best qualified node.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={fetchNodes} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
          </Button>
        </div>

        {loading && nodes.length === 0 ? (
          <div className="flex items-center justify-center py-16"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>
        ) : nodes.length === 0 ? (
          <Card><CardContent className="p-8 text-center text-muted-foreground text-sm">No remote nodes registered. Add a node under Servers to scale beyond this host.</CardContent></Card>
        ) : (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {nodes.map((node) => {
              const res = (node.resources || {}) as Record<string, number>;
              const mem = Number(res.mem ?? NaN), cpu = Number(res.cpu ?? NaN), disk = Number(res.disk ?? NaN);
              const allows = node.allow_user_workloads !== false;
              return (
                <Card key={node.id} className="border-border/50">
                  <CardHeader className="pb-3 border-b border-border/50">
                    <div className="flex items-start justify-between">
                      <div>
                        <CardTitle className="text-base font-mono">{node.name}</CardTitle>
                        <CardDescription className="font-mono text-[11px]">
                          {node.host}{node.is_lite_agent ? ' • lite agent' : ''}{node.node_type ? ` • ${node.node_type}` : ''}
                        </CardDescription>
                      </div>
                      <div className="flex gap-1">
                        <span className={cn("px-2 py-0.5 rounded-full text-[10px] font-bold uppercase",
                          node.status === 'ONLINE' ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400")}>{node.status}</span>
                        {node.qualified ? (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase bg-blue-500/10 text-blue-400">Qualified</span>
                        ) : (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase bg-amber-500/10 text-amber-400">Below bar</span>
                        )}
                        {!allows && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase bg-zinc-500/10 text-zinc-400">Cordoned</span>
                        )}
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent className="p-4 space-y-4">
                    <div className="grid grid-cols-3 gap-2 text-center font-mono text-xs">
                      <div className="p-2 rounded bg-muted/30">
                        <div className="font-bold text-sm">{node.score >= 0 ? node.score.toFixed(0) : '—'}</div>
                        <div className="text-[9px] text-muted-foreground uppercase">Score{min_score(node)}</div>
                      </div>
                      <div className="p-2 rounded bg-muted/30">
                        <div className="font-bold text-sm">{node.replica_count}</div>
                        <div className="text-[9px] text-muted-foreground uppercase">Replicas</div>
                      </div>
                      <div className="p-2 rounded bg-muted/30">
                        <div className="font-bold text-sm">{node.storage ? `${node.storage.used_percent.toFixed(0)}%` : '—'}</div>
                        <div className="text-[9px] text-muted-foreground uppercase">Disk used</div>
                      </div>
                    </div>

                    <div className="space-y-2">
                      <div className="text-[11px] font-bold uppercase text-muted-foreground">Autoscaler view — free resources</div>
                      {Number.isFinite(mem) && <ResRow icon={<MemoryStick size={12} />} label="Memory" freePct={mem} />}
                      {Number.isFinite(cpu) && <ResRow icon={<Cpu size={12} />} label="CPU" freePct={cpu} />}
                      {Number.isFinite(disk) && <ResRow icon={<HardDrive size={12} />} label="Disk" freePct={disk} />}
                      {!Number.isFinite(mem) && !Number.isFinite(cpu) && (
                        <p className="text-[11px] text-muted-foreground">No metrics yet — node unscorable until Prometheus sees it.</p>
                      )}
                    </div>

                    <div>
                      <div className="text-[11px] font-bold uppercase text-muted-foreground mb-1">Replicas on this node ({node.replicas?.length ?? 0})</div>
                      {(node.replicas?.length ?? 0) === 0 ? (
                        <p className="text-[11px] text-muted-foreground">None — vertical placements will land here when this node wins.</p>
                      ) : (
                        <div className="space-y-1 max-h-40 overflow-y-auto">
                          {node.replicas.map((r) => (
                            <div key={r.id} className="flex items-center justify-between text-[11px] font-mono px-2 py-1 rounded bg-muted/30">
                              <span className="truncate">{r.service_name || r.service}<span className="text-muted-foreground"> · {r.container_name || r.id.slice(0, 8)}</span></span>
                              <span className={cn("uppercase text-[10px] font-bold", r.status === 'RUNNING' ? "text-emerald-400" : "text-amber-400")}>{r.status}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" className="flex-1 h-7 text-xs gap-1" disabled={busy === node.id} onClick={() => handleCordon(node)}>
                        {allows ? <><PauseCircle size={12} /> Cordon</> : <><PlayCircle size={12} /> Uncordon</>}
                      </Button>
                      <Button variant="outline" size="sm" className="flex-1 h-7 text-xs gap-1 hover:bg-red-500/10 hover:text-red-400" disabled={busy === node.id} onClick={() => handleDrain(node)}>
                        {busy === node.id ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />} Drain Node
                      </Button>
                    </div>
                    {node.last_heartbeat && (
                      <p className="text-[10px] text-muted-foreground font-mono">Last heartbeat: {new Date(node.last_heartbeat).toLocaleString()}</p>
                    )}
                  </CardContent>
                </Card>
              );
              function min_score(n: AutoscalerNode) {
                return n.min_score !== undefined ? ` / ${n.min_score}` : '';
              }
            })}
          </div>
        )}
      </div>
    </DashboardShell>
  );
}
