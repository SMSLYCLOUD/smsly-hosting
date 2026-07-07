"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Shield, ShieldCheck, ShieldX, Cpu, Lock, Eye,
  AlertTriangle, CheckCircle2, XCircle, Loader2,
  RefreshCw, Bug, FileWarning, ShieldAlert
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import api from "@/lib/api";

interface VulnSummary {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

interface VulnFinding {
  id: string;
  severity: string;
  pkg: string;
  title: string;
}

interface VulnReport {
  summary?: VulnSummary;
  findings?: VulnFinding[];
  scan_time?: string;
  image?: string;
  error?: string;
  status?: string;
  fail_on_severity?: string;
}

export function SecurityStatusTab({ serviceId }: { serviceId: string }) {
  const [report, setReport] = useState<VulnReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [latestDeployId, setLatestDeployId] = useState<string | null>(null);

  const fetchScan = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const svcRes = await api.get(`/services/${serviceId}/`);
      const service = svcRes.data;
      const latest = service?.latest_deployment;

      if (latest?.vulnerability_report) {
        setReport(latest.vulnerability_report);
        setLatestDeployId(latest.id);
      } else {
        setReport(null);
        setLatestDeployId(latest?.id || null);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to load scan report");
    } finally {
      setLoading(false);
    }
  }, [serviceId]);

  useEffect(() => { fetchScan(); }, [fetchScan]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        <span className="ml-2 text-sm text-muted-foreground">Loading scan report...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-12">
        <ShieldX className="w-10 h-10 text-red-500 mx-auto mb-3" />
        <p className="text-sm text-muted-foreground mb-3">{error}</p>
        <Button onClick={fetchScan} variant="outline" size="sm">
          <RefreshCw className="w-3 h-3 mr-1" /> Retry
        </Button>
      </div>
    );
  }

  const summary = report?.summary;

  return (
    <div className="space-y-6">
      {/* Scan Report */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold flex items-center gap-2">
            <Bug className="w-4 h-4 text-amber-500" />
            Vulnerability Scan Report
          </h3>
          <Button onClick={fetchScan} variant="ghost" size="sm" disabled={loading}>
            <RefreshCw className={`w-3 h-3 mr-1 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>

        {report?.error ? (
          <Card>
            <CardContent className="p-4 text-sm text-muted-foreground">
              Scan error: {report.error}
            </CardContent>
          </Card>
        ) : summary ? (
          <div className="space-y-3">
            {/* Severity counts */}
            <div className="grid grid-cols-4 gap-2">
              <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-center">
                <div className="text-xl font-bold text-red-500">{summary.critical ?? 0}</div>
                <div className="text-[10px] text-muted-foreground uppercase">Critical</div>
              </div>
              <div className="p-3 rounded-lg bg-orange-500/10 border border-orange-500/20 text-center">
                <div className="text-xl font-bold text-orange-500">{summary.high ?? 0}</div>
                <div className="text-[10px] text-muted-foreground uppercase">High</div>
              </div>
              <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-center">
                <div className="text-xl font-bold text-amber-500">{summary.medium ?? 0}</div>
                <div className="text-[10px] text-muted-foreground uppercase">Medium</div>
              </div>
              <div className="p-3 rounded-lg bg-zinc-500/10 border border-zinc-500/20 text-center">
                <div className="text-xl font-bold text-zinc-400">{summary.low ?? 0}</div>
                <div className="text-[10px] text-muted-foreground uppercase">Low</div>
              </div>
            </div>

            {/* Metadata */}
            <div className="text-xs text-muted-foreground space-y-1">
              {report.image && <p>Image: <code className="text-foreground">{report.image}</code></p>}
              {report.scan_time && <p>Scanned: {new Date(report.scan_time).toLocaleString()}</p>}
              {report.fail_on_severity && <p>Fail on severity: <Badge variant="outline" className="text-[10px] ml-1">{report.fail_on_severity}</Badge></p>}
              {latestDeployId && <p>Deployment: <code className="text-foreground">{latestDeployId.slice(0, 8)}</code></p>}
            </div>

            {/* Individual Findings */}
            {report.findings && report.findings.length > 0 && (
              <div className="mt-3">
                <p className="text-xs font-semibold text-muted-foreground mb-2">Findings ({report.findings.length})</p>
                <div className="max-h-60 overflow-y-auto space-y-1">
                  {report.findings.map((f, i) => (
                    <div key={i} className="flex items-center gap-2 p-2 rounded-lg bg-black/20 text-xs">
                      <Badge
                        variant={f.severity === "CRITICAL" ? "destructive" : f.severity === "HIGH" ? "destructive" : "outline"}
                        className={`text-[9px] w-16 justify-center ${
                          f.severity === "CRITICAL" ? "bg-red-500/20 text-red-400 border-red-500/30" :
                          f.severity === "HIGH" ? "bg-orange-500/20 text-orange-400 border-orange-500/30" :
                          f.severity === "MEDIUM" ? "bg-amber-500/20 text-amber-400 border-amber-500/30" :
                          "bg-zinc-500/20 text-zinc-400 border-zinc-500/30"
                        }`}
                      >
                        {f.severity}
                      </Badge>
                      <code className="text-muted-foreground">{f.pkg}</code>
                      <span className="text-foreground truncate flex-1">{f.title || f.id}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <Card>
            <CardContent className="p-8 text-center">
              <FileWarning className="w-8 h-8 text-muted-foreground mx-auto mb-2" />
              <p className="text-sm text-muted-foreground">No vulnerability scan data available.</p>
              <p className="text-xs text-muted-foreground mt-1">
                Scans run automatically during builds when Trivy is enabled.
              </p>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Container Security */}
      <div>
        <h3 className="text-sm font-bold flex items-center gap-2 mb-4">
          <Lock className="w-4 h-4 text-emerald-500" />
          Container Security
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <div className="flex items-center justify-between p-3 rounded-lg bg-card border border-border">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
              <span className="text-sm">AppArmor Profile</span>
            </div>
            <Badge variant="default" className="text-[10px]">docker-default</Badge>
          </div>
          <div className="flex items-center justify-between p-3 rounded-lg bg-card border border-border">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
              <span className="text-sm">no-new-privileges</span>
            </div>
            <Badge variant="default" className="text-[10px]">Enabled</Badge>
          </div>
          <div className="flex items-center justify-between p-3 rounded-lg bg-card border border-border">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
              <span className="text-sm">Capability Drop</span>
            </div>
            <Badge variant="default" className="text-[10px]">ALL</Badge>
          </div>
          <div className="flex items-center justify-between p-3 rounded-lg bg-card border border-border">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-500" />
              <span className="text-sm">seccomp Profile</span>
            </div>
            <Badge variant="default" className="text-[10px]">Default</Badge>
          </div>
        </div>
      </div>
    </div>
  );
}
