'use client';

import React, { useState, useEffect, useCallback } from 'react';
// This page targets /api/v1/domains/, served by apps.domains.urls.GlobalDomainViewSet.
import { motion, AnimatePresence } from 'framer-motion';
import {
  Globe, Plus, RefreshCw, Search, Filter, Loader2,
  AlertCircle, CheckCircle2, ExternalLink, Trash2
} from 'lucide-react';
import { DashboardShell } from '@/components/layout/DashboardShell';
import { PageHeader } from '@/components/ui/page-header';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
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
import { domainsApi, servicesApi, type Domain, type Service } from '@/lib/api';
import Link from 'next/link';

const STATUSES = ['active', 'pending', 'error'];

function getStatusVariant(status: string): 'success' | 'warning' | 'destructive' | 'default' | 'secondary' {
  switch (status?.toLowerCase()) {
    case 'active': return 'success';
    case 'pending': return 'warning';
    case 'error': return 'destructive';
    default: return 'secondary';
  }
}

export default function DomainsPage() {
  const { toast } = useToast();
  const confirm = useConfirm();

  const [domains, setDomains] = useState<Domain[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterStatus, setFilterStatus] = useState('all');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [formData, setFormData] = useState({
    name: '',
    service: '',
    dns_managed: true,
    ssl_enabled: true,
    status: 'pending',
    target: '',
    record_type: 'CNAME',
  });

  const fetchDomains = useCallback(async () => {
    try {
      setLoading(true);
      const data = await domainsApi.list();
      setDomains(data);
    } catch (err: any) {
      toast({
        title: 'Error loading domains',
        description: err?.response?.data?.detail || err.message || 'Failed to load domains.',
        variant: 'destructive',
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  const fetchServices = useCallback(async () => {
    try {
      const data = await servicesApi.list();
      setServices(data);
    } catch {
      // silently fail — services are optional for the form
    }
  }, []);

  useEffect(() => { fetchDomains(); }, [fetchDomains]);
  useEffect(() => { fetchServices(); }, [fetchServices]);

  const openCreateModal = () => {
    setFormData({ name: '', service: '', dns_managed: true, ssl_enabled: true, status: 'pending', target: '', record_type: 'CNAME' });
    setShowCreateModal(true);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.name) {
      toast({ title: 'Validation error', description: 'Domain name is required.', variant: 'destructive' });
      return;
    }

    setActionLoading(true);
    try {
      await domainsApi.create({
        name: formData.name.toLowerCase().trim(),
        service: formData.service || undefined,
        dns_managed: formData.dns_managed,
        ssl_enabled: formData.ssl_enabled,
        status: formData.status,
        target: formData.target || undefined,
        record_type: formData.record_type || undefined,
      });
      toast({ title: 'Domain created', description: `Successfully created ${formData.name}.` });
      setShowCreateModal(false);
      fetchDomains();
    } catch (err: any) {
      toast({
        title: 'Creation failed',
        description: err?.response?.data?.error || err.message || 'An error occurred.',
        variant: 'destructive',
      });
    } finally { setActionLoading(false); }
  };

  const handleDelete = async (id: string) => {
    const domain = domains.find(d => d.id === id);
    const confirmed = await confirm({
      title: 'Delete Domain?',
      message: `Are you sure you want to delete "${domain?.name || 'this domain'}"? This action cannot be undone.`,
      variant: 'destructive',
      confirmText: 'Delete Domain',
    });
    if (!confirmed) return;
    try {
      await domainsApi.delete(id);
      toast({ title: 'Domain deleted', description: 'The domain has been removed.' });
      fetchDomains();
    } catch (err: any) {
      toast({
        title: 'Deletion failed',
        description: err?.response?.data?.error || err.message || 'An error occurred.',
        variant: 'destructive',
      });
    }
  };

  const filteredDomains = domains.filter(d => {
    const q = searchQuery.toLowerCase();
    if (q && !d.name.toLowerCase().includes(q) && !(d.service_name || '').toLowerCase().includes(q)) return false;
    if (filterStatus !== 'all' && d.status?.toLowerCase() !== filterStatus) return false;
    return true;
  });

  const activeCount = domains.filter(d => d.status?.toLowerCase() === 'active').length;
  const pendingCount = domains.filter(d => d.status?.toLowerCase() === 'pending').length;
  const errorCount = domains.filter(d => d.status?.toLowerCase() === 'error').length;

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
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="p-6 rounded-xl border border-border bg-card/50 space-y-4">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-8 w-16" />
                </div>
              ))}
            </div>
            <div className="bg-card border border-border rounded-2xl overflow-hidden">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4 p-4 border-b border-border last:border-0">
                  <Skeleton className="h-8 w-8 rounded-full" />
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-4 w-32 ml-auto" />
                  <Skeleton className="h-6 w-20 rounded-full" />
                </div>
              ))}
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
              title="Domains"
              description="Manage all domains across your services"
              icon={
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/25">
                  <Globe className="text-white" size={22} />
                </div>
              }
              breadcrumbs={[{ label: 'Domains' }]}
            />
            <div className="flex gap-2">
              <Button variant="outline" onClick={fetchDomains} className="border-white/10 text-gray-300 hover:bg-white/5">
                <RefreshCw size={14} className="mr-2" />
                Refresh
              </Button>
              <Button onClick={openCreateModal} className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white shadow-lg shadow-indigo-500/25">
                <Plus size={14} className="mr-2" />
                Add Domain
              </Button>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {[
              { label: 'Total Domains', value: domains.length, icon: Globe, color: 'text-indigo-500 bg-indigo-500/10' },
              { label: 'Active', value: activeCount, icon: CheckCircle2, color: 'text-emerald-500 bg-emerald-500/10' },
              { label: 'Pending', value: pendingCount, icon: AlertCircle, color: 'text-yellow-500 bg-yellow-500/10' },
              { label: 'Error', value: errorCount, icon: AlertCircle, color: 'text-red-500 bg-red-500/10' },
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
                placeholder="Search by domain name or service..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                className="pl-9 bg-black/20 border-white/10 text-white placeholder-zinc-500 rounded-lg focus:border-indigo-500"
              />
            </div>
            <div className="flex gap-2 w-full md:w-auto">
              <div className="flex items-center gap-1.5 px-3 py-2 bg-black/20 border border-white/10 rounded-lg shrink-0">
                <Filter size={14} className="text-zinc-400" />
                <span className="text-xs text-zinc-400">Filter By</span>
              </div>

              <select
                value={filterStatus}
                onChange={e => setFilterStatus(e.target.value)}
                className="px-3 py-2 bg-black/20 border border-white/10 rounded-lg text-xs text-white outline-none focus:border-indigo-500"
              >
                <option value="all">All Statuses</option>
                {STATUSES.map(s => (
                  <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                ))}
              </select>
            </div>
          </div>

          <AnimatePresence mode="popLayout">
            {filteredDomains.length === 0 ? (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="bg-card border border-border rounded-2xl p-16 text-center space-y-4"
              >
                <Globe size={48} className="mx-auto text-zinc-600/30" />
                <h3 className="text-lg font-bold text-white">No domains found</h3>
                <p className="text-sm text-muted-foreground max-w-md mx-auto">
                  {searchQuery || filterStatus !== 'all'
                    ? 'Adjust your search or filters and try again.'
                    : 'Manage all your custom domains in one place. Add a domain to get started.'}
                </p>
                {!searchQuery && filterStatus === 'all' && (
                  <Button onClick={openCreateModal} className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white">
                    <Plus size={14} className="mr-2" />
                    Add Your First Domain
                  </Button>
                )}
              </motion.div>
            ) : (
              <motion.div layout className="bg-card border border-border rounded-2xl overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border">
                        <th className="text-left p-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Domain</th>
                        <th className="text-left p-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Service</th>
                        <th className="text-left p-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Status</th>
                        <th className="text-left p-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider">SSL</th>
                        <th className="text-left p-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider">DNS Managed</th>
                        <th className="text-left p-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Target</th>
                        <th className="text-left p-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Created</th>
                        <th className="text-right p-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {filteredDomains.map((domain, i) => (
                        <motion.tr
                          key={domain.id}
                          initial={{ opacity: 0, x: -10 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: i * 0.03 }}
                          className="group hover:bg-white/[0.02] transition-colors"
                        >
                          <td className="p-4">
                            <div className="flex items-center gap-2">
                              <Globe size={14} className="text-indigo-400 shrink-0" />
                              <span className="font-mono text-sm text-white">{domain.name}</span>
                              <a
                                href={`https://${domain.name}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-muted-foreground hover:text-primary opacity-0 group-hover:opacity-100 transition-opacity"
                              >
                                <ExternalLink size={12} />
                              </a>
                            </div>
                          </td>
                          <td className="p-4">
                            {domain.service ? (
                              <Link
                                href={`/services/${domain.service}`}
                                className="text-primary hover:text-primary/80 transition-colors"
                              >
                                {domain.service_name || 'Service'}
                              </Link>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </td>
                          <td className="p-4">
                            <Badge variant={getStatusVariant(domain.status)} className="text-[10px] px-2 py-0.5">
                              {domain.status || 'unknown'}
                            </Badge>
                          </td>
                          <td className="p-4">
                            <Badge variant={domain.ssl_enabled ? 'success' : 'secondary'} className="text-[10px] px-2 py-0.5">
                              {domain.ssl_enabled ? 'Enabled' : 'Disabled'}
                            </Badge>
                          </td>
                          <td className="p-4">
                            <Badge variant={domain.dns_managed ? 'info' : 'gray'} className="text-[10px] px-2 py-0.5">
                              {domain.dns_managed ? 'Managed' : 'External'}
                            </Badge>
                          </td>
                          <td className="p-4">
                            <span className="text-xs font-mono text-muted-foreground truncate max-w-[160px] inline-block align-middle">
                              {domain.target || '—'}
                            </span>
                          </td>
                          <td className="p-4 text-xs text-muted-foreground whitespace-nowrap">
                            {domain.created_at ? new Date(domain.created_at).toLocaleDateString() : '—'}
                          </td>
                          <td className="p-4 text-right">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10 opacity-0 group-hover:opacity-100 transition-opacity"
                              onClick={() => handleDelete(domain.id)}
                            >
                              <Trash2 size={14} />
                            </Button>
                          </td>
                        </motion.tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <Dialog open={showCreateModal} onOpenChange={setShowCreateModal}>
            <DialogContent className="sm:max-w-[500px] bg-[#0d1117] border-white/10 text-white">
              <DialogHeader>
                <DialogTitle className="text-lg font-bold">Add Domain</DialogTitle>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4 mt-2">
                <div className="space-y-1.5">
                  <Label htmlFor="name" className="text-zinc-300">Domain Name *</Label>
                  <Input
                    id="name"
                    value={formData.name}
                    onChange={e => setFormData({ ...formData, name: e.target.value })}
                    placeholder="e.g. app.example.com"
                    className="bg-black/30 border-white/10 text-white focus:border-indigo-500"
                    required
                  />
                </div>

                <div className="space-y-1.5">
                  <Label className="text-zinc-300">Service (optional)</Label>
                  <Select
                    value={formData.service}
                    onValueChange={v => setFormData({ ...formData, service: v })}
                  >
                    <SelectTrigger className="bg-black/30 border-white/10 text-white focus:border-indigo-500">
                      <SelectValue placeholder="No service" />
                    </SelectTrigger>
                    <SelectContent className="bg-[#0d1117] border-white/10 text-white">
                      <SelectItem value="no-service">No service</SelectItem>
                      {services.map(s => (
                        <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-zinc-300">Record Type</Label>
                    <Select
                      value={formData.record_type}
                      onValueChange={v => setFormData({ ...formData, record_type: v })}
                    >
                      <SelectTrigger className="bg-black/30 border-white/10 text-white focus:border-indigo-500">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="bg-[#0d1117] border-white/10 text-white">
                        {['CNAME', 'A', 'AAAA', 'TXT'].map(t => (
                          <SelectItem key={t} value={t}>{t}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-zinc-300">Status</Label>
                    <Select
                      value={formData.status}
                      onValueChange={v => setFormData({ ...formData, status: v })}
                    >
                      <SelectTrigger className="bg-black/30 border-white/10 text-white focus:border-indigo-500">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="bg-[#0d1117] border-white/10 text-white">
                        {STATUSES.map(s => (
                          <SelectItem key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label className="text-zinc-300">Target (optional)</Label>
                  <Input
                    value={formData.target}
                    onChange={e => setFormData({ ...formData, target: e.target.value })}
                    placeholder="e.g. myapp.cloud.trulay.co"
                    className="bg-black/30 border-white/10 text-white focus:border-indigo-500"
                  />
                </div>

                <div className="flex items-center gap-6">
                  <label className="flex items-center gap-2 text-sm text-zinc-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={formData.dns_managed}
                      onChange={e => setFormData({ ...formData, dns_managed: e.target.checked })}
                      className="rounded border-white/20 bg-black/30"
                    />
                    DNS Managed
                  </label>
                  <label className="flex items-center gap-2 text-sm text-zinc-300 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={formData.ssl_enabled}
                      onChange={e => setFormData({ ...formData, ssl_enabled: e.target.checked })}
                      className="rounded border-white/20 bg-black/30"
                    />
                    SSL Enabled
                  </label>
                </div>

                <DialogFooter className="pt-2 gap-2">
                  <Button type="button" variant="outline" onClick={() => setShowCreateModal(false)} className="border-white/10 text-gray-300 hover:bg-white/5">Cancel</Button>
                  <Button type="submit" disabled={actionLoading} className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white">
                    {actionLoading && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                    Create Domain
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
