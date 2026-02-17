'use client';

import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Service, servicesApi } from '@/lib/api';
import { useToast } from '@/components/ui/use-toast';

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

  // Range slider for replicas: [min, max]
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

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Horizontal Auto-Scaling (HPA)</CardTitle>
          <CardDescription>
            Configure how your service scales based on CPU load.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-8">

          {/* Replicas Range */}
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

          {/* CPU Target */}
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

      <Card>
        <CardHeader>
          <CardTitle>Vertical Auto-Scaling (VPA)</CardTitle>
          <CardDescription>
            Automatically adjust CPU/Memory requests based on usage.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label className="text-base">Enable VPA</Label>
              <p className="text-sm text-muted-foreground">
                Allows the cluster to recommend and apply resource limits.
                <br/>
                <span className="text-xs text-yellow-500">Experimental: May cause pod restarts.</span>
              </p>
            </div>
            <Switch
              checked={vpaEnabled}
              onCheckedChange={setVpaEnabled}
            />
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
