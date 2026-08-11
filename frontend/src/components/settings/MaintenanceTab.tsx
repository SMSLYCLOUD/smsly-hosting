"use client";

import { useCallback, useRef, useState, useEffect } from "react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Loader2, Check, Cloud } from "lucide-react";
import { systemApi } from "@/lib/api";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { cn } from "@/lib/utils";
import UpdateTerminalStream from "@/components/terminal/UpdateTerminalStream";

type MaintenanceAction = "clear" | "refresh" | "update" | "registry_gc" | "build_cache";
type MaintenanceState = "idle" | "queued" | "running" | "success" | "error";

interface MaintenanceTaskState {
  status: MaintenanceState;
  taskId?: string | null;
  message?: string;
}

const INITIAL_STATE: Record<MaintenanceAction, MaintenanceTaskState> = {
  clear: { status: "idle" },
  refresh: { status: "idle" },
  registry_gc: { status: "idle" },
  build_cache: { status: "idle" },
  update: { status: "idle" },
};

const COPY: Record<MaintenanceAction, { title: string; message: string; confirmText: string; variant?: "default" | "destructive" }> = {
  clear: { title: "Clear all system caches?", message: "This will force a refresh of all internal caches. It's safe but might cause a temporary spike in database load.", confirmText: "Clear Caches" },
  refresh: { title: "Sync Proxy Routing?", message: "This regenerates the proxy configuration and asks the host watcher to reload Caddy.", confirmText: "Sync Proxy" },
  registry_gc: { title: "Garbage Collect Registry?", message: "This removes unused layers from the private registry. This cannot be undone.", confirmText: "Run GC" },
  build_cache: { title: "Clear Build Caches?", message: "This clears BuildKit and language caches. Next builds might take longer.", confirmText: "Clear Caches" },
  update: { title: "Update Platform?", message: "This asks the host updater to pull the latest code and rebuild services. The dashboard may briefly disconnect.", confirmText: "Update Platform" },
};

