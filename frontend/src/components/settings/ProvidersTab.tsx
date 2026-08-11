"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Loader2, Plus, Cloud, Trash2, Check } from "lucide-react";
import api from "@/lib/api";
import { useToast } from "@/components/ui/use-toast";
import { Badge } from "@/components/ui/badge";

interface CloudProvider {
  id: string;
  name: string;
  provider_type: string;
  is_active: boolean;
  created_at: string;
}

export function ProvidersTab() {
  const { toast } = useToast();
  const [providers, setProviders] = useState<CloudProvider[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(true);
  const [newProvider, setNewProvider] = useState({ name: "", api_key: "", provider_type: "hetzner" });
  const [addingProvider, setAddingProvider] = useState(false);

  const fetchProviders = useCallback(async () => {
    try {
      const res = await api.get("/cloud/providers/");
      const data = Array.isArray(res.data) ? res.data : (res.data?.results || []);
      setProviders(data);
    } catch {
      console.error("Failed to fetch providers");
    } finally {
      setLoadingProviders(false);
    }
  }, []);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  const handleAddProvider = async () => {
    if (!newProvider.name || !newProvider.api_key) {
      toast({ title: "Error", description: "Please fill all required fields.", variant: "destructive" });
      return;
    }
    setAddingProvider(true);
    try {
      await api.post("/cloud/providers/", newProvider);
      toast({ title: "Provider added", description: `${newProvider.name} has been connected.` });
      setNewProvider({ name: "", api_key: "", provider_type: "hetzner" });
      fetchProviders();
    } catch {
      toast({ title: "Error", description: "Failed to add provider.", variant: "destructive" });
    } finally {
      setAddingProvider(false);
    }
  };

  const handleDeleteProvider = async (id: string) => {
    try {
      await api.delete(`/cloud/providers/${id}/`);
      toast({ title: "Provider removed", description: "Cloud provider has been disconnected." });
      fetchProviders();
    } catch {
      toast({ title: "Error", description: "Failed to remove provider.", variant: "destructive" });
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Add Cloud Provider</CardTitle>
          <CardDescription>Connect a new cloud infrastructure provider.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div className="space-y-2">
              <Label>Provider Name</Label>
              <Input placeholder="My Hetzner Account" value={newProvider.name} onChange={(e) => setNewProvider({ ...newProvider, name: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label>Provider Type</Label>
              <select className="w-full h-10 px-3 border rounded-md bg-background" value={newProvider.provider_type} onChange={(e) => setNewProvider({ ...newProvider, provider_type: e.target.value })}>
                <option value="hetzner">Hetzner Cloud</option>
                <option value="digitalocean">DigitalOcean</option>
                <option value="aws">AWS</option>
                <option value="gcp">Google Cloud</option>
                <option value="azure">Azure</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label>API Key</Label>
              <Input type="password" placeholder="Enter API key" value={newProvider.api_key} onChange={(e) => setNewProvider({ ...newProvider, api_key: e.target.value })} />
            </div>
          </div>
          <Button onClick={handleAddProvider} disabled={addingProvider}>
            {addingProvider ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Adding...</> : <><Plus className="mr-2 h-4 w-4" /> Add Provider</>}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Connected Providers</CardTitle>
          <CardDescription>Your configured cloud infrastructure.</CardDescription>
        </CardHeader>
        <CardContent>
          {loadingProviders ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : providers.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Cloud className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No providers connected yet.</p>
              <p className="text-sm">Add a cloud provider above to get started.</p>
            </div>
          ) : (
            <div className="space-y-3">
              {providers.map((provider) => (
                <div key={provider.id} className="flex items-center justify-between p-4 border rounded-lg hover:bg-muted/50 transition-colors">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 bg-primary/10 rounded-lg flex items-center justify-center">
                      <Cloud className="h-5 w-5 text-primary" />
                    </div>
                    <div>
                      <p className="font-medium">{provider.name}</p>
                      <p className="text-sm text-muted-foreground capitalize">{provider.provider_type}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <Badge variant={provider.is_active ? "default" : "secondary"}>
                      {provider.is_active ? <><Check className="h-3 w-3 mr-1" /> Active</> : "Inactive"}
                    </Badge>
                    <Button variant="ghost" size="icon" onClick={() => handleDeleteProvider(provider.id)} className="text-red-500 hover:text-red-600 hover:bg-red-50">
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
