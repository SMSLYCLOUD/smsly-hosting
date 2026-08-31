"use client";

import React, { useState } from 'react';
import { Network, Copy, Check, Shield, ShieldOff, Loader2 } from 'lucide-react';
import { servicesApi, Service } from '@/lib/api';
import { toast } from '@/components/ui/use-toast';

interface InternalNetworkCardProps {
  service: Service;
  onUpdated?: (updated: Service) => void;
}

export function InternalNetworkCard({ service, onUpdated }: InternalNetworkCardProps) {
  const [toggling, setToggling] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const addresses = Array.isArray(service.internal_addresses) ? service.internal_addresses : [];
  const useInternal = service.use_internal_network !== false;

  const copyAddress = (value: string, label: string) => {
    navigator.clipboard.writeText(value);
    setCopied(label);
    setTimeout(() => setCopied(null), 1200);
    toast({ title: 'Copied!', description: value });
  };

  const toggleInternal = async () => {
    const newVal = !useInternal;
    try {
      setToggling(true);
      const updated = await servicesApi.update(service.id, { use_internal_network: newVal });
      onUpdated?.(updated as Service);
      toast({
        title: newVal ? 'Internal network enabled' : 'Internal network disabled',
        description: newVal
          ? 'Service joins the project bridge + platform bridge on next deploy. Traffic stays host-internal — no public DNS, no TLS overhead.'
          : 'Service stays on the shared platform bridge only. Takes effect on next deploy.',
      });
    } catch (err: any) {
      toast({
        title: 'Failed to update internal network',
        description: err?.response?.data?.error || 'Could not save the setting.',
        variant: 'destructive',
      });
    } finally {
      setToggling(false);
    }
  };

  const netLabel = (network: string): string => {
    if (network === 'smsly-platform-net') return 'Platform bridge (cross-project)';
    if (network === 'smsly-net') return 'Platform shared (default)';
    if (network.startsWith('smsly-net-')) return 'Project bridge (isolated)';
    return network;
  };

  return (
    <div className="col-span-1 md:col-span-4 bg-card border border-border p-6 rounded-xl shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Network className="w-4 h-4 text-emerald-500" />
          <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider">
            Internal Network
          </h4>
        </div>
        <button
          onClick={toggleInternal}
          disabled={toggling}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold transition-all ${
            useInternal
              ? 'bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20'
              : 'bg-zinc-500/10 text-zinc-400 hover:bg-zinc-500/20'
          }`}
        >
          {toggling ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : useInternal ? (
            <Shield className="w-3.5 h-3.5" />
          ) : (
            <ShieldOff className="w-3.5 h-3.5" />
          )}
          {useInternal ? 'Enabled' : 'Disabled'}
        </button>
      </div>

      <p className="text-xs text-muted-foreground mb-4">
        Host-internal addresses for service-to-service traffic. Requests on these
        IPs never leave the host — lower latency, no Cloudflare DNS round-trip.
      </p>

      {addresses.length === 0 ? (
        <div className="text-sm text-muted-foreground italic">
          No internal addresses — the service is not deployed yet or the internal
          network is disabled.
        </div>
      ) : (
        <div className="space-y-2">
          {addresses.map((addr, idx) => (
            <div
              key={`${addr.network}-${idx}`}
              className="flex items-center gap-2 p-3 bg-muted/30 border border-border rounded-lg"
            >
              <div className="min-w-0 flex-1">
                <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-0.5">
                  {netLabel(addr.network)}
                </p>
                <p className="font-mono text-sm text-foreground truncate">
                  {addr.ip}:{addr.port}
                </p>
                {addr.aliases && addr.aliases.length > 0 && (
                  <p className="font-mono text-[10px] text-muted-foreground truncate mt-0.5">
                    {addr.aliases.join(', ')}
                  </p>
                )}
              </div>
              <button
                onClick={() => copyAddress(`${addr.ip}:${addr.port}`, addr.network)}
                className="p-2 hover:bg-muted rounded-md transition-colors"
                title="Copy host:port"
              >
                {copied === addr.network ? (
                  <Check className="w-4 h-4 text-emerald-500" />
                ) : (
                  <Copy className="w-4 h-4 text-muted-foreground" />
                )}
              </button>
            </div>
          ))}
        </div>
      )}

      {addresses.length > 0 && (
        <p className="text-[10px] text-muted-foreground mt-3">
          Use <code className="font-mono bg-muted/50 px-1 rounded">http://{addresses[0].aliases?.[0] || addresses[0].ip}:{addresses[0].port}</code> in
          other services&apos; env vars for direct host-internal calls.
        </p>
      )}
    </div>
  );
}
