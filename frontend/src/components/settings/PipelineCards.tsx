"use client";

import React from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Server, Clock, Zap } from "lucide-react";

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
        <div className="flex items-center justify-between rounded-lg border border-border p-3">
          <div className="space-y-0.5">
            <Label className="flex items-center gap-2"><Zap className="h-4 w-4 text-amber-500" /> Fast Deploy (platform default)</Label>
            <p className="text-xs text-muted-foreground">Skip AI analysis and REVIEW/STAGED gates — deploy straight to live. Per-service setting overrides this.</p>
          </div>
          <Switch checked={!!config.fast_deploy_default} onCheckedChange={(v) => onChange("fast_deploy_default", v)} />
        </div>
        <div className="space-y-2 pt-2">
          <Label className="text-xs font-bold uppercase tracking-wider text-muted-foreground">Promotion Readiness (STAGED → ACTIVE)</Label>
          <p className="text-xs text-muted-foreground">
            Conditions a staged deployment must meet before it may promote. Per-service overrides live in the service&apos;s promotion policy.
          </p>
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border p-3">
          <div className="space-y-0.5">
            <Label>Require Healthy Green</Label>
            <p className="text-xs text-muted-foreground">Block promotion while the green container is missing, stopped, or unhealthy (local targets).</p>
          </div>
          <Switch checked={config.promote_require_green_healthy ?? true} onCheckedChange={(v) => onChange("promote_require_green_healthy", v)} />
        </div>
        <div className="space-y-2">
          <Label>Minimum Staging Soak Seconds (0-86400)</Label>
          <Input
            type="number"
            min="0"
            max="86400"
            value={config.promote_min_staging_seconds ?? 0}
            onChange={(e) => onChange("promote_min_staging_seconds", parseInt(e.target.value))}
          />
          <p className="text-xs text-muted-foreground">
            How long a deployment must sit STAGED before promoting. Set to 0 for no soak requirement.
          </p>
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border p-3">
          <div className="space-y-0.5">
            <Label>Require Passed Migration Check</Label>
            <p className="text-xs text-muted-foreground">Block promotion unless this commit has a PASSED migration validation. Off by default (warn-only).</p>
          </div>
          <Switch checked={!!config.promote_require_migration_passed} onCheckedChange={(v) => onChange("promote_require_migration_passed", v)} />
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border p-3">
          <div className="space-y-0.5">
            <Label>Require Approval for High-Risk Migrations</Label>
            <p className="text-xs text-muted-foreground">Block promotion of HIGH/CRITICAL-risk migrations without an approved deployment approval.</p>
          </div>
          <Switch checked={config.promote_require_approval_high_critical ?? true} onCheckedChange={(v) => onChange("promote_require_approval_high_critical", v)} />
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border p-3">
          <div className="space-y-0.5">
            <Label>Block Promotion While Canary Active</Label>
            <p className="text-xs text-muted-foreground">Force an explicit ramp-to-100 or abort before promoting a split service.</p>
          </div>
          <Switch checked={!!config.promote_block_when_canary_active} onCheckedChange={(v) => onChange("promote_block_when_canary_active", v)} />
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border p-3">
          <div className="space-y-0.5">
            <Label>Block Contract-Unsafe Promotion</Label>
            <p className="text-xs text-muted-foreground">Block promotion when migrations are contract-unsafe (post-promote rollback would be impossible). Off by default — warns instead.</p>
          </div>
          <Switch checked={!!config.promote_block_contract_unsafe} onCheckedChange={(v) => onChange("promote_block_contract_unsafe", v)} />
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border p-3">
          <div className="space-y-0.5">
            <Label>Canary: GET/HEAD Only</Label>
            <p className="text-xs text-muted-foreground">Restrict weighted splits to reads — writes always stay on live. Safest for shared-DB splits. Applies on next weight change.</p>
          </div>
          <Switch checked={!!config.promote_canary_get_only} onCheckedChange={(v) => onChange("promote_canary_get_only", v)} />
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border p-3">
          <div className="space-y-0.5">
            <Label>Canary: Sticky Sessions</Label>
            <p className="text-xs text-muted-foreground">Pin clients to one variant with a cookie — for stateful sessions that break when bounced. Applies on next weight change.</p>
          </div>
          <Switch checked={!!config.promote_canary_sticky} onCheckedChange={(v) => onChange("promote_canary_sticky", v)} />
        </div>
      </CardContent>
    </Card>
  );
}
