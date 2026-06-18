'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { 
  ShieldCheck, 
  Database, 
  GitBranch, 
  Loader2, 
  AlertTriangle, 
  CheckCircle2, 
  Trash2, 
  RefreshCcw,
  ExternalLink,
  Info
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from '@/components/ui/use-toast';
import { api, githubApi, servicesApi } from '@/lib/api';

interface SafeDeployPanelProps {
  serviceId: string;
  preview?: any;
}

export const SafeDeployPanel: React.FC<SafeDeployPanelProps> = ({ serviceId, preview }) => {
  const [previews, setPreviews] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [branchName, setBranchName] = useState('');
  const [commitSha, setCommitSha] = useState('');
  
  const [githubRepo, setGithubRepo] = useState<string | null>(null);
  const [branches, setBranches] = useState<any[]>([]);
  const [commits, setCommits] = useState<any[]>([]);
  const [loadingBranches, setLoadingBranches] = useState(false);
  const [loadingCommits, setLoadingCommits] = useState(false);

  useEffect(() => {
    servicesApi.get(serviceId).then(s => {
      if (s.repository_url && s.repository_url.includes('github.com')) {
        const match = s.repository_url.match(/github\.com\/([^\/]+\/[^\/]+)/);
        if (match) {
          let repo = match[1];
          if (repo.endsWith('.git')) repo = repo.slice(0, -4);
          setGithubRepo(repo);
        }
      }
    }).catch(() => {});
  }, [serviceId]);

  useEffect(() => {
    if (!githubRepo) return;
    setLoadingBranches(true);
    githubApi.branches(githubRepo)
      .then(data => {
        if (Array.isArray(data)) setBranches(data);
      })
      .catch(() => {})
      .finally(() => setLoadingBranches(false));
  }, [githubRepo]);

  useEffect(() => {
    if (!githubRepo || !branchName) return;
    setLoadingCommits(true);
    githubApi.commits(githubRepo, branchName)
      .then(data => {
        if (Array.isArray(data)) {
          setCommits(data);
          if (data.length > 0 && !commitSha) {
            setCommitSha(data[0].sha);
          }
        }
      })
      .catch(() => {})
      .finally(() => setLoadingCommits(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [githubRepo, branchName]);

  const fetchPreviews = useCallback(async () => {
    try {
      const res = await api.get(`/services/${serviceId}/previews/`);
      const data = res.data;
      setPreviews(Array.isArray(data) ? data : (data?.results || []));
    } catch (err) {
      console.error('Failed to fetch previews', err);
      setPreviews([]);
    } finally {
      setLoading(false);
    }
  }, [serviceId]);

  useEffect(() => {
    fetchPreviews();
    const interval = setInterval(fetchPreviews, 10000);
    return () => clearInterval(interval);
  }, [serviceId, fetchPreviews]);

  const handleCreatePreview = async () => {
    if (!branchName || !commitSha) {
      toast({
        title: 'Validation Error',
        description: 'Branch and Commit SHA are required',
        variant: 'destructive',
      });
      return;
    }

    setCreating(true);
    try {
      await api.post(`/services/${serviceId}/previews/`, {
        branch_name: branchName,
        commit_sha: commitSha,
      });
      toast({
        title: 'SafeDeploy Initiated',
        description: 'Preview environment provisioning started.',
      });
      setBranchName('');
      setCommitSha('');
      fetchPreviews();
    } catch (err: any) {
      toast({
        title: 'Deployment Failed',
        description: err.response?.data?.error || 'Failed to create preview',
        variant: 'destructive',
      });
    } finally {
      setCreating(false);
    }
  };

  const handleDeletePreview = async (previewId: string) => {
    try {
      await api.post(`/services/${serviceId}/previews/${previewId}/destroy_preview/`);
      toast({
        title: 'Teardown Initiated',
        description: 'SafeDeploy environment is being removed.',
      });
      fetchPreviews();
    } catch (err) {
      toast({
        title: 'Action Failed',
        description: 'Failed to destroy preview environment.',
        variant: 'destructive',
      });
    }
  };

  const handleRebuildPreview = async (previewId: string) => {
    try {
      await api.post(`/services/${serviceId}/previews/${previewId}/rebuild/`);
      toast({
        title: 'Rebuild Initiated',
        description: 'SafeDeploy environment is being rebuilt.',
      });
      fetchPreviews();
    } catch (err) {
      toast({
        title: 'Action Failed',
        description: 'Failed to rebuild preview environment.',
        variant: 'destructive',
      });
    }
  };

  const getRiskColor = (level: string) => {
    switch (level?.toUpperCase()) {
      case 'LOW': return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20';
      case 'MEDIUM': return 'bg-amber-500/10 text-amber-500 border-amber-500/20';
      case 'HIGH': return 'bg-orange-500/10 text-orange-500 border-orange-500/20';
      case 'CRITICAL': return 'bg-red-500/10 text-red-500 border-red-500/20';
      default: return 'bg-muted text-muted-foreground border-border';
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-bold flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-primary" />
            SafeDeploy Environments
          </h3>
          <p className="text-sm text-muted-foreground mt-1">Clone production DB and validate migrations on ephemeral branches.</p>
        </div>
      </div>

      {/* Creation Form */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-semibold">Provisional Environment</CardTitle>
          <CardDescription>Deploy an isolated copy of this service with a cloned production database.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="branch-name" className="text-xs">Target Branch</Label>
              <div className="relative">
                <GitBranch className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                {githubRepo ? (
                  <select
                    id="branch-name"
                    value={branchName}
                    onChange={(e) => {
                      setBranchName(e.target.value);
                      setCommitSha('');
                    }}
                    disabled={loadingBranches}
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background pl-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <option value="">{loadingBranches ? 'Loading branches...' : 'Select branch'}</option>
                    {branches.map((b: any) => (
                      <option key={b.name} value={b.name}>{b.name}</option>
                    ))}
                  </select>
                ) : (
                  <Input 
                    id="branch-name"
                    value={branchName}
                    onChange={(e) => setBranchName(e.target.value)}
                    placeholder="e.g. feature/new-auth"
                    className="pl-10"
                  />
                )}
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="commit-sha" className="text-xs">Commit SHA</Label>
              {githubRepo && branchName ? (
                <select
                  id="commit-sha"
                  value={commitSha}
                  onChange={(e) => setCommitSha(e.target.value)}
                  disabled={loadingCommits}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="">{loadingCommits ? 'Loading commits...' : 'Select commit'}</option>
                  {commits.map((c: any) => (
                    <option key={c.sha} value={c.sha}>
                      {c.sha.substring(0, 7)} - {c.commit?.message?.split('\n')[0] || 'No message'}
                    </option>
                  ))}
                </select>
              ) : (
                <Input 
                  id="commit-sha"
                  value={commitSha}
                  onChange={(e) => setCommitSha(e.target.value)}
                  placeholder="e.g. a1b2c3d"
                />
              )}
            </div>
            <div className="flex items-end">
              <Button 
                onClick={handleCreatePreview}
                disabled={creating}
                className="w-full gap-2"
              >
                {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Database className="w-4 h-4" />}
                Create Preview
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Previews List */}
      <div className="space-y-4">
        {loading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="w-8 h-8 text-primary animate-spin" />
          </div>
        ) : previews.length === 0 ? (
          <div className="text-center py-12 bg-muted/30 border border-dashed border-border rounded-xl">
            <Database className="w-12 h-12 text-muted-foreground/30 mx-auto mb-3" />
            <p className="text-muted-foreground text-sm font-medium">No active previews found for this service.</p>
          </div>
        ) : (
          previews.map((preview) => (
            <Card key={preview.id} className="overflow-hidden border-border bg-card">
              <div className="p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="flex items-start gap-4">
                  <div className="mt-1 p-2 bg-primary/10 rounded-lg">
                    <GitBranch className="w-5 h-5 text-primary" />
                  </div>
                  <div className="space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-bold text-foreground">{preview.branch_name}</span>
                      <Badge variant="secondary" className="font-mono text-[10px]">
                        {String(preview.commit_sha || '').substring(0, 7) || 'N/A'}
                      </Badge>
                      <Badge className={`text-[10px] font-bold ${
                        preview.status === 'READY' ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20' :
                        (preview.status || '').includes('FAILED') ? 'bg-red-500/10 text-red-500 border-red-500/20' :
                        'bg-muted text-muted-foreground'
                      }`}>
                        {preview.status || 'UNKNOWN'}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-muted-foreground font-medium">
                      <span className="flex items-center gap-1">
                        <Database className="w-3.5 h-3.5" />
                        {preview.database_clone?.clone_database_name || 'Allocating...'}
                      </span>
                      {preview.preview_url && (
                        <a 
                          href={preview.preview_url} 
                          target="_blank" 
                          rel="noopener noreferrer"
                          className="flex items-center gap-1 text-primary hover:underline transition-colors"
                        >
                          <ExternalLink className="w-3.5 h-3.5" />
                          Visit Preview
                        </a>
                      )}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <Button 
                    variant="outline" 
                    size="sm" 
                    className="gap-2 font-bold h-9"
                    onClick={() => handleRebuildPreview(preview.id)}
                  >
                    <RefreshCcw className="w-3.5 h-3.5" />
                    Rebuild
                  </Button>
                  <Button 
                    variant="ghost" 
                    size="icon" 
                    onClick={() => handleDeletePreview(preview.id)}
                    className="text-muted-foreground hover:text-red-500 h-9 w-9"
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              </div>

              {/* Status Details */}
              <div className="px-5 py-4 bg-muted/20 border-t border-border grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Database Clone Info */}
                <div className="space-y-2">
                  <h4 className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest flex items-center gap-2">
                    <Database className="w-3 h-3" />
                    Snapshot Status
                  </h4>
                  <div className="p-3 bg-muted/40 rounded-lg border border-border">
                    {preview.database_clone ? (
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-foreground">{preview.database_clone.status}</span>
                        {preview.database_clone.status === 'READY' && (
                          <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                        )}
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground italic">Initializing clone...</p>
                    )}
                  </div>
                </div>

                {/* Migration Risk Info */}
                <div className="space-y-2">
                  <h4 className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest flex items-center gap-2">
                    <AlertTriangle className="w-3 h-3" />
                    Migration Impact
                  </h4>
                  <div className={`p-3 rounded-lg border flex items-center justify-between ${
                    preview.migration_validation ? getRiskColor(preview.migration_validation.risk_level) : 'bg-muted/40 border-border text-muted-foreground'
                  }`}>
                    {preview.migration_validation ? (
                      <>
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-bold">{preview.migration_validation.risk_level} RISK</span>
                          <span className="text-[10px] font-bold opacity-70">Score: {preview.migration_validation.risk_score}</span>
                        </div>
                        <Info className="w-4 h-4 opacity-50 cursor-help" />
                      </>
                    ) : (
                      <p className="text-sm italic font-medium">Pending Analysis</p>
                    )}
                  </div>
                </div>
              </div>

              {/* Risk Summary if exists */}
              {preview.migration_validation?.summary && (
                <div className="px-5 py-3 bg-card border-t border-border text-[11px] text-muted-foreground font-medium">
                  <p className="line-clamp-2 leading-relaxed">{preview.migration_validation.summary}</p>
                </div>
              )}
            </Card>
          ))
        )}
      </div>
    </div>
  );
};
