'use client';

import React, { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Service, servicesApi, githubApi, gitlabApi, bitbucketApi } from '@/lib/api';
import { useToast } from '@/components/ui/use-toast';
import { BuildpackSelector, BuildpackType } from '@/components/deployments/BuildpackSelector';
import { FolderRoot, Container, Layers, AlertTriangle, GitBranch, Github } from 'lucide-react';

interface BuildTabProps {
  service: Service;
}

export function BuildTab({ service }: BuildTabProps) {
  const { toast } = useToast();
  const [repositoryUrl, setRepositoryUrl] = useState(service.repository_url || '');
  const [branch, setBranch] = useState(service.branch || 'main');
  const [buildpack, setBuildpack] = useState<BuildpackType>(service.buildpack || 'DOCKER');
  const [rootDirectory, setRootDirectory] = useState(service.root_directory || '/');
  const [buildCommand, setBuildCommand] = useState(service.build_command || '');
  const [deployMode, setDeployMode] = useState<'SINGLE' | 'COMPOSE'>(service.deploy_mode || 'SINGLE');
  const [saving, setSaving] = useState(false);

  // Branch fetching state
  const [branches, setBranches] = useState<any[]>([]);
  const [loadingBranches, setLoadingBranches] = useState(false);

  useEffect(() => {
    if (!repositoryUrl) return;
    // Extract repo slug from URL
    const match = repositoryUrl.match(/github\.com\/([^\/]+\/[^\/]+)/)
      || repositoryUrl.match(/gitlab\.com\/([^\/]+\/[^\/]+)/)
      || repositoryUrl.match(/bitbucket\.org\/([^\/]+\/[^\/]+)/);
    if (!match) { setBranches([]); return; }
    let repo = match[1];
    if (repo.endsWith('.git')) repo = repo.slice(0, -4);

    setLoadingBranches(true);
    const api = match[0].includes('github.com') ? githubApi
      : match[0].includes('gitlab.com') ? gitlabApi
      : bitbucketApi;
    api.branches(repo)
      .then((data: any) => { if (Array.isArray(data)) setBranches(data); })
      .catch(() => setBranches([]))
      .finally(() => setLoadingBranches(false));
  }, [repositoryUrl]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await servicesApi.update(service.id, {
        repository_url: repositoryUrl || undefined,
        branch: branch || 'main',
        buildpack: buildpack,
        root_directory: rootDirectory,
        build_command: buildCommand,
        deploy_mode: deployMode,
      });
      toast({
        title: "Build settings updated",
        description: `Service will use ${deployMode === 'COMPOSE' ? 'Docker Compose' : buildpack} from "${rootDirectory}" for the next deployment.`,
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

          {/* Source Repository */}
          <div className="space-y-4 p-4 rounded-lg border border-border bg-muted/30">
            <h4 className="text-sm font-semibold flex items-center gap-2">
              <Github className="h-4 w-4" />
              Source Repository
            </h4>
            <div className="space-y-2">
              <Label htmlFor="repository-url">Repository URL</Label>
              <Input
                id="repository-url"
                value={repositoryUrl}
                onChange={(e) => setRepositoryUrl(e.target.value)}
                placeholder="https://github.com/username/repo"
              />
              {repositoryUrl !== (service.repository_url || '') && (
                <div className="flex items-center gap-2 text-xs text-amber-500 bg-amber-500/10 border border-amber-500/20 rounded-md px-3 py-2">
                  <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
                  <span>Repository URL changed. Save and redeploy to deploy from the new repo.</span>
                </div>
              )}
              <p className="text-xs text-muted-foreground">
                The Git repository to clone and build from. Change this to deploy from a different repo.
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="branch" className="flex items-center gap-1.5">
                <GitBranch className="h-3.5 w-3.5" />
                Branch
              </Label>
              {branches.length > 0 ? (
                <select
                  id="branch"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  {branches.map((b: any) => (
                    <option key={b.name || b} value={b.name || b}>{b.name || b}</option>
                  ))}
                </select>
              ) : (
                <Input
                  id="branch"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  placeholder={loadingBranches ? "Loading branches..." : "main"}
                  disabled={loadingBranches}
                />
              )}
              <p className="text-xs text-muted-foreground">
                Branch, tag, or commit to deploy from. Defaults to <code>main</code>.
              </p>
            </div>
          </div>

          {/* Deploy Mode Selector */}
          <div className="space-y-3">
            <Label className="text-sm font-medium">Deploy Mode</Label>
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setDeployMode('SINGLE')}
                className={`relative flex flex-col items-start gap-2 rounded-lg border-2 p-4 text-left transition-all hover:border-primary/50 ${
                  deployMode === 'SINGLE'
                    ? 'border-primary bg-primary/5 shadow-sm'
                    : 'border-border hover:bg-muted/50'
                }`}
              >
                <div className="flex items-center gap-2">
                  <Container className={`h-5 w-5 ${deployMode === 'SINGLE' ? 'text-primary' : 'text-muted-foreground'}`} />
                  <span className="font-semibold text-sm">Single Container</span>
                </div>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  Build and deploy a single Dockerfile. Best for microservices and frontends deployed via Grid.
                </p>
                {deployMode === 'SINGLE' && (
                  <div className="absolute top-2 right-2 h-2 w-2 rounded-full bg-primary" />
                )}
              </button>

              <button
                type="button"
                onClick={() => setDeployMode('COMPOSE')}
                className={`relative flex flex-col items-start gap-2 rounded-lg border-2 p-4 text-left transition-all hover:border-primary/50 ${
                  deployMode === 'COMPOSE'
                    ? 'border-primary bg-primary/5 shadow-sm'
                    : 'border-border hover:bg-muted/50'
                }`}
              >
                <div className="flex items-center gap-2">
                  <Layers className={`h-5 w-5 ${deployMode === 'COMPOSE' ? 'text-primary' : 'text-muted-foreground'}`} />
                  <span className="font-semibold text-sm">Docker Compose</span>
                </div>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  Deploy multi-container apps using docker-compose.yml. Includes all services defined in the compose file.
                </p>
                {deployMode === 'COMPOSE' && (
                  <div className="absolute top-2 right-2 h-2 w-2 rounded-full bg-primary" />
                )}
              </button>
            </div>
            {deployMode !== (service.deploy_mode || 'SINGLE') && (
              <div className="flex items-center gap-2 text-xs text-amber-500 bg-amber-500/10 border border-amber-500/20 rounded-md px-3 py-2">
                <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
                <span>Deploy mode changed. Save and redeploy for it to take effect.</span>
              </div>
            )}
          </div>

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
