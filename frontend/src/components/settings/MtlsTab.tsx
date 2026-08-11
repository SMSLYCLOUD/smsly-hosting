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
  ShieldCheck,
  ShieldOff,
  RefreshCw,
  Clock,
  AlertTriangle,
  Lock,
  Unlock,
  Plus,
  Trash2,
  Server,
  Wifi,
  Box,
} from "lucide-react";
import api from "@/lib/api";
import { Progress } from "@/components/ui/progress";

interface MtlsHealth {
  spire_server_healthy: boolean;
  spire_agent_healthy: boolean;
  total_services: number;
  mtls_enabled_services: number;
  expired_svids: number;
  trust_domain: string;
  platform?: {
    spire_server_healthy: boolean;
    spire_agent_healthy: boolean;
    trust_domain: string;
  };
  ecosystem?: {
    spire_server_healthy: boolean;
    spire_agent_healthy: boolean;
    trust_domain: string;
  };
}

interface MtlsConfig {
  service_id: string;
  service_name: string;
  mtls_enabled: boolean;
  spiffe_id: string;
  svid_expiry: string | null;
  is_svid_expired: boolean;
  last_rotation: string | null;
  sidecar_enabled?: boolean;
}

interface MtlsAuthorizationPolicy {
  id: number;
  name: string;
  source_spiffe_id: string;
  target_service_id: string;
  target_service_name: string;
  paths: string[];
  methods: string[];
  action: "allow" | "deny";
  priority: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

type Tab = "services" | "policies";

export function MtlsTab() {
  const { toast } = useToast();
  const [activeTab, setActiveTab] = useState<Tab>("services");
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState<MtlsHealth | null>(null);
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
        api.get("/mtls/health/").catch(() => ({ data: null })),
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

  const toggleMtls = async (serviceId: string, enable: boolean) => {
    if (!enable && !confirm("Disable mTLS? The service will lose cryptographic identity.")) return;
    setToggling(serviceId);
    try {
      const endpoint = enable ? `/services/${serviceId}/mtls/enable/` : `/services/${serviceId}/mtls/disable/`;
      await api.post(endpoint);
      toast({ title: enable ? "mTLS Enabled" : "mTLS Disabled", description: enable ? "Service will receive SPIFFE identity on next deploy." : "Service will lose SPIFFE identity on next deploy." });
      fetchData();
    } catch (e: any) {
      toast({ title: "Error", description: e?.response?.data?.detail || "Failed to toggle mTLS", variant: "destructive" });
    } finally {
      setToggling(null);
    }
  };

  const toggleSidecar = async (serviceId: string, enable: boolean) => {
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

  const ecosystemHealthy = health?.ecosystem?.spire_server_healthy && health?.ecosystem?.spire_agent_healthy;

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Health Status */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Shield className="h-5 w-5 text-emerald-500" /> mTLS Status</CardTitle>
        </CardHeader>
        <CardContent>
          {health ? (
            <div className="grid grid-cols-2 gap-4">
              <div className="flex items-center gap-3 p-3 rounded-lg border">
                {ecosystemHealthy ? <ShieldCheck className="h-5 w-5 text-emerald-500" /> : <ShieldOff className="h-5 w-5 text-red-500" />}
                <div>
                  <p className="text-sm font-medium">Ecosystem SPIRE</p>
                  <p className="text-xs text-muted-foreground">Trust domain: {health.ecosystem?.trust_domain || "ecosystem.local"}</p>
                </div>
                <Badge variant={ecosystemHealthy ? "default" : "destructive"} className="ml-auto">{ecosystemHealthy ? "Healthy" : "Unhealthy"}</Badge>
              </div>
              <div className="flex items-center gap-3 p-3 rounded-lg border">
                {health.platform?.spire_server_healthy ? <ShieldCheck className="h-5 w-5 text-emerald-500" /> : <ShieldOff className="h-5 w-5 text-slate-400" />}
                <div>
                  <p className="text-sm font-medium">Platform SPIRE</p>
                  <p className="text-xs text-muted-foreground">Trust domain: {health.platform?.trust_domain || "platform.local"}</p>
                </div>
                <Badge variant={health.platform?.spire_server_healthy ? "default" : "secondary"}>{health.platform?.spire_server_healthy ? "Healthy" : "N/A"}</Badge>
              </div>
              <div className="flex items-center gap-4 text-xs text-muted-foreground col-span-2">
                <span>{health.mtls_enabled_services} / {health.total_services} services with mTLS</span>
                {health.expired_svids > 0 && (
                  <span className="flex items-center gap-1 text-amber-600"><AlertTriangle className="h-3 w-3" />{health.expired_svids} expired SVIDs</span>
                )}
              </div>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Unable to connect to SPIRE infrastructure.</p>
          )}
        </CardContent>
      </Card>

      {/* Tab Navigation */}
      <div className="flex gap-1 p-1 rounded-lg bg-muted">
        <button onClick={() => setActiveTab("services")} className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${activeTab === "services" ? "bg-background shadow-sm" : "hover:bg-background/50"}`}>
          <Lock className="h-4 w-4" /> Services
        </button>
        <button onClick={() => setActiveTab("policies")} className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-colors ${activeTab === "policies" ? "bg-background shadow-sm" : "hover:bg-background/50"}`}>
          <Shield className="h-4 w-4" /> Policies
        </button>
      </div>

      {/* Services Tab */}
      {activeTab === "services" && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2"><Lock className="h-5 w-5" /> Service mTLS</CardTitle>
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
                {services.map((svc) => {
                  const svidExpiry = svc.svid_expiry ? new Date(svc.svid_expiry) : null;
                  const now = new Date();
                  const ttlRemaining = svidExpiry ? Math.max(0, svidExpiry.getTime() - now.getTime()) : 0;
                  const ttlHours = Math.floor(ttlRemaining / (1000 * 60 * 60));
                  const ttlMinutes = Math.floor((ttlRemaining % (1000 * 60 * 60)) / (1000 * 60));
                  const ttlPercent = svidExpiry ? Math.min(100, (ttlRemaining / (3600 * 1000)) * 100) : 0;

                  return (
                    <div key={svc.service_id} className="flex items-start justify-between p-4 rounded-lg border">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-2">
                          {svc.mtls_enabled ? <Shield className="h-4 w-4 text-emerald-500" /> : <ShieldOff className="h-4 w-4 text-muted-foreground" />}
                          <span className="text-sm font-semibold truncate">{svc.service_name}</span>
                          <Badge variant={svc.mtls_enabled ? "default" : "secondary"}>{svc.mtls_enabled ? "Active" : "Disabled"}</Badge>
                          {svc.sidecar_enabled && <Badge variant="outline" className="gap-1"><Box className="h-3 w-3" /> Envoy</Badge>}
                        </div>
                        <p className="text-xs font-mono text-muted-foreground truncate mb-2">{svc.spiffe_id}</p>
                        {svc.mtls_enabled && svidExpiry && (
                          <div className="mb-2">
                            <div className="flex items-center gap-1 mb-1">
                              <Clock className="h-3 w-3 text-muted-foreground" />
                              <span className="text-xs text-muted-foreground">SVID expires in {ttlHours}h {ttlMinutes}m</span>
                            </div>
                            <Progress value={ttlPercent} className={`h-1.5 ${svc.is_svid_expired ? "text-red-500" : "text-emerald-500"}`} />
                          </div>
                        )}
                        {svc.last_rotation && (
                          <div className="flex items-center gap-1">
                            <RefreshCw className="h-3 w-3 text-muted-foreground" />
                            <span className="text-xs text-muted-foreground">Last rotated: {new Date(svc.last_rotation).toLocaleString()}</span>
                          </div>
                        )}
                      </div>
                      <div className="flex flex-col items-end gap-2 ml-4">
                        <Button size="sm" variant={svc.mtls_enabled ? "outline" : "default"} disabled={toggling === svc.service_id} onClick={() => toggleMtls(svc.service_id, !svc.mtls_enabled)}>
                          {toggling === svc.service_id ? <Loader2 className="h-4 w-4 animate-spin" /> : svc.mtls_enabled ? "Disable" : "Enable"}
                        </Button>
                        {svc.mtls_enabled && (
                          <Button size="sm" variant={svc.sidecar_enabled ? "outline" : "ghost"} disabled={toggling === svc.service_id} onClick={() => toggleSidecar(svc.service_id, !svc.sidecar_enabled)} className="text-xs gap-1">
                            <Box className="h-3 w-3" /> {svc.sidecar_enabled ? "Envoy On" : "Envoy Off"}
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
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
