'use client';

import React from 'react';
import { motion } from 'framer-motion';
import {
  Cloud, Trash2, Edit, ChevronDown, ChevronRight,
  Clock, Globe, Cpu
} from 'lucide-react';
import type { CloudResource } from '@/lib/api';
import { Badge } from '@/components/ui/badge';

const PROVIDER_STYLES: Record<string, { bg: string; border: string; text: string; badge: string; icon: string }> = {
  aws: {
    bg: 'from-orange-500/10 to-amber-600/5',
    border: 'border-orange-500/20',
    text: 'text-orange-400',
    badge: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
    icon: '☁️',
  },
  gcp: {
    bg: 'from-blue-500/10 to-emerald-600/5',
    border: 'border-blue-500/20',
    text: 'text-blue-400',
    badge: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
    icon: '🔵',
  },
  azure: {
    bg: 'from-cyan-500/10 to-blue-600/5',
    border: 'border-cyan-500/20',
    text: 'text-cyan-400',
    badge: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
    icon: '🟦',
  },
  digitalocean: {
    bg: 'from-sky-500/10 to-blue-600/5',
    border: 'border-sky-500/20',
    text: 'text-sky-400',
    badge: 'bg-sky-500/10 text-sky-400 border-sky-500/20',
    icon: '🌊',
  },
  linode: {
    bg: 'from-emerald-500/10 to-green-600/5',
    border: 'border-emerald-500/20',
    text: 'text-emerald-400',
    badge: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    icon: '🌿',
  },
  vultr: {
    bg: 'from-blue-600/10 to-indigo-600/5',
    border: 'border-blue-600/20',
    text: 'text-blue-500',
    badge: 'bg-blue-600/10 text-blue-500 border-blue-600/20',
    icon: '⚡',
  },
  hetzner: {
    bg: 'from-red-500/10 to-red-700/5',
    border: 'border-red-500/20',
    text: 'text-red-400',
    badge: 'bg-red-500/10 text-red-400 border-red-500/20',
    icon: '🔴',
  },
  ovh: {
    bg: 'from-purple-500/10 to-purple-800/5',
    border: 'border-purple-500/20',
    text: 'text-purple-400',
    badge: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
    icon: '🟣',
  },
  scaleway: {
    bg: 'from-pink-500/10 to-rose-600/5',
    border: 'border-pink-500/20',
    text: 'text-pink-400',
    badge: 'bg-pink-500/10 text-pink-400 border-pink-500/20',
    icon: '🎯',
  },
  upcloud: {
    bg: 'from-teal-500/10 to-teal-700/5',
    border: 'border-teal-500/20',
    text: 'text-teal-400',
    badge: 'bg-teal-500/10 text-teal-400 border-teal-500/20',
    icon: '⬆️',
  },
  serverion: {
    bg: 'from-zinc-500/10 to-zinc-700/5',
    border: 'border-zinc-500/20',
    text: 'text-zinc-400',
    badge: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20',
    icon: '🖥️',
  },
  contabo: {
    bg: 'from-yellow-500/10 to-amber-700/5',
    border: 'border-yellow-500/20',
    text: 'text-yellow-400',
    badge: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20',
    icon: '🟡',
  },
};

const DEFAULT_PROVIDER = {
  bg: 'from-slate-500/10 to-slate-800/5',
  border: 'border-slate-500/20',
  text: 'text-slate-400',
  badge: 'bg-slate-500/10 text-slate-400 border-slate-500/20',
  icon: '☁️',
};

const STATUS_VARIANTS: Record<string, 'success' | 'warning' | 'destructive' | 'gray' | 'default' | 'info' | 'purple'> = {
  running: 'success',
  active: 'success',
  available: 'success',
  online: 'success',
  provisioning: 'warning',
  creating: 'warning',
  pending: 'warning',
  updating: 'warning',
  stopped: 'gray',
  paused: 'gray',
  error: 'destructive',
  failed: 'destructive',
  deletion_failed: 'destructive',
};

