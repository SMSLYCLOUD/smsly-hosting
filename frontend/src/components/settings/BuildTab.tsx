'use client';

import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Service, servicesApi } from '@/lib/api';
import { useToast } from '@/components/ui/use-toast';
import { BuildpackSelector, BuildpackType } from '@/components/deployments/BuildpackSelector';

interface BuildTabProps {
  service: Service;
}

export function BuildTab({ service }: BuildTabProps) {
  const { toast } = useToast();
  const [buildpack, setBuildpack] = useState<BuildpackType>(service.buildpack || 'NIXPACKS');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await servicesApi.update(service.id, {
        buildpack: buildpack,
      });
      toast({
        title: "Build settings updated",
        description: `Service will use ${buildpack} for next deployment.`,
      });
    } catch (error) {
      console.error(error);
      toast({
        title: "Update failed",
        description: "Could not save build settings.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
      <Card>
        <CardHeader>
          <CardTitle>Build Configuration</CardTitle>
          <CardDescription>
            Configure how your application is built.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <BuildpackSelector value={buildpack} onChange={setBuildpack} />

          <div className="flex justify-end">
            <Button onClick={handleSave} disabled={saving}>
              {saving ? "Saving..." : "Save Changes"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
