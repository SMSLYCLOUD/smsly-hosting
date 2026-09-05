"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import {
  Brain, Sparkles, AlertTriangle, CheckCircle2, Loader2, GitBranch, Clock, Wrench,
  XCircle, TrendingUp, Cpu, Gauge, BarChart3, Shield, Activity, Server, Zap, Siren, Terminal, RefreshCw
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SecurityStatusTab } from "@/components/insights/SecurityStatusTab";
import { IncidentReportTab } from "@/components/settings/IncidentReportTab";
import { LogsTab } from "@/components/logs/LogsTab";
import { LogsView } from "@/components/logs/LogsView";

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
  const [scaleError, setScaleError] = useState<string | null>(null);
  const [anomalies, setAnomalies] = useState<any[]>([]);
  const [platformReport, setPlatformReport] = useState<any>(null);
  const [serviceInfo, setServiceInfo] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [logsSubTab, setLogsSubTab] = useState<'live' | 'loki'>('live');

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
      if (scaleRes.status === "fulfilled") {
        setScaleAnalysis(scaleRes.value.data);
        setScaleError(null);
      } else {
        setScaleAnalysis(null);
        const reason = (scaleRes as PromiseRejectedResult).reason as any;
        setScaleError(
          reason?.response?.data?.error ||
          reason?.response?.data?.detail ||
          reason?.message ||
          "Scaling analysis is unavailable for this service."
        );
      }
      if (anomalyRes.status === "fulfilled") {
        const data = anomalyRes.value.data;
        setAnomalies(Array.isArray(data) ? data : (data?.anomalies || data?.results || []));
      }
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
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4">
      {/* Header */}
      <Card className="p-6 border-border shadow-md">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-xl bg-violet-500/10">
            <Brain className="w-8 h-8 text-violet-500" />
          </div>
          <div>
            <h3 className="font-bold text-lg">Insights & Auto-Fix</h3>
            <p className="text-sm text-muted-foreground">AI diagnostics, scaling analysis, incident history, and system security status.</p>
          </div>
        </div>
      </Card>

      <Tabs defaultValue="ai" className="w-full">
        <TabsList>
          <TabsTrigger value="ai"><Brain className="w-4 h-4 mr-1" /> AI Insights</TabsTrigger>
          <TabsTrigger value="logs"><Terminal className="w-4 h-4 mr-1" /> Logs & Diagnostics</TabsTrigger>
          <TabsTrigger value="security"><Shield className="w-4 h-4 mr-1" /> Security</TabsTrigger>
          <TabsTrigger value="incidents"><Siren className="w-4 h-4 mr-1" /> Incidents</TabsTrigger>
          <TabsTrigger value="platform"><Server className="w-4 h-4 mr-1" /> Platform</TabsTrigger>
        </TabsList>

        <TabsContent value="ai" className="space-y-6 mt-6">

          {/* Live Scaling Analysis */}
          {!scaleAnalysis && scaleError && (
            <Card className="p-6 border-border shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <BarChart3 className="w-4 h-4 text-muted-foreground" />
                <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider">Live Scaling Analysis</h4>
              </div>
              <p className="text-xs text-muted-foreground">{scaleError}</p>
              <Button variant="outline" size="sm" className="mt-3" onClick={() => fetchData()}>
                <RefreshCw className="w-3 h-3 mr-1" /> Retry
              </Button>
            </Card>
          )}
          {scaleAnalysis && (
            <Card className="p-6 border-border shadow-sm">
              <div className="flex items-center gap-2 mb-4">
                <BarChart3 className="w-4 h-4 text-violet-500" />
                <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider">Live Scaling Analysis</h4>
                <Badge variant="outline" className="text-[10px]">{scaleAnalysis.engine}</Badge>
              </div>

              {/* Metrics */}
              <div className="grid grid-cols-3 gap-3 mb-4">
                <div className="p-3 rounded-lg bg-muted/30 text-center">
                  <Cpu className="w-3 h-3 text-blue-400 mx-auto mb-1" />
                  <div className="text-lg font-bold">{scaleAnalysis.metrics?.cpu_percent?.toFixed(0) ?? "—"}%</div>
                  <div className="text-[10px] text-muted-foreground">CPU</div>
                </div>
                <div className="p-3 rounded-lg bg-muted/30 text-center">
                  <Gauge className="w-3 h-3 text-emerald-400 mx-auto mb-1" />
                  <div className="text-lg font-bold">{scaleAnalysis.metrics?.memory_mb?.toFixed(0) ?? "—"}MB</div>
                  <div className="text-[10px] text-muted-foreground">Memory</div>
                </div>
                <div className="p-3 rounded-lg bg-muted/30 text-center">
                  <TrendingUp className="w-3 h-3 text-amber-400 mx-auto mb-1" />
                  <div className="text-lg font-bold">{(scaleAnalysis.metrics?.memory_trend ?? 0) > 0 ? "+" : ""}{scaleAnalysis.metrics?.memory_trend?.toFixed(1) ?? "0"}×</div>
                  <div className="text-[10px] text-muted-foreground">Trend MB/min</div>
                </div>
              </div>

              {/* Recommendation */}
              {rec && (
                <div className={`p-4 rounded-lg border ${
                  rec.urgency === "critical" ? "border-red-500/20 bg-red-500/5" :
                  rec.urgency === "high" ? "border-amber-500/20 bg-amber-500/5" :
                  rec.action === "none" ? "border-emerald-500/20 bg-emerald-500/5" :
                  "border-blue-500/20 bg-blue-500/5"
                }`}>
                  <div className="flex items-center gap-2 mb-2">
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
              <div className="flex items-center gap-2 mt-3 text-xs text-muted-foreground">
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
            </Card>
          )}

          {/* Stats Row */}
          <div className="grid grid-cols-3 gap-3">
            <Card className="p-4 border-border shadow-sm">
              <Brain className="w-5 h-5 text-violet-500 mb-2" />
              <div className="text-2xl font-bold">{totalFixes}</div>
              <div className="text-xs text-muted-foreground">Jules Fix Attempts</div>
            </Card>
            <Card className="p-4 border-border shadow-sm">
              <CheckCircle2 className="w-5 h-5 text-emerald-500 mb-2" />
              <div className="text-2xl font-bold">{successfulFixes}</div>
              <div className="text-xs text-muted-foreground">Successful Fixes</div>
            </Card>
            <Card className="p-4 border-border shadow-sm">
              <XCircle className="w-5 h-5 text-red-500 mb-2" />
              <div className="text-2xl font-bold">{failedFixes}</div>
              <div className="text-xs text-muted-foreground">Failed Fixes</div>
            </Card>
          </div>

          {/* Jules Fix History */}
          {entries.length === 0 ? (
            <div className="text-center py-8">
              <Sparkles className="w-10 h-10 text-muted-foreground mx-auto mb-2" />
              <p className="text-sm text-muted-foreground">No Jules auto-fix history yet.</p>
            </div>
          ) : (
            <div className="space-y-3">
              <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider flex items-center gap-2">
                <Wrench className="w-4 h-4" /> Jules Fix History
              </h4>
              {entries.map((entry, i) => (
                <Card key={i} className={`p-4 border-border shadow-sm ${
                  entry.fix_applied ? "border-emerald-500/20" :
                  entry.fix_failed ? "border-red-500/20" : ""
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
                    <div className="mt-2 p-3 rounded-lg bg-muted/30 font-mono text-xs space-y-0.5 max-h-40 overflow-y-auto">
                      {entry.jules_events.map((event, j) => (
                        <div key={j} className="text-muted-foreground leading-relaxed whitespace-pre-wrap break-all">{event}</div>
                      ))}
                    </div>
                  )}
                </Card>
              ))}
            </div>
          )}

          {/* Anomalies Timeline */}
          {anomalies.length > 0 && (
            <div className="space-y-3">
              <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-500" />
                Detected Anomalies
              </h4>
              <div className="space-y-3">
                {anomalies.map((anomaly) => (
                  <Card key={anomaly.id} className="p-4 border-border shadow-sm">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-semibold">{anomaly.issue_type}</span>
                      <span className="text-xs text-muted-foreground">{new Date(anomaly.detected_at).toLocaleString()}</span>
                    </div>
                    <p className="text-xs text-muted-foreground">{anomaly.severity || 'Unknown severity'}</p>
                    {anomaly.auto_fixed && <Badge variant="outline" className="text-[10px] mt-1">Auto-Fixed</Badge>}
                  </Card>
                ))}
              </div>
            </div>
          )}
        </TabsContent>

        <TabsContent value="logs" className="mt-6 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider flex items-center gap-2">
                <Terminal className="w-4 h-4 text-violet-500" />
                Live Service & Container Diagnostics
              </h4>
              <p className="text-xs text-muted-foreground mt-1">
                Inspect real-time container runtime logs, build stream, and historical observability logs.
              </p>
            </div>
            <div className="flex bg-muted rounded-lg p-1 gap-1 text-xs font-medium">
              <button
                type="button"
                onClick={() => setLogsSubTab('live')}
                className={`px-3 py-1.5 rounded-md transition-all ${
                  logsSubTab === 'live' ? 'bg-background shadow text-foreground' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                Live Container Stream
              </button>
              <button
                type="button"
                onClick={() => setLogsSubTab('loki')}
                className={`px-3 py-1.5 rounded-md transition-all ${
                  logsSubTab === 'loki' ? 'bg-background shadow text-foreground' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                Historical Logs (Loki)
              </button>
            </div>
          </div>

          <Card className="border-border shadow-sm overflow-hidden">
            {logsSubTab === 'live' ? (
              <LogsTab deployment={serviceInfo?.latest_deployment || null} />
            ) : (
              <LogsView searchParams={{ service: serviceId }} embed={true} />
            )}
          </Card>
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
            <Card className="p-4 border-border shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <Activity className="w-3.5 h-3.5 text-emerald-500" />
                <span className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">Status</span>
              </div>
              <div className="text-xl font-bold">{serviceInfo?.status || "—"}</div>
            </Card>
            <Card className="p-4 border-border shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <Zap className="w-3.5 h-3.5 text-blue-500" />
                <span className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">Deploys</span>
              </div>
              <div className="text-xl font-bold">{serviceInfo?.deployment_count ?? "—"}</div>
            </Card>
            <Card className="p-4 border-border shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <XCircle className="w-3.5 h-3.5 text-red-500" />
                <span className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">Failures</span>
              </div>
              <div className="text-xl font-bold text-red-500">{serviceInfo?.failed_deployments ?? "—"}</div>
            </Card>
            <Card className="p-4 border-border shadow-sm">
              <div className="flex items-center gap-2 mb-2">
                <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
                <span className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">Anomalies</span>
              </div>
              <div className="text-xl font-bold text-amber-500">{anomalies.length}</div>
            </Card>
          </div>

          {/* Latest Deployment */}
          {serviceInfo?.latest_deployment && (
            <Card className="p-6 border-border shadow-sm">
              <div className="flex items-center gap-2 mb-4">
                <Server className="w-4 h-4 text-blue-500" />
                <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider">Latest Deployment</h4>
                <Badge variant={serviceInfo.latest_deployment.status === "SUCCESS" || serviceInfo.latest_deployment.status === "RUNNING" ? "outline" : "destructive"} className="text-[10px] ml-auto">
                  {serviceInfo.latest_deployment.status}
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground space-y-1">
                {serviceInfo.latest_deployment.commit_message && <p>Commit: {serviceInfo.latest_deployment.commit_message.slice(0, 200)}</p>}
                {serviceInfo.latest_deployment.branch && <p>Branch: {serviceInfo.latest_deployment.branch}</p>}
                {serviceInfo.latest_deployment.created_at && <p>Deployed: {new Date(serviceInfo.latest_deployment.created_at).toLocaleString()}</p>}
              </div>
            </Card>
          )}

          {/* Service Report */}
          {platformReport && platformReport.available && (
            <Card className="p-6 border-border shadow-sm">
              <div className="flex items-center gap-2 mb-4">
                <BarChart3 className="w-4 h-4 text-violet-500" />
                <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider">Intelligence Report</h4>
                {platformReport.generated_at && (
                  <span className="text-[10px] text-muted-foreground ml-auto">
                    {new Date(platformReport.generated_at).toLocaleString()}
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="p-3 rounded-lg bg-muted/30 text-center">
                  <div className="text-lg font-bold">{platformReport.total_deployments ?? 0}</div>
                  <div className="text-[10px] text-muted-foreground">Total Deploys</div>
                </div>
                <div className="p-3 rounded-lg bg-muted/30 text-center">
                  <div className="text-lg font-bold text-red-500">{platformReport.failed_deployments ?? 0}</div>
                  <div className="text-[10px] text-muted-foreground">Failed</div>
                </div>
                <div className="p-3 rounded-lg bg-muted/30 text-center">
                  <div className="text-lg font-bold text-amber-500">{platformReport.anomalies_detected ?? 0}</div>
                  <div className="text-[10px] text-muted-foreground">Anomalies</div>
                </div>
                <div className="p-3 rounded-lg bg-muted/30 text-center">
                  <div className="text-lg font-bold text-emerald-500">{platformReport.success_rate ?? "N/A"}</div>
                  <div className="text-[10px] text-muted-foreground">Success Rate</div>
                </div>
              </div>
            </Card>
          )}

          {/* Scaling Recommendations for This Service */}
          {scaleAnalysis?.recommendation && scaleAnalysis.recommendation.action !== "none" && (
            <Card className="p-6 border-border shadow-sm">
              <div className="flex items-center gap-2 mb-3">
                <Zap className="w-4 h-4 text-amber-500" />
                <h4 className="text-muted-foreground text-xs font-bold uppercase tracking-wider">Scaling Recommendation</h4>
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
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
