"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Plus, Loader2, Database, CheckCircle2, AlertTriangle } from "lucide-react";
import { ecosystemApi, type EcosystemPlanSummary } from "@/lib/api";
import { useToast } from "@/components/ui/use-toast";
import { cn } from "@/lib/utils";

const ADDON_CHOICES = ["POSTGRES", "REDIS", "RABBITMQ", "MINIO", "MONGODB", "MYSQL"] as const;

interface RulesState {
  use_shared_addons: boolean;
  shared_addon_config: Record<string, { shared?: boolean }>;
  shared_addons: { type: string; name: string; addon_id: string; service_id: string; rule_shared: boolean }[];
  project_name: string;
}

/**
 * AddServiceForm — post-deploy "add a service to this ecosystem project".
 * Loads the plan's addon rules (use_shared_addons + per-type overrides)
 * and existing `{type}-shared` addons, then asks shared-vs-new per type
 * with the rule preselected. Unlisted types fall back to the plan default.
 */
export function AddServiceForm() {
  const [plans, setPlans] = useState<EcosystemPlanSummary[]>([]);
  const [planId, setPlanId] = useState("");
  const [rules, setRules] = useState<RulesState | null>(null);
  const [rulesLoading, setRulesLoading] = useState(false);
  const [name, setName] = useState("");
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("main");
  const [port, setPort] = useState("8000");
  const [trigger, setTrigger] = useState(false);
  const [picked, setPicked] = useState<Record<string, "shared" | "dedicated">>({});
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const { toast } = useToast();

  useEffect(() => {
    ecosystemApi.listPlans({ status: "completed" })
      .then((d: unknown) => setPlans(Array.isArray(d) ? d as EcosystemPlanSummary[] : ((d as { results?: EcosystemPlanSummary[] }).results || [])))
      .catch(() => setPlans([]));
  }, []);

  const loadRules = useCallback(async (id: string) => {
    setPlanId(id);
    setRules(null);
    setPicked({});
    if (!id) return;
    setRulesLoading(true);
    try {
      const r = await ecosystemApi.getPlanAddons(id);
      setRules({
        use_shared_addons: !!r.use_shared_addons,
        shared_addon_config: r.shared_addon_config || {},
        shared_addons: r.shared_addons || [],
        project_name: r.project_name || "",
      });
    } catch {
      toast({ title: "Could not load plan rules", variant: "destructive" });
    } finally {
      setRulesLoading(false);
    }
  }, [toast]);

  const ruleFor = (t: string): boolean => {
    if (!rules) return true;
    const c = rules.shared_addon_config[t];
    if (c && typeof c.shared === "boolean") return c.shared;
    return rules.use_shared_addons;
  };
  const existingFor = (t: string) => rules?.shared_addons.find((s) => s.type === t);

  const toggleType = (t: string) => {
    setPicked((p) => {
      if (p[t]) {
        const next = { ...p };
        delete next[t];
        return next;
      }
      return { ...p, [t]: ruleFor(t) ? "shared" : "dedicated" };
    });
  };

  const submit = async () => {
    if (!planId || !name.trim() || !repo.trim() || !port.trim()) {
      toast({ title: "Plan, name, repo and port are required", variant: "destructive" });
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      const res = await ecosystemApi.addServiceToPlan({
        plan_id: planId,
        name: name.trim(),
        repo_url: repo.trim(),
        branch: branch.trim() || "main",
        port: parseInt(port, 10),
        trigger_deploy: trigger,
        addons: Object.entries(picked).map(([type, mode]) => ({ type, mode })),
      });
      const eff = (res.addons_effective || []).map((a: { type: string; mode: string; reused?: boolean }) =>
        `${a.type}:${a.mode}${a.reused ? " (reused)" : ""}`).join(", ");
      setResult({ ok: true, text: `Service '${res.service?.name || name}' created${eff ? ` — addons: ${eff}` : ""}.` });
      toast({ title: "Service added to ecosystem project" });
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error || (err as Error).message;
      setResult({ ok: false, text: msg });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border border-border rounded-xl p-5 space-y-4">
      <div className="flex items-center gap-2">
        <Plus size={16} className="text-emerald-500" />
        <h3 className="font-bold text-sm">Add service to ecosystem project</h3>
      </div>

      <label className="block text-xs font-semibold text-muted-foreground">
        Ecosystem plan (completed)
        <select value={planId} onChange={(e) => loadRules(e.target.value)}
          className="mt-1 w-full px-2 py-1.5 text-sm rounded border border-border bg-background text-foreground font-normal">
          <option value="">Select a plan…</option>
          {plans.map((p) => (
            <option key={p.id} value={p.id}>
              {p.id.slice(0, 8)} · {new Date(p.created_at).toLocaleDateString()} · {p.services_created.length} services
            </option>
          ))}
        </select>
      </label>

      {rulesLoading && <p className="text-xs text-muted-foreground">Loading plan rules…</p>}

      {rules && (
        <div className="text-xs rounded-lg border border-border/50 bg-muted/20 p-3 space-y-1">
          <p className="font-semibold">Project rules — {rules.project_name}</p>
          <p className="text-muted-foreground">
            Default: <span className="font-mono font-bold text-foreground">{rules.use_shared_addons ? "shared addons" : "dedicated addons"}</span>
            {rules.shared_addons.length > 0 && (
              <> · existing shared: <span className="font-mono">{rules.shared_addons.map((s) => s.type).join(", ")}</span></>
            )}
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="block text-xs font-semibold text-muted-foreground">
          Service name
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="billing-api"
            className="mt-1 w-full px-2 py-1.5 text-sm rounded border border-border bg-background text-foreground font-normal" />
        </label>
        <label className="block text-xs font-semibold text-muted-foreground">
          Port
          <input value={port} onChange={(e) => setPort(e.target.value)} placeholder="8000"
            className="mt-1 w-full px-2 py-1.5 text-sm rounded border border-border bg-background text-foreground font-normal" />
        </label>
      </div>
      <label className="block text-xs font-semibold text-muted-foreground">
        Repo URL (GitHub / GitLab / Bitbucket HTTPS)
        <input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="https://github.com/org/repo"
          className="mt-1 w-full px-2 py-1.5 text-sm rounded border border-border bg-background text-foreground font-normal font-mono" />
      </label>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="block text-xs font-semibold text-muted-foreground">
          Branch
          <input value={branch} onChange={(e) => setBranch(e.target.value)}
            className="mt-1 w-full px-2 py-1.5 text-sm rounded border border-border bg-background text-foreground font-normal" />
        </label>
        <label className="flex items-center gap-2 text-xs text-muted-foreground pt-5">
          <input type="checkbox" checked={trigger} onChange={(e) => setTrigger(e.target.checked)} />
          Deploy immediately
        </label>
      </div>

      {planId && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-muted-foreground">Addons — rule preselected, override per type</p>
          {ADDON_CHOICES.map((t) => {
            const on = !!picked[t];
            const ex = existingFor(t);
            return (
              <div key={t} className={cn("flex items-center gap-3 rounded-lg border px-3 py-2 text-xs",
                on ? "border-primary/40 bg-primary/5" : "border-border/50")}>
                <input type="checkbox" checked={on} onChange={() => toggleType(t)} />
                <Database size={14} className="text-muted-foreground" />
                <span className="font-mono font-bold">{t}</span>
                <span className="text-muted-foreground">
                  rule: {ruleFor(t) ? "shared" : "dedicated"}
                  {ex ? ` · exists (${ex.name} — will reuse)` : ""}
                </span>
                {on && (
                  <span className="ml-auto flex gap-1">
                    {(["shared", "dedicated"] as const).map((m) => (
                      <button key={m} onClick={() => setPicked((p) => ({ ...p, [t]: m }))}
                        className={cn("px-2 py-0.5 rounded-full font-bold",
                          picked[t] === m ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
                        {m === "shared" ? "Use shared" : "New one"}
                      </button>
                    ))}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {result && (
        <div className={cn("flex items-start gap-2 text-xs rounded-lg border p-3",
          result.ok ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5")}>
          {result.ok ? <CheckCircle2 size={14} className="text-emerald-500 mt-0.5" /> : <AlertTriangle size={14} className="text-red-500 mt-0.5" />}
          <span>{result.text}</span>
        </div>
      )}

      <button onClick={submit} disabled={busy || !planId}
        className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-sm font-bold flex items-center gap-2">
        {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
        Add service
      </button>
    </div>
  );
}
