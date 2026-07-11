"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { Cloud, Plus, Trash2, Loader2, Check, Wifi, WifiOff, Server, HardDrive } from "lucide-react";
import api from "@/lib/api";

interface CloudDestination {
  id: string;
  name: string;
  provider: string;
  provider_display: string;
  bucket: string;
  region: string;
  endpoint: string;
  access_key: string;
  secret_key_masked: string;
  is_active: boolean;
  created_at: string;
  service: string | null;
  service_name: string | null;
}

const PROVIDER_TEMPLATES: Record<string, { name: string; endpoint: string; region: string }> = {
  r2: { name: "Cloudflare R2", endpoint: "https://{account_id}.r2.cloudflarestorage.com", region: "auto" },
  s3: { name: "Amazon S3", endpoint: "", region: "us-east-1" },
  minio: { name: "MinIO / Self-Hosted S3", endpoint: "https://your-server:9000", region: "us-east-1" },
  b2: { name: "Backblaze B2", endpoint: "https://s3.us-west-004.backblazeb2.com", region: "us-west-004" },
  digitalocean: { name: "DigitalOcean Spaces", endpoint: "https://nyc3.digitaloceanspaces.com", region: "nyc3" },
  wasabi: { name: "Wasabi Hot Storage", endpoint: "https://s3.wasabisys.com", region: "us-east-1" },
  vps: { name: "Custom Storage VPS", endpoint: "https://your-vps:9000", region: "us-east-1" },
};

