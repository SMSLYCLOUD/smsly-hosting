'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  Activity,
  Ban,
  Brain,
  Bug,
  Camera,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Copy,
  Cpu,
  Download,
  FileCheck2,
  Filter,
  GitFork,
  Globe,
  Loader2,
  Lock,
  Package,
  PauseOctagon,
  RefreshCw,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Target,
  Terminal,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Switch } from '@/components/ui/switch';
import { useConfirm } from '@/components/ui/confirm-dialog';
import { useToast } from '@/components/ui/use-toast';
import { cn } from '@/lib/utils';
import {
  securityCenterApi,
  type ComplianceControlItem,
  type ForensicSnapshotItem,
  type SbomArtifactItem,
  type SecurityAnalysisResponse,
  type SecurityEventsResponse,
  type SecurityFindingItem,
  type SecurityOverviewData,
  type SecurityStatusData,
} from '@/lib/api';

type SecuritySubTab = 'overview' | 'streams' | 'findings' | 'compliance';
type StreamMode = 'live' | 'ocsf';

const PAGE_SIZE = 25;

// WCAG 1.4.3 AA-compliant semantic severity badges in both light (:root) and dark themes.
const SEV_BADGE: Record<string, string> = {
  CRITICAL: 'bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/30',
  HIGH: 'bg-orange-500/10 text-orange-700 dark:text-orange-400 border-orange-500/30',
  MEDIUM: 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30',
  WARNING: 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30',
  LOW: 'bg-sky-500/10 text-sky-700 dark:text-sky-400 border-sky-500/30',
  INFO: 'bg-sky-500/10 text-sky-700 dark:text-sky-400 border-sky-500/30',
};

const SOURCE_CHIP: Record<string, string> = {
  falco: 'bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/20',
  crowdsec: 'bg-purple-500/10 text-purple-700 dark:text-purple-400 border-purple-500/20',
  fail2ban: 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20',
  openappsec: 'bg-teal-500/10 text-teal-700 dark:text-teal-400 border-teal-500/20',
  coraza: 'bg-orange-500/10 text-orange-700 dark:text-orange-400 border-orange-500/20',
  trivy: 'bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/20',
  image_cve: 'bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/20',
  auditd: 'bg-slate-500/10 text-slate-700 dark:text-zinc-300 border-border',
  dspm: 'bg-cyan-500/10 text-cyan-700 dark:text-cyan-400 border-cyan-500/20',
  secrets: 'bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20',
  aspm: 'bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/20',
};

const STATUS_OK_BADGE =
  'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/25';
const STATUS_WARN_BADGE =
  'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/25';
const STATUS_CRIT_BADGE =
  'bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/25';
const STATUS_OFF_BADGE =
  'bg-muted/60 text-slate-700 dark:text-zinc-400 border-border';

export interface SecurityIntelWorkspaceProps {
  serviceId?: string;
  securityStatus: SecurityStatusData | null;
  securityEvents: SecurityEventsResponse | null;
  securityLoading: boolean;
  securityAnalysis: SecurityAnalysisResponse | null;
  securityAnalyzing: boolean;
  securitySourceFilter: string;
  securityEventsSource: string;
  securitySeverityFilter: string;
  securitySearchQuery: string;
  onChangeSourceFilter: (src: string) => void;
  onChangeSeverityFilter: (sev: string) => void;
  onChangeSearchQuery: (q: string) => void;
  onRefreshSecurity: (source?: string) => Promise<void> | void;
  onRunSecurityAnalysis: () => Promise<void> | void;
  onUnbanCrowdSec: (ip: string) => Promise<void> | void;
  onUnbanFail2ban: (ip: string, jail?: string) => Promise<void> | void;
  onCrowdSecToggle: () => Promise<void> | void;
  onOpenAppSecToggle: () => Promise<void> | void;
  onOpenAppSecModeSwitch: () => Promise<void> | void;
  onTrivySeverityChange: (severity: string) => Promise<void> | void;
  onTrivyToggle: () => Promise<void> | void;
  onDeviceTrustToggle: () => Promise<void> | void;
  unbanningIp: string | null;
  unbanningF2bIp: string | null;
  domainUpdating: string | null;
}