const STATUS_LABELS: Record<string, string> = {
  running: 'Running',
  active: 'Active',
  available: 'Available',
  online: 'Online',
  provisioning: 'Provisioning',
  creating: 'Creating',
  pending: 'Pending',
  updating: 'Updating',
  stopped: 'Stopped',
  paused: 'Paused',
  error: 'Error',
  failed: 'Failed',
  deletion_failed: 'Deletion Failed',
};

interface CloudResourceCardProps {
  resource: CloudResource;
  onEdit: (resource: CloudResource) => void;
  onDelete: (id: string) => void;
}

export const CloudResourceCard = React.memo(function CloudResourceCard({ resource, onEdit, onDelete }: CloudResourceCardProps) {
  const [configOpen, setConfigOpen] = React.useState(false);
  const provider = PROVIDER_STYLES[resource.provider?.toLowerCase()] || DEFAULT_PROVIDER;
  const statusKey = resource.status?.toLowerCase();
  const statusVariant = STATUS_VARIANTS[statusKey] || 'default';
  const statusLabel = STATUS_LABELS[statusKey] || resource.status;

  const configSummary = resource.config ? Object.entries(resource.config).slice(0, 3) : [];

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      whileHover={{ y: -2 }}
      transition={{ duration: 0.2 }}
      className={`relative group bg-gradient-to-br ${provider.bg} border ${provider.border} rounded-2xl p-5 shadow-lg shadow-black/10 backdrop-blur-md flex flex-col justify-between h-full`}
    >
      <div className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-2xl shrink-0">{provider.icon}</span>
            <div className="min-w-0">
              <h3 className="text-base font-bold text-white tracking-tight truncate">{resource.name}</h3>
              <p className="text-xs text-zinc-500 font-medium truncate capitalize">{resource.provider}</p>
            </div>
          </div>
          <Badge variant={statusVariant} className="shrink-0">
            {statusLabel}
          </Badge>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="text-xs border-white/10 text-zinc-400 flex items-center gap-1">
            <Globe className="h-3 w-3" />
            {resource.region || 'global'}
          </Badge>
          <Badge variant="outline" className="text-xs border-white/10 text-zinc-400 flex items-center gap-1">
            <Cpu className="h-3 w-3" />
            {resource.type || 'N/A'}
          </Badge>
        </div>

        {configSummary.length > 0 && (
          <div>
            <button
              onClick={() => setConfigOpen(!configOpen)}
              className="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-300 transition"
            >
              {configOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              Config ({Object.keys(resource.config).length} keys)
            </button>
            {configOpen && (
              <div className="mt-2 p-2 rounded-lg bg-black/20 border border-white/5 space-y-1">
                {Object.entries(resource.config).map(([key, value]) => (
                  <div key={key} className="flex justify-between text-[11px]">
                    <span className="text-zinc-500 font-mono">{key}</span>
                    <span className="text-zinc-300 font-mono truncate ml-2 max-w-[200px]">
                      {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="mt-4 pt-3 border-t border-white/5 space-y-3">
        <div className="flex items-center gap-3 text-[11px] text-zinc-500">
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            Created {new Date(resource.created_at).toLocaleDateString()}
          </span>
          <span className="flex items-center gap-1">
            <Clock className="h-3 w-3" />
            Updated {new Date(resource.updated_at).toLocaleDateString()}
          </span>
        </div>
        <div className="flex items-center justify-end gap-1 opacity-70 group-hover:opacity-100 transition-opacity">
          <button
            onClick={() => onEdit(resource)}
            className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-white/5 transition"
            title="Edit Resource"
          >
            <Edit className="h-4 w-4" />
          </button>
          <button
            onClick={() => onDelete(resource.id)}
            className="p-1.5 rounded-lg text-red-400 hover:text-red-300 hover:bg-red-500/10 transition"
            title="Delete Resource"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>
    </motion.div>
  );
});