export function CloudStorageTab({ serviceId }: { serviceId?: string }) {
  const { toast } = useToast();
  const [destinations, setDestinations] = useState<CloudDestination[]>([]);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({
    name: "", provider: "r2", bucket: "", region: "auto",
    endpoint: "", access_key: "", secret_key: "",
  });

  const fetchDestinations = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (serviceId) {
        params.set('service', serviceId);
      } else {
        // Settings page: show ALL destinations regardless of scope
        params.set('show_all', 'true');
      }
      const res = await api.get(`/cloud-storage/?${params.toString()}`);
      setDestinations(Array.isArray(res.data) ? res.data : res.data?.results || []);
    } catch { toast({ title: "Failed to load destinations", variant: "destructive" }); }
    finally { setLoading(false); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serviceId]);

  useEffect(() => { fetchDestinations(); }, [fetchDestinations]);

  const handleProviderChange = (provider: string) => {
    const tmpl = PROVIDER_TEMPLATES[provider];
    setForm(f => ({ ...f, provider, endpoint: tmpl?.endpoint || "", region: tmpl?.region || "us-east-1" }));
  };

  const handleCreate = async () => {
    if (!form.name || !form.bucket || !form.access_key || !form.secret_key) {
      toast({ title: "Fill all required fields", variant: "destructive" }); return;
    }
    try {
      const payload: any = { ...form };
      if (serviceId) payload.service = serviceId;
      await api.post("/cloud-storage/", payload);
      toast({ title: "Destination added" });
      setForm({ name: "", provider: "r2", bucket: "", region: "auto", endpoint: "", access_key: "", secret_key: "" });
      setAdding(false);
      fetchDestinations();
    } catch (err: any) {
      toast({ title: "Failed", description: err?.response?.data?.detail || "Could not create destination", variant: "destructive" });
    }
  };

  const handleDelete = async (id: string) => {
    try { await api.delete(`/cloud-storage/${id}/`); fetchDestinations(); }
    catch { toast({ title: "Failed to delete", variant: "destructive" }); }
  };

  const handleTest = async (id: string) => {
    setTesting(id);
    try {
      const res = await api.post(`/cloud-storage/${id}/test/`);
      const ok = res.data.status === "ok";
      toast({
        title: ok ? "Connection OK" : "Connection failed",
        description: ok ? undefined : (res.data.message ?? "Upload failed — check credentials and endpoint"),
        variant: ok ? "default" : "destructive",
      });
    } catch (err: any) {
      const msg = err?.response?.data?.message ?? err?.response?.data?.detail ?? "Could not reach the storage endpoint";
      toast({ title: "Test failed", description: msg, variant: "destructive" });
    } finally { setTesting(null); }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><Cloud className="w-5 h-5" /> Cloud Storage Destinations</CardTitle>
          <CardDescription>Offload backups to R2, S3, MinIO, B2, DigitalOcean Spaces, or any S3-compatible storage.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {destinations.length === 0 && !loading && (
            <p className="text-sm text-muted-foreground text-center py-8">No destinations configured. Add one below.</p>
          )}
          {loading && <div className="flex justify-center py-8"><Loader2 className="w-6 h-6 animate-spin" /></div>}
          {destinations.map(d => (
            <Card key={d.id} className="border-zinc-800">
              <CardContent className="p-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-zinc-800"><HardDrive className="w-4 h-4 text-blue-400" /></div>
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-sm">{d.name}</p>
                      {d.service ? (
                        <Badge variant="outline" className="text-[10px] bg-purple-500/10 text-purple-400 border-purple-500/30">
                          Service: {d.service_name || d.service?.slice(0, 8)}
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="text-[10px] bg-sky-500/10 text-sky-400 border-sky-500/30">
                          Server-Wide
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs text-zinc-500">
                      {d.provider_display} · {d.bucket} · {d.region}
                      {d.endpoint && ` · ${d.endpoint}`}
                    </p>
                    <p className="text-xs text-zinc-600">Key: {d.access_key?.slice(0, 8)}... / {d.secret_key_masked}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={() => handleTest(d.id)} disabled={testing === d.id}>
                    {testing === d.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Wifi className="w-3 h-3" />}
                    <span className="ml-1">Test</span>
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => handleDelete(d.id)}>
                    <Trash2 className="w-3 h-3 text-red-400" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}

          {!adding && (
            <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
              <Plus className="w-4 h-4 mr-1" /> Add Destination
            </Button>
          )}

          {adding && (
            <Card className="border-emerald-500/30 bg-emerald-500/5">
              <CardContent className="p-4 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label className="text-xs">Name</Label>
                    <Input className="h-8 text-sm" placeholder="My R2 Bucket" value={form.name} onChange={e => setForm(f => ({...f, name: e.target.value}))} />
                  </div>
                  <div>
                    <Label className="text-xs">Provider</Label>
                    <Select value={form.provider} onValueChange={handleProviderChange}>
                      <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {Object.entries(PROVIDER_TEMPLATES).map(([k, v]) => (
                          <SelectItem key={k} value={k}>{v.name}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div><Label className="text-xs">Bucket</Label><Input className="h-8 text-sm" placeholder="my-backups" value={form.bucket} onChange={e => setForm(f => ({...f, bucket: e.target.value}))} /></div>
                  <div><Label className="text-xs">Region</Label><Input className="h-8 text-sm" value={form.region} onChange={e => setForm(f => ({...f, region: e.target.value}))} /></div>
                </div>
                <div><Label className="text-xs">Endpoint (leave blank for AWS S3)</Label><Input className="h-8 text-sm" placeholder="https://..." value={form.endpoint} onChange={e => setForm(f => ({...f, endpoint: e.target.value}))} /></div>
                <div className="grid grid-cols-2 gap-3">
                  <div><Label className="text-xs">Access Key</Label><Input className="h-8 text-sm" value={form.access_key} onChange={e => setForm(f => ({...f, access_key: e.target.value}))} /></div>
                  <div><Label className="text-xs">Secret Key</Label><Input className="h-8 text-sm" type="password" value={form.secret_key} onChange={e => setForm(f => ({...f, secret_key: e.target.value}))} /></div>
                </div>
                <div className="flex gap-2 pt-1">
                  <Button size="sm" onClick={handleCreate}><Check className="w-3 h-3 mr-1" /> Save</Button>
                  <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
                </div>
              </CardContent>
            </Card>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
