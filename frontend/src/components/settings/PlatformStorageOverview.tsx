"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  HardDrive,
  Database,
  Layers,
  Trash2,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  FileText,
  Box,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { useToast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  platformStorageApi,
  StorageOverviewData,
  StorageActionType,
} from "@/lib/api";

const ACTION_CONFIG: Record<
  StorageActionType,
  {
    title: string;
    description: string;
    buttonText: string;
    icon: React.ComponentType<{ className?: string }>;
    variant?: "default" | "destructive" | "outline" | "secondary";
    confirmTitle: string;
    confirmMessage: string;
  }
> = {
  prune_build_cache: {
    title: "Prune BuildKit Cache",
    description: "Clears BuildKit compiler caches and untagged intermediate builder layers.",
    buttonText: "Prune Build Cache",
    icon: Sparkles,
    variant: "outline",
    confirmTitle: "Prune BuildKit Cache?",
    confirmMessage:
      "This will clear BuildKit layers and compiler caches. Subsequent builds might take slightly longer to compile.",
  },
  prune_images: {
    title: "Prune Dangling Images",
    description: "Removes untagged, orphaned Docker image layers not associated with any container.",
    buttonText: "Prune Dangling Images",
    icon: Trash2,
    variant: "outline",
    confirmTitle: "Prune Dangling Images?",
    confirmMessage:
      "This will delete all untagged Docker images. Active container images will not be affected.",
  },
  registry_gc: {
    title: "Registry Garbage Collection",
    description: "Purges unreferenced manifests and layer blobs from the local container registry.",
    buttonText: "Run Registry GC",
    icon: Database,
    variant: "outline",
    confirmTitle: "Run Registry Garbage Collection?",
    confirmMessage:
      "This performs dry-run / live mark-and-sweep garbage collection on the private registry storage.",
  },
  clear_containers: {
    title: "Clear Orphaned Containers",
    description: "Cleans dead, stopped, or exited containers and flushes host runtime cache directories.",
    buttonText: "Clear Dead Containers",
    icon: Box,
    variant: "outline",
    confirmTitle: "Clear Dead Containers & Caches?",
    confirmMessage:
      "This will remove stopped and dead containers and clear temporary directories. Running containers are untouched.",
  },
  clean_logs: {
    title: "Clean Historical Build Logs",
    description: "Archives build logs older than 14 days to recover database and filesystem space.",
    buttonText: "Clean Historical Logs",
    icon: FileText,
    variant: "outline",
    confirmTitle: "Clean Historical Deployment Logs?",
    confirmMessage:
      "This truncates verbose build logs on deployments older than 14 days. Deployment metadata and status remain intact.",
  },
  docker_recovery: {
    title: "Docker Engine Recovery",
    description: "Deep containerd ingest reset, builder cache flush, and Docker daemon restart.",
    buttonText: "Full Docker Recovery",
    icon: RotateCcw,
    variant: "destructive",
    confirmTitle: "Perform Full Docker Recovery?",
    confirmMessage:
      "Warning: This clears containerd ingest, flushes build caches, and restarts the Docker daemon. Running services will momentarily restart. Use when Docker daemon is unresponsive or suffering from layer corruption.",
  },
};

