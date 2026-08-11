"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Loader2, Globe, Lock, Shield, Server, Settings as SettingsIcon, Eye, EyeOff } from "lucide-react";
import { systemApi } from "@/lib/api";
import { useToast } from "@/components/ui/use-toast";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export function InfraTab() {
  const { toast } = useToast();
  const [systemConfig, setSystemConfig] = useState<any>(null);
  const [domainConfig, setDomainConfig] = useState<any>(null);
  const [domainForm, setDomainForm] = useState({ domain: "", use_ssl: false, enable_crowdsec_waf: false, wildcard_subdomains: true, cloudflare_api_token: "", server_ip: "" });
  const [savingDomain, setSavingDomain] = useState(false);
  const [showCfToken, setShowCfToken] = useState(false);
  const [cfTokenTouched, setCfTokenTouched] = useState(false);
  const [recheckLoading, setRecheckLoading] = useState(false);
  const [recheckLastRun, setRecheckLastRun] = useState<string | null>(null);

  const fetchDomainConfig = useCallback(async () => {
    try {
      const data = await systemApi.getDomainConfig();
      setDomainConfig(data);
      setDomainForm({ domain: data.domain || "", use_ssl: data.use_ssl || false, enable_crowdsec_waf: data.enable_crowdsec_waf || false, wildcard_subdomains: data.wildcard_subdomains ?? true, cloudflare_api_token: "", server_ip: data.server_ip || "" });
      setCfTokenTouched(false);
    } catch { console.error("Failed to fetch domain config"); }
  }, []);

  const fetchSystemConfig = useCallback(async () => {
    try {
      const config = await systemApi.getConfig();
      setSystemConfig(config);
    } catch { console.error("Failed to fetch system config"); }
  }, []);

  useEffect(() => {
    fetchSystemConfig();
    fetchDomainConfig();
  }, [fetchSystemConfig, fetchDomainConfig]);

  const handleSaveDomainConfig = async () => {
    setSavingDomain(true);
    try {
      const payload: any = { ...domainForm };
      if (!cfTokenTouched) delete payload.cloudflare_api_token;
      else payload.cloudflare_api_token = String(payload.cloudflare_api_token || "").trim();
      const result = await systemApi.updateDomainConfig(payload);
      toast({ title: "Domain Config Saved", description: result.message || "Configuration applied." });
      window.dispatchEvent(new CustomEvent("DOMAIN_SYNC_TRIGGER", { detail: { domain: payload.domain, type: "PLATFORM" } }));
      setCfTokenTouched(false);
      fetchDomainConfig();
    } catch (err: any) {
      toast({ title: "Error", description: err?.response?.data?.error || "Failed to save domain config.", variant: "destructive" });
    } finally { setSavingDomain(false); }
  };

  const handleRouteRecheck = async () => {
    setRecheckLoading(true);
    try {
      const result = await systemApi.routeRecheck();
      setRecheckLastRun(new Date().toISOString());
      toast({ title: "Route recheck triggered", description: result.message });
    } catch (err: any) {
      toast({ title: "Error", description: err?.response?.data?.error || "Failed to trigger route recheck.", variant: "destructive" });
    } finally { setRecheckLoading(false); }
  };

  if (!systemConfig) return <div className="flex items-center justify-center py-8"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>;

  return (
    <div className="space-y-6">
      {/* Domain & SSL */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Globe className="h-5 w-5 text-cyan-500" /> Domain & SSL</CardTitle>
          <CardDescription>Configure your domain, SSL certificates, and wildcard subdomains for deployed services.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-2"><Label>Domain</Label><Input placeholder="cloud.example.com" value={domainForm.domain} onChange={(e) => setDomainForm((prev) => ({ ...prev, domain: e.target.value }))} /><p className="text-xs text-muted-foreground">Leave empty for IP-only mode</p></div>
            <div className="space-y-2"><Label>Server IP</Label><Input placeholder="Auto-detected" value={domainForm.server_ip} onChange={(e) => setDomainForm((prev) => ({ ...prev, server_ip: e.target.value }))} /><p className="text-xs text-muted-foreground">Public IPv4 of this server</p></div>
          </div>
          <div className="flex flex-wrap items-center gap-4 py-2">
            <div className="flex items-center gap-2">
              <Button variant={domainForm.use_ssl ? "default" : "outline"} size="sm" onClick={() => setDomainForm((prev) => ({ ...prev, use_ssl: !prev.use_ssl }))}><Lock className="h-3 w-3 mr-1" />{domainForm.use_ssl ? "SSL Enabled" : "SSL Disabled"}</Button>
            </div>
            <div className="flex items-center gap-2">
              <Button variant={domainForm.wildcard_subdomains ? "default" : "outline"} size="sm" onClick={() => setDomainForm((prev) => ({ ...prev, wildcard_subdomains: !prev.wildcard_subdomains }))} disabled={!domainForm.use_ssl}>{domainForm.wildcard_subdomains ? "Wildcard Subdomains Enabled" : "Wildcard Subdomains Disabled"}</Button>
            </div>
            <div className="flex items-center gap-2">
              <Button variant={domainForm.enable_crowdsec_waf ? "default" : "outline"} size="sm" onClick={() => setDomainForm((prev) => ({ ...prev, enable_crowdsec_waf: !prev.enable_crowdsec_waf }))}><Shield className="h-3 w-3 mr-1" />{domainForm.enable_crowdsec_waf ? "CrowdSec WAF Enabled" : "CrowdSec WAF Disabled"}</Button>
            </div>
            {domainConfig && <Badge variant={domainConfig.caddy_status === "applied" ? "default" : domainConfig.caddy_status === "error" ? "destructive" : "secondary"}>Caddy: {domainConfig.caddy_status}</Badge>}
          </div>
          {domainForm.use_ssl && domainForm.wildcard_subdomains && (
            <div className="space-y-2">
              <Label>Cloudflare API Token</Label>
              <div className="flex gap-2">
                <Input type={showCfToken ? "text" : "password"} placeholder={domainConfig?.cloudflare_api_token_set ? "Configured token (hidden)" : "Enter Cloudflare API Token"} value={domainForm.cloudflare_api_token} onChange={(e) => { setCfTokenTouched(true); setDomainForm((prev) => ({ ...prev, cloudflare_api_token: e.target.value })); }} className="flex-1" />
                <Button variant="outline" size="sm" onClick={() => { setCfTokenTouched(true); setDomainForm((prev) => ({ ...prev, cloudflare_api_token: "" })); }}>Clear</Button>
                <Button variant="ghost" size="icon" onClick={() => setShowCfToken(!showCfToken)}>{showCfToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</Button>
              </div>
              <p className="text-xs text-muted-foreground">Required for wildcard SSL. Create in Cloudflare &gt; API Tokens &gt; Edit Zone DNS.</p>
            </div>
          )}
          <div className="flex gap-3">
            <Button onClick={handleSaveDomainConfig} disabled={savingDomain}>{savingDomain ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Applying...</> : "Save & Apply"}</Button>
            <Button variant="outline" onClick={fetchDomainConfig}>Refresh</Button>
          </div>
        </CardContent>
      </Card>

      {/* Redis / Celery */}
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><Server className="h-5 w-5 text-orange-500" /> Redis & Celery</CardTitle><CardDescription>Task queue and caching infrastructure.</CardDescription></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
            <TableBody>
              <TableRow><TableCell className="font-mono">REDIS_HOST</TableCell><TableCell>{systemConfig.REDIS_HOST}</TableCell><TableCell><Badge variant="outline">Config</Badge></TableCell></TableRow>
              <TableRow><TableCell className="font-mono">REDIS_PORT</TableCell><TableCell>{systemConfig.REDIS_PORT}</TableCell><TableCell><Badge variant="outline">Config</Badge></TableCell></TableRow>
              <TableRow><TableCell className="font-mono">REDIS_PASSWORD</TableCell><TableCell>{systemConfig.REDIS_PASSWORD_SET ? "Set" : "Not set"}</TableCell><TableCell><Badge variant={systemConfig.REDIS_PASSWORD_SET ? "default" : "secondary"}>{systemConfig.REDIS_PASSWORD_SET ? "Secure" : "Unprotected"}</Badge></TableCell></TableRow>
              <TableRow><TableCell className="font-mono">CELERY_BACKEND</TableCell><TableCell>{systemConfig.CELERY_RESULT_BACKEND}</TableCell><TableCell><Badge variant="outline">Config</Badge></TableCell></TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Container Registry */}
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><SettingsIcon className="h-5 w-5 text-blue-500" /> Container Registry</CardTitle><CardDescription>Docker image registry for deployments.</CardDescription></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
            <TableBody>
              <TableRow><TableCell className="font-mono">REGISTRY_URL</TableCell><TableCell>{systemConfig.CONTAINER_REGISTRY_URL || "Not set"}</TableCell><TableCell><Badge variant={systemConfig.CONTAINER_REGISTRY_URL ? "default" : "destructive"}>{systemConfig.CONTAINER_REGISTRY_URL ? "Configured" : "Missing"}</Badge></TableCell></TableRow>
              <TableRow><TableCell className="font-mono">REGISTRY_USER</TableCell><TableCell>{systemConfig.REGISTRY_USER}</TableCell><TableCell><Badge variant={systemConfig.REGISTRY_USER ? "default" : "secondary"}>{systemConfig.REGISTRY_USER || "Not set"}</Badge></TableCell></TableRow>
              <TableRow><TableCell className="font-mono">REGISTRY_PASSWORD</TableCell><TableCell>{systemConfig.REGISTRY_PASSWORD_SET ? "Set" : "Not set"}</TableCell><TableCell><Badge variant={systemConfig.REGISTRY_PASSWORD_SET ? "default" : "secondary"}>{systemConfig.REGISTRY_PASSWORD_SET ? "Secure" : "Missing"}</Badge></TableCell></TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Rate Limiting */}
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><Shield className="h-5 w-5 text-yellow-500" /> Rate Limiting</CardTitle><CardDescription>API throttle rates per client type.</CardDescription></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Scope</TableHead><TableHead>Limit</TableHead><TableHead>Type</TableHead></TableRow></TableHeader>
            <TableBody>
              {systemConfig.THROTTLE_RATES && Object.entries(systemConfig.THROTTLE_RATES).map(([key, value]) => (
                <TableRow key={key}><TableCell className="font-mono">{key}</TableCell><TableCell>{String(value)}</TableCell><TableCell><Badge variant="outline">Throttle</Badge></TableCell></TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Route Recheck */}
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><SettingsIcon className="h-5 w-5 text-green-500" /> Route Recheck</CardTitle><CardDescription>Re-check network routes across the mesh.</CardDescription></CardHeader>
        <CardContent>
          <div className="flex flex-col gap-4">
            <p className="text-sm text-muted-foreground">Trigger a fresh scan of all mesh network routes.</p>
            <div className="flex items-center gap-3">
              <Button onClick={handleRouteRecheck} disabled={recheckLoading}>{recheckLoading ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Rechecking...</> : "Recheck Routes"}</Button>
              {recheckLastRun && <span className="text-xs text-muted-foreground">Last recheck: {new Date(recheckLastRun).toLocaleString()}</span>}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Database */}
      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><SettingsIcon className="h-5 w-5 text-purple-500" /> Database</CardTitle><CardDescription>Database connection info (read-only).</CardDescription></CardHeader>
        <CardContent>
          <Table>
            <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
            <TableBody>
              <TableRow><TableCell className="font-mono">ENGINE</TableCell><TableCell className="truncate max-w-[300px]">{systemConfig.DATABASE_ENGINE}</TableCell><TableCell><Badge variant="outline">Info</Badge></TableCell></TableRow>
              <TableRow><TableCell className="font-mono">DATABASE</TableCell><TableCell>{systemConfig.DATABASE_NAME}</TableCell><TableCell><Badge variant="outline">Info</Badge></TableCell></TableRow>
              <TableRow><TableCell className="font-mono">HOST</TableCell><TableCell>{systemConfig.DATABASE_HOST}</TableCell><TableCell><Badge variant="outline">Info</Badge></TableCell></TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
