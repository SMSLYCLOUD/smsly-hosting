"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import {
  Brain, Sparkles, AlertTriangle, CheckCircle2, Loader2, GitBranch, Clock, Wrench,
  XCircle, TrendingUp, Cpu, Gauge, BarChart3, Shield, Activity, DollarSign,
  Server, Wifi, WifiOff, Bug, FileWarning, RefreshCw, Zap, Siren
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SecurityStatusTab } from "@/components/insights/SecurityStatusTab";
import { IncidentReportTab } from "@/components/settings/IncidentReportTab";

interface JulesEntry {
  deployment_id: string;
  branch: string;
  status: string;
  created_at: string | null;
  jules_events: string[];
  fix_applied: boolean;
  fix_failed: boolean;
}

interface ScalingAnalysis {
  service: string;
  service_name: string;
  timestamp: string;
  engine: string;
  ai_configured: boolean;
  metrics: { cpu_percent?: number; memory_mb?: number; memory_trend?: number };
  error_analysis: { oom_detected?: boolean; crash_loop?: boolean; error_count_1h?: number };
  recommendation: { action: string; reason: string; scale_up_by: number; urgency: string };
  guardrails: { running_replicas: number; max_replicas: number; at_capacity: boolean };
}

export function AIInsightsTab({ serviceId }: { serviceId: string }) {
  const [julesData, setJulesData] = useState<{ entries: JulesEntry[] } | null>(null);
  const [scaleAnalysis, setScaleAnalysis] = useState<ScalingAnalysis | null>(null);
  const [anomalies, setAnomalies] = useState<any[]>([]);
  const [platformReport, setPlatformReport] = useState<any>(null);
  const [serviceInfo, setServiceInfo] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [julesRes, scaleRes, anomalyRes, reportRes, serviceRes] = await Promise.allSettled([
        api.get(`/ai/jules-history/${serviceId}/`),
        api.post(`/scaling/${serviceId}/analyze/`),
        api.get(`/ai/anomalies/`, { params: { service_id: serviceId } }),
        api.get(`/ai/report/`, { params: { service_id: serviceId } }),
        api.get(`/services/${serviceId}/`),
      ]);
      if (julesRes.status === "fulfilled") setJulesData(julesRes.value.data);
      if (scaleRes.status === "fulfilled") setScaleAnalysis(scaleRes.value.data);
      if (anomalyRes.status === "fulfilled") setAnomalies(Array.isArray(anomalyRes.value.data) ? anomalyRes.value.data : (anomalyRes.value.data?.results || []));
      if (reportRes.status === "fulfilled") setPlatformReport(reportRes.value.data);
      if (serviceRes.status === "fulfilled") setServiceInfo(serviceRes.value.data);
    } catch (err: any) {
      setError(err?.message || "Failed to load insights");
    } finally {
      setLoading(false);
    }
  }, [serviceId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
        <span className="ml-3 text-muted-foreground">Loading AI insights...</span>
      </div>
    );
  }

  const entries = julesData?.entries || [];
  const totalFixes = entries.length;
  const successfulFixes = entries.filter((e) => e.fix_applied).length;
  const failedFixes = entries.filter((e) => e.fix_failed && !e.fix_applied).length;
  const rec = scaleAnalysis?.recommendation;

  return (
    <div className="space-y-6 py-6">
      {/* Header */}
      <div className="flex items-center gap-3 pb-4 border-b border-border">
        <div className="p-2 rounded-lg bg-violet-500/10">
          <Brain className="w-6 h-6 text-violet-500" />
        </div>
        <div>
          <h2 className="text-lg font-bold">Insights & Auto-Fix</h2>
          <p className="text-sm text-muted-foreground">AI diagnostics, scaling analysis, incident history, and system security status.</p>
        </div>
      </div>

      <Tabs defaultValue="ai" className="w-full">
        <TabsList>
          <TabsTrigger value="ai"><Brain className="w-4 h-4 mr-1" /> AI Insights</TabsTrigger>
          <TabsTrigger value="security"><Shield className="w-4 h-4 mr-1" /> Security</TabsTrigger>
          <TabsTrigger value="incidents"><Siren className="w-4 h-4 mr-1" /> Incidents</TabsTrigger>
          <TabsTrigger value="platform"><Server className="w-4 h-4 mr-1" /> Platform</TabsTrigger>
        </TabsList>

        <TabsContent value="ai" className="space-y-6 mt-6">

      {/* ── Live Scaling Analysis ────────────────────────────────── */}
      {scaleAnalysis && (
        <div className="p-4 rounded-xl border border-border bg-gradient-to-r from-violet-500/5 to-blue-500/5">
          <div className="flex items-center gap-2 mb-3">
            <BarChart3 className="w-4 h-4 text-violet-500" />
            <h3 className="text-sm font-bold">Live Scaling Analysis</h3>
            <Badge variant="outline" className="text-[10px]">{scaleAnalysis.engine}</Badge>
          </div>

          {/* Metrics */}
          <div className="grid grid-cols-3 gap-2 mb-3">
            <div className="p-2 rounded-lg bg-black/20 text-center">
              <Cpu className="w-3 h-3 text-blue-400 mx-auto mb-1" />
              <div className="text-lg font-bold">{scaleAnalysis.metrics?.cpu_percent?.toFixed(0) ?? "—"}%</div>
              <div className="text-[10px] text-muted-foreground">CPU</div>
            </div>
            <div className="p-2 rounded-lg bg-black/20 text-center">
              <Gauge className="w-3 h-3 text-emerald-400 mx-auto mb-1" />
              <div className="text-lg font-bold">{scaleAnalysis.metrics?.memory_mb?.toFixed(0) ?? "—"}MB</div>
              <div className="text-[10px] text-muted-foreground">Memory</div>
            </div>
            <div className="p-2 rounded-lg bg-black/20 text-center">
              <TrendingUp className="w-3 h-3 text-amber-400 mx-auto mb-1" />
              <div className="text-lg font-bold">{(scaleAnalysis.metrics?.memory_trend ?? 0) > 0 ? "+" : ""}{scaleAnalysis.metrics?.memory_trend?.toFixed(1) ?? "0"}×</div>
              <div className="text-[10px] text-muted-foreground">Trend MB/min</div>
            </div>
          </div>

          {/* Recommendation */}
          {rec && (
            <div className={`p-3 rounded-lg ${
              rec.urgency === "critical" ? "bg-red-500/10 border border-red-500/20" :
              rec.urgency === "high" ? "bg-amber-500/10 border border-amber-500/20" :
              rec.action === "none" ? "bg-emerald-500/10 border border-emerald-500/20" :
              "bg-blue-500/10 border border-blue-500/20"
            }`}>
              <div className="flex items-center gap-2 mb-1">
                <Badge variant={rec.action === "scale_up" ? "destructive" : rec.action === "scale_down" ? "secondary" : "outline"} className="text-[10px]">
                  {rec.action === "scale_up" ? "SCALE UP" : rec.action === "scale_down" ? "SCALE DOWN" : "STABLE"}
                </Badge>
                <span className="text-xs font-semibold">{rec.urgency.toUpperCase()}</span>
                {rec.scale_up_by > 0 && <span className="text-xs text-muted-foreground">+{rec.scale_up_by} replicas</span>}
              </div>
              <p className="text-xs text-muted-foreground">{rec.reason}</p>
            </div>
          )}

          {/* Replicas */}
          <div className="flex items-center gap-2 mt-2 text-xs text-muted-foreground">
            <span>Replicas: {scaleAnalysis.guardrails?.running_replicas ?? 0} / {scaleAnalysis.guardrails?.max_replicas ?? "?"}</span>
            {scaleAnalysis.guardrails?.at_capacity && <Badge variant="outline" className="text-[10px] text-red-400">AT CAPACITY</Badge>}
          </div>

          {/* Errors */}
          {(scaleAnalysis.error_analysis?.oom_detected || scaleAnalysis.error_analysis?.crash_loop || (scaleAnalysis.error_analysis?.error_count_1h ?? 0) > 0) && (
            <div className="mt-2 flex flex-wrap gap-1">
              {scaleAnalysis.error_analysis?.oom_detected && <Badge variant="destructive" className="text-[10px]">OOM</Badge>}
              {scaleAnalysis.error_analysis?.crash_loop && <Badge variant="destructive" className="text-[10px]">CRASH LOOP</Badge>}
              {(scaleAnalysis.error_analysis?.error_count_1h ?? 0) > 0 && <Badge variant="outline" className="text-[10px] text-amber-400">{scaleAnalysis.error_analysis?.error_count_1h} errors/h</Badge>}
            </div>
          )}
        </div>
      )}

      {/* ── Stats Row ───────────────────────────────────────────── */}
      <div className="grid grid-cols-3 gap-3">
        <div className="p-4 rounded-xl border border-border bg-violet-500/5">
          <Brain className="w-5 h-5 text-violet-500 mb-2" />
          <div className="text-2xl font-bold">{totalFixes}</div>
          <div className="text-xs text-muted-foreground">Jules Fix Attempts</div>
        </div>
        <div className="p-4 rounded-xl border border-border bg-emerald-500/5">
          <CheckCircle2 className="w-5 h-5 text-emerald-500 mb-2" />
          <div className="text-2xl font-bold">{successfulFixes}</div>
          <div className="text-xs text-muted-foreground">Successful Fixes</div>
        </div>
        <div className="p-4 rounded-xl border border-border bg-red-500/5">
          <XCircle className="w-5 h-5 text-red-500 mb-2" />
          <div className="text-2xl font-bold">{failedFixes}</div>
          <div className="text-xs text-muted-foreground">Failed Fixes</div>
        </div>
      </div>

      {/* ── Jules Fix History ───────────────────────────────────── */}
      {entries.length === 0 ? (
        <div className="text-center py-8">
          <Sparkles className="w-10 h-10 text-muted-foreground mx-auto mb-2" />
          <p className="text-sm text-muted-foreground">No Jules auto-fix history yet.</p>
        </div>
      ) : (
        <div className="space-y-3">
          <h3 className="text-sm font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
            <Wrench className="w-4 h-4" /> Jules Fix History
          </h3>
          {entries.map((entry, i) => (
            <div key={i} className={`p-4 rounded-xl border ${
              entry.fix_applied ? "border-emerald-500/20 bg-emerald-500/5" :
              entry.fix_failed ? "border-red-500/20 bg-red-500/5" : "border-border bg-card"
            }`}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  {entry.fix_applied ? <CheckCircle2 className="w-4 h-4 text-emerald-500" /> :
                   entry.fix_failed ? <XCircle className="w-4 h-4 text-red-500" /> :
                   <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />}
                  <span className="text-sm font-semibold">Deployment {entry.deployment_id.slice(0, 8)}</span>
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  {entry.branch && <span className="flex items-center gap-1"><GitBranch className="w-3 h-3" />{entry.branch}</span>}
                  {entry.created_at && <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{new Date(entry.created_at).toLocaleString()}</span>}
                </div>
              </div>
              {entry.jules_events.length > 0 && (
                <div className="mt-2 p-3 rounded-lg bg-black/20 font-mono text-xs space-y-0.5 max-h-40 overflow-y-auto">
                  {entry.jules_events.map((event, j) => (
                    <div key={j} className="text-muted-foreground leading-relaxed whitespace-pre-wrap break-all">{event}</div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── Anomalies Timeline ────────────────────────────────── */}
      {anomalies.length > 0 && (
        <div className="mt-8">
          <h3 className="text-sm font-bold mb-4 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-500" />
            Detected Anomalies
          </h3>
          <div className="space-y-3">
            {anomalies.map((anomaly) => (
              <div key={anomaly.id} className="p-3 rounded-lg border border-border bg-card">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-semibold">{anomaly.metric_name}</span>
                  <span className="text-xs text-muted-foreground">{new Date(anomaly.timestamp).toLocaleString()}</span>
                </div>
                <p className="text-xs text-muted-foreground">{anomaly.description || `Severity: ${anomaly.severity}`}</p>
              </div>
            ))}
          </div>
        </div>
      )}
        </TabsContent>

        <TabsContent value="security" className="mt-6">
          <SecurityStatusTab serviceId={serviceId} />
        </TabsContent>

        <TabsContent value="incidents" className="mt-6">
          <IncidentReportTab serviceId={serviceId} />
        </TabsContent>

        <TabsContent value="platform" className="space-y-6 mt-6">
          {/* Service Health Overview */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div className="p-3 rounded-lg border border-border bg-card">
              <div className="flex items-center gap-2 mb-1">
                <Activity className="w-3.5 h-3.5 text-emerald-500" />
                <span className="text-[10px] text-muted-foreground uppercase">Status</span>
              </div>
              <div className="text-xl font-bold">{serviceInfo?.status || "—"}</div>
            </div>
            <div className="p-3 rounded-lg border border-border bg-card">
              <div className="flex items-center gap-2 mb-1">
                <RefreshCw className="w-3.5 h-3.5 text-blue-500" />
                <span className="text-[10px] text-muted-foreground uppercase">Deploys</span>
              </div>
              <div className="text-xl font-bold">{serviceInfo?.deployment_count ?? "—"}</div>
            </div>
            <div className="p-3 rounded-lg border border-border bg-card">
              <div className="flex items-center gap-2 mb-1">
                <Bug className="w-3.5 h-3.5 text-red-500" />
                <span className="text-[10px] text-muted-foreground uppercase">Failures</span>
              </div>
              <div className="text-xl font-bold text-red-500">{serviceInfo?.failed_deployments ?? "—"}</div>
            </div>
            <div className="p-3 rounded-lg border border-border bg-card">
              <div className="flex items-center gap-2 mb-1">
                <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
                <span className="text-[10px] text-muted-foreground uppercase">Anomalies</span>
              </div>
              <div className="text-xl font-bold text-amber-500">{anomalies.length}</div>
            </div>
          </div>

          {/* Latest Deployment */}
          {serviceInfo?.latest_deployment && (
            <div className="p-4 rounded-xl border border-border bg-card">
              <div className="flex items-center gap-2 mb-3">
                <Server className="w-4 h-4 text-blue-500" />
                <h3 className="text-sm font-bold">Latest Deployment</h3>
                <Badge variant={serviceInfo.latest_deployment.status === "SUCCESS" || serviceInfo.latest_deployment.status === "RUNNING" ? "outline" : "destructive"} className="text-[10px] ml-auto">
                  {serviceInfo.latest_deployment.status}
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground space-y-1">
                {serviceInfo.latest_deployment.commit_message && <p>Commit: {serviceInfo.latest_deployment.commit_message.slice(0, 200)}</p>}
                {serviceInfo.latest_deployment.branch && <p>Branch: {serviceInfo.latest_deployment.branch}</p>}
                {serviceInfo.latest_deployment.created_at && <p>Deployed: {new Date(serviceInfo.latest_deployment.created_at).toLocaleString()}</p>}
              </div>
            </div>
          )}

          {/* Service Report */}
          {platformReport && platformReport.available && (
            <div className="p-4 rounded-xl border border-border bg-gradient-to-r from-violet-500/5 to-blue-500/5">
              <div className="flex items-center gap-2 mb-3">
                <BarChart3 className="w-4 h-4 text-violet-500" />
                <h3 className="text-sm font-bold">Intelligence Report</h3>
                {platformReport.generated_at && (
                  <span className="text-[10px] text-muted-foreground ml-auto">
                    {new Date(platformReport.generated_at).toLocaleString()}
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="p-2 rounded-lg bg-black/20 text-center">
                  <div className="text-lg font-bold">{platformReport.total_deployments ?? 0}</div>
                  <div className="text-[10px] text-muted-foreground">Total Deploys</div>
                </div>
                <div className="p-2 rounded-lg bg-black/20 text-center">
                  <div className="text-lg font-bold text-red-500">{platformReport.failed_deployments ?? 0}</div>
                  <div className="text-[10px] text-muted-foreground">Failed</div>
                </div>
                <div className="p-2 rounded-lg bg-black/20 text-center">
                  <div className="text-lg font-bold text-amber-500">{platformReport.anomalies_detected ?? 0}</div>
                  <div className="text-[10px] text-muted-foreground">Anomalies</div>
                </div>
                <div className="p-2 rounded-lg bg-black/20 text-center">
                  <div className="text-lg font-bold text-emerald-500">{platformReport.success_rate ?? "N/A"}</div>
                  <div className="text-[10px] text-muted-foreground">Success Rate</div>
                </div>
              </div>
            </div>
          )}

          {/* Scaling Recommendations for This Service */}
          {scaleAnalysis?.recommendation && scaleAnalysis.recommendation.action !== "none" && (
            <div className="p-4 rounded-xl border border-border bg-gradient-to-r from-amber-500/5 to-orange-500/5">
              <div className="flex items-center gap-2 mb-2">
                <Zap className="w-4 h-4 text-amber-500" />
                <h3 className="text-sm font-bold">Scaling Recommendation</h3>
              </div>
              <div className="flex items-center gap-2 mb-2">
                <Badge variant={scaleAnalysis.recommendation.action === "scale_up" ? "destructive" : "secondary"} className="text-[10px]">
                  {scaleAnalysis.recommendation.action.toUpperCase()}
                </Badge>
                <Badge variant="outline" className="text-[10px]">{scaleAnalysis.recommendation.urgency}</Badge>
                {scaleAnalysis.recommendation.scale_up_by > 0 && (
                  <span className="text-xs text-muted-foreground">+{scaleAnalysis.recommendation.scale_up_by} replicas</span>
                )}
              </div>
              <p className="text-xs text-muted-foreground">{scaleAnalysis.recommendation.reason}</p>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