export const SecurityIntelWorkspace = React.memo(function SecurityIntelWorkspace({
  serviceId,
  securityStatus,
  securityEvents,
  securityLoading,
  securityAnalysis,
  securityAnalyzing,
  securitySourceFilter,
  securityEventsSource,
  securitySeverityFilter,
  securitySearchQuery,
  onChangeSourceFilter,
  onChangeSeverityFilter,
  onChangeSearchQuery,
  onRefreshSecurity,
  onRunSecurityAnalysis,
  onUnbanCrowdSec,
  onUnbanFail2ban,
  onCrowdSecToggle,
  onOpenAppSecToggle,
  onOpenAppSecModeSwitch,
  onTrivySeverityChange,
  onTrivyToggle,
  onDeviceTrustToggle,
  unbanningIp,
  unbanningF2bIp,
  domainUpdating,
}: SecurityIntelWorkspaceProps) {
  const { toast } = useToast();
  const confirm = useConfirm();

  const [subTab, setSubTab] = useState<SecuritySubTab>('overview');
  const [streamMode, setStreamMode] = useState<StreamMode>('live');

  // Center / CNAPP / GRC / OCSF state
  const [overview, setOverview] = useState<SecurityOverviewData | null>(null);
  const [findings, setFindings] = useState<SecurityFindingItem[]>([]);
  const [sboms, setSboms] = useState<SbomArtifactItem[]>([]);
  const [forensics, setForensics] = useState<ForensicSnapshotItem[]>([]);
  const [ocsfEvents, setOcsfEvents] = useState<any[]>([]);
  const [compliance, setCompliance] = useState<{
    frameworks: Record<string, any>;
    controls: ComplianceControlItem[];
  } | null>(null);

  const [centerLoading, setCenterLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [soarBusy, setSoarBusy] = useState<string | null>(null);
  const [policyBusy, setPolicyBusy] = useState<string | null>(null);

  // Filters & disclosures
  const [pillarFilter, setPillarFilter] = useState<string>('ALL');
  const [statusFilter, setStatusFilter] = useState<string>('OPEN');
  const [ocsfSourceFilter, setOcsfSourceFilter] = useState<string>('ALL');
  const [ocsfSeverityFilter, setOcsfSeverityFilter] = useState<string>('ALL');
  const [packageSearch, setPackageSearch] = useState<string>('');
  const [mitreFilter, setMitreFilter] = useState<'all' | 'detected' | 'monitored'>('all');
  const [showAllSboms, setShowAllSboms] = useState(false);
  const [showAllForensics, setShowAllForensics] = useState(false);

  // Pagination & JSON expansion
  const [streamPage, setStreamPage] = useState(1);
  const [ocsfPage, setOcsfPage] = useState(1);
  const [findingsPage, setFindingsPage] = useState(1);
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);
  const [expandedOcsfId, setExpandedOcsfId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // AI Threat Hunter state
  const [huntQuery, setHuntQuery] = useState('');
  const [huntResult, setHuntResult] = useState<any>(null);
  const [hunting, setHunting] = useState(false);

  const loadCenterData = useCallback(async () => {
    setCenterLoading(true);
    try {
      const [ov, fnd, sb, dfir, evts] = await Promise.all([
        securityCenterApi.getOverview(serviceId).catch(() => null),
        securityCenterApi
          .getFindings({
            ...(serviceId ? { service_id: serviceId } : {}),
            ...(pillarFilter !== 'ALL' ? { pillar: pillarFilter } : {}),
            ...(statusFilter !== 'OPEN' ? { status: statusFilter } : {}),
            limit: 150,
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
            ...(ocsfSourceFilter !== 'ALL' ? { source: ocsfSourceFilter.toLowerCase() } : {}),
            ...(ocsfSeverityFilter !== 'ALL' ? { severity: ocsfSeverityFilter } : {}),
            limit: 100,
          })
          .catch(() => ({ schema: 'OCSF-1.1.0', count: 0, results: [] })),
      ]);
      if (ov) setOverview(ov);
      setFindings(fnd.results || []);
      setSboms(sb.results || []);
      setForensics(dfir.results || []);
      setOcsfEvents(evts.results || []);

      if (ov?.compliance_frameworks !== undefined) {
        const grc = await securityCenterApi.getCompliance().catch(() => null);
        if (grc) setCompliance({ frameworks: grc.frameworks, controls: grc.controls });
      }
    } finally {
      setCenterLoading(false);
    }
  }, [serviceId, pillarFilter, statusFilter, ocsfSourceFilter, ocsfSeverityFilter]);

  useEffect(() => {
    loadCenterData();
  }, [loadCenterData]);

  useEffect(() => {
    const trimmed = packageSearch.trim();
    const timer = setTimeout(() => {
      securityCenterApi
        .getSboms({
          ...(serviceId ? { service_id: serviceId } : {}),
          ...(trimmed ? { package: trimmed } : {}),
        })
        .then((sb) => {
          setSboms(sb.results || []);
          setShowAllSboms(false);
        })
        .catch(() => {});
    }, 250);
    return () => clearTimeout(timer);
  }, [packageSearch, serviceId]);

  const handleUnifiedRefresh = async () => {
    await Promise.all([
      Promise.resolve(onRefreshSecurity(securitySourceFilter !== 'all' ? securitySourceFilter : undefined)),
      loadCenterData(),
    ]);
  };

  const handleRunScan = async () => {
    setScanning(true);
    try {
      await securityCenterApi.triggerScan(serviceId);
      toast({
        title: 'Security Posture Sweep Complete',
        description: 'CNAPP, DSPM, KSPM, and Compliance findings have been refreshed.',
      });
      await handleUnifiedRefresh();
    } catch (err: any) {
      toast({
        title: 'Scan Failed',
        description: err?.response?.data?.error || err.message || 'Could not run security scan.',
        variant: 'destructive',
      });
    } finally {
      setScanning(false);
    }
  };

  const handleFindingStatus = async (findingId: string, nextStatus: SecurityFindingItem['status']) => {
    if (nextStatus === 'RISK_ACCEPTED') {
      const ok = await confirm({
        title: 'Accept security risk?',
        message: 'Marking a finding as Risk Accepted suppresses it for 90 days in audit views. Continue?',
        confirmText: 'Accept Risk',
        variant: 'destructive',
      });
      if (!ok) return;
    }
    try {
      await securityCenterApi.updateFindingStatus(findingId, nextStatus);
      toast({ title: `Finding marked ${nextStatus}` });
      await loadCenterData();
    } catch (err: any) {
      toast({
        title: 'Update Failed',
        description: err?.response?.data?.error || 'Could not update finding status.',
        variant: 'destructive',
      });
    }
  };

  const handleSoar = async (action: string, targetServiceId?: string | null) => {
    const sid = targetServiceId || serviceId;
    if (!sid) return;
    if (action === 'quarantine_service' || action === 'lock_service_cryptd') {
      const ok = await confirm({
        title: action === 'lock_service_cryptd' ? 'Lock service DEK in grid-cryptd?' : 'Quarantine service?',
        message:
          action === 'lock_service_cryptd'
            ? 'Locking immediately revokes the RAM lease and blocks DB/Redis/volume access until unlocked.'
            : 'Quarantining isolates ingress and pauses workload traffic.',
        confirmText: action === 'lock_service_cryptd' ? 'Lock Cryptd' : 'Quarantine',
        variant: 'destructive',
      });
      if (!ok) return;
    }
    setSoarBusy(`${action}:${sid}`);
    try {
      const res = await securityCenterApi.executeSoar({ action, service_id: sid });
      toast({
        title: `SOAR Action Executed (${action})`,
        description: res.sha256_digest
          ? `DFIR Snapshot SHA-256: ${String(res.sha256_digest).slice(0, 16)}...`
          : 'Containment playbook completed cleanly.',
      });
      await handleUnifiedRefresh();
    } catch (err: any) {
      toast({
        title: `SOAR ${action} failed`,
        description: err?.response?.data?.error || err.message || 'Action failed.',
        variant: 'destructive',
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
        title: 'AI Threat Hunt Blocked / Failed',
        description: err?.response?.data?.reason || err?.response?.data?.error || 'Query failed.',
        variant: 'destructive',
      });
    } finally {
      setHunting(false);
    }
  };

  const handleTogglePolicy = async (key: string, currentVal: boolean) => {
    if (key === 'auto_quarantine_on_critical_runtime' && !currentVal) {
      const ok = await confirm({
        title: 'Enable Auto-Quarantine Runtime?',
        message: 'Critical XDR alerts will automatically quarantine offending services at runtime.',
        confirmText: 'Enable Gate',
      });
      if (!ok) return;
    }
    setPolicyBusy(key);
    try {
      const updated = await securityCenterApi.updatePolicy({ [key]: !currentVal });
      setOverview((prev) => (prev ? { ...prev, policy: { ...prev.policy, ...updated } } : prev));
      toast({ title: `Policy updated: ${key} = ${!currentVal}` });
    } catch (err: any) {
      toast({
        title: 'Policy Update Failed',
        description: err?.response?.data?.error || 'Admin privileges required.',
        variant: 'destructive',
      });
    } finally {
      setPolicyBusy(null);
    }
  };

  const handleExportGrc = async () => {
    try {
      const bundle = await securityCenterApi.exportComplianceBundle();
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `smsly-compliance-evidence-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast({ title: 'Compliance export requires admin role', variant: 'destructive' });
    }
  };

  const copyJson = (id: string, data: any) => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Filtered Live Events
  const filteredLiveEvents = (securityEvents?.recent_activities || []).filter((evt) => {
    if (securityEventsSource !== 'all' && evt.source !== securityEventsSource) return false;
    if (securitySeverityFilter !== 'all' && evt.severity !== securitySeverityFilter) return false;
    if (securitySearchQuery.trim()) {
      const q = securitySearchQuery.toLowerCase();
      if (
        !evt.title?.toLowerCase().includes(q) &&
        !evt.details?.toLowerCase().includes(q) &&
        !evt.target?.toLowerCase().includes(q) &&
        !evt.source?.toLowerCase().includes(q)
      ) {
        return false;
      }
    }
    return true;
  });

  // Reset pagination when filters change
  useEffect(() => {
    setStreamPage(1);
  }, [securityEventsSource, securitySeverityFilter, securitySearchQuery]);
  useEffect(() => {
    setOcsfPage(1);
  }, [ocsfSourceFilter, ocsfSeverityFilter, securitySearchQuery]);
  useEffect(() => {
    setFindingsPage(1);
  }, [statusFilter, pillarFilter]);

  const filteredOcsfEvents = ocsfEvents.filter((ev) => {
    if (!securitySearchQuery.trim()) return true;
    const q = securitySearchQuery.toLowerCase();
    return (
      ev.title?.toLowerCase().includes(q) ||
      ev.details?.toLowerCase().includes(q) ||
      ev.target?.toLowerCase().includes(q) ||
      ev.source?.toLowerCase().includes(q) ||
      ev.mitre_technique?.toLowerCase().includes(q)
    );
  });

  const liveTotalPages = Math.max(1, Math.ceil(filteredLiveEvents.length / PAGE_SIZE));
  const safeLivePage = Math.min(streamPage, liveTotalPages);
  const pagedLiveEvents = filteredLiveEvents.slice((safeLivePage - 1) * PAGE_SIZE, safeLivePage * PAGE_SIZE);

  const ocsfTotalPages = Math.max(1, Math.ceil(filteredOcsfEvents.length / PAGE_SIZE));
  const safeOcsfPage = Math.min(ocsfPage, ocsfTotalPages);
  const pagedOcsfEvents = filteredOcsfEvents.slice((safeOcsfPage - 1) * PAGE_SIZE, safeOcsfPage * PAGE_SIZE);

  const findingsTotalPages = Math.max(1, Math.ceil(findings.length / PAGE_SIZE));
  const safeFindingsPage = Math.min(findingsPage, findingsTotalPages);
  const pagedFindings = findings.slice((safeFindingsPage - 1) * PAGE_SIZE, safeFindingsPage * PAGE_SIZE);

  const cryptdState = securityStatus?.cryptd?.state || overview?.cryptd_status?.state || 'INACTIVE';
  const cryptdEnabled = Boolean(securityStatus?.cryptd?.enabled ?? overview?.cryptd_status?.enabled);
  const cryptdWeek = securityStatus?.cryptd?.current_week ?? overview?.cryptd_status?.current_week ?? 0;
  const cryptdLeases = securityStatus?.cryptd?.active_leases ?? overview?.cryptd_status?.active_leases ?? 0;
  const cryptdOpted = securityStatus?.cryptd?.opted_services ?? overview?.cryptd_status?.opted_services ?? 0;
  const totalBlockedIps =
    (securityEvents?.summary?.crowdsec_bans_count ?? securityStatus?.crowdsec?.active_bans ?? 0) +
    (securityEvents?.summary?.fail2ban_banned_count ?? 0);

  const toxicList = overview?.toxic_combinations || [];
  const mitreMatrix = overview?.mitre_summary?.matrix || [];
  const filteredMitre = mitreMatrix.filter((m) => {
    if (mitreFilter === 'detected') return m.coverage_status === 'DETECTED';
    if (mitreFilter === 'monitored') return m.coverage_status !== 'DETECTED';
    return true;
  });

  const visibleSboms = showAllSboms ? sboms : sboms.slice(0, 10);
  const visibleForensics = showAllForensics ? forensics : forensics.slice(0, 6);
  const cosignOn = overview?.engines?.cosign_enabled ?? true;
  const isBusyAnyRefresh = securityLoading || centerLoading;

  return (
    <div className="space-y-5">
      {/* ═══════════════════════════════════════════════════════════════
          TIER 1: PERSISTENT EXECUTIVE POSTURE RIBBON & COMMAND BAR
         ═══════════════════════════════════════════════════════════════ */}
      <section className="card-enterprise rounded-lg overflow-hidden" aria-label="Security Posture Executive Summary">
        <div className="p-4 sm:p-5 border-b border-border flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <Shield className="w-5 h-5 text-emerald-700 dark:text-emerald-400 shrink-0" aria-hidden="true" />
              <h2 className="text-lg sm:text-xl font-semibold tracking-tight text-foreground">
                Security Infrastructure &amp; Threat Intelligence
              </h2>
              <Badge variant="outline" className="text-[11px] font-mono tabular-nums">
                SMSLY Aegis OCSF v1.1
              </Badge>
            </div>
            <p className="text-xs sm:text-[13px] text-muted-foreground mt-1 max-w-[75ch]">
              Unified perimeter WAFs, kernel eBPF runtime telemetry, cryptographic custody, CNAPP findings, and automated compliance verification.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2 shrink-0">
            {serviceId && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  className="min-h-11 sm:min-h-8"
                  disabled={soarBusy !== null}
                  onClick={() => handleSoar('run_dast', serviceId)}
                >
                  {soarBusy === `run_dast:${serviceId}` ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" /> : <Globe className="w-3.5 h-3.5 mr-1.5" />}
                  Run DAST
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="min-h-11 sm:min-h-8"
                  disabled={soarBusy !== null}
                  onClick={() => handleSoar('capture_forensics', serviceId)}
                >
                  {soarBusy === `capture_forensics:${serviceId}` ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" /> : <Camera className="w-3.5 h-3.5 mr-1.5" />}
                  DFIR Snapshot
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  className="min-h-11 sm:min-h-8"
                  disabled={soarBusy !== null}
                  onClick={() => handleSoar('quarantine_service', serviceId)}
                >
                  {soarBusy === `quarantine_service:${serviceId}` ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" /> : <PauseOctagon className="w-3.5 h-3.5 mr-1.5" />}
                  Quarantine
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="min-h-11 sm:min-h-8"
                  disabled={soarBusy !== null}
                  onClick={() => handleSoar('unquarantine_service', serviceId)}
                >
                  {soarBusy === `unquarantine_service:${serviceId}` ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" /> : <ShieldCheck className="w-3.5 h-3.5 mr-1.5" />}
                  Unquarantine
                </Button>
              </>
            )}

            <Button
              onClick={() => {
                setSubTab('overview');
                onRunSecurityAnalysis();
              }}
              disabled={securityAnalyzing}
              className="min-h-11 sm:min-h-9 bg-primary hover:bg-primary/90 text-primary-foreground"
            >
              {securityAnalyzing ? (
                <Loader2 className="w-4 h-4 animate-spin mr-1.5" />
              ) : (
                <Sparkles className="w-4 h-4 mr-1.5" />
              )}
              Run AI Threat Assessment
            </Button>

            <Button
              variant="outline"
              onClick={handleRunScan}
              disabled={scanning}
              className="min-h-11 sm:min-h-9"
            >
              {scanning ? <Loader2 className="w-4 h-4 animate-spin mr-1.5" /> : <ShieldAlert className="w-4 h-4 mr-1.5" />}
              Run CNAPP/DSPM Scan
            </Button>

            <Button
              variant="outline"
              onClick={handleUnifiedRefresh}
              disabled={isBusyAnyRefresh}
              aria-label="Refresh all security telemetry"
              className="min-h-11 sm:min-h-9"
            >
              <RefreshCw className={cn('w-4 h-4 mr-1.5', isBusyAnyRefresh && 'animate-spin')} />
              Refresh
            </Button>
          </div>
        </div>

        {/* 5 Hairline KPI Panels (.grid-enterprise) */}
        <div className="grid-enterprise grid-cols-1 sm:grid-cols-2 lg:grid-cols-5">
          {/* KPI 1: Risk Fusion Score */}
          <div className="space-y-1.5">
            <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Risk Fusion Score
            </div>
            {centerLoading && !overview ? (
              <div className="h-7 w-24 rounded bg-muted animate-pulse" />
            ) : (
              <div className="flex items-baseline gap-2">
                <span className="text-xl sm:text-2xl font-semibold font-mono tabular-nums text-foreground">
                  {overview?.risk_score ?? 0}/100
                </span>
                <Badge
                  variant="outline"
                  className={cn(
                    'text-[11px] font-mono',
                    (overview?.risk_score ?? 0) >= 60 ? STATUS_CRIT_BADGE : STATUS_OK_BADGE
                  )}
                >
                  Grade {overview?.posture_grade ?? 'A'}
                </Badge>
              </div>
            )}
            <div className="text-xs text-muted-foreground font-mono tabular-nums">
              {securityAnalysis
                ? `AI Neural: ${securityAnalysis.threat_level} (${securityAnalysis.risk_score}/100)`
                : 'Click AI Threat Assessment for neural posture'}
            </div>
          </div>

          {/* KPI 2: Open Findings */}
          <button
            type="button"
            onClick={() => setSubTab('findings')}
            className="text-left space-y-1.5 hover:bg-muted/30 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Open Findings
            </div>
            {centerLoading && !overview ? (
              <div className="h-7 w-20 rounded bg-muted animate-pulse" />
            ) : (
              <div className="flex items-baseline gap-2">
                <span className="text-xl sm:text-2xl font-semibold font-mono tabular-nums text-foreground">
                  {overview?.open_findings?.total ?? findings.length}
                </span>
                <span
                  className={cn(
                    'text-xs font-mono tabular-nums font-medium',
                    (overview?.open_findings?.critical ?? 0) > 0
                      ? 'text-red-700 dark:text-red-400'
                      : 'text-emerald-700 dark:text-emerald-400'
                  )}
                >
                  ({overview?.open_findings?.critical ?? 0} crit)
                </span>
              </div>
            )}
            <div className="text-xs text-muted-foreground">
              Toxic paths: <span className="font-mono tabular-nums text-foreground">{toxicList.length}</span> • Inspect →
            </div>
          </button>

          {/* KPI 3: cryptd At-Rest Custody */}
          <div className="space-y-1.5">
            <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              cryptd At-Rest Coverage
            </div>
            {centerLoading && !overview ? (
              <div className="h-7 w-24 rounded bg-muted animate-pulse" />
            ) : (
              <div className="flex items-baseline gap-2">
                <span className="text-xl sm:text-2xl font-semibold font-mono tabular-nums text-foreground">
                  {overview?.encryption_coverage?.encrypted_services ?? cryptdOpted}/
                  {overview?.encryption_coverage?.total_services ?? securityStatus?.coraza?.services_protected ?? 0}
                </span>
                <Badge variant="outline" className={cn('text-[11px] font-mono', cryptdEnabled && cryptdState === 'ACTIVE' ? STATUS_OK_BADGE : STATUS_OFF_BADGE)}>
                  {cryptdState}
                </Badge>
              </div>
            )}
            <div className="text-xs text-muted-foreground font-mono tabular-nums">
              Wk {cryptdWeek} • {cryptdLeases}/{cryptdOpted} active leases
            </div>
          </div>

          {/* KPI 4: Supply Chain, Trivy & Cosign */}
          <button
            type="button"
            onClick={() => setSubTab('compliance')}
            className="text-left space-y-1.5 hover:bg-muted/30 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              Supply Chain &amp; Cosign
            </div>
            {centerLoading && !overview ? (
              <div className="h-7 w-24 rounded bg-muted animate-pulse" />
            ) : (
              <div className="flex items-baseline gap-2">
                <span className="text-xl sm:text-2xl font-semibold font-mono tabular-nums text-foreground">
                  {overview?.sbom_summary?.artifacts_count ?? sboms.length} BOMs
                </span>
                <Badge variant="outline" className={cn('text-[11px]', cosignOn ? STATUS_OK_BADGE : STATUS_WARN_BADGE)}>
                  Cosign {cosignOn ? 'ON' : 'OFF'}
                </Badge>
              </div>
            )}
            <div className="text-xs text-muted-foreground font-mono tabular-nums">
              Trivy {securityStatus?.trivy?.fail_on_severity || overview?.engines?.trivy_severity || 'CRITICAL'} gate
            </div>
          </button>

          {/* KPI 5: MITRE & Active Bans */}
          <button
            type="button"
            onClick={() => setSubTab('streams')}
            className="text-left space-y-1.5 hover:bg-muted/30 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <div className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              MITRE &amp; Active Bans
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-xl sm:text-2xl font-semibold font-mono tabular-nums text-foreground">
                {overview?.mitre_summary?.techniques_detected ?? 0}/{overview?.mitre_summary?.techniques_total ?? 14}
              </span>
              <span className="text-xs font-mono tabular-nums text-purple-700 dark:text-purple-400 font-medium">
                {totalBlockedIps} bans
              </span>
            </div>
            <div className="text-xs text-muted-foreground font-mono tabular-nums">
              {securityEvents?.summary?.total_events ?? 0} live • {overview?.ocsf_events_count ?? ocsfEvents.length} OCSF
            </div>
          </button>
        </div>

        {/* Conditional Toxic Attack Paths Banner */}
        {toxicList.length > 0 && (
          <div className="px-4 py-3 bg-red-500/5 border-t border-red-500/25 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
            <div className="flex items-start sm:items-center gap-2 min-w-0">
              <GitFork className="w-4 h-4 text-red-700 dark:text-red-400 shrink-0 mt-0.5 sm:mt-0" aria-hidden="true" />
              <div className="min-w-0">
                <span className="font-semibold text-red-700 dark:text-red-400">
                  {toxicList.length} Toxic Attack Path{toxicList.length > 1 ? 's' : ''} Detected:
                </span>{' '}
                <span className="font-mono text-foreground">{toxicList[0].path}</span>{' '}
                <span className="text-muted-foreground">
                  (Risk {toxicList[0].risk_score} — {toxicList[0].factors.slice(0, 2).join(' • ')})
                </span>
              </div>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="min-h-11 sm:min-h-8 shrink-0 border-red-500/30 text-red-700 dark:text-red-400 hover:bg-red-500/10"
              onClick={() => setSubTab('findings')}
            >
              Inspect all {toxicList.length} paths →
            </Button>
          </div>
        )}
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          TIER 2: SUB-TAB NAVIGATION (CRISP UNDERLINE BAR)
         ═══════════════════════════════════════════════════════════════ */}
      <nav
        aria-label="Security Intel Views"
        className="flex items-center gap-1 border-b border-border overflow-x-auto scrollbar-hide"
        role="tablist"
      >
        {(
          [
            { id: 'overview', label: 'Overview & Engines', count: null },
            {
              id: 'streams',
              label: 'Live Threats & WAF Streams',
              count: securityEvents?.summary?.total_events ?? ocsfEvents.length,
            },
            {
              id: 'findings',
              label: 'Findings, AI Hunter & SOAR',
              count: overview?.open_findings?.total ?? findings.length,
            },
            {
              id: 'compliance',
              label: 'Supply Chain, MITRE & GRC',
              count: compliance?.controls?.length ?? null,
            },
          ] as const
        ).map((tab) => {
          const active = subTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setSubTab(tab.id)}
              className={cn(
                'relative whitespace-nowrap px-3.5 py-2.5 min-h-11 text-xs sm:text-sm font-medium transition-colors flex items-center gap-2 border-b-2 -mb-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                active
                  ? 'border-primary text-foreground font-semibold'
                  : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border'
              )}
            >
              <span>{tab.label}</span>
              {tab.count !== null && (
                <span className="px-1.5 py-0.5 rounded-full bg-muted text-foreground font-mono tabular-nums text-[11px]">
                  {tab.count}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* ═══════════════════════════════════════════════════════════════
          SUB-TAB 1: OVERVIEW & ENGINES
         ═══════════════════════════════════════════════════════════════ */}
      {subTab === 'overview' && (
        <div className="space-y-6" role="tabpanel">
          {/* AI Threat Posture & Attack Vector Analysis */}
          <Card className="border-border bg-card">
            {securityAnalysis ? (
              <>
                <CardHeader className="pb-3 border-b border-border">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Brain className="w-5 h-5 text-purple-700 dark:text-purple-400" aria-hidden="true" />
                      <div>
                        <CardTitle className="text-base font-semibold">
                          AI Threat Posture &amp; Attack Vector Analysis
                        </CardTitle>
                        <CardDescription className="text-xs">
                          Neural synthesis of active bans, open-appsec ML anomalies, Coraza blocks, Falco eBPF events, and host audits.
                        </CardDescription>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className="text-xs text-muted-foreground font-mono tabular-nums">
                        Analyzed: {new Date(securityAnalysis.analyzed_at).toLocaleTimeString()}
                      </span>
                      <Badge
                        variant="outline"
                        className={cn(
                          'font-semibold uppercase tracking-wider text-xs',
                          securityAnalysis.threat_level === 'LOW' && STATUS_OK_BADGE,
                          securityAnalysis.threat_level === 'ELEVATED' && STATUS_WARN_BADGE,
                          securityAnalysis.threat_level === 'HIGH' && SEV_BADGE.HIGH,
                          securityAnalysis.threat_level === 'SEVERE' && STATUS_CRIT_BADGE
                        )}
                      >
                        {securityAnalysis.threat_level} THREAT
                      </Badge>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="p-0">
                  <div className="grid-enterprise grid-cols-1 lg:grid-cols-12">
                    <div className="lg:col-span-5 space-y-4">
                      <div className="space-y-1.5">
                        <div className="flex justify-between items-center text-xs">
                          <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                            System Risk Score
                          </span>
                          <span className="font-mono tabular-nums font-bold text-sm text-foreground">
                            {securityAnalysis.risk_score} <span className="text-muted-foreground font-normal text-xs">/ 100</span>
                          </span>
                        </div>
                        <Progress
                          value={securityAnalysis.risk_score}
                          className={cn(
                            'h-2',
                            securityAnalysis.risk_score < 25
                              ? '[&>div]:bg-emerald-500'
                              : securityAnalysis.risk_score < 60
                                ? '[&>div]:bg-amber-500'
                                : securityAnalysis.risk_score < 80
                                  ? '[&>div]:bg-orange-500'
                                  : '[&>div]:bg-red-500'
                          )}
                        />
                      </div>
                      <div className="text-xs sm:text-[13px] leading-relaxed text-foreground border-l-2 border-purple-500 pl-3 whitespace-pre-wrap max-w-[75ch]">
                        {securityAnalysis.executive_summary}
                      </div>
                    </div>

                    <div className="lg:col-span-7 grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                          <Target className="w-3.5 h-3.5 text-amber-700 dark:text-amber-400" aria-hidden="true" />
                          Identified Attack Vectors ({securityAnalysis.attack_vectors?.length || 0})
                        </h3>
                        <ul className="space-y-1.5 text-xs text-muted-foreground max-w-[75ch]">
                          {securityAnalysis.attack_vectors?.map((vec, idx) => (
                            <li key={idx} className="flex items-start gap-2">
                              <span className="text-amber-700 dark:text-amber-400 font-bold">•</span>
                              <span>{vec}</span>
                            </li>
                          ))}
                        </ul>
                      </div>

                      <div className="space-y-2">
                        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                          <ShieldCheck className="w-3.5 h-3.5 text-emerald-700 dark:text-emerald-400" aria-hidden="true" />
                          Hardening Recommendations ({securityAnalysis.hardening_actions?.length || 0})
                        </h3>
                        <ul className="space-y-1.5 text-xs text-muted-foreground max-w-[75ch]">
                          {securityAnalysis.hardening_actions?.map((act, idx) => (
                            <li key={idx} className="flex items-start gap-2">
                              <span className="text-emerald-700 dark:text-emerald-400 font-bold">✓</span>
                              <span>{act}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </>
            ) : (
              <CardContent className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="w-9 h-9 rounded-lg bg-purple-500/10 border border-purple-500/20 flex items-center justify-center shrink-0">
                    <Sparkles className="w-4 h-4 text-purple-700 dark:text-purple-400" aria-hidden="true" />
                  </div>
                  <div>
                    <div className="text-sm font-semibold text-foreground">
                      AI Threat Posture &amp; Attack Vector Analysis
                    </div>
                    <p className="text-xs text-muted-foreground max-w-[75ch]">
                      Synthesize recent kernel eBPF events, CrowdSec bans, open-appsec ML verdicts, Coraza blocks, and Fail2ban logs into an executive risk report.
                    </p>
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onRunSecurityAnalysis}
                  disabled={securityAnalyzing}
                  className="min-h-11 sm:min-h-8 shrink-0"
                >
                  {securityAnalyzing ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" /> : <Brain className="w-3.5 h-3.5 mr-1.5" />}
                  Generate Assessment
                </Button>
              </CardContent>
            )}
          </Card>

          {/* TIER A: EDGE & PERIMETER WAF (3 UNIFORM COLUMNS) */}
          <div className="space-y-2.5">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Layer I — Edge &amp; Perimeter WAF
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {/* open-appsec ML WAF */}
              <div
                className="card-enterprise status-indicator rounded-lg p-4 flex flex-col justify-between gap-3"
                data-status={securityStatus?.openappsec?.agent_running ? 'healthy' : 'offline'}
              >
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                      <Lock className="w-4 h-4 text-teal-700 dark:text-teal-400" aria-hidden="true" />
                      <span>open-appsec ML WAF</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn('text-xs px-2 py-0.5', securityStatus?.openappsec?.agent_running ? STATUS_OK_BADGE : STATUS_OFF_BADGE)}
                    >
                      {!securityStatus?.openappsec?.enabled
                        ? 'DISABLED'
                        : securityStatus?.openappsec?.policy_mode?.toUpperCase() || 'MONITORING'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Dual-engine contextual AI payload parsing &amp; OWASP Top 10 defense.
                  </p>
                  <div className="pt-2 border-t border-border grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Agent / Envoy</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        {securityStatus?.openappsec?.agent_running ? 'OK' : 'Down'} /{' '}
                        {securityStatus?.openappsec?.envoy_running ? 'OK' : 'Down'}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Shadow Port</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        :{securityStatus?.openappsec?.shadow_port || 18081}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/60 flex items-center justify-between gap-2 flex-wrap">
                  <span className="text-[11px] text-muted-foreground uppercase tracking-wider">Policy Mode</span>
                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      className="min-h-11 sm:min-h-8 text-xs"
                      disabled={domainUpdating === 'openappsec-enabled'}
                      onClick={onOpenAppSecToggle}
                    >
                      {domainUpdating === 'openappsec-enabled' ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : securityStatus?.openappsec?.enabled ? (
                        'Disable'
                      ) : (
                        'Enable'
                      )}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="min-h-11 sm:min-h-8 text-xs"
                      disabled={domainUpdating?.startsWith('openappsec-mode-') || !securityStatus?.openappsec?.enabled}
                      onClick={onOpenAppSecModeSwitch}
                      title={securityStatus?.openappsec?.enabled ? undefined : 'Enable the stack first'}
                    >
                      {domainUpdating?.startsWith('openappsec-mode-') ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (securityStatus?.openappsec?.mode_configured || 'prevent') === 'prevent' ? (
                        'Demote to shadow'
                      ) : (
                        'Promote to prevent'
                      )}
                    </Button>
                  </div>
                </div>
              </div>

              {/* Coraza WAF (Caddy) */}
              <div
                className="card-enterprise status-indicator rounded-lg p-4 flex flex-col justify-between gap-3"
                data-status={securityStatus?.coraza?.module_loaded ? 'healthy' : 'critical'}
              >
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                      <Filter className="w-4 h-4 text-orange-700 dark:text-orange-400" aria-hidden="true" />
                      <span>Coraza WAF (Caddy)</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn('text-xs px-2 py-0.5', securityStatus?.coraza?.module_loaded ? STATUS_OK_BADGE : STATUS_CRIT_BADGE)}
                    >
                      {securityStatus?.coraza?.module_loaded ? 'Enforcing' : 'Missing'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    OWASP CRS inspection embedded in Caddy. Per-service opt-out only — fail-closed by design.
                  </p>
                  <div className="pt-2 border-t border-border grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Protected / Opted-out</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        {securityStatus?.coraza?.services_protected ?? 0} / {securityStatus?.coraza?.services_opted_out ?? 0}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Edge-JWT Gated</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        {securityStatus?.coraza?.edge_jwt_gated ?? 0} services
                      </span>
                    </div>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/60 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span className="font-mono tabular-nums uppercase text-[11px]">
                    {securityStatus?.coraza?.site_imports ?? 0} site imports
                  </span>
                  <span>Per-service Scaling settings</span>
                </div>
              </div>

              {/* Cloudflare Edge Bouncer */}
              <div
                className="card-enterprise status-indicator rounded-lg p-4 flex flex-col justify-between gap-3"
                data-status={
                  !securityStatus?.cf_bouncer?.enabled
                    ? 'offline'
                    : securityStatus?.cf_bouncer?.running && securityStatus?.cf_bouncer?.stale === false
                      ? 'healthy'
                      : securityStatus?.cf_bouncer?.running
                        ? 'degraded'
                        : 'critical'
                }
              >
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                      <Globe className="w-4 h-4 text-sky-700 dark:text-sky-400" aria-hidden="true" />
                      <span>Cloudflare Edge Bouncer</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn(
                        'text-xs px-2 py-0.5',
                        !securityStatus?.cf_bouncer?.enabled
                          ? STATUS_OFF_BADGE
                          : securityStatus?.cf_bouncer?.running && securityStatus?.cf_bouncer?.stale === false
                            ? STATUS_OK_BADGE
                            : securityStatus?.cf_bouncer?.running
                              ? STATUS_WARN_BADGE
                              : STATUS_CRIT_BADGE
                      )}
                    >
                      {!securityStatus?.cf_bouncer?.enabled
                        ? 'Off'
                        : securityStatus?.cf_bouncer?.running && securityStatus?.cf_bouncer?.stale === false
                          ? 'Enforcing'
                          : securityStatus?.cf_bouncer?.running
                            ? 'Degraded'
                            : 'Down'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Syncs LAPI decision stream to Cloudflare Rulesets API v4 custom WAF lists.
                  </p>
                  <div className="pt-2 border-t border-border grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Last LAPI Pull</span>
                      <span className="font-mono tabular-nums font-medium text-foreground block truncate" title={securityStatus?.cf_bouncer?.last_pull || ''}>
                        {securityStatus?.cf_bouncer?.last_pull
                          ? new Date(securityStatus.cf_bouncer.last_pull).toLocaleTimeString()
                          : 'never'}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Edge List &amp; Zones</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        {securityStatus?.cf_bouncer?.synced_ips !== undefined && securityStatus?.cf_bouncer?.synced_ips !== null
                          ? `${securityStatus.cf_bouncer.synced_ips} IPs / ${securityStatus.cf_bouncer.zones ?? 0} zones`
                          : `${securityEvents?.summary?.crowdsec_bans_count ?? 0} shared`}
                      </span>
                    </div>
                  </div>
                  {securityStatus?.cf_bouncer?.recent_error && (
                    <p className="text-xs font-mono text-amber-700 dark:text-amber-400 break-all leading-relaxed">
                      {securityStatus.cf_bouncer.recent_error}
                    </p>
                  )}
                </div>
                <div className="pt-2 border-t border-border/60 flex items-center justify-between gap-2">
                  <span className="text-[11px] text-muted-foreground uppercase tracking-wider font-mono truncate">
                    {securityStatus?.cf_bouncer?.list_id ? `List ${securityStatus.cf_bouncer.list_id.slice(0, 8)}...` : 'Account Token'}
                  </span>
                  <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8 text-xs" asChild>
                    <Link href="/settings">Configure</Link>
                  </Button>
                </div>
              </div>
            </div>
          </div>

          {/* TIER B: HOST, IPS & KERNEL RUNTIME (4 UNIFORM COLUMNS) */}
          <div className="space-y-2.5">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Layer II — Host IPS, Kernel eBPF &amp; Sandboxing
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
              {/* CrowdSec IPS */}
              <div
                className="card-enterprise status-indicator rounded-lg p-4 flex flex-col justify-between gap-3"
                data-status={securityStatus?.crowdsec?.running ? 'healthy' : 'offline'}
              >
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                      <Shield className="w-4 h-4 text-purple-700 dark:text-purple-400" aria-hidden="true" />
                      <span>CrowdSec IPS</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn('text-xs px-2 py-0.5', securityStatus?.crowdsec?.running ? STATUS_OK_BADGE : STATUS_OFF_BADGE)}
                    >
                      {securityStatus?.crowdsec?.running ? 'Armed' : 'Disabled'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Behavioral First-Strike IPS + shared crowd intelligence bouncers.
                  </p>
                  <div className="pt-2 border-t border-border grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Active Decisions</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        {securityEvents?.summary?.crowdsec_bans_count ?? securityStatus?.crowdsec?.active_bans ?? 0} banned
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">First-Strike</span>
                      <span className="font-mono font-medium text-foreground">
                        {securityStatus?.crowdsec?.first_strike_enabled ? 'Active' : 'Standard'}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/60 flex items-center justify-between">
                  <span className="text-[11px] text-muted-foreground uppercase tracking-wider">Control</span>
                  <Button
                    variant="outline"
                    size="sm"
                    className="min-h-11 sm:min-h-8 text-xs"
                    disabled={domainUpdating === 'crowdsec-enabled'}
                    onClick={onCrowdSecToggle}
                  >
                    {domainUpdating === 'crowdsec-enabled' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : securityStatus?.crowdsec?.enabled ? (
                      'Disable IPS'
                    ) : (
                      'Enable IPS'
                    )}
                  </Button>
                </div>
              </div>

              {/* Fail2ban Host Defense */}
              <div
                className="card-enterprise status-indicator rounded-lg p-4 flex flex-col justify-between gap-3"
                data-status={securityStatus?.fail2ban?.active ? 'healthy' : 'offline'}
              >
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                      <Ban className="w-4 h-4 text-amber-700 dark:text-amber-400" aria-hidden="true" />
                      <span>Fail2ban Defense</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn('text-xs px-2 py-0.5', securityStatus?.fail2ban?.active ? STATUS_OK_BADGE : STATUS_OFF_BADGE)}
                    >
                      {securityStatus?.fail2ban?.active ? 'Active' : 'Inactive'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Brute-force mitigation across SSH, auth endpoints, &amp; host DoS limits.
                  </p>
                  <div className="pt-2 border-t border-border grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Jails</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        {securityStatus?.fail2ban?.jails?.length || 0} active
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Banned</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        {securityEvents?.summary?.fail2ban_banned_count || 0} IPs
                      </span>
                    </div>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/60 text-xs text-muted-foreground font-mono truncate">
                  {securityStatus?.fail2ban?.jails?.length
                    ? `Jails: ${securityStatus.fail2ban.jails.join(', ')}`
                    : 'Host jail enforcement active'}
                </div>
              </div>

              {/* Falco Kernel eBPF */}
              <div
                className="card-enterprise status-indicator rounded-lg p-4 flex flex-col justify-between gap-3"
                data-status={securityStatus?.falco?.running ? 'healthy' : 'offline'}
              >
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                      <ShieldAlert className="w-4 h-4 text-blue-700 dark:text-blue-400" aria-hidden="true" />
                      <span>Falco Kernel eBPF</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn('text-xs px-2 py-0.5', securityStatus?.falco?.running ? STATUS_OK_BADGE : STATUS_OFF_BADGE)}
                    >
                      {securityStatus?.falco?.running ? 'Active' : 'Offline'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Real-time kernel syscall tracing, shell spawn &amp; anomaly alerting.
                  </p>
                  <div className="pt-2 border-t border-border grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Driver</span>
                      <span className="font-mono font-medium text-foreground">
                        {securityStatus?.falco?.driver || 'modern_ebpf'}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Events</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        {securityStatus?.falco?.events_detected ?? securityEvents?.summary?.falco_alerts_count ?? 0}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/60 text-xs text-muted-foreground font-mono flex justify-between">
                  <span>Syscall sensor</span>
                  <span>{securityStatus?.ufw?.active ? 'UFW + auditd ON' : 'Host monitor'}</span>
                </div>
              </div>

              {/* Sandboxed Runtime */}
              <div
                className="card-enterprise status-indicator rounded-lg p-4 flex flex-col justify-between gap-3"
                data-status={securityStatus?.container_runtime?.sandboxed ? 'healthy' : 'degraded'}
              >
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                      <Cpu className="w-4 h-4 text-emerald-700 dark:text-emerald-400" aria-hidden="true" />
                      <span>Sandboxed Runtime</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn(
                        'text-xs px-2 py-0.5 font-mono',
                        securityStatus?.container_runtime?.sandboxed ? STATUS_OK_BADGE : STATUS_WARN_BADGE
                      )}
                    >
                      {securityStatus?.container_runtime?.active || 'runc'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    MicroVM / user-space gVisor syscall boundary &amp; kernel namespace hardening.
                  </p>
                  <div className="pt-2 border-t border-border grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">AppArmor / Seccomp</span>
                      <span className="font-mono font-medium text-foreground">
                        {securityStatus?.apparmor?.enabled ? 'Active' : 'Off'} /{' '}
                        {securityStatus?.seccomp?.enabled ? 'Active' : 'Off'}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Kernel Isolation</span>
                      <span className="font-mono font-medium text-foreground">
                        {securityStatus?.kernel_hardening?.enabled ? 'Hardened' : 'Standard'}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/60 text-xs text-muted-foreground font-mono flex justify-between">
                  <span>Socket Proxy: {securityStatus?.docker_socket_proxy?.enabled ? 'ON' : 'OFF'}</span>
                  <span>no-new-privs: {securityStatus?.no_new_privileges?.enabled ? 'ON' : 'OFF'}</span>
                </div>
              </div>
            </div>
          </div>

          {/* TIER C: SUPPLY CHAIN, CUSTODY & AEGS POLICY GATES */}
          <div className="space-y-2.5">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Layer III — Supply Chain, Cryptographic Custody &amp; Policy Gates
            </h3>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* Trivy CVE, Cosign & Device Trust */}
              <div
                className="card-enterprise status-indicator rounded-lg p-4 flex flex-col justify-between gap-3"
                data-status={securityStatus?.trivy?.enabled ? 'healthy' : 'offline'}
              >
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                      <Bug className="w-4 h-4 text-rose-700 dark:text-rose-400" aria-hidden="true" />
                      <span>Trivy CVE, Cosign &amp; Device Trust</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn('text-xs px-2 py-0.5', securityStatus?.trivy?.enabled ? STATUS_OK_BADGE : STATUS_OFF_BADGE)}
                    >
                      {securityStatus?.trivy?.enabled ? 'Enforced' : 'Inactive'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Build-time container CVE vulnerability gates, SLSA Cosign image signatures &amp; WebAuthn device trust.
                  </p>
                  <div className="pt-2 border-t border-border grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Gate Policy</span>
                      <span className="font-mono font-medium text-foreground">
                        {securityStatus?.trivy?.fail_on_severity || 'CRITICAL'}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Cosign Signing</span>
                      <span className="font-mono font-medium text-foreground">
                        {cosignOn ? 'Enforced' : 'Disabled'}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Device Trust</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        {securityStatus?.device_trust?.enabled
                          ? `MFA (${securityStatus.device_trust.registered_devices ?? 0})`
                          : 'Standard'}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="pt-2 border-t border-border/60 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <label htmlFor="trivy-cve-gate-select" className="text-[11px] text-muted-foreground uppercase tracking-wider">
                      CVE Gate
                    </label>
                    <select
                      id="trivy-cve-gate-select"
                      aria-label="Trivy CVE gate severity threshold"
                      className="min-h-11 sm:min-h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      value={(securityStatus?.trivy?.fail_on_severity || 'CRITICAL').split(',')[0]}
                      disabled={domainUpdating === 'trivy-severity' || !securityStatus?.trivy?.enabled}
                      onChange={(e) => onTrivySeverityChange(e.target.value)}
                    >
                      {['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].map((sev) => (
                        <option key={sev} value={sev}>
                          {sev}+
                        </option>
                      ))}
                    </select>
                    <Button
                      variant="outline"
                      size="sm"
                      className="min-h-11 sm:min-h-8 text-xs"
                      disabled={domainUpdating === 'trivy-enabled'}
                      onClick={onTrivyToggle}
                    >
                      {domainUpdating === 'trivy-enabled' ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : securityStatus?.trivy?.enabled ? (
                        'Disable Trivy'
                      ) : (
                        'Enable Trivy'
                      )}
                    </Button>
                  </div>

                  <Button
                    variant="outline"
                    size="sm"
                    className="min-h-11 sm:min-h-8 text-xs"
                    disabled={domainUpdating === 'device-trust'}
                    onClick={onDeviceTrustToggle}
                  >
                    {domainUpdating === 'device-trust' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : securityStatus?.device_trust?.enabled ? (
                      'Disable Device Trust'
                    ) : (
                      'Enforce Device Trust (Beta)'
                    )}
                  </Button>
                </div>
              </div>

              {/* grid-cryptd Vault & LUKS2 */}
              <div
                className="card-enterprise status-indicator rounded-lg p-4 flex flex-col justify-between gap-3"
                data-status={cryptdEnabled && cryptdState === 'ACTIVE' ? 'healthy' : 'offline'}
              >
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                      <Lock className="w-4 h-4 text-cyan-700 dark:text-cyan-400" aria-hidden="true" />
                      <span>grid-cryptd Vault &amp; LUKS2</span>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn(
                        'text-xs px-2 py-0.5',
                        cryptdEnabled && cryptdState === 'ACTIVE' ? STATUS_OK_BADGE : STATUS_OFF_BADGE
                      )}
                    >
                      {cryptdEnabled ? cryptdState : 'Standby'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Hierarchical MK → weekly SMK → per-service SEK custody with point-of-contact DB/Redis &amp; LUKS2 locking.
                  </p>
                  <div className="pt-2 border-t border-border grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Epoch / Key Version</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        Wk {cryptdWeek} ({securityStatus?.cryptd?.key_version || overview?.cryptd_status?.key_version || 'mk-v1'})
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground block text-[11px] uppercase">Active Service Leases</span>
                      <span className="font-mono tabular-nums font-medium text-foreground">
                        {cryptdLeases} / {cryptdOpted} unlocked
                      </span>
                    </div>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/60 flex items-center justify-between text-xs">
                  <span className="text-[11px] text-muted-foreground uppercase tracking-wider font-mono">
                    Provider: {securityStatus?.cryptd?.provider || overview?.cryptd_status?.provider || 'http_vault'}
                  </span>
                  <span className="text-xs text-emerald-700 dark:text-emerald-400 font-mono">
                    mlock2 + zero-on-lock
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Aegis Enforcement Gates Strip (Admin Policy Switches) */}
          {overview?.compliance_frameworks !== undefined && overview?.policy && (
            <Card className="border-border bg-card">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-emerald-700 dark:text-emerald-400" aria-hidden="true" />
                  Aegis Pipeline Enforcement Gates
                </CardTitle>
                <CardDescription className="text-xs">
                  Real-time CI/CD &amp; runtime policy enforcement toggles across all tenant deployments.
                </CardDescription>
              </CardHeader>
              <CardContent className="p-0 border-t border-border">
                <div className="grid-enterprise grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
                  {(
                    [
                      ['aspm_block_on_secrets', 'Block on Secrets', 'Reject builds with leaked credentials or keys'],
                      ['aspm_block_on_critical_sast', 'Block on Critical SAST', 'Halt deploy on critical code flaws'],
                      ['aspm_block_on_critical_iac', 'Block on Critical IaC', 'Block privileged Docker/Compose manifests'],
                      ['dast_on_deploy_enabled', 'Post-Deploy DAST', 'Run active HTTP surface probe after promote'],
                      ['ai_prompt_firewall_enabled', 'AI-SPM Firewall', 'Redact secrets before outbound LLM calls'],
                      ['auto_quarantine_on_critical_runtime', 'Auto-Quarantine Runtime', 'Contain service on critical XDR alert'],
                    ] as const
                  ).map(([key, label, hint]) => {
                    const enabled = Boolean(overview.policy[key]);
                    const inputId = `gate-switch-${key}`;
                    return (
                      <div key={key} className="flex items-center justify-between gap-3">
                        <label htmlFor={inputId} className="cursor-pointer min-w-0">
                          <div className="text-xs font-semibold text-foreground">{label}</div>
                          <div className="text-xs text-muted-foreground truncate">{hint}</div>
                        </label>
                        <Switch
                          id={inputId}
                          checked={enabled}
                          disabled={policyBusy === key}
                          onCheckedChange={() => handleTogglePolicy(key, enabled)}
                        />
                      </div>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════════
          SUB-TAB 2: LIVE THREATS & WAF STREAMS
         ═══════════════════════════════════════════════════════════════ */}
      {subTab === 'streams' && (
        <Card className="border-border bg-card" role="tabpanel">
          <CardHeader className="pb-3 space-y-3 border-b border-border">
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
              <div>
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  <Activity className="w-4 h-4 text-emerald-700 dark:text-emerald-400" aria-hidden="true" />
                  Unified Security Telemetry &amp; Audit Streams
                  {securityLoading && (
                    <span className="text-xs font-normal text-muted-foreground inline-flex items-center gap-1">
                      <Loader2 className="w-3.5 h-3.5 animate-spin" /> Refreshing...
                    </span>
                  )}
                </CardTitle>
                <CardDescription className="text-xs">
                  Switch between raw real-time defense engine events and normalized OCSF v1.1.0 detection logs.
                </CardDescription>
              </div>

              {/* Stream Mode Switcher */}
              <div className="inline-flex rounded-lg border border-border bg-muted/40 p-1 self-start lg:self-auto">
                <button
                  type="button"
                  onClick={() => setStreamMode('live')}
                  className={cn(
                    'px-3 py-1.5 min-h-9 rounded-md text-xs font-medium transition-colors font-mono tabular-nums',
                    streamMode === 'live'
                      ? 'bg-background text-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  Live Defense Feed ({securityEvents?.summary?.total_events || 0})
                </button>
                <button
                  type="button"
                  onClick={() => setStreamMode('ocsf')}
                  className={cn(
                    'px-3 py-1.5 min-h-9 rounded-md text-xs font-medium transition-colors font-mono tabular-nums',
                    streamMode === 'ocsf'
                      ? 'bg-background text-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  OCSF v1.1.0 ({ocsfEvents.length} / {overview?.ocsf_events_count ?? ocsfEvents.length})
                </button>
              </div>
            </div>

            {/* Global Clickable Count Strip (Live mode) */}
            {streamMode === 'live' && (
              <div className="flex flex-wrap items-center gap-1.5 text-xs font-mono tabular-nums">
                <button
                  type="button"
                  onClick={() => {
                    onChangeSourceFilter('all');
                    onRefreshSecurity(undefined);
                  }}
                  className="px-2.5 py-1 rounded-md border border-border bg-muted/40 text-foreground hover:bg-muted transition-colors"
                >
                  Total: {securityEvents?.summary?.total_events || 0}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    onChangeSourceFilter('falco');
                    onRefreshSecurity('falco');
                  }}
                  className={cn('px-2.5 py-1 rounded-md border transition-colors', SOURCE_CHIP.falco)}
                >
                  Falco: {securityEvents?.summary?.falco_alerts_count || 0}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    onChangeSourceFilter('crowdsec');
                    onRefreshSecurity('crowdsec');
                  }}
                  className={cn('px-2.5 py-1 rounded-md border transition-colors', SOURCE_CHIP.crowdsec)}
                >
                  CrowdSec: {securityEvents?.summary?.crowdsec_bans_count || 0}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    onChangeSourceFilter('fail2ban');
                    onRefreshSecurity('fail2ban');
                  }}
                  className={cn('px-2.5 py-1 rounded-md border transition-colors', SOURCE_CHIP.fail2ban)}
                >
                  Fail2ban: {securityEvents?.summary?.fail2ban_banned_count || 0}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    onChangeSourceFilter('openappsec');
                    onRefreshSecurity('openappsec');
                  }}
                  className={cn('px-2.5 py-1 rounded-md border transition-colors', SOURCE_CHIP.openappsec)}
                >
                  WAF: {securityEvents?.summary?.waf_events_count || 0}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    onChangeSourceFilter('coraza');
                    onRefreshSecurity('coraza');
                  }}
                  className={cn('px-2.5 py-1 rounded-md border transition-colors', SOURCE_CHIP.coraza)}
                >
                  Coraza: {securityEvents?.summary?.coraza_events_count ?? 0}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    onChangeSourceFilter('trivy');
                    onRefreshSecurity('trivy');
                  }}
                  className={cn('px-2.5 py-1 rounded-md border transition-colors', SOURCE_CHIP.trivy)}
                >
                  CVEs: {securityEvents?.summary?.trivy_cves_count || 0}
                </button>
              </div>
            )}

            {/* 2-Row Filter Bar */}
            <div className="space-y-2.5 pt-1">
              <div className="flex flex-col sm:flex-row gap-2.5">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  <Input
                    aria-label="Search security telemetry events by title, IP address, container, host, or rule"
                    placeholder="Search by event title, IP address, host, MITRE technique, or rule..."
                    value={securitySearchQuery}
                    onChange={(e) => onChangeSearchQuery(e.target.value)}
                    className="pl-9 min-h-11 sm:min-h-9 text-xs"
                  />
                </div>
                <div className="flex items-center gap-1 flex-wrap">
                  {(['all', 'CRITICAL', 'HIGH', 'WARNING', 'INFO'] as const).map((sev) => {
                    const active =
                      streamMode === 'live'
                        ? securitySeverityFilter === sev
                        : (ocsfSeverityFilter === 'ALL' ? 'all' : ocsfSeverityFilter) === sev;
                    return (
                      <Button
                        key={sev}
                        variant={active ? 'secondary' : 'outline'}
                        size="sm"
                        className="min-h-11 sm:min-h-9 text-xs px-2.5"
                        onClick={() => {
                          if (streamMode === 'live') {
                            onChangeSeverityFilter(sev);
                          } else {
                            setOcsfSeverityFilter(sev === 'all' ? 'ALL' : sev === 'WARNING' ? 'MEDIUM' : sev);
                          }
                        }}
                      >
                        {sev === 'all' ? 'All Sev' : sev}
                      </Button>
                    );
                  })}
                </div>
              </div>

              {streamMode === 'live' ? (
                <div className="flex flex-wrap gap-1.5">
                  {[
                    { id: 'all', label: 'All Sources' },
                    { id: 'falco', label: 'Falco' },
                    { id: 'crowdsec', label: 'CrowdSec' },
                    { id: 'fail2ban', label: 'Fail2ban' },
                    { id: 'openappsec', label: 'open-appsec' },
                    { id: 'coraza', label: 'Coraza WAF' },
                    { id: 'trivy', label: 'Trivy' },
                    { id: 'auditd', label: 'Auditd / Cryptd' },
                  ].map((tab) => (
                    <Button
                      key={tab.id}
                      variant={securitySourceFilter === tab.id ? 'secondary' : 'outline'}
                      size="sm"
                      className="min-h-11 sm:min-h-8 text-xs px-2.5"
                      onClick={() => {
                        onChangeSourceFilter(tab.id);
                        onRefreshSecurity(tab.id !== 'all' ? tab.id : undefined);
                      }}
                    >
                      {tab.label}
                    </Button>
                  ))}
                </div>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {['ALL', 'openappsec', 'coraza', 'crowdsec', 'falco', 'fail2ban', 'image_cve', 'dspm', 'secrets', 'aspm', 'auditd'].map(
                    (src) => (
                      <Button
                        key={src}
                        size="sm"
                        variant={ocsfSourceFilter === src ? 'secondary' : 'outline'}
                        className="min-h-11 sm:min-h-8 px-2.5 text-xs font-mono"
                        onClick={() => setOcsfSourceFilter(src)}
                      >
                        {src}
                      </Button>
                    )
                  )}
                </div>
              )}
            </div>
          </CardHeader>

          <CardContent className="p-0">
            {streamMode === 'live' ? (
              pagedLiveEvents.length === 0 ? (
                <div className="text-center py-10 px-4 text-muted-foreground text-sm">
                  <ShieldCheck className="h-8 w-8 mx-auto mb-2 text-emerald-700 dark:text-emerald-400 opacity-60" aria-hidden="true" />
                  <p>No security activity events matching current filters.</p>
                  <p className="text-xs text-muted-foreground mt-0.5">All monitored security layers are operating normally.</p>
                </div>
              ) : (
                <div className="divide-y divide-border">
                  {pagedLiveEvents.map((evt) => {
                    const isExpanded = expandedEventId === evt.id;
                    const isCrowdSecBan = evt.source === 'crowdsec' && evt.type === 'ban';
                    const isF2bBan = evt.source === 'fail2ban' && (evt.type === 'ban' || evt.type === 'restore_ban');
                    const targetIp = evt.target && /^[\d\.:a-fA-F]+$/.test(evt.target) ? evt.target : null;
                    const f2bJail = (() => {
                      const raw = evt.raw as any;
                      if (raw && typeof raw === 'object' && typeof raw.jail === 'string' && raw.jail) return raw.jail;
                      const m = /Jail:\s*(\S+)/.exec(evt.details || '');
                      return m ? m[1] : 'sshd';
                    })();

                    return (
                      <div key={evt.id} className="p-3.5 sm:px-5 hover:bg-muted/20 transition-colors text-xs space-y-1.5">
                        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                          <div className="flex items-center gap-2 flex-wrap">
                            <Badge variant="outline" className={cn('text-[11px] font-semibold uppercase', SEV_BADGE[evt.severity] || '')}>
                              {evt.severity}
                            </Badge>
                            <Badge variant="outline" className={cn('text-[11px] uppercase font-mono', SOURCE_CHIP[evt.source] || '')}>
                              {evt.source}
                            </Badge>
                            <span className="font-semibold text-foreground">{evt.title}</span>
                            {evt.target && (
                              <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-mono text-[11px]">
                                target: {evt.target}
                              </span>
                            )}
                          </div>

                          <div className="flex items-center gap-1.5 shrink-0 text-muted-foreground">
                            <span className="font-mono tabular-nums text-[11px]">
                              {new Date(evt.timestamp).toLocaleString()}
                            </span>
                            {targetIp && isCrowdSecBan && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="min-h-11 sm:min-h-7 px-2 text-xs text-red-700 dark:text-red-400 hover:bg-red-500/10"
                                disabled={unbanningIp === targetIp}
                                onClick={() => onUnbanCrowdSec(targetIp)}
                              >
                                {unbanningIp === targetIp ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Ban className="w-3.5 h-3.5 mr-1" />}
                                Unban IP
                              </Button>
                            )}
                            {targetIp && isF2bBan && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="min-h-11 sm:min-h-7 px-2 text-xs text-amber-700 dark:text-amber-400 hover:bg-amber-500/10"
                                disabled={unbanningF2bIp === targetIp}
                                onClick={() => onUnbanFail2ban(targetIp, f2bJail)}
                                title={`Unban from fail2ban jail ${f2bJail}`}
                              >
                                {unbanningF2bIp === targetIp ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Ban className="w-3.5 h-3.5 mr-1" />}
                                Unban IP
                              </Button>
                            )}
                            <Button
                              variant="ghost"
                              size="sm"
                              aria-label="Toggle raw event payload"
                              aria-expanded={isExpanded}
                              className="min-h-11 sm:min-h-7 px-2 text-xs"
                              onClick={() => setExpandedEventId(isExpanded ? null : evt.id)}
                            >
                              {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                            </Button>
                          </div>
                        </div>

                        <div className="text-muted-foreground break-words font-mono text-xs">
                          {evt.details}
                        </div>

                        {isExpanded && (
                          <div className="mt-2 pt-2 border-t border-border/50 space-y-1.5">
                            <div className="flex items-center justify-between">
                              <span className="text-[11px] uppercase font-semibold text-muted-foreground flex items-center gap-1">
                                <Terminal className="w-3.5 h-3.5" aria-hidden="true" /> Raw Event Payload
                              </span>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="min-h-9 sm:min-h-7 px-2 text-xs"
                                onClick={() => copyJson(evt.id, evt.raw || evt)}
                              >
                                {copiedId === evt.id ? (
                                  <>
                                    <Check className="w-3.5 h-3.5 text-emerald-700 dark:text-emerald-400 mr-1" /> Copied
                                  </>
                                ) : (
                                  <>
                                    <Copy className="w-3.5 h-3.5 mr-1" /> Copy JSON
                                  </>
                                )}
                              </Button>
                            </div>
                            <pre className="p-3 rounded-md bg-slate-950 text-slate-100 border border-border text-xs font-mono overflow-x-auto max-h-60">
                              {JSON.stringify(evt.raw || evt, null, 2)}
                            </pre>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )
            ) : pagedOcsfEvents.length === 0 ? (
              <div className="text-center py-10 px-4 text-muted-foreground text-sm">
                No normalized OCSF telemetry events matching source filter &ldquo;{ocsfSourceFilter}&rdquo;.
              </div>
            ) : (
              <div className="divide-y divide-border">
                {pagedOcsfEvents.map((ev) => {
                  const isExp = expandedOcsfId === ev.id;
                  return (
                    <div key={ev.id} className="p-3.5 sm:px-5 hover:bg-muted/20 transition-colors text-xs space-y-1.5">
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge variant="outline" className={cn('text-[11px]', SEV_BADGE[ev.severity] || '')}>
                            {ev.severity}
                          </Badge>
                          <Badge variant="outline" className={cn('text-[11px] font-mono uppercase', SOURCE_CHIP[ev.source] || '')}>
                            {ev.source}
                          </Badge>
                          {ev.mitre_technique && (
                            <Badge variant="outline" className="text-[11px] font-mono">
                              {ev.mitre_technique}
                            </Badge>
                          )}
                          <span className="font-semibold text-foreground">{ev.title}</span>
                          {ev.target && (
                            <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-mono text-[11px]">
                              target: {ev.target}
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2 text-muted-foreground shrink-0">
                          <span className="font-mono tabular-nums text-[11px]">
                            {ev.occurred_at ? new Date(ev.occurred_at).toLocaleString() : ''}
                          </span>
                          <Button
                            size="sm"
                            variant="ghost"
                            aria-label="Toggle OCSF JSON payload"
                            aria-expanded={isExp}
                            className="min-h-11 sm:min-h-7 px-2 text-xs"
                            onClick={() => setExpandedOcsfId(isExp ? null : ev.id)}
                          >
                            {isExp ? 'Hide OCSF' : 'OCSF JSON'}
                          </Button>
                        </div>
                      </div>
                      {ev.details && <div className="text-xs text-muted-foreground font-mono break-words">{ev.details}</div>}
                      {isExp && (
                        <div className="mt-2 pt-2 border-t border-border/50 space-y-1.5">
                          <div className="flex items-center justify-between">
                            <span className="text-[11px] uppercase font-semibold text-muted-foreground">
                              OCSF v1.1.0 Envelope
                            </span>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="min-h-9 sm:min-h-7 px-2 text-xs"
                              onClick={() => copyJson(`ocsf-${ev.id}`, ev.raw_ocsf || ev)}
                            >
                              {copiedId === `ocsf-${ev.id}` ? (
                                <>
                                  <Check className="w-3.5 h-3.5 text-emerald-700 dark:text-emerald-400 mr-1" /> Copied
                                </>
                              ) : (
                                <>
                                  <Copy className="w-3.5 h-3.5 mr-1" /> Copy JSON
                                </>
                              )}
                            </Button>
                          </div>
                          <pre className="p-3 rounded-md bg-slate-950 text-slate-100 border border-border text-xs font-mono overflow-x-auto max-h-60">
                            {JSON.stringify(ev.raw_ocsf || ev, null, 2)}
                          </pre>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Pagination Footer */}
            {((streamMode === 'live' && filteredLiveEvents.length > PAGE_SIZE) ||
              (streamMode === 'ocsf' && filteredOcsfEvents.length > PAGE_SIZE)) && (
              <div className="px-4 py-3 border-t border-border flex items-center justify-between gap-3 text-xs text-muted-foreground font-mono tabular-nums">
                <span>
                  {streamMode === 'live'
                    ? `Page ${safeLivePage} of ${liveTotalPages} (${filteredLiveEvents.length} events)`
                    : `Page ${safeOcsfPage} of ${ocsfTotalPages} (${filteredOcsfEvents.length} OCSF events)`}
                </span>
                <div className="flex items-center gap-1.5">
                  <Button
                    variant="outline"
                    size="sm"
                    className="min-h-10 sm:min-h-8"
                    disabled={streamMode === 'live' ? safeLivePage <= 1 : safeOcsfPage <= 1}
                    onClick={() =>
                      streamMode === 'live'
                        ? setStreamPage((p) => Math.max(1, p - 1))
                        : setOcsfPage((p) => Math.max(1, p - 1))
                    }
                  >
                    <ChevronLeft className="w-3.5 h-3.5 mr-1" /> Prev
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="min-h-10 sm:min-h-8"
                    disabled={streamMode === 'live' ? safeLivePage >= liveTotalPages : safeOcsfPage >= ocsfTotalPages}
                    onClick={() =>
                      streamMode === 'live'
                        ? setStreamPage((p) => Math.min(liveTotalPages, p + 1))
                        : setOcsfPage((p) => Math.min(ocsfTotalPages, p + 1))
                    }
                  >
                    Next <ChevronRight className="w-3.5 h-3.5 ml-1" />
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* ═══════════════════════════════════════════════════════════════
          SUB-TAB 3: FINDINGS, AI HUNTER & SOAR
         ═══════════════════════════════════════════════════════════════ */}
      {subTab === 'findings' && (
        <div className="space-y-6" role="tabpanel">
          {/* AI-SPM Guarded Threat Hunter */}
          <Card className="border-border bg-card">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-purple-700 dark:text-purple-400" aria-hidden="true" />
                SMSLY Aegis AI Threat Hunter (Protected by AI-SPM Prompt Firewall)
              </CardTitle>
              <CardDescription className="text-xs">
                Query posture findings, exposed addons, and encrypted custody across services with automatic secret redaction.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-col sm:flex-row gap-2">
                <Input
                  aria-label="Ask AI Threat Hunter a security question"
                  placeholder="Ask e.g. 'Which public services have critical findings or unencrypted Postgres addons?'"
                  value={huntQuery}
                  onChange={(e) => setHuntQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleHunt()}
                  className="text-xs min-h-11 sm:min-h-9"
                />
                <Button
                  size="sm"
                  onClick={handleHunt}
                  disabled={hunting || !huntQuery.trim()}
                  className="min-h-11 sm:min-h-9 px-4 shrink-0"
                >
                  {hunting ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Hunt'}
                </Button>
              </div>
              {huntResult && (
                <div className="p-3.5 rounded-lg bg-muted/30 border-l-2 border-purple-500 text-xs sm:text-[13px] space-y-1.5">
                  <p className="text-foreground whitespace-pre-wrap leading-relaxed max-w-[75ch]">
                    {huntResult.answer}
                  </p>
                  {Array.isArray(huntResult.redacted_types) && huntResult.redacted_types.length > 0 && (
                    <p className="text-xs text-amber-700 dark:text-amber-400 font-mono">
                      AI-SPM Firewall redacted sensitive tokens before LLM call: {huntResult.redacted_types.join(', ')}
                    </p>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Complete Toxic Attack Paths */}
          {toxicList.length > 0 && (
            <Card className="border-red-500/30 bg-card">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-semibold text-red-700 dark:text-red-400 flex items-center gap-2">
                  <GitFork className="w-4 h-4" aria-hidden="true" />
                  Toxic Attack Paths ({toxicList.length})
                </CardTitle>
              </CardHeader>
              <CardContent className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {toxicList.map((tc) => (
                  <div key={tc.service_id} className="p-3 rounded-lg border border-red-500/25 bg-red-500/5 text-xs space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono font-semibold text-foreground">{tc.path}</span>
                      <Badge variant="outline" className={cn('text-[11px] font-mono tabular-nums', STATUS_CRIT_BADGE)}>
                        Risk {tc.risk_score}
                      </Badge>
                    </div>
                    <div className="text-muted-foreground">{tc.factors.join(' • ')}</div>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {/* Prioritized Security Findings Ledger */}
          <Card className="border-border bg-card">
            <CardHeader className="pb-3 border-b border-border">
              <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
                <div>
                  <CardTitle className="text-sm font-semibold flex items-center gap-2">
                    <ShieldAlert className="w-4 h-4 text-orange-700 dark:text-orange-400" aria-hidden="true" />
                    Prioritized Security Findings ({findings.length})
                  </CardTitle>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex flex-wrap gap-1">
                    {['OPEN', 'RESOLVED', 'RISK_ACCEPTED', 'ALL'].map((st) => (
                      <Button
                        key={st}
                        size="sm"
                        variant={statusFilter === st ? 'secondary' : 'outline'}
                        className="min-h-11 sm:min-h-8 text-xs px-2.5"
                        onClick={() => setStatusFilter(st)}
                      >
                        {st.replace('_', ' ')}
                      </Button>
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {['ALL', 'ASPM', 'CNAPP', 'XDR'].map((pil) => (
                      <Button
                        key={pil}
                        size="sm"
                        variant={pillarFilter === pil ? 'secondary' : 'outline'}
                        className="min-h-11 sm:min-h-8 text-xs px-2.5 font-mono"
                        onClick={() => setPillarFilter(pil)}
                      >
                        {pil}
                      </Button>
                    ))}
                  </div>
                </div>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              {centerLoading ? (
                <div className="text-xs text-muted-foreground py-8 text-center">Loading findings...</div>
              ) : pagedFindings.length === 0 ? (
                <div className="text-xs text-muted-foreground py-8 text-center flex flex-col items-center gap-1.5">
                  <ShieldCheck className="w-7 h-7 text-emerald-700 dark:text-emerald-400 opacity-60" aria-hidden="true" />
                  <span>No findings matching current filters.</span>
                </div>
              ) : (
                <div className="divide-y divide-border">
                  {pagedFindings.map((f) => (
                    <div key={f.id} className="p-4 hover:bg-muted/20 transition-colors text-xs space-y-1.5">
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge variant="outline" className={cn('text-[11px] font-mono tabular-nums', SEV_BADGE[f.severity] || '')}>
                            {f.severity} (Risk {f.risk_score})
                          </Badge>
                          <Badge variant="outline" className="text-[11px] font-mono">
                            {f.pillar}/{f.category}
                          </Badge>
                          {f.mitre_technique && (
                            <Badge variant="outline" className="text-[11px] font-mono">
                              {f.mitre_technique}
                            </Badge>
                          )}
                          <span className="font-semibold text-foreground">{f.title}</span>
                        </div>
                        <div className="flex items-center gap-1.5 shrink-0 flex-wrap">
                          {f.service_id && f.category === 'DSPM' && (
                            <Button
                              size="sm"
                              variant="outline"
                              className="min-h-11 sm:min-h-8 px-2.5 text-xs"
                              onClick={() => handleSoar('lock_service_cryptd', f.service_id)}
                            >
                              <Lock className="w-3.5 h-3.5 mr-1" /> Lock Cryptd
                            </Button>
                          )}
                          {f.status !== 'OPEN' ? (
                            <Button
                              size="sm"
                              variant="outline"
                              className="min-h-11 sm:min-h-8 px-2.5 text-xs text-amber-700 dark:text-amber-400"
                              onClick={() => handleFindingStatus(f.id, 'OPEN')}
                            >
                              Reopen
                            </Button>
                          ) : (
                            <>
                              <Button
                                size="sm"
                                variant="outline"
                                className="min-h-11 sm:min-h-8 px-2.5 text-xs text-emerald-700 dark:text-emerald-400"
                                onClick={() => handleFindingStatus(f.id, 'RESOLVED')}
                              >
                                Resolve
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="min-h-11 sm:min-h-8 px-2.5 text-xs text-muted-foreground"
                                onClick={() => handleFindingStatus(f.id, 'RISK_ACCEPTED')}
                              >
                                Accept Risk
                              </Button>
                            </>
                          )}
                        </div>
                      </div>
                      <p className="text-muted-foreground max-w-[85ch]">{f.description}</p>
                      {f.remediation && (
                        <p className="text-xs text-emerald-700 dark:text-emerald-400">Fix: {f.remediation}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {findings.length > PAGE_SIZE && (
                <div className="px-4 py-3 border-t border-border flex items-center justify-between gap-3 text-xs text-muted-foreground font-mono tabular-nums">
                  <span>
                    Page {safeFindingsPage} of {findingsTotalPages} ({findings.length} findings)
                  </span>
                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      className="min-h-10 sm:min-h-8"
                      disabled={safeFindingsPage <= 1}
                      onClick={() => setFindingsPage((p) => Math.max(1, p - 1))}
                    >
                      <ChevronLeft className="w-3.5 h-3.5 mr-1" /> Prev
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="min-h-10 sm:min-h-8"
                      disabled={safeFindingsPage >= findingsTotalPages}
                      onClick={() => setFindingsPage((p) => Math.min(findingsTotalPages, p + 1))}
                    >
                      Next <ChevronRight className="w-3.5 h-3.5 ml-1" />
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* SHA-256 DFIR Forensic Snapshots (Zero Silent Drop) */}
          <Card className="border-border bg-card">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <Camera className="w-4 h-4 text-cyan-700 dark:text-cyan-400" aria-hidden="true" />
                  SHA-256 DFIR Forensic Snapshots ({forensics.length})
                </CardTitle>
                {forensics.length > 6 && (
                  <Button
                    size="sm"
                    variant="outline"
                    className="min-h-10 sm:min-h-8 text-xs"
                    onClick={() => setShowAllForensics((v) => !v)}
                  >
                    {showAllForensics ? 'Show fewer' : `Show all (${forensics.length})`}
                  </Button>
                )}
              </div>
            </CardHeader>
            <CardContent>
              {forensics.length === 0 ? (
                <p className="text-xs text-muted-foreground py-4 text-center">
                  No forensic snapshots captured yet.
                </p>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                  {visibleForensics.map((snap) => (
                    <div
                      key={snap.id}
                      className="p-2.5 rounded-md border border-border bg-muted/15 text-xs flex items-center justify-between gap-2"
                    >
                      <div className="truncate">
                        <span className="font-semibold text-foreground">{snap.service_name}</span>{' '}
                        <span className="text-muted-foreground">({snap.trigger})</span>
                      </div>
                      <Badge
                        variant="outline"
                        className="font-mono tabular-nums text-[11px] shrink-0"
                        title={snap.sha256_digest}
                      >
                        SHA256:{snap.sha256_digest.slice(0, 12)}
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* ═══════════════════════════════════════════════════════════════
          SUB-TAB 4: SUPPLY CHAIN, MITRE & GRC COMPLIANCE
         ═══════════════════════════════════════════════════════════════ */}
      {subTab === 'compliance' && (
        <div className="space-y-6" role="tabpanel">
          {/* Continuous GRC Compliance */}
          <Card className="border-border bg-card">
            <CardHeader className="pb-3 border-b border-border">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                  <CardTitle className="text-sm font-semibold flex items-center gap-2">
                    <FileCheck2 className="w-4 h-4 text-emerald-700 dark:text-emerald-400" aria-hidden="true" />
                    Continuous GRC Compliance (SOC 2 • ISO 27001 • NDPR • PCI-DSS)
                  </CardTitle>
                  <CardDescription className="text-xs">
                    Live posture control verification backed by immutable SHA-256 evidence bundles.
                  </CardDescription>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="min-h-11 sm:min-h-8 text-xs self-start sm:self-auto"
                  onClick={handleExportGrc}
                >
                  <Download className="w-3.5 h-3.5 mr-1.5" /> Export Evidence
                </Button>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              {compliance ? (
                <>
                  <div className="grid-enterprise grid-cols-2 sm:grid-cols-4 border-b border-border">
                    {Object.entries(compliance.frameworks).map(([fw, stat]: [string, any]) => {
                      const pct = Number(stat.score_pct ?? 0);
                      const health = pct >= 90 ? 'good' : pct >= 70 ? 'warn' : 'bad';
                      return (
                        <div key={fw} className="space-y-2">
                          <div className="flex items-center justify-between">
                            <span className="text-[11px] uppercase font-bold text-muted-foreground">{fw}</span>
                            <span className="text-xs font-mono tabular-nums text-muted-foreground">
                              {stat.passed}/{stat.controls} pass
                            </span>
                          </div>
                          <div className="text-2xl font-semibold font-mono tabular-nums text-foreground">
                            {pct}%
                          </div>
                          <div className="metric-bar">
                            <div
                              className="metric-bar-fill"
                              data-health={health}
                              style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="divide-y divide-border">
                    {compliance.controls.map((c) => (
                      <div
                        key={`${c.framework}-${c.control_id}`}
                        className="flex flex-col sm:flex-row sm:items-center justify-between text-xs p-3 sm:px-5 gap-2 hover:bg-muted/20"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-mono font-semibold text-foreground">{c.control_id}</span>
                            <span className="font-medium text-foreground">{c.control_name}</span>
                          </div>
                          {c.evidence_summary && (
                            <p className="text-xs text-muted-foreground mt-0.5">{c.evidence_summary}</p>
                          )}
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <span className="font-mono tabular-nums text-muted-foreground">{c.score_pct}%</span>
                          <Badge
                            variant="outline"
                            className={cn(
                              'text-[11px]',
                              c.status === 'PASS'
                                ? STATUS_OK_BADGE
                                : c.status === 'PARTIAL'
                                  ? STATUS_WARN_BADGE
                                  : STATUS_CRIT_BADGE
                            )}
                          >
                            {c.status}
                          </Badge>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <p className="text-xs text-muted-foreground py-6 text-center">
                  Compliance scorecard available to platform administrators.
                </p>
              )}
            </CardContent>
          </Card>

          {/* CycloneDX SBOM & SLSA v1.0 Provenance */}
          <Card className="border-border bg-card">
            <CardHeader className="pb-3 border-b border-border">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                  <Package className="w-4 h-4 text-cyan-700 dark:text-cyan-400" aria-hidden="true" />
                  CycloneDX SBOM &amp; SLSA v1.0 Provenance ({sboms.length})
                </CardTitle>
                <div className="flex items-center gap-2 flex-wrap">
                  <div className="relative w-full sm:w-56">
                    <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
                    <Input
                      aria-label="Search SBOM packages by name"
                      placeholder="Search pkg e.g. django"
                      value={packageSearch}
                      onChange={(e) => setPackageSearch(e.target.value)}
                      className="min-h-11 sm:min-h-8 pl-8 text-xs"
                    />
                  </div>
                  {sboms.length > 10 && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="min-h-11 sm:min-h-8 text-xs"
                      onClick={() => setShowAllSboms((v) => !v)}
                    >
                      {showAllSboms ? 'Show top 10' : `Show all (${sboms.length})`}
                    </Button>
                  )}
                </div>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              {sboms.length === 0 ? (
                <p className="text-xs text-muted-foreground py-6 text-center">
                  No SBOM artifacts recorded yet. Deploy a service to generate CycloneDX v1.5 BOMs.
                </p>
              ) : (
                <div className="divide-y divide-border">
                  {visibleSboms.map((sb) => (
                    <div
                      key={sb.id}
                      className="p-3 sm:px-5 hover:bg-muted/20 text-xs flex items-center justify-between gap-2"
                    >
                      <div>
                        <div className="font-semibold text-foreground">{sb.service_name}</div>
                        <div className="text-xs text-muted-foreground font-mono tabular-nums">
                          CycloneDX v{sb.spec_version} • {sb.package_count} packages
                        </div>
                      </div>
                      <Badge
                        variant="outline"
                        className={cn('text-[11px]', sb.provenance_verified ? STATUS_OK_BADGE : STATUS_WARN_BADGE)}
                      >
                        {sb.provenance_verified ? 'SLSA v1.0 Verified' : 'Unsigned'}
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* MITRE ATT&CK Technique Coverage Matrix */}
          {mitreMatrix.length > 0 && (
            <Card className="border-border bg-card">
              <CardHeader className="pb-3 border-b border-border">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <CardTitle className="text-sm font-semibold flex items-center gap-2">
                    <Shield className="w-4 h-4 text-sky-700 dark:text-sky-400" aria-hidden="true" />
                    MITRE ATT&amp;CK Technique Coverage Matrix ({overview?.mitre_summary?.techniques_detected ?? 0}/
                    {overview?.mitre_summary?.techniques_total ?? 14} detected)
                  </CardTitle>
                  <div className="flex items-center gap-1">
                    {(
                      [
                        { id: 'all', label: 'All' },
                        { id: 'detected', label: 'Detected' },
                        { id: 'monitored', label: 'Monitored' },
                      ] as const
                    ).map((opt) => (
                      <Button
                        key={opt.id}
                        size="sm"
                        variant={mitreFilter === opt.id ? 'secondary' : 'outline'}
                        className="min-h-11 sm:min-h-8 text-xs px-2.5"
                        onClick={() => setMitreFilter(opt.id)}
                      >
                        {opt.label}
                      </Button>
                    ))}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="p-0">
                <div className="grid-enterprise grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
                  {filteredMitre.map((m) => {
                    const detected = m.coverage_status === 'DETECTED';
                    return (
                      <div
                        key={m.technique_id}
                        className="status-indicator text-xs space-y-1"
                        data-status={detected ? 'degraded' : 'healthy'}
                      >
                        <div className="flex items-center justify-between gap-1">
                          <span className="font-mono font-bold text-xs text-foreground">{m.technique_id}</span>
                          <Badge
                            variant="outline"
                            className={cn(
                              'text-[11px] font-mono tabular-nums',
                              detected ? STATUS_WARN_BADGE : STATUS_OK_BADGE
                            )}
                          >
                            {detected ? `${m.events_count + m.open_findings_count} hits` : 'MONITORED'}
                          </Badge>
                        </div>
                        <div className="font-medium text-foreground truncate">{m.name}</div>
                        <div className="text-[11px] text-muted-foreground truncate">{m.tactic}</div>
                      </div>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
});
