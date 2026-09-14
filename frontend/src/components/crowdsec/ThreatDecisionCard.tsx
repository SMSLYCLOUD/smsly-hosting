"use client";

import { Loader2, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { CrowdSecDecision } from "@/lib/api";

function fmtDT(v?: string | null): string {
  if (!v) return "";
  try {
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? v : d.toLocaleString();
  } catch {
    return v;
  }
}

interface ThreatDecisionCardProps {
  decision: CrowdSecDecision;
  unbanningIp?: string | null;
  onUnban: (ip: string) => void;
  /** Show the owning-service badge (platform-wide lists). */
  showService?: boolean;
}

/** Detailed CrowdSec ban card shared by Settings → Threat Blocks and the
 *  per-service Security tab. Every field is optional-safe: records parsed
 *  from older cscli shapes may lack enrichment. */
export function ThreatDecisionCard({ decision: d, unbanningIp, onUnban, showService }: ThreatDecisionCardProps) {
  const ip = d.value || d.source_ip || "unknown";
  const attackerBits = [d.country, d.asn_org, d.ip_range].filter(Boolean);
  const canUnban = Boolean(d.value) && unbanningIp !== d.value;

  return (
    <div className="p-2.5 rounded-lg bg-black/20 text-xs space-y-1.5">
      <div className="flex items-center gap-2">
        <code className="text-foreground font-semibold text-sm">{ip}</code>
        <Badge variant="destructive" className="text-[9px]">
          {d.type || d.scope || "ban"}
        </Badge>
        {d.simulated && (
          <Badge variant="outline" className="text-[9px]">simulated</Badge>
        )}
        <span className="flex-1" />
        <Button
          variant="outline"
          size="sm"
          className="h-6 text-[10px]"
          disabled={!canUnban}
          onClick={() => d.value && onUnban(d.value)}
        >
          {unbanningIp === d.value ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <><ShieldCheck className="h-3 w-3 mr-1" /> Unblock</>
          )}
        </Button>
      </div>

      <div className="text-muted-foreground" title={d.scenario}>
        {d.scenario || "unknown scenario"} · {d.events_count} events
        {d.origin ? ` · origin ${d.origin}` : ""}
        {d.duration ? ` · duration ${d.duration}` : ""}
      </div>

      {(d.target_host || (showService && d.service_name)) && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-muted-foreground">Target:</span>
          {d.target_host ? (
            <code className="text-foreground">{d.target_host}</code>
          ) : (
            <span className="text-muted-foreground">unattributed host</span>
          )}
          {showService && d.service_name && (
            <Badge variant="outline" className="text-[9px]">{d.service_name}</Badge>
          )}
        </div>
      )}

      {attackerBits.length > 0 && (
        <div className="text-muted-foreground">
          Attacker: {attackerBits.join(" · ")}
        </div>
      )}

      {d.paths && d.paths.length > 0 && (
        <div className="text-muted-foreground truncate" title={d.paths.join(", ")}>
          Probed: <code>{d.paths.slice(0, 4).join(", ")}</code>
          {d.paths.length > 4 ? ` +${d.paths.length - 4} more` : ""}
        </div>
      )}

      <div className="flex items-center gap-3 flex-wrap text-muted-foreground">
        {(d.first_seen || d.start_time) && (
          <span>First seen {fmtDT(d.first_seen || d.start_time)}</span>
        )}
        {d.end_time ? (
          <span>Expires {fmtDT(d.end_time)}</span>
        ) : (
          <span>Active ban (no expiry parsed)</span>
        )}
      </div>

      {d.message && (
        <div className="text-muted-foreground/80 truncate" title={d.message}>
          {d.message}
        </div>
      )}
    </div>
  );
}
