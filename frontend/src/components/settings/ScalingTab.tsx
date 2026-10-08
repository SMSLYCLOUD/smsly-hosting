'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Service, servicesApi, Replica, scalingApi } from '@/lib/api';
import { useToast } from '@/components/ui/use-toast';
import { Loader2, Plus, Trash2, RefreshCw } from 'lucide-react';

interface ScalingTabProps {
  service: Service;
  onUpdate?: () => void;
}

export default function ScalingTab({ service, onUpdate }: ScalingTabProps) {
  const { toast } = useToast();
  const [minReplicas, setMinReplicas] = useState(service.min_replicas || 1);
  const [maxReplicas, setMaxReplicas] = useState(service.max_replicas || 1);
  const [cpuTarget, setCpuTarget] = useState(service.autoscale_cpu_target || 80);
  const [vpaEnabled, setVpaEnabled] = useState(service.vpa_enabled || false);
  const [edgeJwt, setEdgeJwt] = useState(service.edge_jwt_required || false);
  const [sablier, setSablier] = useState(service.sablier_enabled || false);
  const [sablierSession, setSablierSession] = useState(service.sablier_session || '10m');
  const [placement, setPlacement] = useState<'AUTO' | 'MASTER' | 'NODE'>(service.addon_placement || 'AUTO');
  const [wafOptOut, setWafOptOut] = useState(service.waf_opt_out || false);
  const [saving, setSaving] = useState(false);
  const [replicas, setReplicas] = useState<Replica[]>([]);
  const [loadingReplicas, setLoadingReplicas] = useState(false);
  const [spawning, setSpawning] = useState(false);

  const fetchReplicas = useCallback(async () => {
    setLoadingReplicas(true);
    try {
      const data = await scalingApi.getReplicas(service.id);
      setReplicas(data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingReplicas(false);
    }
  }, [service.id]);

  useEffect(() => {
    fetchReplicas();
  }, [fetchReplicas]);

  const handleSpawn = async () => {
    setSpawning(true);
    try {
      await scalingApi.spawnReplica(service.id, 'horizontal');
      toast({ title: 'Replica spawned', description: 'A new replica is being created on this service\u2019s home server.' });
      fetchReplicas();
    } catch (err: any) {
      console.error(err);
      const msg = err?.response?.data?.error || err?.response?.data?.hint || 'Could not spawn replica.';
      toast({ title: 'Failed to spawn replica', description: String(msg), variant: 'destructive' });
    } finally {
      setSpawning(false);
    }
  };

  const handleDestroy = async (replicaId: string) => {
    try {
      await scalingApi.destroyReplica(replicaId);
      toast({ title: 'Replica destroyed', description: `Replica ${replicaId} has been destroyed.` });
      fetchReplicas();
    } catch (err) {
      console.error(err);
      toast({ title: 'Failed to destroy replica', description: 'Could not destroy replica.', variant: 'destructive' });
    }
  };

  const handleReplicaChange = (value: number[]) => {
    if (value.length === 2) {
      setMinReplicas(value[0]);
      setMaxReplicas(value[1]);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await servicesApi.update(service.id, {
        min_replicas: minReplicas,
        max_replicas: maxReplicas,
        autoscale_cpu_target: cpuTarget,
        vpa_enabled: vpaEnabled,
        edge_jwt_required: edgeJwt,
        sablier_enabled: sablier,
        sablier_session: sablierSession,
        addon_placement: placement,
        waf_opt_out: wafOptOut,
      });
      toast({
        title: "Scaling settings updated",
        description: "The autoscaler will adjust replicas based on these rules.",
      });
      if (onUpdate) onUpdate();
    } catch (error) {
      console.error(error);
      toast({
        title: "Update failed",
        description: "Could not save scaling settings.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const [applyingVpa, setApplyingVpa] = useState(false);

  const handleApplyVpa = async () => {
    setApplyingVpa(true);
    try {
      const result = await scalingApi.applyVpa(service.id);
      toast({
        title: "Vertical scaling applied",
        description: result.node
          ? `Updated container on ${result.node}`
          : `Updated container ${result.container}`,
      });
    } catch (err: any) {
      const msg = err?.response?.data?.error || err?.response?.data?.hint || err.message || 'Apply failed';
      toast({ title: 'Vertical scaling failed', description: String(msg), variant: 'destructive' });
    } finally {
      setApplyingVpa(false);
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'RUNNING': return <Badge variant="success">Running</Badge>;
      case 'SPAWNING': return <Badge variant="warning">Spawning</Badge>;
      case 'DRAINING': return <Badge variant="warning">Draining</Badge>;
      case 'DESTROYING': return <Badge variant="warning">Destroying</Badge>;
      case 'DESTROYED': return <Badge variant="gray">Destroyed</Badge>;
      default: return <Badge variant="outline">{status}</Badge>;
    }
  };

  const getReplicaHealth = (replica: Replica) => {
    if (replica.status !== 'RUNNING') return <span className="text-muted-foreground text-xs">—</span>;
    const snap = replica.metrics_snapshot;
    if (!snap || (snap.cpu_percent === undefined && snap.memory_usage_mb === undefined)) {
      return <Badge variant="outline">No data yet</Badge>;
    }
    const cpu = snap.cpu_percent ?? 0;
    const memLimit = snap.memory_limit_mb ?? 0;
    const memUsed = snap.memory_usage_mb ?? 0;
    const memPct = memLimit > 0 ? (memUsed / memLimit) * 100 : 0;
    const hot = cpu >= 90 || memPct >= 90;
    const warm = cpu >= 70 || memPct >= 70;
    return (
      <div className="flex flex-col gap-0.5 text-xs">
        <span className={hot ? 'text-red-500 font-semibold' : warm ? 'text-yellow-500 font-semibold' : 'text-emerald-500 font-semibold'}>
          {hot ? 'Hot' : warm ? 'Warm' : 'Healthy'} · CPU {cpu.toFixed(0)}%
          {memLimit > 0 ? ` · MEM ${memPct.toFixed(0)}%` : ''}
        </span>
        {snap.checked_at && (
          <span className="text-[10px] text-muted-foreground">
            checked {new Date(snap.checked_at).toLocaleTimeString()}
          </span>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Active Replicas */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Active Replicas</CardTitle>
              <CardDescription>
                Manage running replica containers for this service.
              </CardDescription>
              <p className="text-[11px] text-muted-foreground mt-1 max-w-[520px]">
                Spawn Replica scales horizontally on this service&rsquo;s home server
                {service.node_url ? ' (its node)' : ' (the master)'} — same-server replicas, no extra hop.
                To place a replica on a different server, use vertical scaling from the Autoscaler page.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={fetchReplicas} disabled={loadingReplicas}>
                <RefreshCw className={`w-4 h-4 mr-1 ${loadingReplicas ? 'animate-spin' : ''}`} />
                Refresh
              </Button>
              <Button onClick={handleSpawn} disabled={spawning} size="sm">
                {spawning ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Plus className="w-4 h-4 mr-1" />}
                Spawn Replica
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {loadingReplicas && replicas.length === 0 ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : replicas.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <p>No replicas found for this service.</p>
              <p className="text-sm">Click &quot;Spawn Replica&quot; to create one.</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Replica</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Health</TableHead>
                  <TableHead>Node</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {replicas.map((replica) => (
                  <TableRow key={replica.id}>
                    <TableCell>
                      <div className="font-mono text-xs">{replica.id.slice(0, 8)}</div>
                      <div className="font-mono text-[10px] text-muted-foreground truncate max-w-[180px]" title={replica.container_name || undefined}>
                        {replica.container_name || '—'}
                      </div>
                    </TableCell>
                    <TableCell>
                      {getStatusBadge(replica.status)}
                    </TableCell>
                    <TableCell>
                      {getReplicaHealth(replica)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{replica.node_name || '\u2014'}</TableCell>
                    <TableCell className="text-xs text-muted-foreground max-w-[200px] truncate" title={replica.spawn_reason || undefined}>
                      {replica.spawn_reason || '—'}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDestroy(replica.id)}
                        disabled={replica.status === 'DESTROYED'}
                        className="text-red-500 hover:text-red-600 hover:bg-red-50"
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* HPA Config */}
      <Card>
        <CardHeader>
          <CardTitle>Horizontal Auto-Scaling (HPA)</CardTitle>
          <CardDescription>
            Replicates containers on the same server for low-latency inter-replica communication. Falls back to remote nodes when local capacity is exceeded.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-8">
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <Label>Replica Range (Min - Max)</Label>
              <span className="text-sm font-medium text-muted-foreground">
                {minReplicas} - {maxReplicas} containers
              </span>
            </div>
            <Slider
              value={[minReplicas, maxReplicas]}
              min={1}
              max={20}
              step={1}
              minStepsBetweenThumbs={0}
              onValueChange={handleReplicaChange}
              className="py-4"
            />
            <p className="text-xs text-muted-foreground">
              The service will never scale below {minReplicas} or above {maxReplicas} replicas.
            </p>
          </div>
          <div className="space-y-4">
            <div className="flex justify-between items-center">
              <Label>CPU Target</Label>
              <span className="text-sm font-medium text-muted-foreground">
                {cpuTarget}%
              </span>
            </div>
            <Slider
              value={[cpuTarget]}
              min={10}
              max={100}
              step={5}
              onValueChange={(val) => setCpuTarget(val[0])}
              className="py-4"
            />
            <p className="text-xs text-muted-foreground">
              New replicas will be added when average CPU usage exceeds {cpuTarget}%.
            </p>
          </div>
        </CardContent>
      </Card>

      {/* VPA Config */}
      <Card>
        <CardHeader>
          <CardTitle>Vertical Auto-Scaling (VPA)</CardTitle>
          <CardDescription>
            Adjusts CPU/memory limits on running containers. Works on both local and remote nodes (requires SSH credentials on the node).
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label className="text-base">Enable VPA</Label>
            <p className="text-sm text-muted-foreground">
              When enabled, the autoscaler will periodically apply resource limits.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Button
              variant="outline"
              size="sm"
              disabled={applyingVpa}
              onClick={handleApplyVpa}
            >
              {applyingVpa ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Apply Now
            </Button>
            <Switch checked={vpaEnabled} onCheckedChange={setVpaEnabled} />
          </div>
        </CardContent>
      </Card>

      {/* Edge: scale-to-zero, JWT gate, WAF */}
      <Card>
        <CardHeader>
          <CardTitle>Edge &amp; Scale-to-Zero</CardTitle>
          <CardDescription>
            Sablier sleeps idle containers and wakes them on request. Edge JWT gates the service at Traefik/Caddy. Coraza WAF is on unless opted out.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label className="text-base">Scale to zero (Sablier)</Label>
              <p className="text-sm text-muted-foreground">Sleep when idle; wake on first request.</p>
            </div>
            <Switch checked={sablier} onCheckedChange={setSablier} />
          </div>
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label className="text-base">Idle session</Label>
              <p className="text-sm text-muted-foreground">How long to stay awake after last request (e.g. 10m, 1h).</p>
            </div>
            <input value={sablierSession} onChange={(e) => setSablierSession(e.target.value)} className="w-24 px-2 py-1 text-sm rounded border border-border bg-background font-mono" />
          </div>
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label className="text-base">Require edge JWT</Label>
              <p className="text-sm text-muted-foreground">401 at the edge without a valid edge token.</p>
            </div>
            <Switch checked={edgeJwt} onCheckedChange={setEdgeJwt} />
          </div>
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label className="text-base">Disable Coraza WAF</Label>
              <p className="text-sm text-muted-foreground">Opt out of OWASP CRS inspection for this service.</p>
            </div>
            <Switch checked={wafOptOut} onCheckedChange={setWafOptOut} />
          </div>
        </CardContent>
      </Card>

      {/* Addon backend placement */}
      <Card>
        <CardHeader>
          <CardTitle>Addon Placement</CardTitle>
          <CardDescription>
            Where this service&apos;s addon backends (databases, caches, queues) live. Only meaningful on a remote node.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 p-1">
            {(['AUTO', 'MASTER', 'NODE'] as const).map((opt) => (
              <button
                key={opt}
                type="button"
                onClick={() => setPlacement(opt)}
                className={`rounded-full px-3 py-1 text-xs font-semibold transition-all ${
                  placement === opt
                    ? 'bg-emerald-500/20 text-emerald-300 shadow-[inset_0_0_0_1px_rgba(52,211,153,0.35)]'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {opt === 'AUTO' ? 'Auto' : opt === 'MASTER' ? 'Master' : 'Node'}
              </button>
            ))}
          </div>
          <p className="text-sm text-muted-foreground">
            {placement === 'AUTO' && 'Platform default: remote services provision addons on their node, everything else on master.'}
            {placement === 'MASTER' && 'Backends stay on master and reach the service over the mesh. Data stays inside master backups.'}
            {placement === 'NODE' && 'Backends run on the service\u2019s node next to the app. Applies to newly provisioned addons; existing backends move only via migration.'}
          </p>
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={saving}>
          {saving ? "Saving..." : "Save Changes"}
        </Button>
      </div>
    </div>
  );
}