export function MaintenanceTab() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const [tasks, setTasks] = useState<Record<MaintenanceAction, MaintenanceTaskState>>(INITIAL_STATE);
  const pollers = useRef<Partial<Record<MaintenanceAction, ReturnType<typeof setInterval>>>>({});

  const updateTask = useCallback((action: MaintenanceAction, patch: Partial<MaintenanceTaskState>) => {
    setTasks((prev) => ({ ...prev, [action]: { ...prev[action], ...patch } }));
  }, []);

  const stopPolling = useCallback((action: MaintenanceAction) => {
    const poller = pollers.current[action];
    if (poller) { clearInterval(poller); delete pollers.current[action]; }
  }, []);

  const finishTask = useCallback((action: MaintenanceAction, response: any) => {
    const result = response?.result && typeof response.result === "object" ? response.result : response;
    const resultStatus = String(result?.status || response?.status || "").toLowerCase();
    const ok = resultStatus === "success" || response?.state === "SUCCESS";
    const message = result?.message || response?.message || (ok ? "Maintenance task completed." : "Maintenance task failed.");
    updateTask(action, { status: ok ? "success" : "error", taskId: response?.task_id || response?.taskId || null, message });
    toast({ title: ok ? "Maintenance completed" : "Maintenance failed", description: message, variant: ok ? "success" : "destructive" });
  }, [toast, updateTask]);

  const startUpdatePolling = useCallback((updateId: string) => {
    stopPolling("update");
    const poll = async () => {
      try {
        const response = await systemApi.getPlatformUpdate(updateId);
        const status = response?.status || "";
        if (status === "COMPLETED") { stopPolling("update"); updateTask("update", { status: "success", taskId: updateId, message: "Platform update completed successfully." }); toast({ title: "Update completed", description: "Platform update completed successfully.", variant: "success" }); return; }
        if (status === "FAILED" || status === "ROLLED_BACK") { stopPolling("update"); const errorMsg = response?.error_message || "Platform update failed."; updateTask("update", { status: "error", taskId: updateId, message: errorMsg }); toast({ title: "Update failed", description: errorMsg, variant: "destructive" }); return; }
        updateTask("update", { status: "running", taskId: updateId, message: response?.current_step || `Update is ${status.toLowerCase()}...` });
      } catch { updateTask("update", { status: "running", taskId: updateId, message: "Waiting for the backend to reconnect..." }); }
    };
    void poll();
    pollers.current["update"] = setInterval(poll, 5000);
  }, [stopPolling, updateTask, toast]);

  const startPolling = useCallback((action: MaintenanceAction, taskId: string) => {
    stopPolling(action);
    const poll = async () => {
      try {
        const response = await systemApi.getMaintenanceTask(taskId);
        const state = String(response?.state || "").toUpperCase();
        const statusValue = String(response?.status || "").toLowerCase();
        if (state === "SUCCESS" || state === "FAILURE" || statusValue === "success" || statusValue === "error") {
          stopPolling(action);
          if (action === "update" && state === "SUCCESS") {
            const result = response?.result && typeof response.result === "object" ? response.result : null;
            const platformUpdateId = result?.task_id;
            if (platformUpdateId) { updateTask(action, { status: "queued", taskId: platformUpdateId, message: "Platform update initiated, tracking progress..." }); startUpdatePolling(platformUpdateId); return; }
          }
          finishTask(action, response); return;
        }
        updateTask(action, { status: statusValue === "queued" ? "queued" : "running", taskId, message: response?.message || "Maintenance task is running." });
      } catch { updateTask(action, { status: "running", taskId, message: action === "update" ? "Waiting for the backend to reconnect..." : "Waiting for task status..." }); }
    };
    void poll();
    pollers.current[action] = setInterval(poll, 3000);
  }, [finishTask, startUpdatePolling, stopPolling, updateTask]);

  useEffect(() => () => { Object.values(pollers.current).forEach((p) => { if (p) clearInterval(p); }); }, []);

  const handleAction = useCallback(async (action: MaintenanceAction) => {
    const copy = COPY[action];
    const confirmed = await confirm({ title: copy.title, message: copy.message, confirmText: copy.confirmText, variant: copy.variant });
    if (!confirmed) return;
    updateTask(action, { status: "queued", taskId: null, message: "Queueing maintenance task..." });
    try {
      const response = await systemApi.runMaintenance(action);
      const taskId = response?.task_id || response?.taskId;
      if (response?.result || response?.status === "success" || response?.status === "error") {
        if (action === "update") {
          const result = response?.result && typeof response.result === "object" ? response.result : response;
          const platformUpdateId = result?.task_id;
          if (platformUpdateId) { updateTask(action, { status: "queued", taskId: platformUpdateId, message: "Platform update initiated, tracking progress..." }); startUpdatePolling(platformUpdateId); return; }
        }
        finishTask(action, response); return;
      }
      updateTask(action, { status: "queued", taskId, message: response?.message || "Task queued successfully." });
      toast({ title: "Maintenance queued", description: response?.message || "Task queued successfully." });
      if (taskId) startPolling(action, taskId);
    } catch (err: any) {
      const data = err?.response?.data;
      const taskId = data?.task_id || data?.taskId;
      if (err?.response?.status === 409 && taskId) { updateTask(action, { status: "running", taskId, message: data?.message || "This maintenance task is already running." }); startPolling(action, taskId); toast({ title: "Already running", description: data?.message || "This maintenance task is already running." }); return; }
      const errorMsg = err?.response?.data?.message || err?.response?.data?.error?.message || err?.response?.data?.error || err?.message || "Failed to trigger maintenance.";
      updateTask(action, { status: "error", taskId: null, message: errorMsg });
      toast({ title: "Error", description: errorMsg, variant: "destructive" });
    }
  }, [confirm, finishTask, startPolling, startUpdatePolling, toast, updateTask]);

  const renderButton = (action: MaintenanceAction, label: string) => {
    const task = tasks[action];
    if (task.status === "queued" || task.status === "running") return <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> {task.status === "queued" ? "Queued" : "Running"}</>;
    if (task.status === "success") return <><Check className="mr-2 h-4 w-4" /> Done</>;
    return label;
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Cloud className="h-5 w-5 text-red-500" /> System Maintenance</CardTitle>
          <CardDescription>Perform dangerous maintenance actions on the host server. These actions run asynchronously in the background.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex flex-col gap-4 rounded-lg border p-4 bg-muted/20">
            {(["clear", "refresh", "update"] as MaintenanceAction[]).map((action, i) => (
              <div key={action} className={`flex flex-col justify-between gap-4 sm:flex-row sm:items-center ${i > 0 ? "border-t pt-4" : ""}`}>
                <div className="space-y-1">
                  <h4 className="text-sm font-medium">{action === "clear" ? "Clear Orphaned Containers" : action === "refresh" ? "Sync Proxy Routing" : "Update Platform"}</h4>
                  <p className="text-xs text-muted-foreground">{COPY[action].message}</p>
                  {tasks[action].message && <p className={cn("text-xs", tasks[action].status === "error" ? "text-destructive" : "text-muted-foreground")}>{tasks[action].message}</p>}
                </div>
                <Button variant={action === "clear" ? "destructive" : action === "update" ? "default" : "outline"} disabled={tasks[action].status === "queued" || tasks[action].status === "running"} onClick={() => handleAction(action)} className="w-full sm:w-auto">
                  {renderButton(action, COPY[action].confirmText)}
                </Button>
              </div>
            ))}
            {tasks.update.taskId && <UpdateTerminalStream updateId={tasks.update.taskId} />}
          </div>
          <div className="flex justify-end mt-4">
            <Link href="/dashboard"><Button variant="outline">Cancel</Button></Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
