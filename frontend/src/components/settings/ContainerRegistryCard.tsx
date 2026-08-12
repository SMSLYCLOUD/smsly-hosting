"use client";

import React from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Database, CheckCircle2, XCircle } from "lucide-react";

interface ContainerRegistryCardProps {
  config: any;
  onChange: (field: string, value: any) => void;
}

export function ContainerRegistryCard({ config, onChange }: ContainerRegistryCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center space-x-2">
          <Database className="h-5 w-5" />
          <span>Container Registry</span>
          {config.container_registry_url ? (
            (() => {
              const url = config.container_registry_url || '';
              const isExternal = !url.startsWith('registry:') && !url.startsWith('127.') && !url.startsWith('localhost');
              const hasCreds = config.REGISTRY_PASSWORD_SET;
              return (
                <span className="ml-2 flex items-center gap-1 text-xs font-normal">
                  {hasCreds ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                  ) : (
                    <XCircle className="h-3.5 w-3.5 text-yellow-500" />
                  )}
                  <span className={isExternal ? "text-blue-500" : "text-muted-foreground"}>
                    {isExternal ? "External" : "Internal"}
                  </span>
                </span>
              );
            })()
          ) : null}
        </CardTitle>
        <CardDescription>
          Docker image registry for deployments. Supports internal (registry:5000) and external (Docker Hub, GHCR, ECR, etc.) registries.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label>Registry URL</Label>
          <Input
            placeholder="registry:5000 (internal) or docker.io/ghcr.io (external)"
            value={config.container_registry_url || ""}
            onChange={(e) => onChange("container_registry_url", e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Internal: registry:5000, 127.0.0.1:5000 · External: docker.io, ghcr.io, &lt;your-registry&gt;:5000
          </p>
        </div>
        <div className="space-y-2">
          <Label>Registry User</Label>
          <Input
            placeholder="smsly-registry"
            value={config.registry_user || ""}
            onChange={(e) => onChange("registry_user", e.target.value)}
          />
        </div>
        <div className="space-y-2">
          <Label>Registry Password</Label>
          <Input
            type="password"
            placeholder={config.registry_password_set ? "•••••••• (Saved)" : "Leave blank to keep unchanged"}
            onChange={(e) => onChange("registry_password", e.target.value)}
          />
        </div>
      </CardContent>
    </Card>
  );
}