export function PlatformStorageOverview() {
  const { toast } = useToast();
  const confirm = useConfirm();

  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<StorageOverviewData | null>(null);
  const [runningAction, setRunningAction] = useState<StorageActionType | null>(null);
  const [actionMessage, setActionMessage] = useState<string>("");

  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  const fetchStorageOverview = useCallback(async (isRefresh = false) => {
    try {
      if (isRefresh) {
        setLoading(true);
      }
      const res = await platformStorageApi.getOverview();
      setData(res);
    } catch (err: any) {
      console.error("Failed to fetch storage overview:", err);
      toast({
        title: "Failed to fetch storage telemetry",
        description: err.response?.data?.error || err.message,
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    fetchStorageOverview();
  }, [fetchStorageOverview]);

  const pollActionTask = useCallback(
    (action: StorageActionType, taskId: string) => {
      stopPolling();

      const poll = async () => {
        try {
          const res = await platformStorageApi.getTaskStatus(taskId);
          const state = String(res?.state || "").toUpperCase();
          const statusVal = String(res?.status || "").toLowerCase();

          if (
            state === "SUCCESS" ||
            state === "FAILURE" ||
            statusVal === "success" ||
            statusVal === "error"
          ) {
            stopPolling();
            setRunningAction(null);
            setActionMessage("");

            const isOk = state === "SUCCESS" || statusVal === "success";
            const msg =
              res?.result?.message ||
              res?.message ||
              (isOk
                ? `Storage operation '${action}' completed successfully.`
                : `Storage operation '${action}' encountered an error.`);

            toast({
              title: isOk ? "Storage Action Succeeded" : "Storage Action Failed",
              description: msg,
              variant: isOk ? "success" : "destructive",
            });

            // Automatically refresh overview after action finishes
            void fetchStorageOverview();
            return;
          }

          setActionMessage(res?.message || "Executing storage optimization...");
        } catch (pollErr: any) {
          console.debug("Storage action poll check error:", pollErr);
        }
      };

      void poll();
      pollIntervalRef.current = setInterval(poll, 2500);
    },
    [fetchStorageOverview, stopPolling, toast]
  );

  const handleRunAction = async (action: StorageActionType) => {
    const cfg = ACTION_CONFIG[action];
    const confirmed = await confirm({
      title: cfg.confirmTitle,
      message: cfg.confirmMessage,
      confirmText: cfg.buttonText,
      variant: cfg.variant === "destructive" ? "destructive" : "default",
    });

    if (!confirmed) return;

    try {
      setRunningAction(action);
      setActionMessage("Queuing maintenance action...");
      const res = await platformStorageApi.runAction(action);

      toast({
        title: "Action Dispatched",
        description: res.message || `Queued ${cfg.title}`,
      });

      if (res.task_id) {
        pollActionTask(action, res.task_id);
      } else {
        setRunningAction(null);
        void fetchStorageOverview();
      }
    } catch (err: any) {
      setRunningAction(null);
      setActionMessage("");
      toast({
        title: "Action Failed",
        description: err.response?.data?.error || err.message,
        variant: "destructive",
      });
    }
  };

  const getStatusBadge = (status?: string, pct?: number) => {
    if (status === "critical" || (pct && pct >= 90)) {
      return (
        <Badge variant="destructive" className="flex items-center gap-1">
          <AlertTriangle className="h-3 w-3" />
          Critical ({pct || 90}%)
        </Badge>
      );
    }
    if (status === "warning" || (pct && pct >= 80)) {
      return (
        <Badge variant="warning" className="flex items-center gap-1">
          <AlertTriangle className="h-3 w-3" />
          Warning ({pct || 80}%)
        </Badge>
      );
    }
    return (
      <Badge variant="success" className="flex items-center gap-1">
        <CheckCircle2 className="h-3 w-3" />
        Healthy ({pct || 0}%)
      </Badge>
    );
  };

  const getProgressColor = (pct: number) => {
    if (pct >= 90) return "bg-red-500";
    if (pct >= 80) return "bg-amber-500";
    return "bg-emerald-500";
  };

  return (
    <div className="space-y-6">
      {/* Top Header & Actions */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h3 className="text-lg font-medium flex items-center gap-2">
            <HardDrive className="h-5 w-5 text-primary" />
            Storage Usage & Resource Overview
          </h3>
          <p className="text-sm text-muted-foreground">
            Host partition metrics, Docker storage layers (images, cache, volumes), and disk cleanup operations.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {data?.timestamp && (
            <span className="text-xs text-muted-foreground hidden md:inline">
              Updated {new Date(data.timestamp).toLocaleTimeString()}
            </span>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchStorageOverview(true)}
            disabled={loading || !!runningAction}
          >
            <RefreshCw
              className={`h-4 w-4 mr-2 ${loading ? "animate-spin" : ""}`}
            />
            Refresh Telemetry
          </Button>
        </div>
      </div>

      {/* High-level Metric Tiles */}
      <div className="grid gap-4 md:grid-cols-3">
        {/* Tile 1: Root Host Partition */}
        <Card className="relative overflow-hidden">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <HardDrive className="h-4 w-4 text-sky-500" />
                Root Partition ( / )
              </CardTitle>
              {data && getStatusBadge(data.disk.status, data.disk.used_percent)}
            </div>
            <CardDescription className="text-xs">
              Physical host filesystem capacity
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-baseline justify-between">
              <div className="text-2xl font-bold font-mono">
                {data ? `${data.disk.used_gb} GB` : "--"}
              </div>
              <div className="text-xs text-muted-foreground font-mono">
                of {data ? `${data.disk.total_gb} GB` : "--"} Total
              </div>
            </div>

            <div className="space-y-1">
              <div className="relative h-2 w-full overflow-hidden rounded-full bg-secondary">
                <div
                  className={`h-full transition-all duration-500 ${
                    data ? getProgressColor(data.disk.used_percent) : "bg-primary"
                  }`}
                  style={{ width: `${Math.min(data?.disk.used_percent || 0, 100)}%` }}
                />
              </div>
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>{data ? `${data.disk.free_gb} GB Free` : "--"}</span>
                <span>{data ? `${data.disk.used_percent}% Used` : "--"}</span>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Tile 2: Docker Engine Storage */}
        <Card className="relative overflow-hidden">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <Layers className="h-4 w-4 text-indigo-500" />
                Docker Footprint
              </CardTitle>
              {data?.docker.available ? (
                <Badge variant="info" className="text-xs font-normal">
                  Engine Online
                </Badge>
              ) : (
                <Badge variant="outline" className="text-xs font-normal text-muted-foreground">
                  Offline / N/A
                </Badge>
              )}
            </div>
            <CardDescription className="text-xs">
              Images, containers, volumes & BuildKit
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-baseline justify-between">
              <div className="text-2xl font-bold font-mono">
                {data ? `${data.docker.total_docker_gb} GB` : "--"}
              </div>
              <Badge variant="purple" className="text-xs font-mono">
                {data ? `${data.docker.total_reclaimable_gb} GB Reclaimable` : "--"}
              </Badge>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground pt-1 border-t">
              <div>
                Images: <span className="font-mono font-medium text-foreground">{data?.docker.images.count ?? 0}</span> ({data?.docker.images.size_gb ?? 0} GB)
              </div>
              <div>
                Cache: <span className="font-mono font-medium text-foreground">{data?.docker.build_cache.count ?? 0}</span> ({data?.docker.build_cache.size_gb ?? 0} GB)
              </div>
              <div>
                Volumes: <span className="font-mono font-medium text-foreground">{data?.docker.volumes.count ?? 0}</span> ({data?.docker.volumes.size_gb ?? 0} GB)
              </div>
              <div>
                Containers: <span className="font-mono font-medium text-foreground">{data?.docker.containers.count ?? 0}</span> ({data?.docker.containers.size_gb ?? 0} GB)
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Tile 3: Platform Artifacts & Logs */}
        <Card className="relative overflow-hidden">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <FileText className="h-4 w-4 text-emerald-500" />
                Platform Artifacts & Logs
              </CardTitle>
              <Badge variant="secondary" className="text-xs font-normal">
                {data?.artifacts.active_services ?? 0} Active Services
              </Badge>
            </div>
            <CardDescription className="text-xs">
              Build log archives and deployment records
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-baseline justify-between">
              <div className="text-2xl font-bold font-mono">
                {data ? `${data.artifacts.build_logs_mb} MB` : "--"}
              </div>
              <span className="text-xs text-muted-foreground font-mono">
                {data?.artifacts.deployments_count ?? 0} Total Builds
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground pt-1 border-t">
              <div>
                Stale Records: <span className="font-mono font-medium text-amber-500">{data?.artifacts.stale_builds_count ?? 0}</span>
              </div>
              <div>
                Active Deploys: <span className="font-mono font-medium text-foreground">{data?.artifacts.active_services ?? 0}</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Breakdown Details Grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {/* Card: BuildKit Cache */}
        <Card className="bg-card/50">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm font-medium flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <Sparkles className="h-4 w-4 text-sky-500" />
                BuildKit Cache
              </span>
              <span className="font-mono text-sm font-bold">
                {data?.docker.build_cache.size_gb ?? 0} GB
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0 space-y-3">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{data?.docker.build_cache.count ?? 0} cache records</span>
              <span className="text-purple-400 font-medium">
                {data?.docker.build_cache.reclaimable_gb ?? 0} GB reclaimable
              </span>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="w-full text-xs"
              onClick={() => handleRunAction("prune_build_cache")}
              disabled={!!runningAction}
            >
              {runningAction === "prune_build_cache" ? (
                <>
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Pruning...
                </>
              ) : (
                <>
                  <Trash2 className="mr-2 h-3.5 w-3.5" />
                  Prune Build Cache
                </>
              )}
            </Button>
          </CardContent>
        </Card>

        {/* Card: Docker Images */}
        <Card className="bg-card/50">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm font-medium flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <Layers className="h-4 w-4 text-indigo-500" />
                Docker Images
              </span>
              <span className="font-mono text-sm font-bold">
                {data?.docker.images.size_gb ?? 0} GB
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0 space-y-3">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{data?.docker.images.count ?? 0} image layers</span>
              <span className="text-purple-400 font-medium">
                {data?.docker.images.reclaimable_gb ?? 0} GB dangling
              </span>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="w-full text-xs"
              onClick={() => handleRunAction("prune_images")}
              disabled={!!runningAction}
            >
              {runningAction === "prune_images" ? (
                <>
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Pruning...
                </>
              ) : (
                <>
                  <Trash2 className="mr-2 h-3.5 w-3.5" />
                  Prune Dangling
                </>
              )}
            </Button>
          </CardContent>
        </Card>

        {/* Card: Named Volumes */}
        <Card className="bg-card/50">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm font-medium flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <Database className="h-4 w-4 text-amber-500" />
                Persistent Volumes
              </span>
              <span className="font-mono text-sm font-bold">
                {data?.docker.volumes.size_gb ?? 0} GB
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0 space-y-3">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{data?.docker.volumes.count ?? 0} volumes</span>
              <span className="text-purple-400 font-medium">
                {data?.docker.volumes.reclaimable_gb ?? 0} GB unattached
              </span>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="w-full text-xs"
              onClick={() => handleRunAction("clear_containers")}
              disabled={!!runningAction}
            >
              {runningAction === "clear_containers" ? (
                <>
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Clearing...
                </>
              ) : (
                <>
                  <Box className="mr-2 h-3.5 w-3.5" />
                  Clear Orphaned
                </>
              )}
            </Button>
          </CardContent>
        </Card>

        {/* Card: Registry & Logs */}
        <Card className="bg-card/50">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-sm font-medium flex items-center justify-between">
              <span className="flex items-center gap-1.5">
                <FileText className="h-4 w-4 text-emerald-500" />
                Registry & Logs
              </span>
              <span className="font-mono text-sm font-bold">
                {data?.artifacts.build_logs_mb ?? 0} MB
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0 space-y-3">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{data?.artifacts.deployments_count ?? 0} historical logs</span>
              <span className="text-amber-400 font-medium">
                {data?.artifacts.stale_builds_count ?? 0} stale
              </span>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="w-full text-xs"
              onClick={() => handleRunAction("clean_logs")}
              disabled={!!runningAction}
            >
              {runningAction === "clean_logs" ? (
                <>
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  Cleaning...
                </>
              ) : (
                <>
                  <FileText className="mr-2 h-3.5 w-3.5" />
                  Clean Old Logs
                </>
              )}
            </Button>
          </CardContent>
        </Card>
      </div>

      {/* Storage Action Operations Banner */}
      <Card className="border border-border/80">
        <CardHeader>
          <CardTitle className="text-base flex items-center justify-between">
            <span className="flex items-center gap-2">
              <RotateCcw className="h-4 w-4 text-primary" />
              Platform Storage Operations & Maintenance
            </span>
            {runningAction && (
              <Badge variant="warning" className="animate-pulse">
                Operation in Progress: {runningAction}
              </Badge>
            )}
          </CardTitle>
          <CardDescription>
            Execute targeted storage reclamation routines, garbage collection, or full Docker engine recovery.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {runningAction && actionMessage && (
            <div className="flex items-center gap-2 rounded-lg bg-secondary/50 p-3 text-xs text-muted-foreground border">
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
              <span>{actionMessage}</span>
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {/* Prune BuildKit */}
            <div className="flex flex-col justify-between rounded-lg border p-3 space-y-2 bg-background/50">
              <div>
                <div className="font-medium text-sm flex items-center gap-1.5">
                  <Sparkles className="h-4 w-4 text-sky-500" />
                  BuildKit Cache Prune
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Flushes compiler cache layers and language package temp directories.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRunAction("prune_build_cache")}
                disabled={!!runningAction}
                className="w-full mt-2"
              >
                {runningAction === "prune_build_cache" && (
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                )}
                Prune Cache
              </Button>
            </div>

            {/* Prune Dangling Images */}
            <div className="flex flex-col justify-between rounded-lg border p-3 space-y-2 bg-background/50">
              <div>
                <div className="font-medium text-sm flex items-center gap-1.5">
                  <Trash2 className="h-4 w-4 text-indigo-500" />
                  Dangling Image Prune
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Removes unreferenced, untagged images generated during repeated builds.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRunAction("prune_images")}
                disabled={!!runningAction}
                className="w-full mt-2"
              >
                {runningAction === "prune_images" && (
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                )}
                Prune Images
              </Button>
            </div>

            {/* Registry Garbage Collection */}
            <div className="flex flex-col justify-between rounded-lg border p-3 space-y-2 bg-background/50">
              <div>
                <div className="font-medium text-sm flex items-center gap-1.5">
                  <Database className="h-4 w-4 text-purple-500" />
                  Registry Garbage Collection
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Frees blobs and manifest layers inside the local private registry.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRunAction("registry_gc")}
                disabled={!!runningAction}
                className="w-full mt-2"
              >
                {runningAction === "registry_gc" && (
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                )}
                Run Registry GC
              </Button>
            </div>

            {/* Clear Containers & Runtime Caches */}
            <div className="flex flex-col justify-between rounded-lg border p-3 space-y-2 bg-background/50">
              <div>
                <div className="font-medium text-sm flex items-center gap-1.5">
                  <Box className="h-4 w-4 text-emerald-500" />
                  Clear Dead Containers
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Removes dead and stopped containers, flushing ephemeral scratch dirs.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRunAction("clear_containers")}
                disabled={!!runningAction}
                className="w-full mt-2"
              >
                {runningAction === "clear_containers" && (
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                )}
                Clear Containers
              </Button>
            </div>

            {/* Clean Historical Logs */}
            <div className="flex flex-col justify-between rounded-lg border p-3 space-y-2 bg-background/50">
              <div>
                <div className="font-medium text-sm flex items-center gap-1.5">
                  <FileText className="h-4 w-4 text-amber-500" />
                  Clean Historical Logs
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Purges build logs older than 14 days, saving database capacity.
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleRunAction("clean_logs")}
                disabled={!!runningAction}
                className="w-full mt-2"
              >
                {runningAction === "clean_logs" && (
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                )}
                Clean Old Logs
              </Button>
            </div>

            {/* Docker Recovery */}
            <div className="flex flex-col justify-between rounded-lg border border-destructive/30 p-3 space-y-2 bg-destructive/5">
              <div>
                <div className="font-medium text-sm flex items-center gap-1.5 text-destructive">
                  <AlertTriangle className="h-4 w-4" />
                  Full Docker Recovery
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  Containerd reset, cache purge & daemon restart. Momentarily restarts services.
                </p>
              </div>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => handleRunAction("docker_recovery")}
                disabled={!!runningAction}
                className="w-full mt-2"
              >
                {runningAction === "docker_recovery" && (
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                )}
                Recover Docker
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
