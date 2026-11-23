"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Shield,
  ShieldAlert,
  ShieldCheck,
  Search,
  Sparkles,
  Lock,
  FileCheck2,
  Package,
  RefreshCw,
  Loader2,
  Download,
  Camera,
  Globe,
  PauseOctagon,
  GitFork,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/use-toast";
import { cn } from "@/lib/utils";
import {
  securityCenterApi,
  type SecurityOverviewData,
  type SecurityFindingItem,
  type SbomArtifactItem,
  type ComplianceControlItem,
  type ForensicSnapshotItem,
} from "@/lib/api";

interface SecurityCenterViewProps {
  serviceId?: string;
  compact?: boolean;
}

const SEV_BADGE: Record<string, string> = {
  CRITICAL: "bg-red-500/15 text-red-400 border-red-500/30",
  HIGH: "bg-orange-500/15 text-orange-400 border-orange-500/30",
  MEDIUM: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  LOW: "bg-sky-500/15 text-sky-400 border-sky-500/30",
  INFO: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
};

export const SecurityCenterView = React.memo(function SecurityCenterView({ serviceId, compact = false }: SecurityCenterViewProps) {
  const { toast } = useToast();
  const [overview, setOverview] = useState<SecurityOverviewData | null>(null);
  const [findings, setFindings] = useState<SecurityFindingItem[]>([]);
  const [sboms, setSboms] = useState<SbomArtifactItem[]>([]);
  const [forensics, setForensics] = useState<ForensicSnapshotItem[]>([]);
  const [ocsfEvents, setOcsfEvents] = useState<any[]>([]);
  const [eventSourceFilter, setEventSourceFilter] = useState<string>("ALL");
  const [expandedOcsfId, setExpandedOcsfId] = useState<string | null>(null);
  const [compliance, setCompliance] = useState<{ frameworks: Record<string, any>; controls: ComplianceControlItem[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [soarBusy, setSoarBusy] = useState<string | null>(null);
  const [policyBusy, setPolicyBusy] = useState<string | null>(null);
  const [pillarFilter, setPillarFilter] = useState<string>("ALL");
  const [statusFilter, setStatusFilter] = useState<string>("OPEN");
  const [packageSearch, setPackageSearch] = useState<string>("");
  const [huntQuery, setHuntQuery] = useState<string>("");
  const [huntResult, setHuntResult] = useState<any>(null);
  const [hunting, setHunting] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, fnd, sb, dfir, evts] = await Promise.all([
        securityCenterApi.getOverview(serviceId).catch(() => null),
        securityCenterApi
          .getFindings({
            ...(serviceId ? { service_id: serviceId } : {}),
            ...(pillarFilter !== "ALL" ? { pillar: pillarFilter } : {}),
            ...(statusFilter !== "OPEN" ? { status: statusFilter } : {}),
            limit: compact ? 25 : 100,
          })
          .catch(() => ({ count: 0, results: [] })),
        securityCenterApi
          .getSboms({
            ...(serviceId ? { service_id: serviceId } : {}),
          })
          .catch(() => ({ count: 0, results: [] })),
        securityCenterApi.getForensics(serviceId).catch(() => ({ count: 0, results: [] })),
        securityCenterApi
          .getOcsfEvents({
            ...(serviceId ? { service_id: serviceId } : {}),
            ...(eventSourceFilter !== "ALL" ? { source: eventSourceFilter.toLowerCase() } : {}),
            limit: compact ? 20 : 60,
          })
          .catch(() => ({ schema: "OCSF-1.1.0", count: 0, results: [] })),
      ]);
      if (ov) setOverview(ov);
      setFindings(fnd.results || []);
      setSboms(sb.results || []);
      setForensics(dfir.results || []);
      setOcsfEvents(evts.results || []);

      if (!compact && ov?.compliance_frameworks !== undefined) {
        const grc = await securityCenterApi.getCompliance().catch(() => null);
        if (grc) setCompliance({ frameworks: grc.frameworks, controls: grc.controls });
      }
    } finally {
      setLoading(false);
    }
  }, [serviceId, pillarFilter, statusFilter, eventSourceFilter, compact]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  useEffect(() => {
    const trimmed = packageSearch.trim();
    const timer = setTimeout(() => {
      securityCenterApi
        .getSboms({
          ...(serviceId ? { service_id: serviceId } : {}),
          ...(trimmed ? { package: trimmed } : {}),
        })
        .then((sb) => setSboms(sb.results || []))
        .catch(() => {});
    }, 250);
    return () => clearTimeout(timer);
  }, [packageSearch, serviceId]);

  const handleRunScan = async () => {
    setScanning(true);
    try {
      await securityCenterApi.triggerScan(serviceId);
      toast({
        title: "Security Posture Sweep Complete",
        description: "CNAPP, DSPM, KSPM, and Compliance findings have been refreshed.",
      });
      await loadAll();
    } catch (err: any) {
      toast({
        title: "Scan Failed",
        description: err?.response?.data?.error || err.message || "Could not run security scan.",
        variant: "destructive",
      });
    } finally {
      setScanning(false);
    }
  };

  const handleFindingStatus = async (findingId: string, nextStatus: SecurityFindingItem["status"]) => {
    try {
      await securityCenterApi.updateFindingStatus(findingId, nextStatus);
      toast({ title: `Finding marked ${nextStatus}` });
      await loadAll();
    } catch (err: any) {
      toast({
        title: "Update Failed",
        description: err?.response?.data?.error || "Could not update finding status.",
        variant: "destructive",
      });
    }
  };

  const handleSoar = async (action: string, targetServiceId?: string | null) => {
    const sid = targetServiceId || serviceId;
    if (!sid) return;
    setSoarBusy(`${action}:${sid}`);
    try {
      const res = await securityCenterApi.executeSoar({ action, service_id: sid });
      toast({
        title: `SOAR Action Executed (${action})`,
        description: res.sha256_digest
          ? `DFIR Snapshot SHA-256: ${String(res.sha256_digest).slice(0, 16)}...`
          : "Containment playbook completed cleanly.",
      });
      await loadAll();
    } catch (err: any) {
      toast({
        title: `SOAR ${action} failed`,
        description: err?.response?.data?.error || err.message || "Action failed.",
        variant: "destructive",
      });
    } finally {
      setSoarBusy(null);
    }
  };

  const handleHunt = async () => {
    if (!huntQuery.trim()) return;
    setHunting(true);
    try {
      const res = await securityCenterApi.aiHunt(huntQuery.trim());
      setHuntResult(res);
    } catch (err: any) {
      toast({
        title: "AI Threat Hunt Blocked / Failed",
        description: err?.response?.data?.reason || err?.response?.data?.error || "Query failed.",
        variant: "destructive",
      });
    } finally {
      setHunting(false);
    }
  };

  const handleTogglePolicy = async (key: string, currentVal: boolean) => {
    setPolicyBusy(key);
    try {
      const updated = await securityCenterApi.updatePolicy({ [key]: !currentVal });
      setOverview((prev) => (prev ? { ...prev, policy: { ...prev.policy, ...updated } } : prev));
      toast({ title: `Policy updated: ${key} = ${!currentVal}` });
    } catch (err: any) {
      toast({
        title: "Policy Update Failed",
        description: err?.response?.data?.error || "Admin privileges required.",
        variant: "destructive",
      });
    } finally {
      setPolicyBusy(null);
    }
  };

  const handleExportGrc = async () => {
    try {
      const bundle = await securityCenterApi.exportComplianceBundle();
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `smsly-compliance-evidence-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast({ title: "Compliance export requires admin role", variant: "destructive" });
    }
  };

  return (
    <div className={cn("space-y-6", compact && "col-span-1 md:col-span-4")}>
      {/* Header & Summary */}
      <Card className="border-border bg-card">
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base font-bold flex items-center gap-2">
                <Shield className="w-5 h-5 text-emerald-400" />
                {compact
                  ? "Service Security Posture (ASPM / DSPM / SBOM / SOAR)"
                  : "SMSLY Aegis Command Center (OCSF Core • CNAPP • ASPM • XDR • GRC)"}
              </CardTitle>
              <CardDescription>
                Risk Fusion scoring, CycloneDX SBOM + SLSA v1.0 provenance, MITRE ATT&amp;CK coverage, and SOAR containment.
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {serviceId && (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={soarBusy !== null}
                    onClick={() => handleSoar("run_dast", serviceId)}
                  >
                    {soarBusy === `run_dast:${serviceId}` ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Globe className="w-3.5 h-3.5 mr-1" />}
                    Run DAST
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={soarBusy !== null}
                    onClick={() => handleSoar("capture_forensics", serviceId)}
                  >
                    {soarBusy === `capture_forensics:${serviceId}` ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Camera className="w-3.5 h-3.5 mr-1" />}
                    DFIR Snapshot
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={soarBusy !== null}
                    onClick={() => handleSoar("quarantine_service", serviceId)}
                  >
                    {soarBusy === `quarantine_service:${serviceId}` ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <PauseOctagon className="w-3.5 h-3.5 mr-1" />}
                    Quarantine
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={soarBusy !== null}
                    onClick={() => handleSoar("unquarantine_service", serviceId)}
                  >
                    {soarBusy === `unquarantine_service:${serviceId}` ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <ShieldCheck className="w-3.5 h-3.5 mr-1" />}
                    Unquarantine
                  </Button>
                </>
              )}
              <Button variant="outline" size="sm" onClick={handleRunScan} disabled={scanning}>
                {scanning ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" /> : <RefreshCw className="w-3.5 h-3.5 mr-1.5" />}
                Run CNAPP/DSPM Scan
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
              <div className="text-[10px] uppercase text-muted-foreground">Risk Fusion Score</div>
              <div className="text-xl font-bold font-mono mt-0.5">
                {overview?.risk_score ?? 0}/100{" "}
                <Badge variant="outline" className="ml-1 text-[10px]">Grade {overview?.posture_grade ?? "A"}</Badge>
              </div>
            </div>
            <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
              <div className="text-[10px] uppercase text-muted-foreground">Open Findings</div>
              <div className="text-xl font-bold font-mono mt-0.5">
                {overview?.open_findings?.total ?? 0}
                <span className="text-xs font-normal text-red-400 ml-1.5">
                  ({overview?.open_findings?.critical ?? 0} crit)
                </span>
              </div>
            </div>
            <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
              <div className="text-[10px] uppercase text-muted-foreground">cryptd At-Rest Coverage</div>
              <div className="text-xl font-bold font-mono mt-0.5">
                {overview?.encryption_coverage?.encrypted_services ?? 0}/{overview?.encryption_coverage?.total_services ?? 0}
              </div>
            </div>
            <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
              <div className="text-[10px] uppercase text-muted-foreground">CycloneDX SBOM / SLSA</div>
              <div className="text-xl font-bold font-mono mt-0.5">
                {overview?.sbom_summary?.artifacts_count ?? 0} BOMs
              </div>
            </div>
            <div className="p-3 rounded-lg bg-muted/20 border border-border/50">
              <div className="text-[10px] uppercase text-muted-foreground">MITRE Techniques</div>
              <div className="text-xl font-bold font-mono mt-0.5">
                {overview?.mitre_summary?.techniques_detected ?? 0}/{overview?.mitre_summary?.techniques_total ?? 14} active
              </div>
            </div>
          </div>

          {/* Toxic Combinations */}
          {(overview?.toxic_combinations?.length ?? 0) > 0 && (
            <div className="mt-4 space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wider text-red-400 flex items-center gap-1.5">
                <GitFork className="w-3.5 h-3.5" />
                Toxic Attack Paths Detected ({overview!.toxic_combinations.length})
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {overview!.toxic_combinations.slice(0, 4).map((tc) => (
                  <div key={tc.service_id} className="p-3 rounded-lg border border-red-500/30 bg-red-500/5 text-xs space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono font-semibold text-foreground">{tc.path}</span>
                      <Badge variant="outline" className="bg-red-500/15 text-red-400 border-red-500/30 text-[10px]">
                        Risk {tc.risk_score}
                      </Badge>
                    </div>
                    <div className="text-muted-foreground">{tc.factors.join(" • ")}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {/* Admin Policy Enforcement Toggles */}
          {!compact && overview?.compliance_frameworks !== undefined && overview?.policy && (
            <div className="mt-4 pt-3 border-t border-border/50 flex flex-wrap items-center gap-2 text-xs">
              <span className="text-[11px] font-semibold uppercase text-muted-foreground mr-1">
                Enforcement Gates:
              </span>
              {(
                [
                  ["aspm_block_on_secrets", "Block on Secrets"],
                  ["aspm_block_on_critical_sast", "Block on Critical SAST"],
                  ["aspm_block_on_critical_iac", "Block on Critical IaC"],
                  ["dast_on_deploy_enabled", "Post-Deploy DAST"],
                  ["ai_prompt_firewall_enabled", "AI-SPM Firewall"],
                  ["auto_quarantine_on_critical_runtime", "Auto-Quarantine Runtime"],
                ] as const
              ).map(([key, label]) => {
                const enabled = Boolean(overview.policy[key]);
                return (
                  <Button
                    key={key}
                    size="sm"
                    variant={enabled ? "default" : "outline"}
                    disabled={policyBusy === key}
                    onClick={() => handleTogglePolicy(key, enabled)}
                    className="h-6 px-2.5 text-[11px]"
                  >
                    {label}: {enabled ? "ON" : "OFF"}
                  </Button>
                );
              })}
            </div>
          )}

          {/* Live Defense Engines & grid-cryptd Vault Telemetry Strip */}
          {!compact && (overview?.engines || overview?.cryptd_status) && (
            <div className="mt-3 pt-3 border-t border-border/50 flex flex-wrap items-center gap-2 text-xs">
              <span className="text-[11px] font-semibold uppercase text-muted-foreground mr-1">
                Active Engines:
              </span>
              {overview?.engines && (
                <>
                  <Badge variant="outline" className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 text-[10px]">
                    open-appsec: {overview.engines.openappsec_enabled ? overview.engines.openappsec_mode.toUpperCase() : "OFF"}
                  </Badge>
                  <Badge variant="outline" className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 text-[10px]">
                    Coraza L7 WAF: {overview.engines.coraza_protected_services}/{overview.engines.total_services} Protected
                  </Badge>
                  <Badge variant="outline" className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 text-[10px]">
                    CrowdSec IPS: {overview.engines.crowdsec_first_strike ? "FIRST-STRIKE" : "ACTIVE"}
                  </Badge>
                  <Badge
                    variant="outline"
                    className={cn(
                      "text-[10px]",
                      overview.engines.cf_bouncer_enabled
                        ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                        : "bg-zinc-500/10 text-zinc-400"
                    )}
                  >
                    Cloudflare Edge Bouncer: {overview.engines.cf_bouncer_enabled ? "ENFORCING" : "OFF"}
                  </Badge>
                  <Badge variant="outline" className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 text-[10px]">
                    Trivy ({overview.engines.trivy_severity}) + Cosign: {overview.engines.trivy_enabled && overview.engines.cosign_enabled ? "ENFORCING" : "PARTIAL"}
                  </Badge>
                </>
              )}
              {overview?.cryptd_status && (
                <Badge
                  variant="outline"
                  className={cn(
                    "text-[10px] font-mono",
                    overview.cryptd_status.enabled && overview.cryptd_status.state === "ACTIVE"
                      ? "bg-cyan-500/10 text-cyan-400 border-cyan-500/30"
                      : "bg-zinc-500/10 text-zinc-400"
                  )}
                >
                  grid-cryptd: {overview.cryptd_status.state} (Wk {overview.cryptd_status.current_week ?? 0} • {overview.cryptd_status.active_leases ?? 0}/{overview.cryptd_status.opted_services ?? 0} leases)
                </Badge>
              )}
            </div>
          )}

          {/* Recent DFIR Forensic Snapshots */}
          {forensics.length > 0 && (
            <div className="mt-4 pt-3 border-t border-border/50 space-y-1.5">
              <div className="text-[11px] font-semibold uppercase text-muted-foreground flex items-center gap-1.5">
                <Camera className="w-3.5 h-3.5 text-cyan-400" />
                SHA-256 DFIR Forensic Snapshots ({forensics.length})
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {forensics.slice(0, 4).map((snap) => (
                  <div key={snap.id} className="p-2 rounded border border-border/50 bg-muted/15 text-xs flex items-center justify-between gap-2">
                    <div className="truncate">
                      <span className="font-semibold">{snap.service_name}</span>{" "}
                      <span className="text-muted-foreground">({snap.trigger})</span>
                    </div>
                    <Badge variant="outline" className="font-mono text-[10px]">
                      SHA256:{snap.sha256_digest.slice(0, 12)}
                    </Badge>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* AI-SPM Guarded Threat Hunter (full platform view) */}
      {!compact && (
        <Card className="border-border">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-purple-400" />
              SMSLY Aegis AI Threat Hunter (Protected by AI-SPM Prompt Firewall)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex gap-2">
              <Input
                placeholder="Ask e.g. 'Which public services have critical findings or unencrypted Postgres addons?'"
                value={huntQuery}
                onChange={(e) => setHuntQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleHunt()}
                className="text-xs"
              />
              <Button size="sm" onClick={handleHunt} disabled={hunting || !huntQuery.trim()}>
                {hunting ? <Loader2 className="w-4 h-4 animate-spin" /> : "Hunt"}
              </Button>
            </div>
            {huntResult && (
              <div className="p-3 rounded-lg bg-muted/30 border-l-2 border-purple-500 text-xs space-y-1">
                <p className="text-foreground whitespace-pre-wrap">{huntResult.answer}</p>
                {Array.isArray(huntResult.redacted_types) && huntResult.redacted_types.length > 0 && (
                  <p className="text-[10px] text-amber-400">
                    AI-SPM Firewall redacted sensitive tokens before LLM call: {huntResult.redacted_types.join(", ")}
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Findings Table */}
      <Card className="border-border">
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 text-orange-400" />
              Prioritized Security Findings ({findings.length})
            </CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex flex-wrap gap-1">
                {["OPEN", "RESOLVED", "RISK_ACCEPTED", "ALL"].map((st) => (
                  <Button
                    key={st}
                    size="sm"
                    variant={statusFilter === st ? "secondary" : "ghost"}
                    className="h-7 text-[10px] px-2"
                    onClick={() => setStatusFilter(st)}
                  >
                    {st.replace("_", " ")}
                  </Button>
                ))}
              </div>
              <div className="flex flex-wrap gap-1">
                {["ALL", "ASPM", "CNAPP", "XDR"].map((pil) => (
                  <Button
                    key={pil}
                    size="sm"
                    variant={pillarFilter === pil ? "default" : "outline"}
                    className="h-7 text-[11px] px-2.5"
                    onClick={() => setPillarFilter(pil)}
                  >
                    {pil}
                  </Button>
                ))}
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-xs text-muted-foreground py-6 text-center">Loading findings...</div>
          ) : findings.length === 0 ? (
            <div className="text-xs text-muted-foreground py-6 text-center flex flex-col items-center gap-1">
              <ShieldCheck className="w-6 h-6 text-emerald-500/50" />
              <span>No open findings matching current filters.</span>
            </div>
          ) : (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {findings.map((f) => (
                <div key={f.id} className="p-3 rounded-lg border border-border/60 bg-muted/15 text-xs space-y-1.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge variant="outline" className={cn("text-[10px]", SEV_BADGE[f.severity] || "")}>
                        {f.severity} (Risk {f.risk_score})
                      </Badge>
                      <Badge variant="secondary" className="text-[10px] font-mono">
                        {f.pillar}/{f.category}
                      </Badge>
                      {f.mitre_technique && (
                        <Badge variant="outline" className="text-[10px] font-mono">
                          {f.mitre_technique}
                        </Badge>
                      )}
                      <span className="font-semibold text-foreground">{f.title}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      {f.service_id && f.category === "DSPM" && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-6 px-2 text-[10px]"
                          onClick={() => handleSoar("lock_service_cryptd", f.service_id)}
                        >
                          <Lock className="w-3 h-3 mr-1" /> Lock Cryptd
                        </Button>
                      )}
                      {f.status !== "OPEN" ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 px-2 text-[10px] text-amber-400"
                          onClick={() => handleFindingStatus(f.id, "OPEN")}
                        >
                          Reopen
                        </Button>
                      ) : (
                        <>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 px-2 text-[10px] text-emerald-400"
                            onClick={() => handleFindingStatus(f.id, "RESOLVED")}
                          >
                            Resolve
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 px-2 text-[10px] text-muted-foreground"
                            onClick={() => handleFindingStatus(f.id, "RISK_ACCEPTED")}
                          >
                            Accept Risk
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                  <p className="text-muted-foreground">{f.description}</p>
                  {f.remediation && (
                    <p className="text-[11px] text-emerald-400/90">Fix: {f.remediation}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* CycloneDX SBOM & GRC Compliance Row */}
      <div className={cn("grid gap-6", compact ? "grid-cols-1" : "grid-cols-1 lg:grid-cols-2")}>
        <Card className="border-border">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Package className="w-4 h-4 text-cyan-400" />
                CycloneDX SBOM &amp; SLSA v1.0 Provenance ({sboms.length})
              </CardTitle>
              <div className="relative w-48">
                <Search className="w-3.5 h-3.5 absolute left-2.5 top-2 text-muted-foreground" />
                <Input
                  placeholder="Search pkg e.g. django"
                  value={packageSearch}
                  onChange={(e) => setPackageSearch(e.target.value)}
                  className="h-7 pl-7 text-[11px]"
                />
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-2 max-h-64 overflow-y-auto">
            {sboms.length === 0 ? (
              <p className="text-xs text-muted-foreground py-4 text-center">
                No SBOM artifacts recorded yet. Deploy a service to generate CycloneDX v1.5 BOMs.
              </p>
            ) : (
              sboms.slice(0, 10).map((sb) => (
                <div key={sb.id} className="p-2.5 rounded border border-border/50 bg-muted/15 text-xs flex items-center justify-between gap-2">
                  <div>
                    <div className="font-semibold">{sb.service_name}</div>
                    <div className="text-[11px] text-muted-foreground font-mono">
                      CycloneDX v{sb.spec_version} • {sb.package_count} packages
                    </div>
                  </div>
                  <Badge variant="outline" className="text-[10px] bg-emerald-500/10 text-emerald-400 border-emerald-500/20">
                    {sb.provenance_verified ? "SLSA v1.0 Verified" : "Unsigned"}
                  </Badge>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {!compact && (
          <Card className="border-border">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <FileCheck2 className="w-4 h-4 text-emerald-400" />
                  Continuous GRC Compliance (SOC 2 • ISO 27001 • NDPR • PCI-DSS)
                </CardTitle>
                <Button size="sm" variant="outline" className="h-7 text-[11px]" onClick={handleExportGrc}>
                  <Download className="w-3 h-3 mr-1" /> Export Evidence
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {compliance ? (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {Object.entries(compliance.frameworks).map(([fw, stat]: [string, any]) => (
                      <div key={fw} className="p-2.5 rounded bg-muted/20 border border-border/50 text-center">
                        <div className="text-[10px] uppercase font-bold text-muted-foreground">{fw}</div>
                        <div className="text-lg font-bold font-mono">{stat.score_pct}%</div>
                        <div className="text-[10px] text-muted-foreground">{stat.passed}/{stat.controls} pass</div>
                      </div>
                    ))}
                  </div>
                  <div className="space-y-1.5 max-h-40 overflow-y-auto">
                    {compliance.controls.map((c) => (
                      <div key={`${c.framework}-${c.control_id}`} className="flex items-center justify-between text-xs p-2 rounded bg-muted/10 border border-border/40 gap-2">
                        <div className="truncate">
                          <span className="font-mono font-semibold mr-1.5">{c.control_id}</span>
                          <span className="text-muted-foreground">{c.control_name}</span>
                        </div>
                        <Badge
                          variant="outline"
                          className={cn(
                            "text-[10px] shrink-0",
                            c.status === "PASS"
                              ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                              : c.status === "PARTIAL"
                                ? "bg-amber-500/10 text-amber-400 border-amber-500/20"
                                : "bg-red-500/10 text-red-400 border-red-500/20"
                          )}
                        >
                          {c.status}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <p className="text-xs text-muted-foreground py-4 text-center">
                  Compliance scorecard available to platform administrators.
                </p>
              )}
            </CardContent>
          </Card>
        )}
      </div>

      {/* MITRE ATT&CK Coverage Matrix (full platform view) */}
      {!compact && (overview?.mitre_summary?.matrix?.length ?? 0) > 0 && (
        <Card className="border-border">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Shield className="w-4 h-4 text-sky-400" />
              MITRE ATT&amp;CK Technique Coverage Matrix ({overview!.mitre_summary.techniques_detected}/{overview!.mitre_summary.techniques_total} detected)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
              {overview!.mitre_summary.matrix.map((m) => {
                const detected = m.coverage_status === "DETECTED";
                return (
                  <div
                    key={m.technique_id}
                    className={cn(
                      "p-2.5 rounded-lg border text-xs space-y-1",
                      detected
                        ? "border-amber-500/40 bg-amber-500/5"
                        : "border-border/50 bg-muted/10"
                    )}
                  >
                    <div className="flex items-center justify-between gap-1">
                      <span className="font-mono font-bold text-[11px]">{m.technique_id}</span>
                      <Badge
                        variant="outline"
                        className={cn(
                          "text-[9px]",
                          detected
                            ? "bg-amber-500/15 text-amber-400 border-amber-500/30"
                            : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                        )}
                      >
                        {detected ? `${m.events_count + m.open_findings_count} hits` : "MONITORED"}
                      </Badge>
                    </div>
                    <div className="font-medium text-foreground truncate">{m.name}</div>
                    <div className="text-[10px] text-muted-foreground truncate">{m.tactic}</div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* OCSF v1.1.0 Normalized Security Log & Telemetry Stream */}
      <Card className="border-border">
        <CardHeader className="pb-2">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <Shield className="w-4 h-4 text-emerald-400" />
              OCSF v1.1.0 Normalized Security Log &amp; Telemetry Stream ({ocsfEvents.length} shown / {overview?.ocsf_events_count ?? ocsfEvents.length} total)
            </CardTitle>
            <div className="flex flex-wrap gap-1">
              {["ALL", "openappsec", "crowdsec", "falco", "fail2ban", "image_cve", "dspm", "secrets", "aspm", "auditd"].map((src) => (
                <Button
                  key={src}
                  size="sm"
                  variant={eventSourceFilter === src ? "default" : "outline"}
                  className="h-6 px-2 text-[10px] font-mono"
                  onClick={() => setEventSourceFilter(src)}
                >
                  {src}
                </Button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {ocsfEvents.length === 0 ? (
            <div className="text-xs text-muted-foreground py-6 text-center">
              No normalized OCSF telemetry events matching source filter &ldquo;{eventSourceFilter}&rdquo;.
            </div>
          ) : (
            <div className="space-y-2 max-h-80 overflow-y-auto">
              {ocsfEvents.map((ev) => {
                const isExp = expandedOcsfId === ev.id;
                return (
                  <div key={ev.id} className="p-2.5 rounded-lg border border-border/60 bg-muted/15 text-xs space-y-1">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <Badge variant="outline" className={cn("text-[10px]", SEV_BADGE[ev.severity] || "")}>
                          {ev.severity}
                        </Badge>
                        <Badge variant="secondary" className="text-[10px] font-mono uppercase">
                          {ev.source}
                        </Badge>
                        {ev.mitre_technique && (
                          <Badge variant="outline" className="text-[10px] font-mono">
                            {ev.mitre_technique}
                          </Badge>
                        )}
                        <span className="font-semibold text-foreground">{ev.title}</span>
                        {ev.target && (
                          <span className="px-1.5 py-0.5 rounded bg-muted/60 text-muted-foreground font-mono text-[10px]">
                            target: {ev.target}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                        <span>{ev.occurred_at ? new Date(ev.occurred_at).toLocaleString() : ""}</span>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-5 px-1.5 text-[10px]"
                          onClick={() => setExpandedOcsfId(isExp ? null : ev.id)}
                        >
                          {isExp ? "Hide OCSF" : "OCSF JSON"}
                        </Button>
                      </div>
                    </div>
                    {ev.details && <div className="text-[11px] text-muted-foreground font-mono">{ev.details}</div>}
                    {isExp && (
                      <pre className="mt-1.5 p-2 rounded bg-black/40 border border-border/40 text-[10px] font-mono text-zinc-300 overflow-x-auto max-h-44">
                        {JSON.stringify(ev.raw_ocsf || ev, null, 2)}
                      </pre>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
});
