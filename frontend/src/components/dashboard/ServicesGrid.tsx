'use client';

import React from 'react';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { useRouter } from 'next/navigation';
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
  RotateCcw
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Service, servicesApi } from '@/lib/api';

import { motion } from 'framer-motion';
import Link from 'next/link';

interface ServicesGridProps {
  services: Service[];
}

export function ServicesGrid({ services }: ServicesGridProps) {
  const router = useRouter();
  const confirm = useConfirm();
  const [actionLoading, setActionLoading] = React.useState<string | null>(null);

  const handleDeploy = async (serviceId: string) => {
    if (!await confirm({ title: 'Deploy service?', message: 'Trigger a new deployment for this service now?', confirmText: 'Deploy' })) return;
    setActionLoading(serviceId);
    try {
      await servicesApi.deploy(serviceId);
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

  const handleRestart = async (serviceId: string) => {
    if (!await confirm({ title: 'Restart service?', message: 'Fast-restart the container (~5 seconds). No rebuild required.', confirmText: 'Restart' })) return;
    setActionLoading(serviceId);
    try {
      await servicesApi.restart(serviceId);
    } catch (err) {
      console.error('Restart failed:', err);
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

  const handleDelete = async (service: Service) => {
    if (!await confirm({ title: 'Delete service?', message: `Are you sure you want to delete "${service.name}"? This cannot be undone.`, variant: 'destructive', confirmText: 'Delete' })) return;
    setActionLoading(service.id);
    try {
      await servicesApi.delete(service.id);
      // Parent page polls every 5s — no reload needed
    } catch (err) {
      console.error('Delete failed:', err);
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
                {service.name.includes('db') || service.name.includes('postgres') ? (
                  <Database size={20} className="text-blue-500" />
                ) : service.name.includes('redis') ? (
                  <Activity size={20} className="text-red-500" />
                ) : (
                  <Server size={20} className="text-emerald-500" />
                )}
              </div>
              <div>
                <h3 className="font-bold text-sm text-foreground tracking-tight cursor-pointer hover:underline" onClick={() => router.push(`/services/${service.id}`)}>
                  {service.name}
                </h3>
                <p className="text-[11px] text-muted-foreground font-mono truncate w-32">
                  {service.latest_deployment?.commit_hash?.substring(0, 7) || 'HEAD'}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${service.latest_deployment?.status === 'ACTIVE' ? 'bg-emerald-500 animate-pulse' :
                service.latest_deployment?.status === 'FAILED' ? 'bg-red-500' :
                service.latest_deployment?.status === 'BUILDING' || service.latest_deployment?.status === 'DEPLOYING' ? 'bg-blue-500 animate-pulse' :
                'bg-yellow-500'
                }`} />
              <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => router.push(`/services/${service.id}`)}>
                <MoreVertical size={14} />
              </Button>
            </div>
          </div>

          {/* Info Row */}
          <div className="px-4 py-3 space-y-2">
            <div className="flex items-center gap-2">
              <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                service.latest_deployment?.status === 'ACTIVE' ? 'bg-emerald-500/15 text-emerald-400' :
                service.latest_deployment?.status === 'FAILED' ? 'bg-red-500/15 text-red-400' :
                service.latest_deployment?.status === 'BUILDING' || service.latest_deployment?.status === 'DEPLOYING' ? 'bg-blue-500/15 text-blue-400' :
                'bg-yellow-500/15 text-yellow-400'
              }`}>
                {service.latest_deployment?.status || 'PENDING'}
              </span>
              {service.deploy_mode === 'COMPOSE' && (
                <span className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium bg-violet-500/15 text-violet-400">Compose</span>
              )}
              <span className="text-[10px] text-muted-foreground ml-auto font-mono">{service.branch || 'main'}</span>
            </div>
            {service.public_domain && (
              <p className="text-[11px] text-muted-foreground truncate">
                {service.public_domain}
              </p>
            )}
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span>{service.node_metadata?.name || 'Unassigned node'}</span>
              <span title={`Estimated (${service.estimated_cost?.basis || 'fallback'})`}>
                Est. {service.estimated_cost?.currency || 'USD'} {Number(service.estimated_cost?.monthly || 0).toFixed(2)}/mo
              </span>
            </div>
          </div>

          {/* Footer / Actions */}
          <div className="p-3 border-t border-border/50 bg-muted/20 flex justify-between items-center">
            <div className="flex gap-1">
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 hover:bg-emerald-500/10 hover:text-emerald-500"
                title="Logs"
                onClick={() => router.push(`/services/${service.id}?tab=logs`)}
              >
                <Terminal size={14} />
              </Button>
              {service.public_domain && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 hover:bg-blue-500/10 hover:text-blue-500"
                  title="Open App"
                  onClick={() => window.open(`https://${service.public_domain}`, '_blank')}
                >
                  <ExternalLink size={14} />
                </Button>
              )}
            </div>
            <div className="flex gap-1">
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-muted-foreground hover:text-cyan-500"
                title="Recheck Health"
                disabled={actionLoading === service.id}
                onClick={() => handleRecheck(service.id)}
              >
                <RefreshCw size={12} className={actionLoading === service.id ? 'animate-spin' : ''} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-muted-foreground hover:text-emerald-500"
                title="Redeploy"
                disabled={actionLoading === service.id}
                onClick={() => handleDeploy(service.id)}
              >
                <Play size={12} fill="currentColor" />
              </Button>
              {service.latest_deployment?.status === 'ACTIVE' && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 text-muted-foreground hover:text-yellow-500"
                  title="Restart"
                  disabled={actionLoading === service.id}
                  onClick={() => handleRestart(service.id)}
                >
                  <RotateCcw size={12} />
                </Button>
              )}
              {service.latest_deployment?.status === 'ACTIVE' && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 text-muted-foreground hover:text-red-500"
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
                className="h-8 w-8 text-muted-foreground hover:text-foreground"
                title="Service Details"
                onClick={() => router.push(`/services/${service.id}`)}
              >
                <ExternalLink size={12} />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-muted-foreground hover:text-red-500"
                title="Delete Service"
                disabled={actionLoading === service.id}
                onClick={() => handleDelete(service)}
              >
                <Trash2 size={12} />
              </Button>
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}

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
