"use client";

import React, { useState, useEffect, useCallback } from "react";
import { networkScopesApi } from "@/lib/api";
import { usePermissions } from "@/hooks/usePermissions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Loader2, Shield, Globe, Save, Trash2 } from "lucide-react";
import { toast } from "@/components/ui/use-toast";
import { useConfirm } from "@/components/ui/confirm-dialog";

interface ScopedNetworkRow {
  id: string;
  network_name?: string;
  effective_name?: string;
  isolated?: boolean;
  internal?: boolean;
  allowed_egress_networks?: string[];
  scope_name?: string;
}

function parseCidrs(text: string): { cidrs: string[]; errors: string[] } {
  const cidrs: string[] = [];
  const errors: string[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const m = line.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?:\/(\d{1,2}))?$/);
    if (!m) {
      errors.push(`Invalid CIDR: ${line}`);
      continue;
    }
    const octets = m.slice(1, 5).map(Number);
    const prefix = m[5] === undefined ? 32 : Number(m[5]);
    if (octets.some((o) => o > 255) || prefix > 32) {
      errors.push(`Invalid CIDR: ${line}`);
      continue;
    }
    cidrs.push(`${octets.join(".")}/${prefix}`);
  }
  return { cidrs, errors };
}

export function NetworkTab({
  projectId,
}: {
  projectId?: string | null;
}) {
  const { isStaff, isSuperuser } = usePermissions();
  const canEdit = isStaff || isSuperuser;
  const confirm = useConfirm();
  const [loading, setLoading] = useState(true);
  const [row, setRow] = useState<ScopedNetworkRow | null>(null);
  const [mode, setMode] = useState<"unrestricted" | "restricted">("unrestricted");
  const [cidrText, setCidrText] = useState("");
  const [isolated, setIsolated] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    if (!projectId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const rows = await networkScopesApi.list({
        scope_type: "project",
        object_id: projectId,
      });
      const found = (rows || [])[0] || null;
      setRow(found);
      const egress = found?.allowed_egress_networks || [];
      if (egress.length > 0) {
        setMode("restricted");
        setCidrText(egress.join("\n"));
      } else {
        setMode("unrestricted");
        setCidrText("");
      }
      setIsolated(found?.isolated ?? true);
    } catch (e) {
      toast({
        title: "Failed to load network scope",
        description: e instanceof Error ? e.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSave = async () => {
    if (!projectId) return;
    const { cidrs, errors } = parseCidrs(cidrText);
    if (mode === "restricted") {
      if (errors.length > 0) {
        toast({ title: "Invalid CIDRs", description: errors.join("; "), variant: "destructive" });
        return;
      }
      if (cidrs.length === 0) {
        toast({
          title: "Empty allowlist",
          description: "Restricted mode needs at least one CIDR. Switch to Unrestricted instead.",
          variant: "destructive",
        });
        return;
      }
      if (!await confirm({
        title: "Restrict container egress?",
        message: "Outbound traffic will be limited to these ranges (+ DNS + same-bridge addons). Too-narrow ranges break your app's own API/DB calls. Continue?",
        variant: "destructive",
        confirmText: "Apply restriction",
      })) return;
    }
    setSaving(true);
    try {
      const egress = mode === "restricted" ? cidrs : [];
      if (row) {
        await networkScopesApi.update(row.id, {
          allowed_egress_networks: egress,
          isolated,
        });
      } else {
        await networkScopesApi.create({
          scope_type_input: "project",
          scope_id: projectId,
          network_name: "",
          isolated,
          allowed_egress_networks: egress,
        });
      }
      toast({ title: mode === "restricted" ? "Egress restricted — live on the bridge now" : "Egress unrestricted" });
      await load();
    } catch (e) {
      toast({
        title: "Save failed",
        description: e instanceof Error ? e.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async () => {
    if (!row) return;
    if (!await confirm({
      title: "Remove project network override?",
      message: "The project inherits the parent scope again. Host rules converge back on the next edit or reconcile.",
      variant: "destructive",
      confirmText: "Remove override",
    })) return;
    setSaving(true);
    try {
      await networkScopesApi.delete(row.id);
      toast({ title: "Override removed" });
      await load();
    } catch (e) {
      toast({
        title: "Delete failed",
        description: e instanceof Error ? e.message : "Unknown error",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  if (!projectId) {
    return (
      <Card className="p-6 text-sm text-muted-foreground">
        This service has no project — network scope cannot be configured.
      </Card>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground p-6">
        <Loader2 size={14} className="animate-spin" /> Loading network scope…
      </div>
    );
  }

  const effectiveEgress = row?.allowed_egress_networks || [];

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-1.5">
          <Globe size={12} /> Effective egress
        </p>
        <div className="text-sm font-mono space-y-1">
          <div>
            <span className="text-zinc-500">network: </span>
            <span className="text-foreground">{row?.effective_name || "smsly-net"}</span>
          </div>
          <div>
            <span className="text-zinc-500">mode: </span>
            {effectiveEgress.length === 0 ? (
              <span className="text-amber-400">unrestricted (full internet)</span>
            ) : (
              <span className="text-emerald-400">restricted to {effectiveEgress.length} range(s)</span>
            )}
          </div>
          {effectiveEgress.length > 0 && (
            <div className="text-zinc-400 break-all">{effectiveEgress.join(", ")}</div>
          )}
          <div className="text-[11px] text-zinc-500">
            DNS (udp/53) and same-bridge addon traffic are always allowed. Cloud metadata (169.254.169.254) is always blocked.
          </div>
        </div>
      </Card>

      {canEdit ? (
        <Card className="p-4 space-y-3">
          <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
            <Shield size={12} /> Project egress policy
          </p>
          <div className="flex gap-2">
            <Button
              variant={mode === "unrestricted" ? "default" : "outline"}
              size="sm"
              onClick={() => setMode("unrestricted")}
            >
              Unrestricted
            </Button>
            <Button
              variant={mode === "restricted" ? "default" : "outline"}
              size="sm"
              onClick={() => setMode("restricted")}
            >
              Restricted allowlist
            </Button>
          </div>
          {mode === "restricted" && (
            <Textarea
              value={cidrText}
              onChange={(e) => setCidrText(e.target.value)}
              placeholder={"10.0.0.0/8\n192.168.0.0/16"}
              className="font-mono text-xs"
              rows={5}
            />
          )}
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <Input
              type="checkbox"
              checked={isolated}
              onChange={(e) => setIsolated(e.target.checked)}
              className="w-3.5 h-3.5"
            />
            Isolated project bridge (no cross-project container traffic)
          </label>
          <div className="flex gap-2">
            <Button size="sm" onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 size={12} className="animate-spin mr-1" /> : <Save size={12} className="mr-1" />}
              Apply now
            </Button>
            {row && (
              <Button size="sm" variant="outline" onClick={handleRemove} disabled={saving}>
                <Trash2 size={12} className="mr-1" /> Remove override
              </Button>
            )}
          </div>
          <p className="text-[11px] text-zinc-500">
            Applies to the host firewall immediately — narrowing takes effect on save, no redeploy needed.
            SaaS APIs on rotating IPs (e.g. Resend, Stripe) need broad ranges; when in doubt, keep Unrestricted.
          </p>
        </Card>
      ) : (
        <Card className="p-4 text-sm text-muted-foreground">
          Egress policy is admin-managed. Contact an administrator to change it.
        </Card>
      )}
    </div>
  );
}
