"use client";

import React, { useEffect, useState } from 'react';
import { KeyRound, Lock, Unlock, Shield, ShieldOff, Loader2 } from 'lucide-react';
import { servicesApi, Service, ServiceEncryptionStatus } from '@/lib/api';
import { toast } from '@/components/ui/use-toast';

interface EncryptionAtRestCardProps {
  service: Service;
  onUpdated?: (updated: Service) => void;
}

export function EncryptionAtRestCard({ service, onUpdated }: EncryptionAtRestCardProps) {
  const [toggling, setToggling] = useState(false);
  const [actionBusy, setActionBusy] = useState<'lock' | 'unlock' | null>(null);
  const [statusData, setStatusData] = useState<ServiceEncryptionStatus | null>(null);

  const optedIn = Boolean(service.encryption_at_rest_enabled);

  useEffect(() => {
    let active = true;
    servicesApi.getEncryptionStatus(service.id)
      .then((res) => {
        if (active) setStatusData(res);
      })
      .catch(() => {
        // Optional controller telemetry; do not disrupt settings view if cryptd is offline
      });
    return () => {
      active = false;
    };
  }, [service.id, optedIn]);

  const toggleOptIn = async () => {
    const nextVal = !optedIn;
    try {
      setToggling(true);
      const res = await servicesApi.setEncryptionOptIn(service.id, nextVal);
      onUpdated?.({ ...service, encryption_at_rest_enabled: res.encryption_at_rest_enabled });
      if (res.platform_cryptd_enabled && res.sync && res.sync.ok === false) {
        toast({
          title: 'Saved locally, but controller sync failed',
          description: res.sync.error || 'Could not reach grid-cryptd daemon to sync policy.',
          variant: 'destructive',
        });
      } else {
        toast({
          title: nextVal ? 'Encryption at rest opted IN' : 'Encryption at rest opted OUT',
          description: nextVal
            ? 'Unique weekly service key (SEK), volume encryption, and Postgres/Redis gate protection enabled.'
            : 'Service opted out of grid-cryptd enforcement and DB locking.',
        });
      }
    } catch (err: any) {
      toast({
        title: 'Failed to update encryption opt-in',
        description: err?.response?.data?.error || 'Could not update service encryption setting.',
        variant: 'destructive',
      });
    } finally {
      setToggling(false);
    }
  };

  const handleLockToggle = async (action: 'lock' | 'unlock') => {
    try {
      setActionBusy(action);
      if (action === 'lock') {
        await servicesApi.lockEncryption(service.id);
        toast({
          title: 'Service locked at DB point of contact',
          description: 'Active Postgres/Redis connections severed and encrypted mounts locked.',
        });
      } else {
        await servicesApi.unlockEncryption(service.id);
        toast({
          title: 'Service unlocked',
          description: 'Key lease renewed for database, Redis, and volume access.',
        });
      }
      const latest = await servicesApi.getEncryptionStatus(service.id);
      setStatusData(latest);
    } catch (err: any) {
      toast({
        title: `Failed to ${action} service encryption`,
        description: err?.response?.data?.error || 'Check grid-cryptd controller availability.',
        variant: 'destructive',
      });
    } finally {
      setActionBusy(null);
    }
  };

  const isAllowed = statusData?.policy?.allowed ?? true;

  return (
    <div className="col-span-1 md:col-span-4 bg-card border border-border p-6 rounded-xl shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <KeyRound className="w-4 h-4 text-emerald-500" />
          <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider">
            Encryption at Rest &amp; DB Lock (Opt-In)
          </h4>
        </div>
        <button
          type="button"
          onClick={toggleOptIn}
          disabled={toggling}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-bold transition-all ${
            optedIn
              ? 'bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20'
              : 'bg-zinc-500/10 text-zinc-400 hover:bg-zinc-500/20'
          }`}
        >
          {toggling ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : optedIn ? (
            <Shield className="w-3.5 h-3.5" />
          ) : (
            <ShieldOff className="w-3.5 h-3.5" />
          )}
          {optedIn ? 'Enabled' : 'Opted Out'}
        </button>
      </div>

      <p className="text-xs text-muted-foreground mb-4">
        Optional per-service cryptographic isolation via <span className="font-mono">grid-cryptd</span>.
        When opted in, this service gets a unique weekly HKDF sub-key for Postgres/Redis gate locking,
        transparent Redis value encryption, volume encryption, and backup DEK wrapping.
      </p>

      {optedIn && (
        <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-border/60">
          <div className="text-xs text-muted-foreground space-y-0.5">
            <div>
              Controller:{' '}
              <span className="font-mono text-foreground">
                {statusData?.platform_cryptd_enabled ? (statusData?.daemon_status?.state || 'ACTIVE') : 'Daemon not configured'}
              </span>
              {statusData?.daemon_status?.current_week ? (
                <span className="ml-2 font-mono text-muted-foreground">
                  (Week #{statusData.daemon_status.current_week})
                </span>
              ) : null}
            </div>
            <div>
              DB/Redis Lease:{' '}
              <span className={`font-semibold ${isAllowed ? 'text-emerald-500' : 'text-amber-500'}`}>
                {isAllowed ? 'Unlocked' : 'Locked'}
              </span>
            </div>
          </div>

          {statusData?.platform_cryptd_enabled && (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => handleLockToggle('lock')}
                disabled={actionBusy !== null}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-destructive/10 text-destructive hover:bg-destructive/20 transition-colors"
              >
                {actionBusy === 'lock' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Lock className="w-3.5 h-3.5" />}
                Lock DB Access
              </button>
              <button
                type="button"
                onClick={() => handleLockToggle('unlock')}
                disabled={actionBusy !== null}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 transition-colors"
              >
                {actionBusy === 'unlock' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Unlock className="w-3.5 h-3.5" />}
                Unlock Lease
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
