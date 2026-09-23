"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { Gauge, Loader2, Check } from "lucide-react";
import { systemApi } from "@/lib/api";
import { ScrollReveal } from "@/components/ui/ScrollReveal";
import { cn } from "@/lib/utils";

const WORKERS = [
  { role: "main", title: "Main worker", hint: "Drains all queues it listens on. min 0 = sleeps when idle.", maxKey: "CELERY_MAIN_MAX", minKey: "CELERY_MAIN_MIN", defMax: 4, defMin: 0 },
  { role: "fast", title: "Fast worker", hint: "Heartbeats + lightweight I/O scans.", maxKey: "CELERY_FAST_MAX", minKey: "CELERY_FAST_MIN", defMax: 2, defMin: 1 },
  { role: "deploy", title: "Deploy worker", hint: "Builds serialize on the fleet lock; extra children serve quick tasks.", maxKey: "CELERY_DEPLOY_MAX", minKey: "CELERY_DEPLOY_MIN", defMax: 3, defMin: 0 },
] as const;

const BEATS = [
  { title: "Mesh health", key: "MESH_HEALTH_INTERVAL", hint: "WireGuard topology freshness (s). Liveness stays on heartbeats.", def: 120 },
  { title: "Replication health", key: "REPLICATION_HEALTH_INTERVAL", hint: "WAL lag gauge resolution (s). Failover stays on the 30s addon watchdog.", def: 60 },
] as const;

type RestartState = "idle" | "queued" | "running" | "success" | "error";

function PendingBadge({ pending, live }: { pending?: boolean; live?: string | null }) {
  if (pending) return <span className="text-xs font-medium text-amber-600 dark:text-amber-400">Restart needed</span>;
  if (live) return <span className="text-xs text-muted-foreground">Applied</span>;
  return <span className="text-xs text-muted-foreground">Unknown (daemon unreachable)</span>;
}

