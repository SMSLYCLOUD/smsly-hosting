'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Cloud, Plus, RefreshCw, Search, Filter, Loader2,
  AlertCircle, CheckCircle2, Globe, Cpu
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { PageHeader } from '@/components/ui/page-header';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { SkeletonCard } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/use-toast';
import { useConfirm } from '@/components/ui/confirm-dialog';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  cloudResourceApi,
  type CloudResource,
} from '@/lib/api';
import { CloudResourceCard } from '@/components/cloud/CloudResourceCard';

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

export default function CloudResourcesPage() {
  const { toast } = useToast();
  const confirm = useConfirm();

  const [resources, setResources] = useState<CloudResource[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterProvider, setFilterProvider] = useState('all');
  const [filterRegion, setFilterRegion] = useState('all');
  const [filterStatus, setFilterStatus] = useState('all');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [selectedResource, setSelectedResource] = useState<CloudResource | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    provider: 'aws',
    region: 'us-east-1',
    type: '',
    status: 'running',
    configRaw: '{}',
  });

  const fetchResources = useCallback(async () => {
    try {
      setLoading(true);
      const data = await cloudResourceApi.list();
      setResources(data);
    } catch (err: any) {
      toast({
        title: 'Error loading resources',
        description: err?.response?.data?.detail || err.message || 'Failed to load cloud resources.',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { fetchResources(); }, [fetchResources]);

  const openCreateModal = () => {
    setFormData({ name: '', provider: 'aws', region: 'us-east-1', type: '', status: 'running', configRaw: '{}' });
    setShowCreateModal(true);
  };

  const openEditModal = (resource: CloudResource) => {
    setSelectedResource(resource);
    setFormData({
      name: resource.name,
      provider: resource.provider,
      region: resource.region,
      type: resource.type,
      status: resource.status,
      configRaw: JSON.stringify(resource.config || {}, null, 2),
    });
    setShowEditModal(true);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name || !formData.type) {
      toast({ title: 'Validation error', description: 'Name and type are required.', variant: 'destructive' });
      return;
    }
    let config = {};
    try { config = JSON.parse(formData.configRaw || '{}'); }
    catch { toast({ title: 'JSON Error', description: 'Invalid config JSON.', variant: 'destructive' }); return; }

    setActionLoading(true);
    try {
      await cloudResourceApi.create({
        name: formData.name,
        provider: formData.provider,
        region: formData.region,
        type: formData.type,
        status: formData.status,
        config,
      });
      toast({ title: 'Resource created', description: `Successfully created ${formData.name}.` });
      setShowCreateModal(false);
      fetchResources();
    } catch (err: any) {
      toast({
        title: 'Creation failed',
        description: err?.response?.data?.error || err.message || 'An error occurred.',
        variant: 'destructive',
      });
    } finally { setActionLoading(false); }
  };

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedResource || !formData.name || !formData.type) {
      toast({ title: 'Validation error', description: 'Name and type are required.', variant: 'destructive' });
      return;
    }
    let config = {};
    try { config = JSON.parse(formData.configRaw || '{}'); }
    catch { toast({ title: 'JSON Error', description: 'Invalid config JSON.', variant: 'destructive' }); return; }

    setActionLoading(true);
    try {
      await cloudResourceApi.update(selectedResource.id, {
        name: formData.name,
        provider: formData.provider,
        region: formData.region,
        type: formData.type,
        status: formData.status,
        config,
      });
      toast({ title: 'Resource updated', description: `Successfully updated ${formData.name}.` });
      setShowEditModal(false);
      fetchResources();
    } catch (err: any) {
      toast({
        title: 'Update failed',
        description: err?.response?.data?.error || err.message || 'An error occurred.',
        variant: 'destructive',
      });
    } finally { setActionLoading(false); }
  };

  const handleDelete = async (id: string) => {
    const resource = resources.find(r => r.id === id);
    const confirmed = await confirm({
      title: 'Delete Cloud Resource?',
      message: `Are you sure you want to delete "${resource?.name || 'this resource'}"? This action cannot be undone.`,
      variant: 'destructive',
      confirmText: 'Delete Resource',
    });
    if (!confirmed) return;
    try {
      await cloudResourceApi.delete(id);
      toast({ title: 'Resource deleted', description: 'The resource has been removed.' });
      fetchResources();
    } catch (err: any) {
      toast({
        title: 'Deletion failed',
        description: err?.response?.data?.error || err.message || 'An error occurred.',
        variant: 'destructive',
      });
    }
  };

  const filteredResources = resources.filter(r => {
    const q = searchQuery.toLowerCase();
    if (q && !r.name.toLowerCase().includes(q) && !r.type?.toLowerCase().includes(q) && !r.provider?.toLowerCase().includes(q)) return false;
    if (filterProvider !== 'all' && r.provider?.toLowerCase() !== filterProvider) return false;
    if (filterRegion !== 'all' && r.region?.toLowerCase() !== filterRegion) return false;
    if (filterStatus !== 'all' && r.status?.toLowerCase() !== filterStatus) return false;
    return true;
  });

  const activeCount = resources.filter(r => ['running', 'active', 'available', 'online'].includes(r.status?.toLowerCase())).length;
  const stoppedCount = resources.filter(r => ['stopped', 'paused'].includes(r.status?.toLowerCase())).length;
  const errorCount = resources.filter(r => ['error', 'failed', 'deletion_failed'].includes(r.status?.toLowerCase())).length;

  if (loading) {
    return (
      <DashboardShell>
        <div className="flex-1 p-8 relative z-10">
          <div className="max-w-6xl mx-auto space-y-8">
            <div className="flex items-center justify-between">
              <div>
                <div className="h-9 w-40 bg-muted/50 rounded animate-pulse" />
                <div className="h-4 w-64 bg-muted/50 rounded animate-pulse mt-2" />
              </div>
              <div className="h-10 w-32 bg-muted/50 rounded animate-pulse" />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
              {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          </div>
        </div>
      </DashboardShell>
    );
  }

  return (
    <DashboardShell>
      <div className="flex-1 p-8 relative z-10">
        <motion.div
          className="max-w-6xl mx-auto space-y-8"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <div className="flex items-center justify-between">
            <PageHeader
              title="Cloud Resources"
              description="Manage compute, storage, and network resources across cloud providers"
              icon={
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/25">
                  <Cloud className="text-white" size={22} />
                </div>
              }
              breadcrumbs={[{ label: 'Cloud' }, { label: 'Resources' }]}
            />
            <div className="flex gap-2">
              <Button variant="outline" onClick={fetchResources} className="border-white/10 text-gray-300 hover:bg-white/5">
                <RefreshCw size={14} className="mr-2" />
                Refresh
              </Button>
              <Button onClick={openCreateModal} className="bg-gradient-to-r from-cyan-500 to-blue-600 text-white shadow-lg shadow-cyan-500/25">
                <Plus size={14} className="mr-2" />
                Add Resource
              </Button>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {[
              { label: 'Total Resources', value: resources.length, icon: Cloud, color: 'text-cyan-500 bg-cyan-500/10' },
              { label: 'Running / Active', value: activeCount, icon: CheckCircle2, color: 'text-emerald-500 bg-emerald-500/10' },
              { label: 'Stopped', value: stoppedCount, icon: Cloud, color: 'text-zinc-400 bg-zinc-500/10' },
              { label: 'Error / Failed', value: errorCount, icon: AlertCircle, color: 'text-red-500 bg-red-500/10' },
            ].map((stat, i) => (
              <motion.div
                key={stat.label}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.05 }}
                className="bg-card border border-border rounded-2xl p-4"
              >
                <div className="flex items-center gap-3 mb-2">
                  <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${stat.color}`}>
                    <stat.icon size={18} />
                  </div>
                  <span className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">{stat.label}</span>
                </div>
                <p className="text-2xl font-bold">{stat.value}</p>
              </motion.div>
            ))}
          </div>

          <div className="flex flex-col md:flex-row gap-3 items-center">
            <div className="relative flex-1 w-full">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-zinc-500" />
              <Input
                placeholder="Search by name, type, or provider..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className="pl-9 bg-black/20 border-white/10 text-white placeholder-zinc-500 rounded-lg focus:border-cyan-500"
              />
            </div>
            <div className="flex gap-2 w-full md:w-auto">
              <div className="flex items-center gap-1.5 px-3 py-2 bg-black/20 border border-white/10 rounded-lg shrink-0">
                <Filter size={14} className="text-zinc-400" />
                <span className="text-xs text-zinc-400">Filter By</span>
              </div>

              <select
                value={filterProvider}
                onChange={e => setFilterProvider(e.target.value)}
                className="px-3 py-2 bg-black/20 border border-white/10 rounded-lg text-xs text-white outline-none focus:border-cyan-500"
              >
                <option value="all">All Providers</option>
                {PROVIDERS.map(p => (
                  <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
                ))}
              </select>

              <select
                value={filterRegion}
                onChange={e => setFilterRegion(e.target.value)}
                className="px-3 py-2 bg-black/20 border border-white/10 rounded-lg text-xs text-white outline-none focus:border-cyan-500"
              >
                <option value="all">All Regions</option>
                {REGIONS.map(r => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>

              <select
                value={filterStatus}
                onChange={e => setFilterStatus(e.target.value)}
                className="px-3 py-2 bg-black/20 border border-white/10 rounded-lg text-xs text-white outline-none focus:border-cyan-500"
              >
                <option value="all">All Statuses</option>
                {STATUSES.map(s => (
                  <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                ))}
              </select>
            </div>
          </div>

          <AnimatePresence mode="popLayout">
            {filteredResources.length === 0 ? (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="bg-card border border-border rounded-2xl p-16 text-center space-y-4"
              >
                <Cloud size={48} className="mx-auto text-zinc-600/30" />
                <h3 className="text-lg font-bold text-white">No cloud resources found</h3>
                <p className="text-sm text-muted-foreground max-w-md mx-auto">
                  {searchQuery || filterProvider !== 'all' || filterRegion !== 'all' || filterStatus !== 'all'
                    ? 'Adjust your search or filters and try again.'
                    : 'Start managing your cloud resources by adding one.'}
                </p>
                {!searchQuery && filterProvider === 'all' && filterRegion === 'all' && filterStatus === 'all' && (
                  <Button onClick={openCreateModal} className="bg-gradient-to-r from-cyan-500 to-blue-600 text-white">
                    <Plus size={14} className="mr-2" />
                    Add Your First Resource
                  </Button>
                )}
              </motion.div>
            ) : (
              <motion.div layout className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                {filteredResources.map(res => (
                  <CloudResourceCard
                    key={res.id}
                    resource={res}
                    onEdit={openEditModal}
                    onDelete={handleDelete}
                  />
                ))}
              </motion.div>
            )}
          </AnimatePresence>

          <Dialog open={showCreateModal} onOpenChange={setShowCreateModal}>
            <DialogContent className="sm:max-w-[500px] bg-[#0d1117] border-white/10 text-white">
              <DialogHeader>
                <DialogTitle className="text-lg font-bold">Add Cloud Resource</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4 mt-2">
                <div className="space-y-1.5">
                  <Label htmlFor="name" className="text-zinc-300">Name *</Label>
                  <Input id="name" value={formData.name} onChange={e => setFormData({ ...formData, name: e.target.value })} placeholder="e.g. production-db" className="bg-black/30 border-white/10 text-white focus:border-cyan-500" required />
                </div>

                <div className="grid grid-cols-2 gap-3">
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
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-zinc-300">Type *</Label>
                    <Input value={formData.type} onChange={e => setFormData({ ...formData, type: e.target.value })} placeholder="e.g. EC2, RDS, Bucket" className="bg-black/30 border-white/10 text-white focus:border-cyan-500" required />
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
                  <textarea value={formData.configRaw} onChange={e => setFormData({ ...formData, configRaw: e.target.value })} placeholder='{ "key": "value" }' rows={4} className="w-full px-3 py-2 bg-black/30 border border-white/10 rounded-lg text-sm text-white font-mono outline-none focus:border-cyan-500" />
                </div>

                <DialogFooter className="pt-2 gap-2">
                  <Button type="button" variant="outline" onClick={() => setShowCreateModal(false)} className="border-white/10 text-gray-300 hover:bg-white/5">Cancel</Button>
                  <Button type="submit" disabled={actionLoading} className="bg-gradient-to-r from-cyan-500 to-blue-600 text-white">
                    {actionLoading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                    Create Resource
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>

          <Dialog open={showEditModal} onOpenChange={setShowEditModal}>
            <DialogContent className="sm:max-w-[500px] bg-[#0d1117] border-white/10 text-white">
              <DialogHeader>
                <DialogTitle className="text-lg font-bold">Edit Cloud Resource</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleUpdate} className="space-y-4 mt-2">
                <div className="space-y-1.5">
                  <Label className="text-zinc-300">Name *</Label>
                  <Input value={formData.name} onChange={e => setFormData({ ...formData, name: e.target.value })} className="bg-black/30 border-white/10 text-white focus:border-cyan-500" required />
                </div>

                <div className="grid grid-cols-2 gap-3">
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
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-zinc-300">Type *</Label>
                    <Input value={formData.type} onChange={e => setFormData({ ...formData, type: e.target.value })} className="bg-black/30 border-white/10 text-white focus:border-cyan-500" required />
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
                  <textarea value={formData.configRaw} onChange={e => setFormData({ ...formData, configRaw: e.target.value })} rows={4} className="w-full px-3 py-2 bg-black/30 border border-white/10 rounded-lg text-sm text-white font-mono outline-none focus:border-cyan-500" />
                </div>

                <DialogFooter className="pt-2 gap-2">
                  <Button type="button" variant="outline" onClick={() => setShowEditModal(false)} className="border-white/10 text-gray-300 hover:bg-white/5">Cancel</Button>
                  <Button type="submit" disabled={actionLoading} className="bg-gradient-to-r from-cyan-500 to-blue-600 text-white">
                    {actionLoading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                    Save Changes
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
        </motion.div>
      </div>
    </DashboardShell>
  );
}
