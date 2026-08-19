'use client';

import React, { memo, useMemo } from 'react';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import {
  Server,
  Database,
  Activity,
  Terminal,
  ExternalLink,
  MoreVertical,
  Play,
  Square,
  Trash2,
  Rocket,
  GitBranch,
  Cloud,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Loader2,
  Plus,
  Scaling
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Service, Addon, servicesApi, addonsApi, scalingApi } from '@/lib/api';
import { toast } from '@/components/ui/use-toast';
import { usePermissions, PERMISSION } from '@/hooks/usePermissions';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { getAddonMetadata } from '@/lib/addonRegistry';

import { motion } from 'framer-motion';
import Link from 'next/link';

interface ServicesGridProps {
  services: (Service & { isAddon?: boolean; addon_type?: string; connection_url?: string })[];
  addons?: Addon[];
}

export const ServicesGrid = memo(function ServicesGrid({ services, addons = [] }: ServicesGridProps) {
  const router = useRouter();
  const confirm = useConfirm();
  const { has } = usePermissions();
  const [actionLoading, setActionLoading] = React.useState<string | null>(null);

  const addonsByService = useMemo(() => {
    const map: Record<string, Addon[]> = {};
    for (const addon of addons) {
      if (!map[addon.service]) map[addon.service] = [];
      map[addon.service].push(addon);
    }
    // Deduplicate by addon_type — show only one icon per type
    const deduped: Record<string, Addon[]> = {};
    for (const [serviceId, serviceAddons] of Object.entries(map)) {
      const seen = new Set<string>();
      deduped[serviceId] = [];
      for (const a of serviceAddons) {
        if (!seen.has(a.addon_type)) {
          seen.add(a.addon_type);
          deduped[serviceId].push(a);
        }
      }
    }
    return deduped;
  }, [addons]);

  const handleDeploy = async (serviceId: string) => {
    if (!await confirm({ title: 'Deploy service?', message: 'Trigger a new deployment for this service now?', confirmText: 'Deploy' })) return;
    setActionLoading(serviceId);
    try {
      const svc = services.find(s => s.id === serviceId);
      const targetId = svc?.latest_deployment?.target_server
        || svc?.node_metadata?.id
        || svc?.server_id
        || undefined;
      await servicesApi.deploy(serviceId, 'HEAD', targetId);
      // Parent page polls every 5s — no reload needed
    } catch (err) {
      console.error('Deploy failed:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleStop = async (serviceId: string) => {
    if (!await confirm({ title: 'Stop service?', message: 'Stop this service and cancel active deployment activity?', variant: 'destructive', confirmText: 'Stop' })) return;
    setActionLoading(serviceId);
    try {
      await servicesApi.stop(serviceId);
      // Parent page polls every 5s — no reload needed
    } catch (err) {
      console.error('Stop failed:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleRestart = async (serviceId: string, serviceName: string, deploymentStatus?: string) => {
    const inProgress = deploymentStatus && !['ACTIVE', 'FAILED', 'CANCELLED'].includes(deploymentStatus);
    const message = inProgress
      ? `A deployment is currently ${deploymentStatus.toLowerCase()}. Restart will cancel it and trigger a full rebuild (~1-5 min).`
      : 'Fast-restart the container (~5 seconds). No rebuild required.';
    if (!await confirm({ title: 'Restart service?', message, confirmText: 'Restart' })) return;
    setActionLoading(serviceId);
    try {
      await servicesApi.restart(serviceId);
      toast({ title: 'Restart triggered', description: `${serviceName} is restarting.` });
    } catch (err) {
      console.error('Restart failed:', err);
      toast({ title: 'Restart failed', description: `Could not restart ${serviceName}.`, variant: 'destructive' });
    } finally {
      setActionLoading(null);
    }
  };

  const handleRecheck = async (serviceId: string) => {
    setActionLoading(serviceId);
    try {
      await servicesApi.recheckHealth(serviceId, true);
    } catch (err) {
      console.error('Recheck failed:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleToggleAutoscale = async (serviceId: string, current: boolean | undefined) => {
    const next = current === false ? true : false;
    setActionLoading(serviceId);
    try {
      await servicesApi.update(serviceId, { autoscale_enabled: next } as any);
      toast({ title: next ? 'Auto-scaling enabled' : 'Auto-scaling disabled' });
    } catch (err) {
      console.error('Toggle autoscale failed:', err);
      toast({ title: 'Failed to toggle auto-scaling', variant: 'destructive' });
    } finally {
      setActionLoading(null);
    }
  };

  const handleManualScale = async (serviceId: string, serviceName: string) => {
    if (!await confirm({ title: 'Scale up?', message: `Spawning an extra replica for "${serviceName}" now.`, confirmText: 'Scale up' })) return;
    setActionLoading(serviceId);
    try {
      await scalingApi.spawnReplica(serviceId);
      toast({ title: 'Replica spawned', description: `A new replica of ${serviceName} is starting.` });
    } catch (err: any) {
      const msg = err?.response?.data?.error || err?.response?.data?.detail || err.message || 'Scale-up failed';
      toast({ title: 'Scale-up failed', description: String(msg), variant: 'destructive' });
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (service: any) => {
    const isAddon = service.isAddon;
    const title = isAddon ? 'Delete addon?' : 'Delete service?';
    const message = `Are you sure you want to delete "${service.name}"? This cannot be undone.`;
    
    if (!await confirm({ title, message, variant: 'destructive', confirmText: 'Delete' })) return;
    
    setActionLoading(service.id);
    try {
      if (isAddon) {
        await addonsApi.delete(service.id);
      } else {
        await servicesApi.delete(service.id);
      }
      // Parent page polls every 5s — no reload needed
    } catch (err) {
      console.error('Delete failed:', err);
    } finally {
      setActionLoading(null);
    }
  };
  
  const handleForceDelete = async (service: Service) => {
    if (!await confirm({ 
      title: 'Force Delete?', 
      message: `CAUTION: This will purge "${service.name}" from the database even if cloud resources cannot be removed. Use this for stuck services only.`, 
      variant: 'destructive', 
      confirmText: 'Force Purge' 
    })) return;
    
    setActionLoading(service.id);
    try {
      await servicesApi.delete(service.id, true);
    } catch (err) {
      console.error('Force delete failed:', err);
    } finally {
      setActionLoading(null);
    }
  };

  if (!services || services.length === 0) {
    return <EmptyServicesState />;
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 p-6">
      {services.map((service) => (
        <Card
          key={service.id}
          className="group hover:border-emerald-500/50 transition-all duration-300 bg-card/50 backdrop-blur-sm"
        >
          {/* Header */}
          <div className="p-4 border-b border-border/50 flex justify-between items-start">
            <div className="flex gap-3">
              <div className="p-2.5 rounded-lg bg-muted border border-border shadow-inner">
                {service.isAddon ? (
                  <Database size={20} className="text-blue-500" />
                ) : service.name.includes('db') || service.name.includes('postgres') ? (
                  <Database size={20} className="text-blue-500" />
                ) : service.name.includes('redis') ? (
                  <Activity size={20} className="text-red-500" />
                ) : (
                  <Server size={20} className="text-emerald-500" />
                )}
              </div>
              <div>
                <h3 className="font-bold text-sm text-foreground tracking-tight cursor-pointer hover:underline" onClick={() => router.push(service.isAddon ? `/addons/${service.id}` : `/services/${service.id}`)}>
                  {service.name}
                </h3>
                <p className="text-[11px] text-muted-foreground font-mono truncate w-32">
                  {service.isAddon ? service.addon_type : (service.latest_deployment?.commit_hash?.substring(0, 7) || 'HEAD')}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {!service.isAddon && (service.autoscale_enabled || (service.min_replicas ?? 0) > 1 || (service.max_replicas ?? 0) > 1) && (
                <span className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold bg-purple-500/15 text-purple-400" title={`Replicas: ${service.min_replicas ?? 1}-${service.max_replicas ?? 1}${service.autoscale_enabled ? ' (auto)' : ''}`}>
                  <Scaling size={10} />
                  {service.min_replicas ?? 1}-{service.max_replicas ?? 1}
                </span>
              )}
              <div className={`w-2 h-2 rounded-full ${
                  service.latest_deployment?.status === 'ACTIVE' || service.latest_deployment?.status === 'LIVE' ? 'bg-emerald-500 animate-pulse' :
                  service.latest_deployment?.status === 'FAILED' ? 'bg-red-500' :
                  service.latest_deployment?.status === 'BUILDING' || service.latest_deployment?.status === 'DEPLOYING' || service.latest_deployment?.status === 'HEALTH_CHECK' ? 'bg-amber-500 animate-pulse' :
                  service.latest_deployment?.status === 'QUEUED' || service.latest_deployment?.status === 'REVIEW' ? 'bg-blue-500 animate-pulse' :
                  'bg-yellow-500'
                  }`} />
              <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => router.push(service.isAddon ? `/addons/${service.id}` : `/services/${service.id}`)}>
                <MoreVertical size={14} />
              </Button>
            </div>
          </div>

          {/* Info Row */}
          <div className="px-4 py-3 space-y-2">
            <div className="flex items-center gap-2">
                <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                  service.isAddon ? 'bg-blue-500/15 text-blue-400' :
                  service.latest_deployment?.status === 'ACTIVE' || service.latest_deployment?.status === 'LIVE' ? 'bg-emerald-500/15 text-emerald-400' :
                  service.latest_deployment?.status === 'FAILED' ? 'bg-red-500/15 text-red-400' :
                  service.latest_deployment?.status === 'BUILDING' || service.latest_deployment?.status === 'DEPLOYING' || service.latest_deployment?.status === 'HEALTH_CHECK' ? 'bg-amber-500/15 text-amber-400' :
                  service.latest_deployment?.status === 'QUEUED' || service.latest_deployment?.status === 'REVIEW' ? 'bg-blue-500/15 text-blue-400' :
                  service.latest_deployment?.status === null ? 'bg-blue-500/15 text-blue-500' :
                  'bg-yellow-500/15 text-yellow-400'
                }`}>
                  {service.isAddon ? 'ADDON' : (service.latest_deployment?.status || 'Ready to Deploy')}
                </span>
              {service.deploy_mode === 'COMPOSE' && (
                <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium bg-violet-500/15 text-violet-400">Compose</span>
              )}
              {!service.isAddon && <span className="text-[10px] text-muted-foreground ml-auto font-mono">{service.branch || 'main'}</span>}
            </div>
            {!service.isAddon && addonsByService[service.id]?.length > 0 && (
              <div className="flex items-center gap-1">
                {addonsByService[service.id].map((addon) => {
                  const meta = getAddonMetadata(addon.addon_type);
                  return (
                    <span
                      key={addon.id}
                      className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-muted/80 border border-border/50"
                      title={meta?.name || addon.addon_type}
                    >
                      {meta?.logo ? (
                        <Image src={meta.logo} alt={meta.name} width={14} height={14} className="shrink-0" />
                      ) : (
                        <Database size={10} className="text-muted-foreground shrink-0" />
                      )}
                    </span>
                  );
                })}
              </div>
            )}
            {(service.public_domain || service.node_url || service.connection_url) && (
              <p className="text-[11px] text-muted-foreground truncate">
                {service.public_domain || service.connection_url}
                {service.node_url && (
                  <span className="ml-1 text-muted-foreground/60">
                    {service.wildcard_url_enabled === false ? '(wildcard off)' : ''}
                  </span>
                )}
              </p>
            )}
            {service.node_url && (
              <p className="text-[11px] text-muted-foreground truncate font-mono" title={`Direct: ${service.node_url}`}>
                <span className="text-muted-foreground/60">direct:</span> {service.node_url.replace('https://', '')}
              </p>
            )}
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span>
                {(['BUILDING', 'QUEUED', 'REVIEW', 'DEPLOYING', 'HEALTH_CHECK'].includes(service.latest_deployment?.status || ''))
                  ? (service.latest_deployment?.target_server_name || service.node_metadata?.name || 'Local Server')
                  : (service.node_metadata?.name || 'Unassigned node')}
              </span>
              <span title={`Estimated (${service.estimated_cost?.basis || 'fallback'})`}>
                Est. {service.estimated_cost?.currency || 'USD'} {Number(service.estimated_cost?.monthly || 0).toFixed(2)}/mo
              </span>
            </div>
          </div>

          {/* Footer / Actions */}
          <div className="px-2 py-1.5 border-t border-border/50 bg-muted/20 flex justify-between items-center gap-1">
            <div className="flex gap-1 shrink-0">
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 hover:bg-emerald-500/10 hover:text-emerald-500"
                title="Logs"
                onClick={() => router.push(`/services/${service.id}?tab=logs`)}
              >
                <Terminal size={14} />
              </Button>
              {service.public_domain && service.wildcard_url_enabled !== false && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 hover:bg-blue-500/10 hover:text-blue-500"
                  title="Open App (wildcard)"
                  onClick={() => window.open(`https://${service.public_domain}`, '_blank')}
                >
                  <ExternalLink size={14} />
                </Button>
              )}
              {service.node_url && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 hover:bg-blue-500/10 hover:text-blue-500"
                  title="Open App (direct node)"
                  onClick={() => window.open(service.node_url!, '_blank')}
                >
                  <ExternalLink size={14} />
                </Button>
              )}
            </div>
            <div className="grid grid-cols-6 gap-1 shrink-0 ml-auto">
              {/* Auto-scale toggle */}
              {!service.isAddon && (
                <Button
                  variant="ghost"
                  size="icon"
                  className={`h-7 w-7 ${service.autoscale_enabled !== false ? 'text-emerald-500' : 'text-muted-foreground hover:text-emerald-500'}`}
                  title={service.autoscale_enabled !== false ? 'Auto-scaling ON (click to disable)' : 'Auto-scaling OFF (click to enable)'}
                  disabled={actionLoading === service.id}
                  onClick={() => handleToggleAutoscale(service.id, service.autoscale_enabled)}
                >
                  <Activity size={12} fill={service.autoscale_enabled !== false ? 'currentColor' : 'none'} />
                </Button>
              )}
              {/* Scale menu */}
              {!service.isAddon && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-muted-foreground hover:text-purple-500"
                      title="Scale service"
                      disabled={actionLoading === service.id}
                    >
                      <Plus size={12} />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-44">
                    <DropdownMenuItem onClick={() => handleManualScale(service.id, service.name)}>
                      <Scaling size={14} className="mr-2" /> New Replica (Horizontal)
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => router.push(`/services/${service.id}?tab=general`)}>
                      <Activity size={14} className="mr-2" /> More Resources (Vertical)
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-cyan-500"
                title="Recheck Health"
                disabled={actionLoading === service.id}
                onClick={() => handleRecheck(service.id)}
              >
                <RefreshCw size={12} className={actionLoading === service.id ? 'animate-spin' : ''} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-emerald-500"
                title="Redeploy"
                disabled={actionLoading === service.id}
                onClick={() => handleDeploy(service.id)}
              >
                <Play size={12} fill="currentColor" />
              </Button>
              {service.latest_deployment?.status === 'ACTIVE' && has(PERMISSION.SERVICE_RESTART) && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground hover:text-yellow-500"
                  title="Restart"
                  disabled={actionLoading === service.id}
                  onClick={() => handleRestart(service.id, service.name, service.latest_deployment?.status)}
                >
                  {actionLoading === service.id ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
                </Button>
              )}
              {service.latest_deployment?.status === 'ACTIVE' && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground hover:text-red-500"
                  title="Stop"
                  disabled={actionLoading === service.id}
                  onClick={() => handleStop(service.id)}
                >
                  <Square size={12} fill="currentColor" />
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-foreground"
                title="Service Details"
                onClick={() => router.push(`/services/${service.id}`)}
              >
                <ExternalLink size={12} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-red-500"
                title="Delete Service"
                disabled={actionLoading === service.id}
                onClick={() => handleDelete(service)}
              >
                <Trash2 size={12} />
              </Button>
              {service.status === 'DELETION_FAILED' && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-red-500 hover:bg-red-500/10"
                  title="Force Purge (stuck)"
                  disabled={actionLoading === service.id}
                  onClick={() => handleForceDelete(service)}
                >
                  <ShieldAlert size={12} />
                </Button>
              )}
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
});

function EmptyServicesState() {
  return (
    <div className="flex flex-col items-center justify-center py-20 px-6">
      {/* Animated Illustration */}
      <div className="relative mb-8">
        <motion.div
          animate={{ y: [0, -8, 0] }}
          transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
          className="absolute -left-12 -top-4"
        >
          <div className="p-3 rounded-xl bg-primary/10 border border-primary/20">
            <GitBranch className="w-6 h-6 text-primary" />
          </div>
        </motion.div>

        <motion.div
          animate={{ y: [0, 10, 0] }}
          transition={{ duration: 4, repeat: Infinity, ease: "easeInOut", delay: 0.5 }}
          className="absolute -right-12 -top-2"
        >
          <div className="p-3 rounded-xl bg-cyan-500/10 border border-cyan-500/20">
            <Cloud className="w-6 h-6 text-cyan-500" />
          </div>
        </motion.div>

        <motion.div
          initial={{ scale: 0.9, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ duration: 0.5 }}
          className="w-24 h-24 rounded-2xl bg-gradient-to-br from-muted to-muted/50 border border-border flex items-center justify-center"
        >
          <Rocket className="w-10 h-10 text-muted-foreground" />
        </motion.div>
      </div>

      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2 }}
        className="text-center space-y-3"
      >
        <h3 className="text-2xl font-bold">No services deployed yet</h3>
        <p className="text-muted-foreground max-w-md">
          Deploy your first application in seconds. Connect a Git repository or use one of our templates.
        </p>
      </motion.div>

      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.4 }}
        className="flex flex-col sm:flex-row gap-3 mt-8"
      >
        <Link href="/new">
          <Button size="lg" className="bg-gradient-to-r from-primary to-cyan-500 text-white shadow-lg shadow-primary/30 hover:opacity-90">
            <Rocket className="mr-2 h-4 w-4" />
            Deploy from GitHub
          </Button>
        </Link>
        <Link href="/store">
          <Button variant="outline" size="lg">
            Browse Templates
          </Button>
        </Link>
      </motion.div>

      {/* Quick Start Tips */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.6 }}
        className="mt-12 grid grid-cols-1 sm:grid-cols-3 gap-4 text-center max-w-2xl"
      >
        {[
          { step: "1", label: "Connect Repo", desc: "Link your GitHub account" },
          { step: "2", label: "Auto-Detect", desc: "We detect your framework" },
          { step: "3", label: "Deploy!", desc: "Go live in seconds" }
        ].map((item, i) => (
          <div key={i} className="p-4 rounded-xl bg-muted/30 border border-border/50">
            <div className="w-8 h-8 rounded-full bg-primary/10 text-primary font-bold flex items-center justify-center mx-auto mb-2">
              {item.step}
            </div>
            <p className="font-medium text-sm">{item.label}</p>
            <p className="text-xs text-muted-foreground">{item.desc}</p>
          </div>
        ))}
      </motion.div>
    </div>
  );
}
