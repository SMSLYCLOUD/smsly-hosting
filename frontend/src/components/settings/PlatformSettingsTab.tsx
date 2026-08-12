"use client";

import React, { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { Loader2, Save } from "lucide-react";
import api from "@/lib/api";
import { EcosystemPipelineCard, DeployPipelineCard } from "./PipelineCards";
import { ContainerRegistryCard } from "./ContainerRegistryCard";
import { ObservabilityCard, BillingSmsCard } from "./ObservabilityCards";
import { FeatureFlagsCard } from "./FeatureFlagsCard";
import { SecurityScanningCard, DeviceTrustCard } from "./SecurityCards";
import { InfisicalCard } from "./InfisicalCard";

export function PlatformSettingsTab() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState<any>({});

  const fetchConfig = async () => {
    try {
      setLoading(true);
      const res = await api.get("/system/domain-config/");
      setConfig(res.data);
    } catch (err) {
      toast({ title: "Error fetching platform config", variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConfig();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleChange = (field: string, value: any) => {
    setConfig((prev: any) => ({ ...prev, [field]: value }));
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      await api.put("/system/domain-config/", config);
      toast({ title: "Platform configuration updated successfully" });
      await fetchConfig();
    } catch (err: any) {
      toast({
        title: "Failed to update platform configuration",
        description: err.response?.data?.error || err.message,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-48 items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-medium">Platform Global Settings</h3>
          <p className="text-sm text-muted-foreground">
            Configure global properties that affect the entire SMSLY hosting platform.
          </p>
        </div>
        <Button onClick={handleSave} disabled={saving}>
          {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          <Save className="mr-2 h-4 w-4" />
          Save Changes
        </Button>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <EcosystemPipelineCard config={config} onChange={handleChange} />
        <DeployPipelineCard config={config} onChange={handleChange} />
        <ContainerRegistryCard config={config} onChange={handleChange} />
        <ObservabilityCard config={config} onChange={handleChange} />
        <BillingSmsCard config={config} onChange={handleChange} />
        <FeatureFlagsCard config={config} onChange={handleChange} />
        <SecurityScanningCard config={config} onChange={handleChange} />
        <DeviceTrustCard config={config} onChange={handleChange} />
        <InfisicalCard />
      </div>
    </div>
  );
}
