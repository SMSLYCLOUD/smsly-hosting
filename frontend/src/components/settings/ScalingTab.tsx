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
      await scalingApi.spawnReplica(service.id);
      toast({ title: 'Replica spawned', description: 'A new replica is being created.' });
      fetchReplicas();
    } catch (err) {
      console.error(err);
      toast({ title: 'Failed to spawn replica', description: 'Could not spawn replica.', variant: 'destructive' });
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

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'RUNNING': return <Badge variant="success">Running</Badge>;
      case 'SPAWNING': return <Badge variant="warning">Spawning</Badge>;
      case 'DESTROYED': return <Badge variant="gray">Destroyed</Badge>;
      default: return <Badge variant="outline">{status}</Badge>;
    }
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
                  <TableHead>Replica ID</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Node</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {replicas.map((replica) => (
                  <TableRow key={replica.id}>
                    <TableCell className="font-mono text-xs">{replica.id}</TableCell>
                    <TableCell>
                      {getStatusBadge(replica.status)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{replica.node_name || '\u2014'}</TableCell>
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
            Spawns replicas across different servers/nodes for fault isolation and geographic distribution.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label className="text-base">Enable VPA</Label>
            <p className="text-sm text-muted-foreground">
              Allows the cluster to recommend and apply resource limits.
              <br />
              <span className="text-xs text-yellow-500">Experimental: May cause pod restarts.</span>
            </p>
          </div>
          <Switch checked={vpaEnabled} onCheckedChange={setVpaEnabled} />
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
