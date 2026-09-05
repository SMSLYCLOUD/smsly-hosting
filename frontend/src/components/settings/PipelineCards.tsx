"use client";

import React from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Server, Clock } from "lucide-react";

interface PipelineCardProps {
  config: any;
  onChange: (field: string, value: any) => void;
}

export function EcosystemPipelineCard({ config, onChange }: PipelineCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center space-x-2">
          <Server className="h-5 w-5" />
          <span>Ecosystem Pipeline</span>
        </CardTitle>
        <CardDescription>Configure concurrency and wave settings for multi-service deployments.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label>Global Max Concurrent Builds (1-10)</Label>
          <Input
            type="number"
            min="1"
            max="10"
            value={config.max_concurrent_builds || 1}
            onChange={(e) => onChange("max_concurrent_builds", parseInt(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Ecosystem Max Concurrent Builds (1-10)</Label>
          <Input
            type="number"
            min="1"
            max="10"
            value={config.ecosystem_max_concurrent_builds || 2}
            onChange={(e) => onChange("ecosystem_max_concurrent_builds", parseInt(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Ecosystem Build Stagger Seconds (0-300)</Label>
          <Input
            type="number"
            min="0"
            max="300"
            value={config.ecosystem_build_stagger_seconds || 30}
            onChange={(e) => onChange("ecosystem_build_stagger_seconds", parseInt(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Default Wave Size (1-5)</Label>
          <Input
            type="number"
            min="1"
            max="5"
            value={config.ecosystem_default_wave_size || 5}
            onChange={(e) => onChange("ecosystem_default_wave_size", parseInt(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Wait Between Wave Checks (1-60 minutes)</Label>
          <Input
            type="number"
            min="60"
            max="3600"
            value={config.ecosystem_wave_recheck_seconds || 1800}
            onChange={(e) => onChange("ecosystem_wave_recheck_seconds", parseInt(e.target.value))}
          />
          <p className="text-xs text-muted-foreground">
            After starting a group of services, the system waits this long before checking if that group is finished. The default is 30 minutes. The wave can wait up to about 2 hours.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

export function DeployPipelineCard({ config, onChange }: PipelineCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center space-x-2">
          <Clock className="h-5 w-5" />
          <span>Deploy Pipeline</span>
        </CardTitle>
        <CardDescription>Configure auto-review and auto-promote timeouts for deployments.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label>Auto-Review Hours (0-72)</Label>
          <Input
            type="number"
            min="0"
            max="72"
            value={config.auto_review_hours ?? 2}
            onChange={(e) => onChange("auto_review_hours", parseInt(e.target.value))}
          />
          <p className="text-xs text-muted-foreground">
            Auto-approve deployments in REVIEW status after this many hours. Set to 0 to disable.
          </p>
        </div>
        <div className="space-y-2">
          <Label>Auto-Promote Hours (0-168)</Label>
          <Input
            type="number"
            min="0"
            max="168"
            value={config.auto_promote_hours ?? 12}
            onChange={(e) => onChange("auto_promote_hours", parseInt(e.target.value))}
          />
          <p className="text-xs text-muted-foreground">
            Auto-promote deployments in STAGED status after this many hours. Set to 0 to disable.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
