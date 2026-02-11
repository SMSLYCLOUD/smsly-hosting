"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { Settings as SettingsIcon, User, Bell, Shield, Cloud, Plus, Trash2, Check, Loader2, Sparkles, Eye, EyeOff } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import api, { systemApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

interface CloudProvider {
  id: string;
  name: string;
  provider_type: string;
  is_active: boolean;
  created_at: string;
}

export default function SettingsPage() {
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);
  const [providers, setProviders] = useState<CloudProvider[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(true);
  const [newProvider, setNewProvider] = useState({ name: "", api_key: "", provider_type: "hetzner" });
  const [addingProvider, setAddingProvider] = useState(false);

  // AI Config State
  const [aiProvider, setAiProvider] = useState("mock");
  const [aiKeys, setAiKeys] = useState({ openai: "", grok: "", gemini: "" });
  const [aiProviders, setAiProviders] = useState<{id: string; name: string; configured: boolean; active: boolean}[]>([]);
  const [loadingAI, setLoadingAI] = useState(true);
  const [savingAI, setSavingAI] = useState(false);
  const [testingAI, setTestingAI] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [systemConfig, setSystemConfig] = useState<any>(null);

  useEffect(() => {
    fetchProviders();
    fetchAIConfig();
    fetchSystemConfig();
  }, []);

  const fetchSystemConfig = async () => {
    try {
      const config = await systemApi.getConfig();
      setSystemConfig(config);
    } catch (err) {
      console.error("Failed to fetch system config", err);
    }
  };

  const fetchAIConfig = async () => {
    try {
      const res = await api.get("/cloud/intelligence/ai_config/");
      setAiProvider(res.data.active_provider === "Mock AI" ? "mock" : res.data.active_provider?.toLowerCase() || "mock");
      setAiProviders(res.data.providers || []);
    } catch (err) {
      console.error("Failed to fetch AI config", err);
    } finally {
      setLoadingAI(false);
    }
  };

  const handleSaveAI = async () => {
    setSavingAI(true);
    try {
      await api.post("/cloud/intelligence/update_ai_config/", {
        provider: aiProvider,
        api_key: aiKeys[aiProvider as keyof typeof aiKeys] || "",
      });
      toast({ title: "AI Configuration saved", description: `Provider set to ${aiProvider}.` });
      fetchAIConfig();
    } catch (err) {
      toast({ title: "Error", description: "Failed to save AI config.", variant: "destructive" });
    } finally {
      setSavingAI(false);
    }
  };

  const handleTestAI = async () => {
    setTestingAI(true);
    try {
      const res = await api.post("/cloud/intelligence/ask/", { message: "Hello, confirm you are working." });
      toast({ title: `${res.data.provider} responded`, description: res.data.response?.substring(0, 100) + "..." });
    } catch (err) {
      toast({ title: "Test failed", description: "Could not reach AI provider.", variant: "destructive" });
    } finally {
      setTestingAI(false);
    }
  };

  const fetchProviders = async () => {
    try {
      const res = await api.get("/cloud/providers/");
      const data = Array.isArray(res.data) ? res.data : (res.data?.results || []);
      setProviders(data);
    } catch (err) {
      console.error("Failed to fetch providers:", err);
    } finally {
      setLoadingProviders(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    await new Promise(r => setTimeout(r, 1000));
    setSaving(false);
    toast({ title: "Settings saved", description: "Your preferences have been updated." });
  };

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
    } catch (err) {
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
    } catch (err) {
      toast({ title: "Error", description: "Failed to remove provider.", variant: "destructive" });
    }
  };

  return (
    <DashboardShell>
    <div className="container mx-auto py-10 max-w-4xl relative z-10">
      <PageHeader
        title="Settings"
        description="Manage your account and preferences."
        icon={<SettingsIcon className="h-8 w-8 text-primary" />}
        breadcrumbs={[{ label: "Settings" }]}
        backHref="/dashboard"
      />

      <Tabs defaultValue="profile" className="space-y-6">
        <TabsList className="grid grid-cols-5 w-full">
          <TabsTrigger value="profile" className="flex items-center gap-2">
            <User className="h-4 w-4" /> Profile
          </TabsTrigger>
          <TabsTrigger value="notifications" className="flex items-center gap-2">
            <Bell className="h-4 w-4" /> Notifications
          </TabsTrigger>
          <TabsTrigger value="security" className="flex items-center gap-2">
            <Shield className="h-4 w-4" /> Security
          </TabsTrigger>
          <TabsTrigger value="providers" className="flex items-center gap-2">
            <Cloud className="h-4 w-4" /> Providers
          </TabsTrigger>
          <TabsTrigger value="ai" className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" /> AI
          </TabsTrigger>
          <TabsTrigger value="system" className="flex items-center gap-2">
            <SettingsIcon className="h-4 w-4" /> System
          </TabsTrigger>
        </TabsList>

        <TabsContent value="profile">
          <Card>
            <CardHeader>
              <CardTitle>Profile Settings</CardTitle>
              <CardDescription>Update your personal information.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>First Name</Label>
                  <Input placeholder="John" />
                </div>
                <div className="space-y-2">
                  <Label>Last Name</Label>
                  <Input placeholder="Doe" />
                </div>
              </div>
              <div className="space-y-2">
                <Label>Email</Label>
                <Input type="email" placeholder="john@example.com" />
              </div>
              <div className="flex gap-2">
                <Button onClick={handleSave} disabled={saving}>
                  {saving ? "Saving..." : "Save Changes"}
                </Button>
                <Link href="/dashboard">
                  <Button variant="outline">Cancel</Button>
                </Link>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="notifications">
          <Card>
            <CardHeader>
              <CardTitle>Notification Preferences</CardTitle>
              <CardDescription>Choose how you want to be notified.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between p-4 border rounded-lg">
                <div>
                  <p className="font-medium">Email Notifications</p>
                  <p className="text-sm text-muted-foreground">Receive deployment updates via email</p>
                </div>
                <Button variant="outline" size="sm">Enable</Button>
              </div>
              <div className="flex items-center justify-between p-4 border rounded-lg">
                <div>
                  <p className="font-medium">Slack Integration</p>
                  <p className="text-sm text-muted-foreground">Get alerts in your Slack channel</p>
                </div>
                <Button variant="outline" size="sm">Connect</Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="security">
          <Card>
            <CardHeader>
              <CardTitle>Security Settings</CardTitle>
              <CardDescription>Manage your account security.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>Current Password</Label>
                <Input type="password" placeholder="••••••••" />
              </div>
              <div className="space-y-2">
                <Label>New Password</Label>
                <Input type="password" placeholder="••••••••" />
              </div>
              <div className="space-y-2">
                <Label>Confirm New Password</Label>
                <Input type="password" placeholder="••••••••" />
              </div>
              <div className="flex gap-2">
                <Button variant="outline">Change Password</Button>
                <Link href="/dashboard">
                  <Button variant="ghost">Cancel</Button>
                </Link>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="providers">
          <div className="space-y-6">
            {/* Add New Provider */}
            <Card>
              <CardHeader>
                <CardTitle>Add Cloud Provider</CardTitle>
                <CardDescription>Connect a new cloud infrastructure provider.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-3 gap-4">
                  <div className="space-y-2">
                    <Label>Provider Name</Label>
                    <Input 
                      placeholder="My Hetzner Account" 
                      value={newProvider.name}
                      onChange={(e) => setNewProvider({...newProvider, name: e.target.value})}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>Provider Type</Label>
                    <select 
                      className="w-full h-10 px-3 border rounded-md bg-background"
                      value={newProvider.provider_type}
                      onChange={(e) => setNewProvider({...newProvider, provider_type: e.target.value})}
                    >
                      <option value="hetzner">Hetzner Cloud</option>
                      <option value="digitalocean">DigitalOcean</option>
                      <option value="aws">AWS</option>
                      <option value="gcp">Google Cloud</option>
                      <option value="azure">Azure</option>
                    </select>
                  </div>
                  <div className="space-y-2">
                    <Label>API Key</Label>
                    <Input 
                      type="password" 
                      placeholder="Enter API key" 
                      value={newProvider.api_key}
                      onChange={(e) => setNewProvider({...newProvider, api_key: e.target.value})}
                    />
                  </div>
                </div>
                <Button onClick={handleAddProvider} disabled={addingProvider}>
                  {addingProvider ? (
                    <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Adding...</>
                  ) : (
                    <><Plus className="mr-2 h-4 w-4" /> Add Provider</>
                  )}
                </Button>
              </CardContent>
            </Card>

            {/* Connected Providers */}
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
                          <Button 
                            variant="ghost" 
                            size="icon"
                            onClick={() => handleDeleteProvider(provider.id)}
                            className="text-red-500 hover:text-red-600 hover:bg-red-50"
                          >
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
        </TabsContent>

        {/* AI Configuration Tab */}
        <TabsContent value="ai">
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="h-5 w-5 text-emerald-500" /> AI Provider
                </CardTitle>
                <CardDescription>Choose your AI provider for the deployment assistant.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-3 gap-3">
                  {[{id: 'openai', name: 'OpenAI', desc: 'GPT-4o'}, {id: 'grok', name: 'Grok', desc: 'xAI'}, {id: 'gemini', name: 'Gemini', desc: 'Google'}].map(p => (
                    <button
                      key={p.id}
                      onClick={() => setAiProvider(p.id)}
                      className={`p-4 rounded-lg border-2 text-left transition-all ${
                        aiProvider === p.id
                          ? 'border-emerald-500 bg-emerald-500/10'
                          : 'border-border hover:border-muted-foreground/30'
                      }`}
                    >
                      <div className="font-semibold text-sm">{p.name}</div>
                      <div className="text-xs text-muted-foreground">{p.desc}</div>
                      {aiProviders.find(ap => ap.id === p.id)?.configured && (
                        <Badge variant="outline" className="mt-2 text-[10px] bg-green-500/10 text-green-500 border-green-500/30">Key Set</Badge>
                      )}
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>API Key</CardTitle>
                <CardDescription>Enter the API key for your selected provider. Keys are encrypted at rest.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="ai-key">{aiProvider === 'openai' ? 'OpenAI' : aiProvider === 'grok' ? 'Grok (xAI)' : 'Gemini'} API Key</Label>
                  <div className="relative">
                    <Input
                      id="ai-key"
                      type={showKey ? 'text' : 'password'}
                      placeholder={aiProvider === 'openai' ? 'sk-...' : aiProvider === 'grok' ? 'xai-...' : 'AI...'}
                      value={aiKeys[aiProvider as keyof typeof aiKeys] || ''}
                      onChange={(e) => setAiKeys(prev => ({ ...prev, [aiProvider]: e.target.value }))}
                    />
                    <button
                      type="button"
                      onClick={() => setShowKey(!showKey)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>

                <div className="flex gap-3">
                  <Button onClick={handleSaveAI} disabled={savingAI}>
                    {savingAI ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}
                    Save Configuration
                  </Button>
                  <Button variant="outline" onClick={handleTestAI} disabled={testingAI}>
                    {testingAI ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                    Test Connection
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
        <TabsContent value="system">
          <Card>
            <CardHeader>
              <CardTitle>System Configuration</CardTitle>
              <CardDescription>Server-side configuration variables (Read-only).</CardDescription>
            </CardHeader>
            <CardContent>
              {systemConfig ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Variable</TableHead>
                      <TableHead>Value</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    <TableRow>
                      <TableCell className="font-mono">VERSION</TableCell>
                      <TableCell>{systemConfig.VERSION}</TableCell>
                      <TableCell><Badge variant="outline">Info</Badge></TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-mono">DEBUG_MODE</TableCell>
                      <TableCell>{systemConfig.DEBUG ? "Enabled" : "Disabled"}</TableCell>
                      <TableCell>
                        <Badge variant={systemConfig.DEBUG ? "destructive" : "default"}>
                          {systemConfig.DEBUG ? "Unsafe" : "Secure"}
                        </Badge>
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-mono">DOMAIN</TableCell>
                      <TableCell>{systemConfig.DOMAIN}</TableCell>
                      <TableCell><Badge variant="outline">Config</Badge></TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-mono">SSL_ENABLED</TableCell>
                      <TableCell>{systemConfig.USE_SSL ? "True" : "False"}</TableCell>
                      <TableCell>
                        <Badge variant={systemConfig.USE_SSL ? "default" : "secondary"}>
                          {systemConfig.USE_SSL ? "Secure" : "Insecure"}
                        </Badge>
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-mono">ALLOWED_HOSTS</TableCell>
                      <TableCell className="max-w-[300px] truncate" title={JSON.stringify(systemConfig.ALLOWED_HOSTS)}>
                        {Array.isArray(systemConfig.ALLOWED_HOSTS) ? systemConfig.ALLOWED_HOSTS.join(", ") : String(systemConfig.ALLOWED_HOSTS)}
                      </TableCell>
                      <TableCell><Badge variant="outline">Security</Badge></TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-mono">CORS_ORIGINS</TableCell>
                      <TableCell className="max-w-[300px] truncate" title={JSON.stringify(systemConfig.CORS_ALLOWED_ORIGINS)}>
                        {Array.isArray(systemConfig.CORS_ALLOWED_ORIGINS) ? systemConfig.CORS_ALLOWED_ORIGINS.join(", ") : String(systemConfig.CORS_ALLOWED_ORIGINS)}
                      </TableCell>
                      <TableCell><Badge variant="outline">Security</Badge></TableCell>
                    </TableRow>
                     <TableRow>
                      <TableCell className="font-mono">TIME_ZONE</TableCell>
                      <TableCell>{systemConfig.TIME_ZONE}</TableCell>
                      <TableCell><Badge variant="outline">Info</Badge></TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              ) : (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
    </DashboardShell>
  );
}
