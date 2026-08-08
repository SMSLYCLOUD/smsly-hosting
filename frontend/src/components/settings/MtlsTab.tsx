"use client";

import React, { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
} from "lucide-react";
import api from "@/lib/api";

interface MtlsService {
  service_id: string;
  service_name: string;
  mtls_enabled: boolean;
  trust_domain: string;
  spiffe_id: string;
  svid_expiry: string | null;
  is_svid_expired: boolean;
}

interface MtlsHealth {
  platform: {
    spire_server_healthy: boolean;
    spire_agent_healthy: boolean;
    trust_domain: string;
  };
  ecosystem: {
    spire_server_healthy: boolean;
    spire_agent_healthy: boolean;
    trust_domain: string;
  };
  total_services: number;
  mtls_enabled_services: number;
  expired_svids: number;
}

export function MtlsTab() {
  const { toast } = useToast();
  const [loading, setLoading] = useState(true);
  const [services, setServices] = useState<MtlsService[]>([]);
  const [health, setHealth] = useState<MtlsHealth | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [servicesRes, healthRes] = await Promise.all([
        api.get("/mtls/configs/").catch(() => ({ data: [] })),
        api.get("/mtls/health/").catch(() => ({ data: null })),
      ]);
      setServices(servicesRes.data || []);
      setHealth(healthRes.data);
    } catch (e) {
      console.error("Failed to fetch mTLS data", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const toggleMtls = async (serviceId: string, enable: boolean) => {
    setToggling(serviceId);
    try {
      const endpoint = enable
        ? `/services/${serviceId}/mtls/enable/`
        : `/services/${serviceId}/mtls/disable/`;
      await api.post(endpoint);
      toast({
        title: enable ? "mTLS Enabled" : "mTLS Disabled",
        description: enable
          ? "Service will receive SPIFFE identity on next deploy."
          : "Service will lose SPIFFE identity on next deploy.",
      });
      fetchData();
    } catch (e: any) {
      toast({
        title: "Error",
        description: e?.response?.data?.detail || "Failed to toggle mTLS",
        variant: "destructive",
      });
    } finally {
      setToggling(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const ecosystemHealthy =
    health?.ecosystem?.spire_server_healthy && health?.ecosystem?.spire_agent_healthy;

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Health Status */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5" /> SPIRE Infrastructure
          </CardTitle>
          <CardDescription>
            Ecosystem SPIRE server provides isolated mTLS identities for your deployed services.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="flex items-center gap-3 p-3 rounded-lg border">
              {ecosystemHealthy ? (
                <ShieldCheck className="h-5 w-5 text-emerald-500" />
              ) : (
                <ShieldOff className="h-5 w-5 text-red-500" />
              )}
              <div>
                <p className="text-sm font-medium">Ecosystem SPIRE</p>
                <p className="text-xs text-muted-foreground">
                  Trust domain: {health?.ecosystem?.trust_domain || "ecosystem.local"}
                </p>
              </div>
              <Badge variant={ecosystemHealthy ? "default" : "destructive"} className="ml-auto">
                {ecosystemHealthy ? "Healthy" : "Unhealthy"}
              </Badge>
            </div>
            <div className="flex items-center gap-3 p-3 rounded-lg border">
              {health?.platform?.spire_server_healthy ? (
                <ShieldCheck className="h-5 w-5 text-emerald-500" />
              ) : (
                <ShieldOff className="h-5 w-5 text-slate-400" />
              )}
              <div>
                <p className="text-sm font-medium">Platform SPIRE</p>
                <p className="text-xs text-muted-foreground">
                  Trust domain: {health?.platform?.trust_domain || "platform.local"}
                </p>
              </div>
              <Badge variant={health?.platform?.spire_server_healthy ? "default" : "secondary"}>
                {health?.platform?.spire_server_healthy ? "Healthy" : "N/A"}
              </Badge>
            </div>
          </div>
          {health && (
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span>{health.mtls_enabled_services} / {health.total_services} services with mTLS</span>
              {health.expired_svids > 0 && (
                <span className="flex items-center gap-1 text-amber-600">
                  <AlertTriangle className="h-3 w-3" />
                  {health.expired_svids} expired SVIDs
                </span>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Per-Service mTLS Toggle */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Lock className="h-5 w-5" /> Service mTLS
              </CardTitle>
              <CardDescription>
                Enable or disable SPIFFE mTLS for individual services. Changes take effect on next deploy.
              </CardDescription>
            </div>
            <Button variant="outline" size="sm" onClick={() => fetchData()}>
              <RefreshCw className="h-4 w-4 mr-1" />
              Refresh
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {services.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              No services found. Deploy a service to configure mTLS.
            </p>
          ) : (
            <div className="space-y-3">
              {services.map((svc) => (
                <div
                  key={svc.service_id}
                  className="flex items-center justify-between p-4 rounded-lg border"
                >
                  <div className="flex items-center gap-3">
                    {svc.mtls_enabled ? (
                      <Lock className="h-4 w-4 text-emerald-500" />
                    ) : (
                      <Unlock className="h-4 w-4 text-muted-foreground" />
                    )}
                    <div>
                      <p className="text-sm font-medium">{svc.service_name}</p>
                      {svc.mtls_enabled && svc.spiffe_id && (
                        <p className="text-xs font-mono text-muted-foreground truncate max-w-md">
                          {svc.spiffe_id}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {svc.mtls_enabled && svc.svid_expiry && (
                      <div className="flex items-center gap-1 text-xs text-muted-foreground">
                        <Clock className="h-3 w-3" />
                        {svc.is_svid_expired ? (
                          <span className="text-amber-600">Expired</span>
                        ) : (
                          <span>
                            Expires {new Date(svc.svid_expiry).toLocaleDateString()}
                          </span>
                        )}
                      </div>
                    )}
                    <Button
                      variant={svc.mtls_enabled ? "destructive" : "default"}
                      size="sm"
                      disabled={toggling === svc.service_id}
                      onClick={() => toggleMtls(svc.service_id, !svc.mtls_enabled)}
                    >
                      {toggling === svc.service_id ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : svc.mtls_enabled ? (
                        "Disable"
                      ) : (
                        "Enable"
                      )}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Info */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">How mTLS Works</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground space-y-2">
          <p>
            Each service with mTLS enabled gets a unique SPIFFE identity (X.509 certificate) from the
            ecosystem SPIRE server. This identity is automatically rotated every hour.
          </p>
          <p>
            Services can use the SPIFFE identity to authenticate to each other over mutual TLS,
            without manual certificate management.
          </p>
          <p>
            Trust domain: <code className="bg-muted px-1 rounded">ecosystem.local</code> — isolated
            from platform-internal services.
          </p>
          <p>
            <a
              href="/console/settings/security"
              className="text-emerald-500 hover:underline"
            >
              Manage authorization policies and Envoy sidecar →
            </a>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
