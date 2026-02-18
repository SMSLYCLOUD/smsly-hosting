'use client';

import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Service, servicesApi } from '@/lib/api';
import { useToast } from '@/components/ui/use-toast';
import { BuildpackSelector, BuildpackType } from '@/components/deployments/BuildpackSelector';
import { FolderRoot } from 'lucide-react';

interface BuildTabProps {
  service: Service;
}

export function BuildTab({ service }: BuildTabProps) {
  const { toast } = useToast();
  const [buildpack, setBuildpack] = useState<BuildpackType>(service.buildpack || 'DOCKER');
  const [rootDirectory, setRootDirectory] = useState(service.root_directory || '/');
  const [buildCommand, setBuildCommand] = useState(service.build_command || '');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await servicesApi.update(service.id, {
        buildpack: buildpack,
        root_directory: rootDirectory,
        build_command: buildCommand,
      });
      toast({
        title: "Build settings updated",
        description: `Service will use ${buildpack} from "${rootDirectory}" for the next deployment.`,
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
            Configure how your application is built and deployed.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <BuildpackSelector value={buildpack} onChange={setBuildpack} />

          <div className="space-y-2">
            <Label htmlFor="root-directory" className="flex items-center gap-2">
              <FolderRoot className="h-4 w-4" />
              Root Directory
            </Label>
            <Input
              id="root-directory"
              value={rootDirectory}
              onChange={(e) => setRootDirectory(e.target.value)}
              placeholder="/ (default — repo root)"
            />
            <p className="text-xs text-muted-foreground">
              For monorepos, set this to the subdirectory containing your app (e.g. <code>/backend</code>).
              The build will run from this directory.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="build-command">Build Command (optional)</Label>
            <Input
              id="build-command"
              value={buildCommand}
              onChange={(e) => setBuildCommand(e.target.value)}
              placeholder="e.g. npm run build"
            />
            <p className="text-xs text-muted-foreground">
              Custom build command. Leave empty to use the default for your buildpack.
            </p>
          </div>

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