export function PerformanceTab() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [config, setConfig] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [workersTask, setWorkersTask] = useState<{ status: RestartState; message?: string }>({ status: "idle" });
  const [beatTask, setBeatTask] = useState<{ status: RestartState; message?: string }>({ status: "idle" });
  const pollers = useRef<{ workers?: ReturnType<typeof setInterval>; beat?: ReturnType<typeof setInterval> }>({});

  const fetchConfig = useCallback(async () => {
    try {
      const result = await systemApi.getConfig();
      setConfig(result);
    } catch {
      console.error("Failed to fetch system config");
    }
  }, []);

  useEffect(() => {
    fetchConfig();
    return () => {
      Object.values(pollers.current).forEach((p) => { if (p) clearInterval(p); });
    };
  }, [fetchConfig]);

  const setTask = useCallback((which: "workers" | "beat", patch: { status: RestartState; message?: string }) => {
    (which === "workers" ? setWorkersTask : setBeatTask)((prev) => ({ ...prev, ...patch }));
  }, []);

  const finishRestart = useCallback((which: "workers" | "beat", response: any) => {
    const result = response?.result && typeof response.result === "object" ? response.result : response;
    const s = String(result?.status || response?.status || "").toLowerCase();
    const ok = s === "success" || response?.state === "SUCCESS";
    const message = result?.message || response?.message || (ok ? "Restart completed." : "Restart failed.");
    const poller = pollers.current[which];
    if (poller) { clearInterval(poller); delete pollers.current[which]; }
    setTask(which, { status: ok ? "success" : "error", message });
    toast({ title: ok ? "Restart completed" : "Restart refused/failed", description: message, variant: ok ? "success" : "destructive" });
    if (ok) void fetchConfig();
  }, [fetchConfig, setTask, toast]);

  const pollRestart = useCallback((which: "workers" | "beat", taskId: string) => {
    const existing = pollers.current[which];
    if (existing) clearInterval(existing);
    const poll = async () => {
      try {
        const response = await systemApi.getMaintenanceTask(taskId);
        const state = String(response?.state || "").toUpperCase();
        const sv = String(response?.status || "").toLowerCase();
        if (state === "SUCCESS" || state === "FAILURE" || sv === "success" || sv === "error") {
          finishRestart(which, response);
          return;
        }
        setTask(which, { status: sv === "queued" ? "queued" : "running", message: response?.message || "Restart is running." });
      } catch {
        setTask(which, { status: "running", message: "Waiting for the backend to reconnect..." });
      }
    };
    void poll();
    pollers.current[which] = setInterval(poll, 3000);
  }, [finishRestart, setTask]);

  const handleRestart = useCallback(async (which: "workers" | "beat") => {
    const action = which === "workers" ? "restart_workers" : "restart_beat";
    const confirmed = await confirm({
      title: which === "workers" ? "Restart Celery workers?" : "Restart Celery beat?",
      message: which === "workers"
        ? "Saved concurrency values apply at (re)start. Refused automatically while a deployment is BUILDING."
        : "Saved cadences apply at beat restart. Refused automatically while a deployment is BUILDING.",
      confirmText: "Restart",
    });
    if (!confirmed) return;
    setTask(which, { status: "queued", message: "Queueing restart..." });
    try {
      const response: any = await systemApi.runMaintenance(action);
      if (response?.result || response?.status === "success" || response?.status === "error") {
        finishRestart(which, response);
        return;
      }
      const taskId = response?.task_id || response?.taskId;
      setTask(which, { status: "queued", message: response?.message || "Restart queued." });
      if (taskId) pollRestart(which, taskId);
    } catch (err: any) {
      const data = err?.response?.data;
      const errorMsg = data?.message || data?.error || err?.message || "Failed to queue restart.";
      setTask(which, { status: "error", message: String(errorMsg) });
      toast({ title: "Error", description: String(errorMsg), variant: "destructive" });
    }
  }, [confirm, finishRestart, pollRestart, setTask, toast]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {};
      for (const w of WORKERS) {
        const max = Math.max(1, Math.min(32, parseInt(config[w.maxKey]) || w.defMax));
        const min = Math.max(0, Math.min(max, parseInt(config[w.minKey]) || 0));
        payload[w.maxKey] = max;
        payload[w.minKey] = min;
      }
      for (const b of BEATS) {
        payload[b.key] = Math.max(15, Math.min(3600, parseInt(config[b.key]) || b.def));
      }
      const result = await systemApi.updateConfig(payload);
      setConfig(result);
      toast({ title: "Saved", description: "Performance values saved. Restart workers/beat to apply pending changes." });
    } catch {
      toast({ title: "Failed", description: "Could not save performance config.", variant: "destructive" });
    } finally {
      setSaving(false);
    }
  }, [config, toast]);

  if (!config) return <div className="flex items-center justify-center py-8">Loading...</div>;

  const desired = config.CELERY_DESIRED || {};
  const live = config.CELERY_LIVE || {};
  const pending = config.CELERY_PENDING_RESTART || {};
  const coverage = config.CELERY_QUEUE_COVERAGE || { main_queues: [], warnings: [] };
  const beatDesired = config.BEAT_DESIRED || {};
  const beatEffective = config.BEAT_EFFECTIVE || {};

  const restartBtn = (which: "workers" | "beat", label: string) => {
    const task = which === "workers" ? workersTask : beatTask;
    if (task.status === "queued" || task.status === "running")
      return <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> {task.status === "queued" ? "Queued" : "Restarting"}</>;
    if (task.status === "success") return <><Check className="mr-2 h-4 w-4" /> Done</>;
    return label;
  };

  return (
    <div className="space-y-6">
      <ScrollReveal variant="scaleIn">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Gauge className="h-5 w-5 text-violet-500" /> Worker Fleet</CardTitle>
            <CardDescription>
              Desired prefork concurrency per worker (max, min). min 0 lets burst workers sleep when idle —
              the main worker drains their queues. Applied at worker (re)start, including plain docker restart.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-6">
              {WORKERS.map((w) => (
                <div key={w.role} className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end border-b pb-4 last:border-0 last:pb-0">
                  <div className="space-y-1 md:col-span-1">
                    <p className="text-sm font-medium">{w.title}</p>
                    <p className="text-xs text-muted-foreground">{w.hint}</p>
                  </div>
                  <div className="space-y-2">
                    <Label>Max (1-32)</Label>
                    <Input type="number" min={1} max={32} value={config[w.maxKey] ?? w.defMax}
                      onChange={(e) => setConfig({ ...config, [w.maxKey]: parseInt(e.target.value) || w.defMax })} />
                  </div>
                  <div className="space-y-2">
                    <Label>Min (0-max)</Label>
                    <Input type="number" min={0} max={32} value={config[w.minKey] ?? w.defMin}
                      onChange={(e) => setConfig({ ...config, [w.minKey]: parseInt(e.target.value) || 0 })} />
                  </div>
                  <div className="space-y-1 text-xs font-mono">
                    <p className="text-muted-foreground">desired {desired[w.role] ?? "—"} · live {live[w.role] ?? "?"}</p>
                    <PendingBadge pending={pending[w.role]} live={live[w.role]} />
                  </div>
                </div>
              ))}
              {coverage.warnings?.length > 0 && (
                <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 space-y-1">
                  {coverage.warnings.map((wrn: string, i: number) => (
                    <p key={i} className="text-xs text-amber-700 dark:text-amber-300">{wrn}</p>
                  ))}
                </div>
              )}
              {workersTask.message && (
                <p className={cn("text-xs", workersTask.status === "error" ? "text-destructive" : "text-muted-foreground")}>{workersTask.message}</p>
              )}
              <div className="flex justify-end gap-2">
                <Button variant="outline" disabled={workersTask.status === "queued" || workersTask.status === "running"} onClick={() => handleRestart("workers")}>
                  {restartBtn("workers", "Restart workers")}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </ScrollReveal>

      <ScrollReveal variant="slideRight" delay={0.1}>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">Beat Cadences</CardTitle>
            <CardDescription>Periodic check intervals in seconds (15-3600). Applied at beat restart. The 30s addon-HA watchdog stays fixed for failover latency.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-6">
              {BEATS.map((b) => (
                <div key={b.key} className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
                  <div className="space-y-2">
                    <Label>{b.title} (s)</Label>
                    <Input type="number" min={15} max={3600} value={config[b.key] ?? b.def}
                      onChange={(e) => setConfig({ ...config, [b.key]: parseInt(e.target.value) || b.def })} />
                    <p className="text-xs text-muted-foreground">{b.hint}</p>
                  </div>
                  <div className="space-y-1 text-xs font-mono md:col-span-2">
                    <p className="text-muted-foreground">
                      desired {beatDesired[b.key] ?? config[b.key] ?? "—"}s · effective {beatEffective[b.key] ?? "?"}s
                    </p>
                    <PendingBadge pending={config.BEAT_PENDING_RESTART} live={beatEffective[b.key] != null ? String(beatEffective[b.key]) : null} />
                  </div>
                </div>
              ))}
              {beatTask.message && (
                <p className={cn("text-xs", beatTask.status === "error" ? "text-destructive" : "text-muted-foreground")}>{beatTask.message}</p>
              )}
              <div className="flex justify-end gap-2">
                <Button onClick={handleSave} disabled={saving}>{saving ? "Saving..." : "Save Performance"}</Button>
                <Button variant="outline" disabled={beatTask.status === "queued" || beatTask.status === "running"} onClick={() => handleRestart("beat")}>
                  {restartBtn("beat", "Restart beat")}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </ScrollReveal>
    </div>
  );
}
