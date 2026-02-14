"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { Settings as SettingsIcon, User, Bell, Shield, Cloud, Plus, Trash2, Check, Loader2, Sparkles, Eye, EyeOff, Key, Server } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import api, { systemApi, aiApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { OAuthTab } from "@/components/settings/OAuthTab";
import { GitHubIntegrationCard } from "@/components/settings/GitHubIntegrationCard";

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

  // Profile state
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");

  // Password state
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);

  // Notification state
  const [emailNotifs, setEmailNotifs] = useState(false);
  const [slackConnected, setSlackConnected] = useState(false);

  // AI Config State
  const [aiData, setAiData] = useState<any>(null);
  const [loadingAI, setLoadingAI] = useState(true);
  const [testingAI, setTestingAI] = useState(false);
  const [systemConfig, setSystemConfig] = useState<any>(null);
  const [aiKeys, setAiKeys] = useState<Record<string, string>>({});
  const [aiModels, setAiModels] = useState<Record<string, string>>({});

  useEffect(() => {
    fetchProviders();
    fetchAIConfig();
    fetchSystemConfig();
    fetchProfile();
  }, []);

  const fetchProfile = async () => {
    try {
      const res = await api.get('/auth/user/');
      setFirstName(res.data.first_name || '');
      setLastName(res.data.last_name || '');
      setEmail(res.data.email || '');
    } catch (err) {
      console.error('Failed to fetch profile', err);
    }
  };

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
      const result = await aiApi.getProviders(true);
      setAiData(result);
    } catch (err) {
      console.error("Failed to fetch AI config", err);
    } finally {
      setLoadingAI(false);
    }
  };

  const handleTestAI = async () => {
    setTestingAI(true);
    try {
      const result = await aiApi.testPrompt("Hello, confirm you are working.");
      toast({ title: `${result.provider} responded`, description: result.response?.substring(0, 100) + "..." });
    } catch (err) {
      toast({ title: "Test failed", description: "Could not reach AI provider.", variant: "destructive" });
    } finally {
      setTestingAI(false);
    }
  };

  const handleChangePassword = async () => {
    if (!currentPassword || !newPassword || !confirmPassword) {
      toast({ title: "Error", description: "Please fill all password fields.", variant: "destructive" });
      return;
    }
    if (newPassword !== confirmPassword) {
      toast({ title: "Error", description: "New passwords do not match.", variant: "destructive" });
      return;
    }
    if (newPassword.length < 8) {
      toast({ title: "Error", description: "Password must be at least 8 characters.", variant: "destructive" });
      return;
    }
    setChangingPassword(true);
    try {
      await api.post('/auth/password/change/', {
        old_password: currentPassword,
        new_password1: newPassword,
        new_password2: confirmPassword,
      });
      toast({ title: "Password changed", description: "Your password has been updated." });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch (err: any) {
      const detail = err?.response?.data?.old_password?.[0] || err?.response?.data?.new_password2?.[0] || "Failed to change password.";
      toast({ title: "Error", description: detail, variant: "destructive" });
    } finally {
      setChangingPassword(false);
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
    try {
      await api.patch('/auth/user/', {
        first_name: firstName,
        last_name: lastName,
        email: email,
      });
      toast({ title: "Profile saved", description: "Your profile has been updated." });
    } catch (err) {
      toast({ title: "Error", description: "Failed to save profile.", variant: "destructive" });
    } finally {
      setSaving(false);
    }
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
        <TabsList className="flex flex-wrap w-full gap-1">
          <TabsTrigger value="profile" className="flex items-center gap-2">
            <User className="h-4 w-4" /> Profile
          </TabsTrigger>
          <TabsTrigger value="notifications" className="flex items-center gap-2">
            <Bell className="h-4 w-4" /> Alerts
          </TabsTrigger>
          <TabsTrigger value="security" className="flex items-center gap-2">
            <Shield className="h-4 w-4" /> Security
          </TabsTrigger>
          <TabsTrigger value="providers" className="flex items-center gap-2">
            <Cloud className="h-4 w-4" /> Cloud
          </TabsTrigger>
          <TabsTrigger value="ai" className="flex items-center gap-2">
            <Sparkles className="h-4 w-4" /> AI
          </TabsTrigger>
          <TabsTrigger value="oauth" className="flex items-center gap-2">
            <Key className="h-4 w-4" /> OAuth
          </TabsTrigger>
          <TabsTrigger value="infra" className="flex items-center gap-2">
            <Server className="h-4 w-4" /> Infra
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
                  <Input placeholder="John" value={firstName} onChange={(e) => setFirstName(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Last Name</Label>
                  <Input placeholder="Doe" value={lastName} onChange={(e) => setLastName(e.target.value)} />
                </div>
              </div>
              <div className="space-y-2">
                <Label>Email</Label>
                <Input type="email" placeholder="john@example.com" value={email} onChange={(e) => setEmail(e.target.value)} />
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
                <Button
                  variant={emailNotifs ? "default" : "outline"}
                  size="sm"
                  onClick={() => {
                    setEmailNotifs(!emailNotifs);
                    toast({ title: emailNotifs ? "Disabled" : "Enabled", description: `Email notifications ${emailNotifs ? 'disabled' : 'enabled'}.` });
                  }}
                >
                  {emailNotifs ? <><Check className="h-3 w-3 mr-1" /> Enabled</> : "Enable"}
                </Button>
              </div>
              <div className="flex items-center justify-between p-4 border rounded-lg">
                <div>
                  <p className="font-medium">Slack Integration</p>
                  <p className="text-sm text-muted-foreground">Get alerts in your Slack channel</p>
                </div>
                <Button
                  variant={slackConnected ? "default" : "outline"}
                  size="sm"
                  onClick={() => {
                    setSlackConnected(!slackConnected);
                    toast({ title: slackConnected ? "Disconnected" : "Connected", description: `Slack integration ${slackConnected ? 'disconnected' : 'connected'}.` });
                  }}
                >
                  {slackConnected ? <><Check className="h-3 w-3 mr-1" /> Connected</> : "Connect"}
                </Button>
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
                <Input type="password" placeholder="••••••••" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label>New Password</Label>
                <Input type="password" placeholder="••••••••" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label>Confirm New Password</Label>
                <Input type="password" placeholder="••••••••" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} />
              </div>
              <div className="flex gap-2">
                <Button onClick={handleChangePassword} disabled={changingPassword}>
                  {changingPassword ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Changing...</> : "Change Password"}
                </Button>
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

        {/* AI Engine Tab */}
        <TabsContent value="ai">
          <div className="space-y-6">
            {/* Mode & Status */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="h-5 w-5 text-emerald-500" /> AI Engine
                </CardTitle>
                <CardDescription>
                  {aiData?.mode_label || 'Loading...'}
                  {aiData?.mode === 'senate_committee' && ' — Providers debate and vote on each answer.'}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* Mode Badge */}
                <div className="flex items-center gap-3">
                  <Badge variant={aiData?.mode === 'senate_committee' ? 'default' : aiData?.mode === 'solo' ? 'secondary' : 'outline'}>
                    {aiData?.mode === 'senate_committee' ? '🏛️ Senate Committee' : aiData?.mode === 'solo' ? '⚡ Solo Mode' : '🤖 Mock Mode'}
                  </Badge>
                  <span className="text-sm text-muted-foreground">
                    {aiData?.active_count || 0} of {aiData?.total_available || 0} active
                  </span>
                </div>

                {/* Provider Grid — Editable */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {aiData?.providers?.map((p: any) => {
                    const modelOptions: Record<string, string[]> = {
                      openai: ['gpt-4o-mini', 'gpt-4o', 'gpt-4-turbo', 'gpt-3.5-turbo', 'o1-mini', 'o1-preview'],
                      grok: ['grok-3-mini', 'grok-3', 'grok-2', 'grok-beta'],
                      gemini: ['gemini-2.0-flash', 'gemini-2.0-pro', 'gemini-1.5-flash', 'gemini-1.5-pro'],
                      claude: ['claude-sonnet-4-20250514', 'claude-opus-4-20250514', 'claude-3-5-sonnet-20241022', 'claude-3-haiku-20240307'],
                    };
                    return (
                      <div
                        key={p.id}
                        className={`p-4 rounded-lg border-2 transition-all ${
                          p.configured
                            ? 'border-emerald-500/50 bg-emerald-500/5'
                            : 'border-border'
                        }`}
                      >
                        <div className="flex items-center justify-between mb-3">
                          <div className="font-semibold text-sm">{p.name}</div>
                          {p.configured ? (
                            <Badge variant="outline" className="text-[10px] bg-green-500/10 text-green-500 border-green-500/30">
                              <Check className="h-3 w-3 mr-1" /> Active
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="text-[10px]">Not Set</Badge>
                          )}
                        </div>

                        {/* API Key Input */}
                        <div className="space-y-2 mb-2">
                          <Label className="text-xs text-muted-foreground">API Key</Label>
                          <Input
                            type="password"
                            placeholder={p.configured ? '••••••••' : 'Enter API key'}
                            className="h-8 text-xs"
                            value={aiKeys[p.id] || ''}
                            onChange={(e) => setAiKeys(prev => ({ ...prev, [p.id]: e.target.value }))}
                          />
                        </div>

                        {/* Model Selector */}
                        <div className="space-y-2 mb-3">
                          <Label className="text-xs text-muted-foreground">Model</Label>
                          <select
                            className="w-full h-8 px-2 text-xs border rounded-md bg-background"
                            value={aiModels[p.id] || p.model || ''}
                            onChange={(e) => setAiModels(prev => ({ ...prev, [p.id]: e.target.value }))}
                          >
                            {(modelOptions[p.id] || []).map((m: string) => (
                              <option key={m} value={m}>{m}</option>
                            ))}
                          </select>
                        </div>

                        {p.balance && (
                          <div className="text-xs text-yellow-500 font-medium">
                            💰 {p.balance.balance}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Save + Actions */}
                <div className="flex gap-3 flex-wrap">
                  <Button
                    variant="default"
                    onClick={async () => {
                      setSaving(true);
                      try {
                        const data: Record<string, string> = {};
                        ['openai', 'grok', 'gemini', 'claude'].forEach((id) => {
                          if (aiKeys[id]) data[`${id}_api_key`] = aiKeys[id];
                          if (aiModels[id]) data[`${id}_model`] = aiModels[id];
                        });
                        await aiApi.updateProviders(data);
                        toast({ title: "AI Config Saved", description: "Provider settings updated." });
                        fetchAIConfig();
                      } catch (err) {
                        toast({ title: "Error", description: "Failed to save AI config.", variant: "destructive" });
                      } finally {
                        setSaving(false);
                      }
                    }}
                    disabled={saving}
                  >
                    {saving ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Saving...</> : 'Save AI Config'}
                  </Button>
                  <Button variant="outline" onClick={handleTestAI} disabled={testingAI}>
                    {testingAI ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                    Test AI
                  </Button>
                  <Button variant="outline" onClick={fetchAIConfig} disabled={loadingAI}>
                    <Loader2 className={`mr-2 h-4 w-4 ${loadingAI ? 'animate-spin' : ''}`} />
                    Refresh
                  </Button>
                  <Link href="/settings/ai">
                    <Button variant="secondary">
                      <Sparkles className="mr-2 h-4 w-4" /> Full AI Dashboard
                    </Button>
                  </Link>
                </div>

                <p className="text-xs text-muted-foreground">
                  Set keys and models above, or via env vars (OPENAI_API_KEY, GROK_API_KEY, etc.), or admin panel.
                </p>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
        {/* OAuth Configuration Tab */}
        <TabsContent value="oauth">
          <div className="space-y-6">
            <GitHubIntegrationCard />
            <OAuthTab />
          </div>
        </TabsContent>

        {/* Infrastructure Tab */}
        <TabsContent value="infra">
          {systemConfig ? (
            <div className="space-y-6">
              {/* Redis / Celery */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Server className="h-5 w-5 text-orange-500" /> Redis & Celery</CardTitle>
                  <CardDescription>Task queue and caching infrastructure.</CardDescription>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                    <TableBody>
                      <TableRow>
                        <TableCell className="font-mono">REDIS_HOST</TableCell>
                        <TableCell>{systemConfig.REDIS_HOST}</TableCell>
                        <TableCell><Badge variant="outline">Config</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">REDIS_PORT</TableCell>
                        <TableCell>{systemConfig.REDIS_PORT}</TableCell>
                        <TableCell><Badge variant="outline">Config</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">REDIS_PASSWORD</TableCell>
                        <TableCell>{systemConfig.REDIS_PASSWORD_SET ? "Set" : "Not set"}</TableCell>
                        <TableCell><Badge variant={systemConfig.REDIS_PASSWORD_SET ? "default" : "secondary"}>{systemConfig.REDIS_PASSWORD_SET ? "Secure" : "Unprotected"}</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">CELERY_BACKEND</TableCell>
                        <TableCell>{systemConfig.CELERY_RESULT_BACKEND}</TableCell>
                        <TableCell><Badge variant="outline">Config</Badge></TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>

              {/* Container Registry */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Cloud className="h-5 w-5 text-blue-500" /> Container Registry</CardTitle>
                  <CardDescription>Docker image registry for deployments.</CardDescription>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                    <TableBody>
                      <TableRow>
                        <TableCell className="font-mono">REGISTRY_URL</TableCell>
                        <TableCell>{systemConfig.CONTAINER_REGISTRY_URL || "Not set"}</TableCell>
                        <TableCell><Badge variant="outline">Config</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">REGISTRY_USER</TableCell>
                        <TableCell>{systemConfig.REGISTRY_USER}</TableCell>
                        <TableCell><Badge variant="outline">Config</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">REGISTRY_PASSWORD</TableCell>
                        <TableCell>{systemConfig.REGISTRY_PASSWORD_SET ? "Set" : "Not set"}</TableCell>
                        <TableCell><Badge variant={systemConfig.REGISTRY_PASSWORD_SET ? "default" : "secondary"}>{systemConfig.REGISTRY_PASSWORD_SET ? "Secure" : "Missing"}</Badge></TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>

              {/* Rate Limiting */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Shield className="h-5 w-5 text-yellow-500" /> Rate Limiting</CardTitle>
                  <CardDescription>API throttle rates per client type.</CardDescription>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>Scope</TableHead><TableHead>Limit</TableHead><TableHead>Type</TableHead></TableRow></TableHeader>
                    <TableBody>
                      {systemConfig.THROTTLE_RATES && Object.entries(systemConfig.THROTTLE_RATES).map(([key, value]) => (
                        <TableRow key={key}>
                          <TableCell className="font-mono">{key}</TableCell>
                          <TableCell>{String(value)}</TableCell>
                          <TableCell><Badge variant="outline">Throttle</Badge></TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>

              {/* Database */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><SettingsIcon className="h-5 w-5 text-purple-500" /> Database</CardTitle>
                  <CardDescription>Database connection info (read-only).</CardDescription>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                    <TableBody>
                      <TableRow>
                        <TableCell className="font-mono">ENGINE</TableCell>
                        <TableCell className="truncate max-w-[300px]">{systemConfig.DATABASE_ENGINE}</TableCell>
                        <TableCell><Badge variant="outline">Info</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">DATABASE</TableCell>
                        <TableCell>{systemConfig.DATABASE_NAME}</TableCell>
                        <TableCell><Badge variant="outline">Info</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">HOST</TableCell>
                        <TableCell>{systemConfig.DATABASE_HOST}</TableCell>
                        <TableCell><Badge variant="outline">Info</Badge></TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>
          ) : (
            <div className="flex items-center justify-center py-8"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
          )}
        </TabsContent>

        {/* System Config Tab (General + Security + Network + Auth) */}
        <TabsContent value="system">
          {systemConfig ? (
            <div className="space-y-6">
              {/* General */}
              <Card>
                <CardHeader>
                  <CardTitle>General</CardTitle>
                  <CardDescription>Core platform settings.</CardDescription>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                    <TableBody>
                      <TableRow>
                        <TableCell className="font-mono">VERSION</TableCell>
                        <TableCell>{systemConfig.VERSION}</TableCell>
                        <TableCell><Badge variant="outline">Info</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">DEBUG</TableCell>
                        <TableCell>{systemConfig.DEBUG ? "Enabled" : "Disabled"}</TableCell>
                        <TableCell><Badge variant={systemConfig.DEBUG ? "destructive" : "default"}>{systemConfig.DEBUG ? "Unsafe" : "Secure"}</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">DOMAIN</TableCell>
                        <TableCell>{systemConfig.DOMAIN}</TableCell>
                        <TableCell><Badge variant="outline">Config</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">TIME_ZONE</TableCell>
                        <TableCell>{systemConfig.TIME_ZONE}</TableCell>
                        <TableCell><Badge variant="outline">Info</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">SITE_ID</TableCell>
                        <TableCell>{systemConfig.SITE_ID}</TableCell>
                        <TableCell><Badge variant="outline">Info</Badge></TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>

              {/* Security */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Shield className="h-5 w-5 text-red-500" /> Security</CardTitle>
                  <CardDescription>TLS, HSTS, cookies, and signature verification.</CardDescription>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                    <TableBody>
                      <TableRow>
                        <TableCell className="font-mono">SSL_REDIRECT</TableCell>
                        <TableCell>{systemConfig.SECURE_SSL_REDIRECT ? "Enabled" : "Disabled"}</TableCell>
                        <TableCell><Badge variant={systemConfig.SECURE_SSL_REDIRECT ? "default" : "secondary"}>{systemConfig.SECURE_SSL_REDIRECT ? "Secure" : "Insecure"}</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">HSTS</TableCell>
                        <TableCell>{systemConfig.SECURE_HSTS_SECONDS ? `${systemConfig.SECURE_HSTS_SECONDS}s` : "Disabled"}</TableCell>
                        <TableCell><Badge variant={systemConfig.SECURE_HSTS_SECONDS ? "default" : "secondary"}>{systemConfig.SECURE_HSTS_SECONDS ? "Active" : "Off"}</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">HSTS_SUBDOMAINS</TableCell>
                        <TableCell>{systemConfig.SECURE_HSTS_INCLUDE_SUBDOMAINS ? "Yes" : "No"}</TableCell>
                        <TableCell><Badge variant="outline">Security</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">HSTS_PRELOAD</TableCell>
                        <TableCell>{systemConfig.SECURE_HSTS_PRELOAD ? "Yes" : "No"}</TableCell>
                        <TableCell><Badge variant="outline">Security</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">SESSION_COOKIE_SECURE</TableCell>
                        <TableCell>{systemConfig.SESSION_COOKIE_SECURE ? "Yes" : "No"}</TableCell>
                        <TableCell><Badge variant={systemConfig.SESSION_COOKIE_SECURE ? "default" : "secondary"}>{systemConfig.SESSION_COOKIE_SECURE ? "Secure" : "Insecure"}</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">CSRF_COOKIE_SECURE</TableCell>
                        <TableCell>{systemConfig.CSRF_COOKIE_SECURE ? "Yes" : "No"}</TableCell>
                        <TableCell><Badge variant={systemConfig.CSRF_COOKIE_SECURE ? "default" : "secondary"}>{systemConfig.CSRF_COOKIE_SECURE ? "Secure" : "Insecure"}</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">SIGNATURE_CHECK</TableCell>
                        <TableCell>{systemConfig.SMSLY_DISABLE_SIGNATURE_CHECK ? "Disabled" : "Enabled"}</TableCell>
                        <TableCell><Badge variant={systemConfig.SMSLY_DISABLE_SIGNATURE_CHECK ? "destructive" : "default"}>{systemConfig.SMSLY_DISABLE_SIGNATURE_CHECK ? "Unsafe" : "Secure"}</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">WEBHOOK_SECRET</TableCell>
                        <TableCell>{systemConfig.GITHUB_WEBHOOK_SECRET_SET ? "Set" : "Not set"}</TableCell>
                        <TableCell><Badge variant={systemConfig.GITHUB_WEBHOOK_SECRET_SET ? "default" : "destructive"}>{systemConfig.GITHUB_WEBHOOK_SECRET_SET ? "Secure" : "Missing"}</Badge></TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>

              {/* Network */}
              <Card>
                <CardHeader>
                  <CardTitle>Network & CORS</CardTitle>
                  <CardDescription>Allowed hosts, CORS origins, and CSRF trusted origins.</CardDescription>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                    <TableBody>
                      <TableRow>
                        <TableCell className="font-mono">ALLOWED_HOSTS</TableCell>
                        <TableCell className="max-w-[300px] truncate" title={JSON.stringify(systemConfig.ALLOWED_HOSTS)}>{Array.isArray(systemConfig.ALLOWED_HOSTS) ? systemConfig.ALLOWED_HOSTS.join(", ") : String(systemConfig.ALLOWED_HOSTS)}</TableCell>
                        <TableCell><Badge variant="outline">Security</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">CORS_ORIGINS</TableCell>
                        <TableCell className="max-w-[300px] truncate" title={JSON.stringify(systemConfig.CORS_ALLOWED_ORIGINS)}>{Array.isArray(systemConfig.CORS_ALLOWED_ORIGINS) ? systemConfig.CORS_ALLOWED_ORIGINS.join(", ") : String(systemConfig.CORS_ALLOWED_ORIGINS)}</TableCell>
                        <TableCell><Badge variant="outline">Security</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">CSRF_ORIGINS</TableCell>
                        <TableCell className="max-w-[300px] truncate" title={JSON.stringify(systemConfig.CSRF_TRUSTED_ORIGINS)}>{Array.isArray(systemConfig.CSRF_TRUSTED_ORIGINS) ? systemConfig.CSRF_TRUSTED_ORIGINS.join(", ") : String(systemConfig.CSRF_TRUSTED_ORIGINS)}</TableCell>
                        <TableCell><Badge variant="outline">Security</Badge></TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>

              {/* Auth */}
              <Card>
                <CardHeader>
                  <CardTitle>Authentication</CardTitle>
                  <CardDescription>Login method and redirect configuration.</CardDescription>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                    <TableBody>
                      <TableRow>
                        <TableCell className="font-mono">AUTH_METHOD</TableCell>
                        <TableCell>{systemConfig.ACCOUNT_AUTH_METHOD}</TableCell>
                        <TableCell><Badge variant="outline">Config</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">LOGIN_REDIRECT</TableCell>
                        <TableCell>{systemConfig.LOGIN_REDIRECT_URL}</TableCell>
                        <TableCell><Badge variant="outline">Config</Badge></TableCell>
                      </TableRow>
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>
          ) : (
            <div className="flex items-center justify-center py-8"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>
          )}
        </TabsContent>
      </Tabs>
    </div>
    </DashboardShell>
  );
}
