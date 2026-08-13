"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/use-toast";
import {
  Loader2,
  Shield,
  ShieldOff,
  RefreshCw,
  Plus,
  Trash2,
} from "lucide-react";
import api from "@/lib/api";
import { MtlsHealthCard } from "@/app/console/settings/security/components/MtlsHealthCard";
import { ServiceMtlsCard } from "@/app/console/settings/security/components/ServiceMtlsCard";
import type { MtlsHealth, MtlsConfig, MtlsAuthorizationPolicy } from "@/app/console/settings/security/types";

type Tab = "services" | "policies";

export function MtlsTab() {
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState<Tab>("services");
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState<MtlsHealth | undefined>(undefined);
  const [services, setServices] = useState<MtlsConfig[]>([]);
  const [toggling, setToggling] = useState<string | null>(null);
  const [policies, setPolicies] = useState<MtlsAuthorizationPolicy[]>([]);
  const [policiesLoading, setPoliciesLoading] = useState(true);
  const [showCreatePolicy, setShowCreatePolicy] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [policyForm, setPolicyForm] = useState({
    name: "",
    source_spiffe_id: "",
    target_service_id: "",
    paths: "",
    methods: "",
    action: "allow" as "allow" | "deny",
    priority: 0,
  });

  const fetchData = useCallback(async () => {
    try {
      const [servicesRes, healthRes] = await Promise.all([
        api.get("/mtls/configs/").catch(() => ({ data: [] })),
        api.get("/mtls/health/").catch(() => ({ data: undefined })),
      ]);
      setServices(servicesRes.data || []);
      setHealth(healthRes.data);
    } catch {
      console.error("Failed to fetch mTLS data");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchPolicies = useCallback(async () => {
    try {
      const res = await api.get("/mtls/policies/?service_id=*").catch(() => ({ data: [] }));
      setPolicies(Array.isArray(res.data) ? res.data : (res.data?.results || []));
    } catch {
      console.error("Failed to fetch policies");
    } finally {
      setPoliciesLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    fetchPolicies();
  }, [fetchData, fetchPolicies]);

  const handleEnable = async (serviceId: string) => {
    setToggling(serviceId);
    try {
      await api.post(`/services/${serviceId}/mtls/enable/`);
      toast({ title: "mTLS Enabled", description: "Service will receive SPIFFE identity on next deploy." });
      fetchData();
    } catch (e: any) {
      toast({ title: "Error", description: e?.response?.data?.detail || "Failed to enable mTLS", variant: "destructive" });
    } finally {
      setToggling(null);
    }
  };

  const handleDisable = async (serviceId: string) => {
    setToggling(serviceId);
    try {
      await api.post(`/services/${serviceId}/mtls/disable/`);
      toast({ title: "mTLS Disabled", description: "Service will lose SPIFFE identity on next deploy." });
      fetchData();
    } catch (e: any) {
      toast({ title: "Error", description: e?.response?.data?.detail || "Failed to disable mTLS", variant: "destructive" });
    } finally {
      setToggling(null);
    }
  };

  const handleToggleSidecar = async (serviceId: string, enable: boolean) => {
    setToggling(serviceId);
    try {
      await api.post(`/services/${serviceId}/mtls/sidecar/`, { enabled: enable });
      toast({ title: enable ? "Envoy Sidecar Enabled" : "Envoy Sidecar Disabled" });
      fetchData();
    } catch (e: any) {
      toast({ title: "Error", description: e?.response?.data?.detail || "Failed to toggle sidecar", variant: "destructive" });
    } finally {
      setToggling(null);
    }
  };

  const createPolicy = async () => {
    setMutating(true);
    try {
      const paths = policyForm.paths.split(",").map((p) => p.trim()).filter(Boolean);
      const methods = policyForm.methods.split(",").map((m) => m.trim().toUpperCase()).filter(Boolean);
      await api.post("/mtls/policies/", {
        name: policyForm.name,
        source_spiffe_id: policyForm.source_spiffe_id,
        target_service_id: policyForm.target_service_id,
        paths,
        methods,
        action: policyForm.action,
        priority: policyForm.priority,
      });
      toast({ title: "Policy Created" });
      setShowCreatePolicy(false);
      setPolicyForm({ name: "", source_spiffe_id: "", target_service_id: "", paths: "", methods: "", action: "allow", priority: 0 });
      fetchPolicies();
    } catch (e: any) {
      toast({ title: "Error", description: e?.response?.data?.detail || "Failed to create policy", variant: "destructive" });
    } finally {
      setMutating(false);
    }
  };

  const togglePolicy = async (id: number, enabled: boolean) => {
    setMutating(true);
    try {
      await api.put(`/mtls/policies/${id}/`, { enabled });
      toast({ title: enabled ? "Policy Enabled" : "Policy Disabled" });
      fetchPolicies();
    } catch {
      toast({ title: "Error", variant: "destructive" });
    } finally {
      setMutating(false);
    }
  };

  const deletePolicy = async (id: number) => {
    if (!confirm("Delete this policy?")) return;
    setMutating(true);
    try {
      await api.delete(`/mtls/policies/${id}/`);
      toast({ title: "Policy Deleted" });
      fetchPolicies();
    } catch {
      toast({ title: "Error", variant: "destructive" });
    } finally {
      setMutating(false);
    }
  };

  if (loading) {
    return <div className="flex items-center justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div className="space-y-6">
      {/* Health Status — uses MtlsHealthCard component */}
      <MtlsHealthCard health={health} isLoading={false} />

      {/* Tab Navigation */}
      <div className="flex gap-1 p-1 rounded-lg bg-muted">
        <button onClick={() => setActiveTab("services")} className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${activeTab === "services" ? "bg-background shadow-sm" : "hover:bg-background/50"}`}>
          <Shield className="h-4 w-4" /> Services
        </button>
        <button onClick={() => setActiveTab("policies")} className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${activeTab === "policies" ? "bg-background shadow-sm" : "hover:bg-background/50"}`}>
          <Shield className="h-4 w-4" /> Policies
        </button>
      </div>

      {/* Services Tab — uses ServiceMtlsCard component */}
      {activeTab === "services" && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2"><Shield className="h-5 w-5" /> Service mTLS</CardTitle>
                <CardDescription>Enable or disable SPIFFE mTLS for individual services. Changes take effect on next deploy.</CardDescription>
              </div>
              <Button variant="outline" size="sm" onClick={() => { setLoading(true); fetchData(); }}><RefreshCw className="h-4 w-4 mr-1" /> Refresh</Button>
            </div>
          </CardHeader>
          <CardContent>
            {services.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">No services found. Deploy a service to configure mTLS.</p>
            ) : (
              <div className="space-y-3">
                {services.map((svc) => (
                  <ServiceMtlsCard
                    key={svc.service_id}
                    config={svc}
                    onEnable={handleEnable}
                    onDisable={handleDisable}
                    onToggleSidecar={handleToggleSidecar}
                    isToggling={toggling === svc.service_id}
                  />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Policies Tab */}
      {activeTab === "policies" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-lg font-semibold">Authorization Policies</h3>
              <p className="text-sm text-muted-foreground">Control which services can call which endpoints. Policies are evaluated by priority (highest first).</p>
            </div>
            <Button onClick={() => setShowCreatePolicy(true)} size="sm"><Plus className="h-4 w-4 mr-1" /> Add Policy</Button>
          </div>

          {policiesLoading ? (
            <div className="space-y-3">{[1, 2].map((i) => <div key={i} className="h-20 rounded-lg animate-pulse bg-muted" />)}</div>
          ) : policies.length > 0 ? (
            <div className="space-y-2">
              {policies.map((policy) => (
                <Card key={policy.id}>
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          {policy.action === "allow" ? <Shield className="h-4 w-4 text-emerald-500" /> : <ShieldOff className="h-4 w-4 text-red-500" />}
                          <span className="text-sm font-semibold">{policy.name}</span>
                          <Badge variant={policy.action === "allow" ? "default" : "destructive"}>{policy.action.toUpperCase()}</Badge>
                          {!policy.enabled && <Badge variant="secondary">Disabled</Badge>}
                        </div>
                        <div className="text-xs font-mono text-muted-foreground mb-1">{policy.source_spiffe_id} → {policy.target_service_name}</div>
                        {policy.paths.length > 0 && <div className="text-xs text-muted-foreground">Paths: {policy.paths.join(", ")}</div>}
                        {policy.methods.length > 0 && <div className="text-xs text-muted-foreground">Methods: {policy.methods.join(", ")}</div>}
                      </div>
                      <div className="flex items-center gap-2 ml-4">
                        <span className="text-xs text-muted-foreground">P{policy.priority}</span>
                        <Button size="sm" variant="ghost" disabled={mutating} onClick={() => togglePolicy(policy.id, !policy.enabled)}>{policy.enabled ? "Disable" : "Enable"}</Button>
                        <Button size="sm" variant="ghost" disabled={mutating} onClick={() => deletePolicy(policy.id)}><Trash2 className="h-4 w-4 text-red-500" /></Button>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <div className="text-center py-12 rounded-lg border-2 border-dashed border-border">
              <Shield className="h-12 w-12 mx-auto mb-3 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">No authorization policies yet. Add a policy to control service-to-service access.</p>
            </div>
          )}

          {/* Create Policy Form */}
          {showCreatePolicy && (
            <Card>
              <CardHeader>
                <CardTitle>Create Authorization Policy</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label>Policy Name</Label>
                  <Input value={policyForm.name} onChange={(e) => setPolicyForm({ ...policyForm, name: e.target.value })} placeholder="e.g., Allow frontend to call API" />
                </div>
                <div className="space-y-2">
                  <Label>Source SPIFFE ID</Label>
                  <Input value={policyForm.source_spiffe_id} onChange={(e) => setPolicyForm({ ...policyForm, source_spiffe_id: e.target.value })} placeholder="spiffe://ecosystem.local/service/frontend or *" />
                  <p className="text-xs text-muted-foreground">Use * to match any source service</p>
                </div>
                <div className="space-y-2">
                  <Label>Target Service</Label>
                  <select className="w-full rounded-md border px-3 py-2 text-sm bg-background" value={policyForm.target_service_id} onChange={(e) => setPolicyForm({ ...policyForm, target_service_id: e.target.value })}>
                    <option value="">Select a service</option>
                    {services.map((s) => <option key={s.service_id} value={s.service_id}>{s.service_name}</option>)}
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Path Prefixes</Label>
                    <Input value={policyForm.paths} onChange={(e) => setPolicyForm({ ...policyForm, paths: e.target.value })} placeholder="/api/, /internal/" />
                    <p className="text-xs text-muted-foreground">Comma-separated, empty = all paths</p>
                  </div>
                  <div className="space-y-2">
                    <Label>HTTP Methods</Label>
                    <Input value={policyForm.methods} onChange={(e) => setPolicyForm({ ...policyForm, methods: e.target.value })} placeholder="GET, POST" />
                    <p className="text-xs text-muted-foreground">Comma-separated, empty = all methods</p>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label>Action</Label>
                    <select className="w-full rounded-md border px-3 py-2 text-sm bg-background" value={policyForm.action} onChange={(e) => setPolicyForm({ ...policyForm, action: e.target.value as "allow" | "deny" })}>
                      <option value="allow">Allow</option>
                      <option value="deny">Deny</option>
                    </select>
                  </div>
                  <div className="space-y-2">
                    <Label>Priority</Label>
                    <Input type="number" value={policyForm.priority} onChange={(e) => setPolicyForm({ ...policyForm, priority: parseInt(e.target.value) || 0 })} />
                    <p className="text-xs text-muted-foreground">Higher = evaluated first</p>
                  </div>
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <Button variant="outline" onClick={() => setShowCreatePolicy(false)}>Cancel</Button>
                  <Button onClick={createPolicy} disabled={!policyForm.name || !policyForm.source_spiffe_id || !policyForm.target_service_id || mutating}>
                    {mutating ? "Creating..." : "Create Policy"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Info */}
      <Card>
        <CardHeader><CardTitle className="text-sm">How mTLS Works</CardTitle></CardHeader>
        <CardContent className="text-sm text-muted-foreground space-y-2">
          <p>Each service with mTLS enabled gets a unique SPIFFE identity (X.509 certificate) from the ecosystem SPIRE server. This identity is automatically rotated every hour.</p>
          <p>Services can use the SPIFFE identity to authenticate to each other over mutual TLS, without manual certificate management.</p>
          <p>Trust domain: <code className="bg-muted px-1 rounded">ecosystem.local</code> — isolated from platform-internal services.</p>
        </CardContent>
      </Card>
    </div>
  );
}
