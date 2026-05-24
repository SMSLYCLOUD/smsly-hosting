"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Brain, Sparkles, AlertTriangle, CheckCircle2, Loader2, GitBranch, Clock, Wrench, XCircle } from "lucide-react";

interface JulesEntry {
  deployment_id: string;
  branch: string;
  status: string;
  created_at: string | null;
  jules_events: string[];
  fix_applied: boolean;
  fix_failed: boolean;
}

interface JulesHistory {
  service_id: string;
  entries: JulesEntry[];
}

export function AIInsightsTab({ serviceId }: { serviceId: string }) {
  const [julesData, setJulesData] = useState<JulesHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await api.get(`/ai/jules-history/${serviceId}/`);
        setJulesData(res.data);
      } catch (err: any) {
        if (err.response?.status !== 404) {
          setError(err.response?.data?.error || err.message || "Failed to load Jules history");
        }
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [serviceId]);

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

  return (
    <div className="space-y-6 py-6">
      {/* Header */}
      <div className="flex items-center gap-3 pb-4 border-b border-border">
        <div className="p-2 rounded-lg bg-violet-500/10">
          <Brain className="w-6 h-6 text-violet-500" />
        </div>
        <div>
          <h2 className="text-lg font-bold">AI Insights & Auto-Fix</h2>
          <p className="text-sm text-muted-foreground">
            Jules AI auto-fix history, deployment analysis, and intelligent remediation.
          </p>
        </div>
      </div>

      {/* Stats Row */}
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

      {/* Error state */}
      {error && (
        <div className="p-4 rounded-xl border border-amber-500/20 bg-amber-500/5">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-500 mt-0.5" />
            <div>
              <p className="text-sm font-medium">AI Analysis Unavailable</p>
              <p className="text-xs text-muted-foreground mt-1">{error}</p>
            </div>
          </div>
        </div>
      )}

      {/* Jules Fix History */}
      {entries.length === 0 && !error ? (
        <div className="text-center py-16">
          <Sparkles className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
          <h3 className="text-lg font-semibold mb-2">No Jules Auto-Fix History</h3>
          <p className="text-sm text-muted-foreground">
            Jules AI auto-fix triggers automatically when a deployment fails. No fixes have been attempted yet for this service.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          <h3 className="text-sm font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
            <Wrench className="w-4 h-4" />
            Jules Fix History
          </h3>
          {entries.map((entry, i) => (
            <div key={i} className={`p-4 rounded-xl border ${
              entry.fix_applied ? "border-emerald-500/20 bg-emerald-500/5" :
              entry.fix_failed ? "border-red-500/20 bg-red-500/5" :
              "border-border bg-card"
            }`}>
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  {entry.fix_applied ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                  ) : entry.fix_failed ? (
                    <XCircle className="w-4 h-4 text-red-500" />
                  ) : (
                    <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />
                  )}
                  <span className="text-sm font-semibold">
                    Deployment {entry.deployment_id.slice(0, 8)}
                  </span>
                </div>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  {entry.branch && (
                    <span className="flex items-center gap-1">
                      <GitBranch className="w-3 h-3" />
                      {entry.branch}
                    </span>
                  )}
                  {entry.created_at && (
                    <span className="flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {new Date(entry.created_at).toLocaleString()}
                    </span>
                  )}
                </div>
              </div>
              {entry.jules_events.length > 0 && (
                <div className="mt-2 p-3 rounded-lg bg-black/20 font-mono text-xs space-y-0.5 max-h-40 overflow-y-auto">
                  {entry.jules_events.map((event, j) => (
                    <div key={j} className="text-muted-foreground leading-relaxed whitespace-pre-wrap break-all">
                      {event}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* About Section */}
      <div className="p-4 rounded-xl border border-border bg-muted/30">
        <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-violet-500" />
          About Jules AI
        </h4>
        <p className="text-xs text-muted-foreground leading-relaxed">
          Jules is an AI-powered auto-fix system that reacts to deployment failures by analyzing error logs, 
          proposing code fixes, creating pull requests, and optionally auto-deploying the fix. 
          Configure Jules in <strong>Settings → Intelligence</strong> with a valid API key and endpoint.
        </p>
      </div>
    </div>
  );
}
