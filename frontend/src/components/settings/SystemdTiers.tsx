"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Moon, Power, Sun, Loader2, RefreshCw } from "lucide-react";
import { autoscalerApi, type InfraTier } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { cn } from "@/lib/utils";
import { SleepingZzz } from "@/components/SleepingZzz";

/**
 * SystemdTiers — napd-managed infra tiers (build cache, Grafana UI, …)
 * on the autoscaler page. Read-only unless napd answers; wake/sleep
 * are staff-only server-side and napd itself refuses non-autosleepable
 * tiers (the sleep button only enables where napd allows it).
 */
export function SystemdTiers() {
  const [tiers, setTiers] = useState<Record<string, InfraTier>>({});
  const [available, setAvailable] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const { toast } = useToast();
  const confirm = useConfirm();

  const load = useCallback(async () => {
    try {
      const res = await autoscalerApi.getInfraTiers();
      setAvailable(res.napd_available);
      setTiers(res.tiers);
    } catch {
      setAvailable(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const act = async (tier: string, op: "wake" | "sleep") => {
    if (op === "sleep") {
      const ok = await confirm({
        title: `Sleep tier '${tier}'?`,
        message: "Its containers stop. Autosleep tiers re-wake on demand (builds wake buildcache); anything else stays down until woken here.",
        confirmText: "Sleep tier",
        variant: "destructive",
      });
      if (!ok) return;
    }
    setBusy(`${op}:${tier}`);
    try {
      const res = op === "wake"
        ? await autoscalerApi.wakeInfraTier(tier)
        : await autoscalerApi.sleepInfraTier(tier);
      if (!res.ok) throw new Error("refused");
      toast({ title: `Tier '${tier}' ${op === "wake" ? "waking" : "sleeping"}`, description: "State refreshes in a few seconds." });
      setTimeout(load, 4000);
    } catch {
      toast({ title: `Could not ${op} '${tier}'`, description: op === "sleep" ? "napd refuses tiers that are not autosleepable." : "napd did not confirm wake.", variant: "destructive" });
    } finally {
      setBusy(null);
    }
  };

  const names = Object.keys(tiers).sort();
  return (
    <Card className="border-border/50">
      <CardHeader className="pb-3 border-b border-border/50">
        <CardTitle className="text-base flex items-center gap-2">
          <Moon size={16} className="text-indigo-400" />
          Systemd tiers
          <span className="text-xs font-normal text-muted-foreground">
            ({available === null ? "…" : available ? `${names.length} napd-managed` : "napd unavailable"} — host sleep/wake, on-demand)
          </span>
          <button onClick={load} className="ml-auto text-muted-foreground hover:text-foreground" title="Refresh tiers">
            <RefreshCw size={14} />
          </button>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {available === false ? (
          <div className="p-8 text-center text-muted-foreground text-sm">napd is not reachable from the backend (secret unset or unit down). Tiers stay awake — nothing sleeps without the daemon.</div>
        ) : names.length === 0 ? (
          <div className="p-8 text-center text-muted-foreground text-sm">No tiers reported.</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 p-4">
            {names.map((name) => {
              const t = tiers[name];
              const sleeping = !t.awake;
              return (
                <div key={name} className="p-4 rounded-xl border border-border/50 bg-card/80 space-y-3">
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="font-bold text-sm font-mono">{name}</div>
                      <div className="text-[11px] text-muted-foreground font-mono truncate max-w-[220px]" title={t.services.join(", ")}>
                        {t.services.slice(0, 3).join(", ")}{t.services.length > 3 ? ` +${t.services.length - 3}` : ""}
                      </div>
                    </div>
                    <span className={cn(
                      "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase flex items-center gap-1",
                      sleeping ? "bg-indigo-500/10 text-indigo-400" : "bg-emerald-500/10 text-emerald-400"
                    )}>
                      {sleeping && <SleepingZzz />}
                      {sleeping ? "asleep" : "awake"}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-center font-mono text-xs">
                    <div className="p-2 rounded bg-muted/30">
                      <div className="font-bold">{t.autosleep ? `${Math.round(t.idle_secs / 60)}m idle` : "manual"}</div>
                      <div className="text-[9px] text-muted-foreground uppercase">Sleep policy</div>
                    </div>
                    <div className="p-2 rounded bg-muted/30">
                      <div className="font-bold">{t.services.length}</div>
                      <div className="text-[9px] text-muted-foreground uppercase">Services</div>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="outline" size="sm" className="flex-1 h-7 text-xs gap-1"
                      disabled={busy !== null || t.awake}
                      onClick={() => act(name, "wake")}
                    >
                      {busy === `wake:${name}` ? <Loader2 size={12} className="animate-spin" /> : <Sun size={12} />} Wake
                    </Button>
                    <Button
                      variant="outline" size="sm" className="flex-1 h-7 text-xs gap-1 hover:bg-indigo-500/10 hover:text-indigo-400"
                      disabled={busy !== null || sleeping || !t.autosleep}
                      title={t.autosleep ? "Sleep this tier" : "napd refuses non-autosleepable tiers"}
                      onClick={() => act(name, "sleep")}
                    >
                      {busy === `sleep:${name}` ? <Loader2 size={12} className="animate-spin" /> : <Power size={12} />} Sleep
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
