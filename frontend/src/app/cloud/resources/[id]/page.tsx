'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  Cloud, ArrowLeft, Trash2, Save, Loader2,
  Globe, Cpu, Clock, Edit2, X
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { PageHeader } from '@/components/ui/page-header';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { cloudResourceApi, type CloudResource } from '@/lib/api';

const PROVIDERS = [
  'aws', 'gcp', 'azure', 'digitalocean', 'linode',
  'vultr', 'hetzner', 'ovh', 'scaleway', 'upcloud',
  'serverion', 'contabo',
];

const REGIONS = [
  'us-east-1', 'us-west-2', 'eu-west-1', 'eu-central-1',
  'ap-southeast-1', 'ap-northeast-1', 'sa-east-1',
];

const STATUSES = ['running', 'stopped', 'error', 'provisioning', 'pending', 'active'];

export default function CloudResourceDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { toast } = useToast();
  const confirm = useConfirm();

  const [resource, setResource] = useState<CloudResource | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [formData, setFormData] = useState({
    name: '',
    provider: 'aws',
    region: 'us-east-1',
    type: '',
    status: 'running',
    configRaw: '{}',
  });

  const fetchResource = useCallback(async () => {
    try {
      setLoading(true);
      const data = await cloudResourceApi.detail(params.id as string);
      setResource(data);
      setFormData({
        name: data.name,
        provider: data.provider,
        region: data.region,
        type: data.type,
        status: data.status,
        configRaw: JSON.stringify(data.config || {}, null, 2),
      });
    } catch (err: any) {
      toast({
        title: 'Error loading resource',
        description: err?.response?.data?.detail || err.message || 'Resource not found.',
        variant: 'destructive',
      });
      router.push('/cloud/resources');
    } finally {
      setLoading(false);
    }
  }, [params.id, toast, router]);

  useEffect(() => { fetchResource(); }, [fetchResource]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name || !formData.type) {
      toast({ title: 'Validation error', description: 'Name and type are required.', variant: 'destructive' });
      return;
    }
    let config = {};
    try { config = JSON.parse(formData.configRaw || '{}'); }
    catch { toast({ title: 'JSON Error', description: 'Invalid config JSON.', variant: 'destructive' }); return; }

    setSaving(true);
    try {
      const updated = await cloudResourceApi.update(params.id as string, {
        name: formData.name,
        provider: formData.provider,
        region: formData.region,
        type: formData.type,
        status: formData.status,
        config,
      });
      setResource(updated);
      setEditing(false);
      toast({ title: 'Resource updated', description: 'Changes saved successfully.' });
    } catch (err: any) {
      toast({
        title: 'Update failed',
        description: err?.response?.data?.error || err.message || 'An error occurred.',
        variant: 'destructive',
      });
    } finally { setSaving(false); }
  };

  const handleDelete = async () => {
    const confirmed = await confirm({
      title: 'Delete Cloud Resource?',
      message: `Are you sure you want to permanently delete "${resource?.name}"? This action cannot be undone.`,
      variant: 'destructive',
      confirmText: 'Delete Forever',
    });
    if (!confirmed) return;
    setDeleting(true);
    try {
      await cloudResourceApi.delete(params.id as string);
      toast({ title: 'Resource deleted', description: 'The resource has been removed.' });
      router.push('/cloud/resources');
    } catch (err: any) {
      toast({
        title: 'Deletion failed',
        description: err?.response?.data?.error || err.message || 'An error occurred.',
        variant: 'destructive',
      });
      setDeleting(false);
    }
  };

  if (loading) {
    return (
      <DashboardShell>
        <div className="flex-1 p-8 relative z-10">
          <div className="max-w-3xl mx-auto space-y-8">
            <div className="h-8 w-48 bg-muted/50 rounded animate-pulse" />
            <div className="h-4 w-72 bg-muted/50 rounded animate-pulse" />
            <Card>
              <CardContent className="p-6 space-y-4">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-5 w-full" />
                ))}
              </CardContent>
            </Card>
          </div>
        </div>
      </DashboardShell>
    );
  }

  if (!resource) return null;

  const statusVariant =
    ['running', 'active', 'available', 'online'].includes(resource.status?.toLowerCase()) ? 'success' as const
    : ['provisioning', 'creating', 'pending', 'updating'].includes(resource.status?.toLowerCase()) ? 'warning' as const
    : ['error', 'failed', 'deletion_failed'].includes(resource.status?.toLowerCase()) ? 'destructive' as const
    : 'secondary' as const;

  return (
    <DashboardShell>
      <div className="flex-1 p-8 relative z-10">
        <motion.div
          className="max-w-3xl mx-auto space-y-8"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <PageHeader
            title={resource.name}
            description={`${resource.provider} ${resource.type} — ${resource.region}`}
            icon={
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/25">
                <Cloud className="text-white" size={22} />
              </div>
            }
            breadcrumbs={[
              { label: 'Cloud' },
              { label: 'Resources', href: '/cloud/resources' },
              { label: resource.name },
            ]}
            actions={
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  onClick={() => router.push('/cloud/resources')}
                  className="border-white/10 text-gray-300 hover:bg-white/5"
                >
                  <ArrowLeft size={14} className="mr-2" />
                  Back to List
                </Button>
                <Button
                  variant="destructive"
                  onClick={handleDelete}
                  disabled={deleting}
                  className="bg-red-600 hover:bg-red-700 text-white"
                >
                  {deleting ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Trash2 size={14} className="mr-2" />}
                  Delete
                </Button>
              </div>
            }
          />

          <Card className="bg-card border-border">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-lg">Resource Details</CardTitle>
              {!editing && (
                <Button variant="outline" size="sm" onClick={() => setEditing(true)} className="border-white/10 text-gray-300 hover:bg-white/5">
                  <Edit2 size={14} className="mr-2" />
                  Edit
                </Button>
              )}
            </CardHeader>
            <CardContent>
              {editing ? (
                <form onSubmit={handleSave} className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1.5">
                      <Label className="text-zinc-300">Name *</Label>
                      <Input value={formData.name} onChange={e => setFormData({ ...formData, name: e.target.value })} className="bg-black/30 border-white/10 text-white focus:border-cyan-500" required />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-zinc-300">Type *</Label>
                      <Input value={formData.type} onChange={e => setFormData({ ...formData, type: e.target.value })} className="bg-black/30 border-white/10 text-white focus:border-cyan-500" required />
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-zinc-300">Provider *</Label>
                      <select value={formData.provider} onChange={e => setFormData({ ...formData, provider: e.target.value })} className="w-full px-3 py-2 bg-black/30 border border-white/10 rounded-lg text-sm text-white outline-none focus:border-cyan-500" required>
                        {PROVIDERS.map(p => (
                          <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
                        ))}
                      </select>
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-zinc-300">Region</Label>
                      <select value={formData.region} onChange={e => setFormData({ ...formData, region: e.target.value })} className="w-full px-3 py-2 bg-black/30 border border-white/10 rounded-lg text-sm text-white outline-none focus:border-cyan-500">
                        {REGIONS.map(r => (
                          <option key={r} value={r}>{r}</option>
                        ))}
                      </select>
                    </div>
                    <div className="space-y-1.5">
                      <Label className="text-zinc-300">Status</Label>
                      <select value={formData.status} onChange={e => setFormData({ ...formData, status: e.target.value })} className="w-full px-3 py-2 bg-black/30 border border-white/10 rounded-lg text-sm text-white outline-none focus:border-cyan-500">
                        {STATUSES.map(s => (
                          <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-zinc-300">Config (JSON)</Label>
                    <textarea value={formData.configRaw} onChange={e => setFormData({ ...formData, configRaw: e.target.value })} rows={6} className="w-full px-3 py-2 bg-black/30 border border-white/10 rounded-lg text-sm text-white font-mono outline-none focus:border-cyan-500" />
                  </div>
                  <div className="flex gap-2 justify-end pt-2">
                    <Button type="button" variant="outline" onClick={() => { setEditing(false); setFormData({ name: resource.name, provider: resource.provider, region: resource.region, type: resource.type, status: resource.status, configRaw: JSON.stringify(resource.config || {}, null, 2) }); }} className="border-white/10 text-gray-300 hover:bg-white/5">
                      <X size={14} className="mr-2" />
                      Cancel
                    </Button>
                    <Button type="submit" disabled={saving} className="bg-gradient-to-r from-cyan-500 to-blue-600 text-white">
                      {saving ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Save size={14} className="mr-2" />}
                      Save Changes
                    </Button>
                  </div>
                </form>
              ) : (
                <div className="space-y-5">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <span className="text-xs text-zinc-500 uppercase tracking-wider font-semibold">Name</span>
                      <p className="text-sm text-white font-medium mt-1">{resource.name}</p>
                    </div>
                    <div>
                      <span className="text-xs text-zinc-500 uppercase tracking-wider font-semibold">Type</span>
                      <p className="text-sm text-white font-medium mt-1 flex items-center gap-1.5">
                        <Cpu className="h-3.5 w-3.5 text-zinc-400" />
                        {resource.type || 'N/A'}
                      </p>
                    </div>
                    <div>
                      <span className="text-xs text-zinc-500 uppercase tracking-wider font-semibold">Provider</span>
                      <p className="text-sm text-white font-medium mt-1 capitalize">{resource.provider}</p>
                    </div>
                    <div>
                      <span className="text-xs text-zinc-500 uppercase tracking-wider font-semibold">Region</span>
                      <p className="text-sm text-white font-medium mt-1 flex items-center gap-1.5">
                        <Globe className="h-3.5 w-3.5 text-zinc-400" />
                        {resource.region || 'global'}
                      </p>
                    </div>
                    <div>
                      <span className="text-xs text-zinc-500 uppercase tracking-wider font-semibold">Status</span>
                      <div className="mt-1">
                        <Badge variant={statusVariant}>{resource.status}</Badge>
                      </div>
                    </div>
                    <div>
                      <span className="text-xs text-zinc-500 uppercase tracking-wider font-semibold">Resource ID</span>
                      <p className="text-sm text-zinc-300 font-mono mt-1">{resource.id}</p>
                    </div>
                    <div>
                      <span className="text-xs text-zinc-500 uppercase tracking-wider font-semibold">Created</span>
                      <p className="text-sm text-white font-medium mt-1 flex items-center gap-1.5">
                        <Clock className="h-3.5 w-3.5 text-zinc-400" />
                        {new Date(resource.created_at).toLocaleString()}
                      </p>
                    </div>
                    <div>
                      <span className="text-xs text-zinc-500 uppercase tracking-wider font-semibold">Updated</span>
                      <p className="text-sm text-white font-medium mt-1 flex items-center gap-1.5">
                        <Clock className="h-3.5 w-3.5 text-zinc-400" />
                        {new Date(resource.updated_at).toLocaleString()}
                      </p>
                    </div>
                  </div>

                  {resource.config && Object.keys(resource.config).length > 0 && (
                    <div className="pt-4 border-t border-white/5">
                      <span className="text-xs text-zinc-500 uppercase tracking-wider font-semibold">Config</span>
                      <pre className="mt-2 p-3 rounded-lg bg-black/20 border border-white/5 text-xs text-zinc-300 font-mono overflow-x-auto">
                        {JSON.stringify(resource.config, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </DashboardShell>
  );
}
