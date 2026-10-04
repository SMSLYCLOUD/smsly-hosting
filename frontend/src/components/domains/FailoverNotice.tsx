"use client";

import React from "react";
import { AlertTriangle } from "lucide-react";

/**
 * FailoverNotice — why node-level custom-domain serving exists and how
 * to use it with zero dashboard interaction.
 *
 * Every node runs an emergency Caddy that serves that node's services
 * (verified custom domains included) even when the master is down.
 * No UI is needed for failover: a Cloudflare DNS change is enough.
 */
export function FailoverNotice({ compact = false }: { compact?: boolean }) {
  return (
    <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 space-y-2">
      <div className="flex items-center gap-2">
        <AlertTriangle size={15} className="text-amber-500 shrink-0" />
        <p className="text-sm font-bold text-amber-400">
          Master-down failover: point DNS at the node, no dashboard needed
        </p>
      </div>
      <div className={`text-xs text-muted-foreground leading-relaxed space-y-1.5 ${compact ? "line-clamp-4" : ""}`}>
        <p>
          Each server runs its own emergency Caddy serving that server's services — including your
          verified custom domains — even if the master control plane is unreachable. Failover needs
          no UI and no input here: it is a DNS change only.
        </p>
        <p>
          <span className="font-semibold text-foreground">In Cloudflare:</span> change the domain's
          A record (apex) or CNAME (subdomain) to the <span className="font-mono">node IP</span> shown
          on the service's Network tab — grey cloud (DNS-only) issues a certificate automatically on
          first request; orange cloud works once the node holds a certificate for the name.
        </p>
        <p>
          <span className="font-semibold text-foreground">Why this exists:</span> platform hostnames
          depend on the master. Your custom domain pointed at the node keeps serving during a
          master outage, then keep it or point it back — both work without redeploying.
        </p>
      </div>
    </div>
  );
}
