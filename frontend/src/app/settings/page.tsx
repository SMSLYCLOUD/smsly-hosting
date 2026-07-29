"use client";

// TODO: Migrate this page to React Query (TanStack Query). This 1605-line page
// fetches config, AI providers, teams, OAuth, webhooks, and more via useEffect.
// React Query would eliminate manual loading/error state, provide automatic
// cache invalidation on mutations, and deduplicate concurrent requests.

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { EcosystemSuggestion } from "@/components/dashboard/EcosystemSuggestion";
import { Settings as SettingsIcon, User, Bell, Shield, Cloud, Plus, Trash2, Check, Loader2, Sparkles, Eye, EyeOff, Key, Server, Globe, Lock, Users, Copy } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import api, { systemApi, aiApi, coreApi, teamsApi } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { OAuthTab } from "@/components/settings/OAuthTab";
import { GitIntegrationCard } from "@/components/settings/GitIntegrationCard";
import { WebhookConfigCard } from "@/components/settings/WebhookConfigCard";
import { CloudStorageTab } from "@/components/settings/CloudStorageTab";
import { PlatformSettingsTab } from "@/components/settings/PlatformSettingsTab";
import { SecurityTab } from "@/components/settings/SecurityTab";
import { TeamsTab } from "@/components/settings/TeamsTab";
import { AlertsTab } from "@/components/settings/AlertsTab";
import { RegistryCredentialsTab } from "@/components/settings/RegistryCredentialsTab";
import { ScopedRegistryTab } from "@/components/settings/ScopedRegistryTab";
import BackupKeysTab from "@/components/settings/BackupKeysTab";
import { Switch } from "@/components/ui/switch";
import UpdateTerminalStream from "@/components/terminal/UpdateTerminalStream";
import { cn } from "@/lib/utils";
import { useConfirm } from "@/components/ui/confirm-dialog";

interface CloudProvider {
  id: string;
  name: string;
  provider_type: string;
  is_active: boolean;
  created_at: string;
}

type MaintenanceAction = "clear" | "refresh" | "update" | "registry_gc" | "build_cache";
type MaintenanceState = "idle" | "queued" | "running" | "success" | "error";

interface MaintenanceTaskState {
  status: MaintenanceState;
  taskId?: string | null;
  message?: string;
}

const INITIAL_MAINTENANCE_STATE: Record<MaintenanceAction, MaintenanceTaskState> = {
  clear: { status: "idle" },
  refresh: { status: "idle" },
  registry_gc: { status: "idle" },
  build_cache: { status: "idle" },
  update: { status: "idle" },
};

const MAINTENANCE_COPY: Record<MaintenanceAction, {
  title: string;
  message: string;
  confirmText: string;
  variant?: "default" | "destructive";
}> = {
  clear: {
    title: "Clear all system caches?",
    message: "This will force a refresh of all internal caches. It's safe but might cause a temporary spike in database load.",
    confirmText: "Clear Caches",
  },
  refresh: {
    title: "Sync Proxy Routing?",
    message: "This regenerates the proxy configuration and asks the host watcher to reload Caddy.",
    confirmText: "Sync Proxy",
  },
  registry_gc: {
    title: "Garbage Collect Registry?",
    message: "This removes unused layers from the private registry. This cannot be undone.",
    confirmText: "Run GC",
  },
  build_cache: {
    title: "Clear Build Caches?",
    message: "This clears BuildKit and language caches. Next builds might take longer.",
    confirmText: "Clear Caches",
  },
  update: {
    title: "Update Platform?",
    message: "This asks the host updater to pull the latest code and rebuild services. The dashboard may briefly disconnect.",
    confirmText: "Update Platform",
  },
};

const SETTINGS_ROUTE_LINKS = [
  { href: "/settings", label: "General", icon: SettingsIcon, match: (pathname: string) => pathname === "/settings" },
  { href: "/settings/ai", label: "AI Engine", icon: Sparkles, match: (pathname: string) => pathname.startsWith("/settings/ai") },
  { href: "/settings/billing", label: "Billing", icon: Cloud, match: (pathname: string) => pathname.startsWith("/settings/billing") },
  { href: "/settings/team", label: "Team", icon: Users, match: (pathname: string) => pathname.startsWith("/settings/team") },
  { href: "/settings/audit-logs", label: "Audit Logs", icon: Shield, match: (pathname: string) => pathname.startsWith("/settings/audit-logs") },
] as const;

const SETTINGS_SECTIONS = [
  { value: "profile", label: "Profile", icon: User },
  { value: "api-keys", label: "API Keys", icon: Key },
  { value: "team", label: "Team", icon: Users },
  { value: "notifications", label: "Alerts", icon: Bell },
  { value: "security", label: "Security", icon: Shield },
  { value: "providers", label: "Cloud", icon: Cloud },
  { value: "ai", label: "AI", icon: Sparkles },
  { value: "oauth", label: "OAuth", icon: Key },
  { value: "autoscaling", label: "Auto-Scaling", icon: Cloud },
  { value: "cloud-storage", label: "Cloud Storage", icon: Cloud },
  { value: "backups", label: "Backups", icon: Cloud },
  { value: "infra", label: "Infra", icon: Server },
  { value: "platform", label: "Platform", icon: Globe },
  { value: "registry", label: "Registry", icon: Cloud },
  { value: "maintenance", label: "Maintenance", icon: Server },
] as const;

export default function SettingsPage() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const pathname = usePathname();
  const [saving, setSaving] = useState(false);
  const [providers, setProviders] = useState<CloudProvider[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(true);
  const [newProvider, setNewProvider] = useState({ name: "", api_key: "", provider_type: "hetzner" });
  const [addingProvider, setAddingProvider] = useState(false);
  const [maintenanceTasks, setMaintenanceTasks] = useState<Record<MaintenanceAction, MaintenanceTaskState>>(INITIAL_MAINTENANCE_STATE);
  const maintenancePollers = useRef<Partial<Record<MaintenanceAction, ReturnType<typeof setInterval>>>>({});

  // Profile state
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");

  // Password state
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);

  // API Keys state
  const [apiKeys, setApiKeys] = useState<any[]>([]);
  const [newKeyName, setNewKeyName] = useState("");
  const [generatedKey, setGeneratedKey] = useState<string | null>(null);

  // Team state
  const [teams, setTeams] = useState<any[]>([]);
  const [teamMembers, setTeamMembers] = useState<any[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("MEMBER");

  // Notification state
  const [notifPrefs, setNotifPrefs] = useState<any[]>([]);

  // AI Config State
  const [aiData, setAiData] = useState<any>(null);
  const [loadingAI, setLoadingAI] = useState(true);
  const [testingAI, setTestingAI] = useState(false);
  const [systemConfig, setSystemConfig] = useState<any>(null);
  const [aiKeys, setAiKeys] = useState<Record<string, string>>({});
  const [aiModels, setAiModels] = useState<Record<string, string>>({});
  const [aiUrls, setAiUrls] = useState<Record<string, string>>({});

  // Domain & SSL Config State
  const [domainConfig, setDomainConfig] = useState<any>(null);
  const [domainForm, setDomainForm] = useState({
    domain: '',
    use_ssl: false,
    enable_crowdsec_waf: false,
    wildcard_subdomains: true,
    cloudflare_api_token: '',
    server_ip: '',
  });
  const [savingDomain, setSavingDomain] = useState(false);
  const [showCfToken, setShowCfToken] = useState(false);
  const [cfTokenTouched, setCfTokenTouched] = useState(false);
  const [recheckLoading, setRecheckLoading] = useState(false);
  const [recheckLastRun, setRecheckLastRun] = useState<string | null>(null);

  const fetchProviders = useCallback(async () => {
    try {
      const res = await api.get("/cloud/providers/");
      const data = Array.isArray(res.data) ? res.data : (res.data?.results || []);
      setProviders(data);
    } catch (err) {
      console.error("Failed to fetch providers:", err);
    } finally {
      setLoadingProviders(false);
    }
  }, []);

  const fetchAIConfig = useCallback(async () => {
    try {
      const result = await aiApi.getProviders(true);
      setAiData(result);
    } catch (err) {
      console.error("Failed to fetch AI config", err);
    } finally {
      setLoadingAI(false);
    }
  }, []);

  const fetchSystemConfig = useCallback(async () => {
    try {
      const config = await systemApi.getConfig();
      setSystemConfig(config);
    } catch (err) {
      console.error("Failed to fetch system config", err);
    }
  }, []);

  const fetchProfile = useCallback(async () => {
    try {
      const res = await api.get('/auth/user/');
      setFirstName(res.data.first_name || '');
      setLastName(res.data.last_name || '');
      setEmail(res.data.email || '');
    } catch (err) {
      console.error('Failed to fetch profile', err);
    }
  }, []);

  const fetchDomainConfig = useCallback(async () => {
    try {
      const data = await systemApi.getDomainConfig();
      setDomainConfig(data);
      setDomainForm({
        domain: data.domain || '',
        use_ssl: data.use_ssl || false,
        enable_crowdsec_waf: data.enable_crowdsec_waf || false,
        wildcard_subdomains: data.wildcard_subdomains ?? true,
        cloudflare_api_token: '',
        server_ip: data.server_ip || '',
      });
      setCfTokenTouched(false);
    } catch (err) {
      console.error('Failed to fetch domain config', err);
    }
  }, []);

  const fetchApiKeys = useCallback(async () => {
      try {
        const keys = await coreApi.getApiKeys();
        setApiKeys(keys);
      } catch (err) { console.error(err); }
  }, []);

  const fetchTeams = useCallback(async () => {
      try {
        const t = await teamsApi.list();
        setTeams(t);
        if (t.length > 0) {
            const members = await teamsApi.members(t[0].id);
            setTeamMembers(members);
        }
      } catch (err) { console.error(err); }
  }, []);

  const fetchNotifPrefs = useCallback(async () => {
      try {
        const prefs = await coreApi.getNotificationPreferences();
        setNotifPrefs(prefs);
      } catch (err) { console.error(err); }
  }, []);

  const updateMaintenanceTask = useCallback((action: MaintenanceAction, patch: Partial<MaintenanceTaskState>) => {
    setMaintenanceTasks((prev) => ({
      ...prev,
      [action]: { ...prev[action], ...patch },
    }));
  }, []);

  const stopMaintenancePolling = useCallback((action: MaintenanceAction) => {
    const poller = maintenancePollers.current[action];
    if (poller) {
      clearInterval(poller);
      delete maintenancePollers.current[action];
    }
  }, []);

  const getMaintenanceErrorMessage = useCallback((err: any) => (
    err?.response?.data?.message
    || err?.response?.data?.error?.message
    || err?.response?.data?.error
    || err?.message
    || "Failed to trigger maintenance."
  ), []);

  const finishMaintenanceTask = useCallback((action: MaintenanceAction, response: any) => {
    const result = response?.result && typeof response.result === "object" ? response.result : response;
    const resultStatus = String(result?.status || response?.status || "").toLowerCase();
    const ok = resultStatus === "success" || response?.state === "SUCCESS";
    const message = result?.message || response?.message || (ok ? "Maintenance task completed." : "Maintenance task failed.");

    updateMaintenanceTask(action, {
      status: ok ? "success" : "error",
      taskId: response?.task_id || response?.taskId || null,
      message,
    });
    toast({
      title: ok ? "Maintenance completed" : "Maintenance failed",
      description: message,
      variant: ok ? "success" : "destructive",
    });
  }, [toast, updateMaintenanceTask]);

  const startPlatformUpdatePolling = useCallback((updateId: string) => {
    stopMaintenancePolling("update");

    const poll = async () => {
      try {
        const response = await systemApi.getPlatformUpdate(updateId);
        const status = response?.status || "";

        if (status === "COMPLETED") {
          stopMaintenancePolling("update");
          updateMaintenanceTask("update", {
            status: "success",
            taskId: updateId,
            message: "Platform update completed successfully.",
          });
          toast({
            title: "Update completed",
            description: "Platform update completed successfully.",
            variant: "success",
          });
          return;
        }

        if (status === "FAILED" || status === "ROLLED_BACK") {
          stopMaintenancePolling("update");
          const errorMsg = response?.error_message || "Platform update failed.";
          updateMaintenanceTask("update", {
            status: "error",
            taskId: updateId,
            message: errorMsg,
          });
          toast({
            title: "Update failed",
            description: errorMsg,
            variant: "destructive",
          });
          return;
        }

        updateMaintenanceTask("update", {
          status: "running",
          taskId: updateId,
          message: response?.current_step || `Update is ${status.toLowerCase()}...`,
        });
      } catch {
        updateMaintenanceTask("update", {
          status: "running",
          taskId: updateId,
          message: "Waiting for the backend to reconnect...",
        });
      }
    };

    void poll();
    maintenancePollers.current["update"] = setInterval(poll, 5000);
  }, [stopMaintenancePolling, updateMaintenanceTask, toast]);

  const startMaintenancePolling = useCallback((action: MaintenanceAction, taskId: string) => {
    stopMaintenancePolling(action);

    const poll = async () => {
      try {
        const response = await systemApi.getMaintenanceTask(taskId);
        const state = String(response?.state || "").toUpperCase();
        const statusValue = String(response?.status || "").toLowerCase();

        if (state === "SUCCESS" || state === "FAILURE" || statusValue === "success" || statusValue === "error") {
          stopMaintenancePolling(action);
          if (action === "update" && state === "SUCCESS") {
            const result = response?.result && typeof response.result === "object" ? response.result : null;
            const platformUpdateId = result?.task_id;
            if (platformUpdateId) {
              updateMaintenanceTask(action, {
                status: "queued",
                taskId: platformUpdateId,
                message: "Platform update initiated, tracking progress...",
              });
              startPlatformUpdatePolling(platformUpdateId);
              return;
            }
          }
          finishMaintenanceTask(action, response);
          return;
        }

        updateMaintenanceTask(action, {
          status: statusValue === "queued" ? "queued" : "running",
          taskId,
          message: response?.message || "Maintenance task is running.",
        });
      } catch (err) {
        updateMaintenanceTask(action, {
          status: "running",
          taskId,
          message: action === "update" ? "Waiting for the backend to reconnect..." : "Waiting for task status...",
        });
      }
    };

    void poll();
    maintenancePollers.current[action] = setInterval(poll, 3000);
  }, [finishMaintenanceTask, startPlatformUpdatePolling, stopMaintenancePolling, updateMaintenanceTask]);

  useEffect(() => () => {
    Object.values(maintenancePollers.current).forEach((poller) => {
      if (poller) clearInterval(poller);
    });
  }, []);

  const handleMaintenanceAction = useCallback(async (action: MaintenanceAction) => {
    const copy = MAINTENANCE_COPY[action];
    const confirmed = await confirm({
      title: copy.title,
      message: copy.message,
      confirmText: copy.confirmText,
      variant: copy.variant,
    });
    if (!confirmed) return;

    updateMaintenanceTask(action, {
      status: "queued",
      taskId: null,
      message: "Queueing maintenance task...",
    });

    try {
      const response = await systemApi.runMaintenance(action);
      const taskId = response?.task_id || response?.taskId;
      if (response?.result || response?.status === "success" || response?.status === "error") {
        if (action === "update") {
          const result = response?.result && typeof response.result === "object" ? response.result : response;
          const platformUpdateId = result?.task_id;
          if (platformUpdateId) {
            updateMaintenanceTask(action, {
              status: "queued",
              taskId: platformUpdateId,
              message: "Platform update initiated, tracking progress...",
            });
            startPlatformUpdatePolling(platformUpdateId);
            return;
          }
        }
        finishMaintenanceTask(action, response);
        return;
      }

      updateMaintenanceTask(action, {
        status: "queued",
        taskId,
        message: response?.message || "Task queued successfully.",
      });
      toast({ title: "Maintenance queued", description: response?.message || "Task queued successfully." });
      if (taskId) {
        startMaintenancePolling(action, taskId);
      }
    } catch (err: any) {
      const data = err?.response?.data;
      const taskId = data?.task_id || data?.taskId;
      if (err?.response?.status === 409 && taskId) {
        updateMaintenanceTask(action, {
          status: "running",
          taskId,
          message: data?.message || "This maintenance task is already running.",
        });
        startMaintenancePolling(action, taskId);
        toast({ title: "Already running", description: data?.message || "This maintenance task is already running." });
        return;
      }

      updateMaintenanceTask(action, {
        status: "error",
        taskId: null,
        message: getMaintenanceErrorMessage(err),
      });
      toast({ title: "Error", description: getMaintenanceErrorMessage(err), variant: "destructive" });
    }
  }, [confirm, finishMaintenanceTask, getMaintenanceErrorMessage, startMaintenancePolling, startPlatformUpdatePolling, toast, updateMaintenanceTask]);

  const renderMaintenanceButtonContent = (action: MaintenanceAction, label: string) => {
    const task = maintenanceTasks[action];
    if (task.status === "queued" || task.status === "running") {
      return <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> {task.status === "queued" ? "Queued" : "Running"}</>;
    }
    if (task.status === "success") {
      return <><Check className="mr-2 h-4 w-4" /> Done</>;
    }
    return label;
  };

  useEffect(() => {
    fetchProviders();
    fetchAIConfig();
    fetchSystemConfig();
    fetchProfile();
    fetchDomainConfig();
    fetchApiKeys();
    fetchTeams();
    fetchNotifPrefs();
  }, [fetchProviders, fetchAIConfig, fetchSystemConfig, fetchProfile, fetchDomainConfig, fetchApiKeys, fetchTeams, fetchNotifPrefs]);

  useEffect(() => {
    if (aiData?.providers) {
      const urls: Record<string, string> = {};
      const models: Record<string, string> = {};
      aiData.providers.forEach((p: any) => {
        if (p.base_url) urls[p.id] = p.base_url;
        if (p.model) models[p.id] = p.model;
      });
      setAiUrls(prev => {
        const next = { ...urls };
        // Preserve user edits for keys that already exist in state
        Object.keys(prev).forEach(key => {
          if (prev[key]) next[key] = prev[key];
        });
        return next;
      });
      setAiModels(prev => {
        const next = { ...models };
        Object.keys(prev).forEach(key => {
          if (prev[key]) next[key] = prev[key];
        });
        return next;
      });
    }
  }, [aiData]);

  const handleCreateApiKey = async () => {
      try {
          const res = await coreApi.createApiKey(newKeyName || 'CLI Token');
          setGeneratedKey(res.key);
          setNewKeyName("");
          fetchApiKeys();
          toast({ title: "API Key Created", description: "Copy it now, you won't see it again." });
      } catch (err) {
          toast({ title: "Error", description: "Failed to create API key", variant: "destructive" });
      }
  };

  const handleRevokeKey = async (id: number) => {
      try {
          await coreApi.revokeApiKey(id);
          fetchApiKeys();
          toast({ title: "API Key Revoked" });
      } catch (err) {
          toast({ title: "Error", description: "Failed to revoke key", variant: "destructive" });
      }
  };

  const handleInvite = async () => {
      let activeTeamId = teams.length > 0 ? teams[0].id : null;
      
      if (!activeTeamId) {
          try {
              const newTeam = await teamsApi.create("My Team");
              activeTeamId = newTeam.id;
              setTeams([newTeam]);
          } catch (err) {
              toast({ title: "Error", description: "Failed to create default team to invite member.", variant: "destructive" });
              return;
          }
      }

      try {
          await teamsApi.inviteMember(activeTeamId, inviteEmail, inviteRole);
          toast({ title: "Invitation Sent", description: `Invited ${inviteEmail}` });
          setInviteEmail("");
          // Refresh members
          const members = await teamsApi.members(activeTeamId);
          setTeamMembers(members);
      } catch (err: any) {
          toast({ 
              title: "Error", 
              description: err?.response?.data?.error || "Failed to invite member", 
              variant: "destructive" 
          });
      }
  };

  const handleRouteRecheck = async () => {
    setRecheckLoading(true);
    try {
      const result = await systemApi.routeRecheck();
      setRecheckLastRun(new Date().toISOString());
      toast({ title: "Route recheck triggered", description: result.message });
    } catch (err: any) {
      const msg = err?.response?.data?.error || err?.response?.data?.message || "Failed to trigger route recheck.";
      toast({ title: "Error", description: msg, variant: "destructive" });
    } finally {
      setRecheckLoading(false);
    }
  };

  const handleSaveDomainConfig = async () => {
    setSavingDomain(true);
    try {
      const payload: any = { ...domainForm };
      if (!cfTokenTouched) {
        delete payload.cloudflare_api_token;
      } else {
        payload.cloudflare_api_token = String(payload.cloudflare_api_token || '').trim();
      }
      const result = await systemApi.updateDomainConfig(payload);
      toast({ title: 'Domain Config Saved', description: result.message || 'Configuration applied.' });
      // Signal the rest of the UI that a domain change occurred
      window.dispatchEvent(new CustomEvent('DOMAIN_SYNC_TRIGGER', { detail: { domain: payload.domain, type: 'PLATFORM' } }));
      setCfTokenTouched(false);
      fetchDomainConfig();
    } catch (err: any) {
      const msg = err?.response?.data?.error || 'Failed to save domain config.';
      toast({ title: 'Error', description: msg, variant: 'destructive' });
    } finally {
      setSavingDomain(false);
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
      <div className="container mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10 relative z-10">
      <PageHeader
        title="Settings"
        description="Manage your account and preferences."
        icon={<SettingsIcon className="h-8 w-8 text-primary" />}
        breadcrumbs={[{ label: "Settings" }]}
        backHref="/dashboard"
      />

      <div className="sticky top-16 z-30 mb-6 rounded-xl border border-border/70 bg-background/80 p-3 backdrop-blur-md">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Settings Navigation</p>
        <nav className="mt-2 flex gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
          {SETTINGS_ROUTE_LINKS.map((item) => {
            const Icon = item.icon;
            const isActive = item.match(pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "inline-flex h-9 shrink-0 items-center gap-2 rounded-md border px-3 text-sm transition-colors",
                  isActive
                    ? "border-primary/40 bg-primary/10 text-primary"
                    : "border-border/70 text-muted-foreground hover:border-primary/20 hover:bg-muted/60 hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>

      <Tabs defaultValue="profile" className="space-y-6">
        <div className="rounded-xl border border-border/70 bg-card/80 p-2 backdrop-blur-sm">
          <TabsList className="h-auto w-full justify-start gap-2 overflow-x-auto bg-transparent p-0 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
            {SETTINGS_SECTIONS.map((section) => {
              const Icon = section.icon;
              return (
                <TabsTrigger
                  key={section.value}
                  value={section.value}
                  className="h-9 shrink-0 items-center gap-2 rounded-md border border-transparent px-3 text-sm data-[state=active]:border-primary/40 data-[state=active]:bg-primary/10 data-[state=active]:text-primary"
                >
                  <Icon className="h-4 w-4" />
                  {section.label}
                </TabsTrigger>
              );
            })}
          </TabsList>
        </div>

        <TabsContent value="profile">
          <Card>
            <CardHeader>
              <CardTitle>Profile Settings</CardTitle>
              <CardDescription>Update your personal information.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
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

        <TabsContent value="api-keys">
            <Card>
                <CardHeader>
                    <CardTitle>API Keys</CardTitle>
                    <CardDescription>Manage API keys for CI/CD and external access.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    {generatedKey && (
                        <div className="p-4 bg-green-500/10 border border-green-500/20 rounded-lg">
                            <p className="text-sm font-semibold text-green-600 mb-2">New API Key Generated</p>
                            <div className="flex items-center gap-2">
                                <code className="flex-1 p-2 bg-background border rounded font-mono text-sm">{generatedKey}</code>
                                <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(generatedKey)}>
                                    <Copy className="w-4 h-4" />
                                </Button>
                            </div>
                            <p className="text-xs text-muted-foreground mt-2">Save this key now. It won&apos;t be shown again.</p>
                        </div>
                    )}

                    <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                        <div className="space-y-2 flex-1">
                            <Label>New Key Name</Label>
                            <Input value={newKeyName} onChange={e => setNewKeyName(e.target.value)} placeholder="e.g. GitHub Actions" />
                        </div>
                        <Button onClick={handleCreateApiKey}>Create Key</Button>
                    </div>

                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Name</TableHead>
                                <TableHead>Prefix</TableHead>
                                <TableHead>Created</TableHead>
                                <TableHead>Last Used</TableHead>
                                <TableHead></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {apiKeys.map(key => (
                                <TableRow key={key.id}>
                                    <TableCell className="font-medium">{key.name}</TableCell>
                                    <TableCell className="font-mono text-xs">{key.prefix}...</TableCell>
                                    <TableCell>{new Date(key.created_at).toLocaleDateString()}</TableCell>
                                    <TableCell>{key.last_used ? new Date(key.last_used).toLocaleDateString() : 'Never'}</TableCell>
                                    <TableCell>
                                        <Button variant="ghost" size="sm" onClick={() => handleRevokeKey(key.id)} className="text-red-500 hover:text-red-600">
                                            <Trash2 className="w-4 h-4" />
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {apiKeys.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center py-6 text-muted-foreground">No API keys found.</TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </TabsContent>
        <TabsContent value="notifications">
          <AlertsTab />
        </TabsContent>


                <TabsContent value="team">
          <TeamsTab />
        </TabsContent>
        <TabsContent value="security">
          <SecurityTab />
        </TabsContent>
        <TabsContent value="platform">
          <PlatformSettingsTab />
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
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
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
                  {aiData?.mode === 'senate_committee' && " - Providers debate and vote on each answer."}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* Mode Badge */}
                <div className="flex items-center gap-3">
                  <Badge variant={aiData?.mode === 'senate_committee' ? 'default' : aiData?.mode === 'solo' ? 'secondary' : 'outline'}>
                    {aiData?.mode === 'senate_committee' ? 'Senate Committee' : aiData?.mode === 'solo' ? 'Solo Mode' : 'Mock Mode'}
                  </Badge>
                  <span className="text-sm text-muted-foreground">
                    {aiData?.active_count || 0} of {aiData?.total_available || 0} active
                  </span>
                </div>

                {/* Provider Grid - Editable */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {aiData?.providers?.map((p: any) => {
                    const modelOptions: Record<string, string[]> = {
                      openai: ['gpt-4o-mini', 'gpt-4o', 'gpt-4-turbo', 'gpt-3.5-turbo', 'o1-mini', 'o1-preview'],
                      grok: ['grok-3-mini', 'grok-3', 'grok-2', 'grok-beta'],
                      gemini: ['gemini-2.0-flash', 'gemini-2.0-pro', 'gemini-1.5-flash', 'gemini-1.5-pro'],
                      claude: ['claude-sonnet-4-20250514', 'claude-opus-4-20250514', 'claude-3-5-sonnet-20241022', 'claude-3-haiku-20240307'],
                      openrouter: ['openrouter/auto', 'openai/gpt-4o', 'anthropic/claude-3.5-sonnet'],
                      groq: ['llama-3.3-70b-versatile', 'llama3-70b-8192', 'mixtral-8x7b-32768'],
                      alibaba: ['qwen-max', 'qwen-plus', 'qwen-turbo'],
                      deepseek: ['deepseek-coder', 'deepseek-chat'],
                      jules: ['jules-latest', 'jules-pro'],
                      localllm: ['local-model'],
                      smslycloud: ['smsly-latest'],
                      freemodel: ['gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo', 'claude-3.5-sonnet', 'llama-3.1-70b'],
                      opencode: ['opencode-latest', 'gpt-4o', 'claude-sonnet-4-20250514'],
                      mistral: ['mistral-small-latest', 'mistral-medium-latest', 'mistral-large-latest', 'codestral-latest', 'pixtral-large-latest', 'ministral-8b-latest'],
                      nvidia: ['nvidia/llama-3.1-nemotron-70b-instruct', 'nvidia/nemotron-4-340b-instruct', 'meta/llama-3.1-8b-instruct', 'mistralai/mixtral-8x22b-instruct-v0.1'],
                      cloudflare: ['@cf/meta/llama-3.1-8b-instruct', '@cf/meta/llama-3.3-70b-instruct', '@cf/qwen/qwen3-30b-a3b-fp8', '@cf/deepseek-ai/deepseek-r1-distill-qwen-32b', '@cf/mistral/mistral-small-3.1-24b-instruct']
                    };
                    const hasUrl = ['jules', 'localllm', 'freemodel', 'opencode', 'mistral', 'nvidia', 'cloudflare'].includes(p.id);
                    
                    return (
                      <div
                        key={p.id}
                        className={`p-4 rounded-xl border-2 transition-all ${
                          p.configured
                            ? 'border-emerald-500/50 bg-emerald-500/5 shadow-sm'
                            : 'border-border bg-card'
                        }`}
                      >
                        <div className="flex items-center justify-between mb-3">
                          <div className="font-bold text-sm uppercase tracking-tight">{p.name}</div>
                          {p.configured ? (
                            <Badge variant="outline" className="text-[10px] bg-emerald-500/10 text-emerald-500 border-emerald-500/30 font-bold uppercase">
                              ACTIVE
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="text-[10px] font-bold uppercase opacity-50">Inactive</Badge>
                          )}
                        </div>

                        {/* API Key Input */}
                        <div className="space-y-1.5 mb-3">
                          <Label className="text-[10px] font-bold uppercase text-muted-foreground tracking-wider">API Key</Label>
                          <Input
                            type="password"
                            placeholder={p.configured ? 'Configured key (hidden)' : 'Enter API key...'}
                            className="h-9 text-xs"
                            value={aiKeys[p.id] || ''}
                            onChange={(e) => setAiKeys(prev => ({ ...prev, [p.id]: e.target.value }))}
                          />
                        </div>

                        <div className="grid grid-cols-1 gap-3">
                          {/* Model Selector */}
                          <div className="space-y-1.5">
                            <Label className="text-[10px] font-bold uppercase text-muted-foreground tracking-wider">Model</Label>
                            <div className="flex gap-2">
                              <select
                                className="flex-1 h-9 px-2 text-xs border rounded-md bg-background"
                                value={aiModels[p.id] || p.model || ''}
                                onChange={(e) => setAiModels(prev => ({ ...prev, [p.id]: e.target.value }))}
                              >
                                {(modelOptions[p.id] || [p.model]).map((m: string) => (
                                  <option key={m} value={m}>{m}</option>
                                ))}
                              </select>
                              <Input
                                placeholder="Custom Model"
                                className="w-1/2 h-9 text-xs"
                                onChange={(e) => setAiModels(prev => ({ ...prev, [p.id]: e.target.value }))}
                              />
                            </div>
                          </div>

                          {/* URL Input (if applicable) */}
                          {hasUrl && (
                            <div className="space-y-1.5">
                              <Label className="text-[10px] font-bold uppercase text-muted-foreground tracking-wider">Base URL</Label>
                              <Input
                                placeholder="https://api.example.com/v1"
                                className="h-9 text-xs font-mono"
                                value={aiUrls[p.id] || ''}
                                onChange={(e) => setAiUrls(prev => ({ ...prev, [p.id]: e.target.value }))}
                              />
                            </div>
                          )}
                        </div>

                        {p.balance && (
                          <div className="mt-3 pt-3 border-t border-border/50 flex justify-between items-center">
                             <span className="text-[10px] font-bold uppercase text-muted-foreground">Credits</span>
                             <span className="text-[11px] text-emerald-500 font-bold">{p.balance.balance}</span>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Save + Actions */}
                <div className="flex gap-3 flex-wrap pt-4 border-t border-border/50">
                  <Button
                    variant="default"
                    onClick={async () => {
                      setSaving(true);
                      try {
                        const data: Record<string, string> = {};
                        const allIds = ['openai', 'grok', 'gemini', 'claude', 'openrouter', 'groq', 'alibaba', 'deepseek', 'jules', 'localllm', 'smslycloud', 'freemodel', 'opencode', 'mistral', 'nvidia', 'cloudflare'];
                        allIds.forEach((id) => {
                          if (aiKeys[id]) data[`${id}_api_key`] = aiKeys[id];
                          if (aiModels[id]) data[`${id}_model`] = aiModels[id];
                          if (aiUrls[id]) data[`${id}_base_url`] = aiUrls[id];
                        });
                        await aiApi.updateProviders(data);
                        toast({ title: "AI Config Saved", description: "The Intelligence Senate has been updated." });
                        fetchAIConfig();
                      } catch (err) {
                        toast({ title: "Error", description: "Failed to update the Senate.", variant: "destructive" });
                      } finally {
                        setSaving(false);
                      }
                    }}
                    disabled={saving}
                    className="bg-primary hover:bg-primary/90 text-primary-foreground font-bold"
                  >
                    {saving ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Saving...</> : 'Apply Senate Changes'}
                  </Button>
                  <Button variant="outline" onClick={handleTestAI} disabled={testingAI} className="font-bold">
                    {testingAI ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
                    Test Committee
                  </Button>
                  <Button variant="ghost" onClick={fetchAIConfig} disabled={loadingAI} className="font-bold">
                    <Loader2 className={`mr-2 h-4 w-4 ${loadingAI ? 'animate-spin' : ''}`} />
                    Sync
                  </Button>
                </div>

                <p className="text-xs text-muted-foreground">
                  Set keys and models above, or via env vars (OPENAI_API_KEY, GROK_API_KEY, GEMINI_API_KEY, CLAUDE_API_KEY, JULES_API_KEY, FREEMODEL_API_KEY, OPENCODE_API_KEY, MISTRAL_API_KEY, NVIDIA_API_KEY, CLOUDFLARE_API_KEY), or admin panel.
                </p>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
        {/* Auto-Scaling Configuration Tab */}
        <TabsContent value="autoscaling">
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><Cloud className="h-5 w-5 text-sky-500" /> Auto-Scaling Configuration</CardTitle>
                <CardDescription>
                  These environment variables control how the SMSLY autoscaler adjusts replicas across your services.
                  They are set in your <code className="text-xs font-mono bg-muted px-1 rounded">.env</code> file on the host.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Variable</TableHead>
                      <TableHead>Purpose</TableHead>
                      <TableHead>Current Value</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    <TableRow>
                      <TableCell className="font-mono text-xs">SCALE_MAX_REPLICAS</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        Maximum number of replica containers allowed per service
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="font-mono">
                          {systemConfig?.SCALE_MAX_REPLICAS ?? 'Not set'}
                        </Badge>
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-mono text-xs">SCALE_CPU_HIGH</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        CPU usage percentage above which a new replica is spawned
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="font-mono">
                          {systemConfig?.SCALE_CPU_HIGH != null ? `${systemConfig.SCALE_CPU_HIGH}%` : 'Not set'}
                        </Badge>
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell className="font-mono text-xs">SCALE_COOLDOWN_MIN</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        Minimum minutes between consecutive scale-up operations
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="font-mono">
                          {systemConfig?.SCALE_COOLDOWN_MIN != null ? `${systemConfig.SCALE_COOLDOWN_MIN} min` : 'Not set'}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
                <p className="text-xs text-muted-foreground mt-4">
                  These values are read from the <code className="font-mono bg-muted px-1 rounded">.env</code> file at startup. To change them, edit the file and restart the SMSLY platform.
                </p>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* Cloud Storage Tab */}
        <TabsContent value="cloud-storage">
          <CloudStorageTab />
        </TabsContent>

        {/* OAuth Configuration Tab */}
        <TabsContent value="oauth">
            <div className="space-y-6">
              <GitIntegrationCard provider="github" />
              <GitIntegrationCard provider="gitlab" />
              <GitIntegrationCard provider="bitbucket" />
              <WebhookConfigCard />
              <OAuthTab />
          </div>
        </TabsContent>

        {/* Cross-Master Backup Keys Tab */}
        <TabsContent value="backups">
          <BackupKeysTab />
        </TabsContent>

        {/* Infrastructure Tab */}
        <TabsContent value="infra">
          {systemConfig ? (
            <div className="space-y-6">
              {/* Domain & SSL Configuration */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Globe className="h-5 w-5 text-cyan-500" /> Domain & SSL</CardTitle>
                  <CardDescription>Configure your domain, SSL certificates, and wildcard subdomains for deployed services.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <Label>Domain</Label>
                      <Input
                        placeholder="cloud.example.com"
                        value={domainForm.domain}
                        onChange={(e) => setDomainForm(prev => ({ ...prev, domain: e.target.value }))}
                      />
                      <p className="text-xs text-muted-foreground">Leave empty for IP-only mode</p>
                    </div>
                    <div className="space-y-2">
                      <Label>Server IP</Label>
                      <Input
                        placeholder="Auto-detected"
                        value={domainForm.server_ip}
                        onChange={(e) => setDomainForm(prev => ({ ...prev, server_ip: e.target.value }))}
                      />
                      <p className="text-xs text-muted-foreground">Public IPv4 of this server</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-4 py-2">
                    <div className="flex items-center gap-2">
                      <Button
                        variant={domainForm.use_ssl ? "default" : "outline"}
                        size="sm"
                        onClick={() => setDomainForm(prev => ({ ...prev, use_ssl: !prev.use_ssl }))}
                      >
                        <Lock className="h-3 w-3 mr-1" />
                        {domainForm.use_ssl ? "SSL Enabled" : "SSL Disabled"}
                      </Button>
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        variant={domainForm.wildcard_subdomains ? "default" : "outline"}
                        size="sm"
                        onClick={() => setDomainForm(prev => ({ ...prev, wildcard_subdomains: !prev.wildcard_subdomains }))}
                        disabled={!domainForm.use_ssl}
                      >
                        {domainForm.wildcard_subdomains ? "Wildcard Subdomains Enabled" : "Wildcard Subdomains Disabled"}
                      </Button>
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        variant={domainForm.enable_crowdsec_waf ? "default" : "outline"}
                        size="sm"
                        onClick={() => setDomainForm(prev => ({ ...prev, enable_crowdsec_waf: !prev.enable_crowdsec_waf }))}
                      >
                        <Shield className="h-3 w-3 mr-1" />
                        {domainForm.enable_crowdsec_waf ? "CrowdSec WAF Enabled" : "CrowdSec WAF Disabled"}
                      </Button>
                    </div>
                    {domainConfig && (
                      <Badge variant={domainConfig.caddy_status === 'applied' ? 'default' : domainConfig.caddy_status === 'error' ? 'destructive' : 'secondary'}>
                        Caddy: {domainConfig.caddy_status}
                      </Badge>
                    )}
                  </div>

                  {domainForm.use_ssl && domainForm.wildcard_subdomains && (
                    <div className="space-y-2">
                      <Label>Cloudflare API Token</Label>
                      <div className="flex gap-2">
                        <Input
                          type={showCfToken ? "text" : "password"}
                          placeholder={domainConfig?.cloudflare_api_token_set ? "Configured token (hidden)" : "Enter Cloudflare API Token"}
                          value={domainForm.cloudflare_api_token}
                          onChange={(e) => {
                            setCfTokenTouched(true);
                            setDomainForm(prev => ({ ...prev, cloudflare_api_token: e.target.value }));
                          }}
                          className="flex-1"
                        />
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setCfTokenTouched(true);
                            setDomainForm(prev => ({ ...prev, cloudflare_api_token: '' }));
                          }}
                        >
                          Clear
                        </Button>
                        <Button variant="ghost" size="icon" onClick={() => setShowCfToken(!showCfToken)}>
                          {showCfToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                        </Button>
                      </div>
                      <p className="text-xs text-muted-foreground">Required for wildcard SSL. Create in Cloudflare &gt; API Tokens &gt; Edit Zone DNS.</p>
                    </div>
                  )}

                  <div className="flex gap-3">
                    <Button onClick={handleSaveDomainConfig} disabled={savingDomain}>
                      {savingDomain ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Applying...</> : 'Save & Apply'}
                    </Button>
                    <Button variant="outline" onClick={fetchDomainConfig}>
                      Refresh
                    </Button>
                  </div>
                </CardContent>
              </Card>

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
                  <CardDescription>Docker image registry for deployments. Supports internal and external registries.</CardDescription>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader><TableRow><TableHead>Variable</TableHead><TableHead>Value</TableHead><TableHead>Status</TableHead></TableRow></TableHeader>
                    <TableBody>
                      <TableRow>
                        <TableCell className="font-mono">REGISTRY_URL</TableCell>
                        <TableCell>{systemConfig.CONTAINER_REGISTRY_URL || "Not set"}</TableCell>
                        <TableCell><Badge variant={systemConfig.CONTAINER_REGISTRY_URL ? "default" : "destructive"}>{systemConfig.CONTAINER_REGISTRY_URL ? "Configured" : "Missing"}</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">REGISTRY_USER</TableCell>
                        <TableCell>{systemConfig.REGISTRY_USER}</TableCell>
                        <TableCell><Badge variant={systemConfig.REGISTRY_USER ? "default" : "secondary"}>{systemConfig.REGISTRY_USER || "Not set"}</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">REGISTRY_PASSWORD</TableCell>
                        <TableCell>{systemConfig.REGISTRY_PASSWORD_SET ? "Set" : "Not set"}</TableCell>
                        <TableCell><Badge variant={systemConfig.REGISTRY_PASSWORD_SET ? "default" : "secondary"}>{systemConfig.REGISTRY_PASSWORD_SET ? "Secure" : "Missing"}</Badge></TableCell>
                      </TableRow>
                      <TableRow>
                        <TableCell className="font-mono">REGISTRY_TYPE</TableCell>
                        <TableCell>{(() => {
                          const url = systemConfig.CONTAINER_REGISTRY_URL || "";
                          if (url.startsWith("registry:") || url.startsWith("127.") || url.startsWith("localhost")) return "Internal (Docker DNS)";
                          return "External";
                        })()}</TableCell>
                        <TableCell><Badge variant="outline">Auto-detected</Badge></TableCell>
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

              {/* Route Recheck */}
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><SettingsIcon className="h-5 w-5 text-green-500" /> Route Recheck</CardTitle>
                  <CardDescription>Re-check network routes across the mesh to ensure optimal routing and connectivity.</CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-4">
                    <p className="text-sm text-muted-foreground">
                      Trigger a fresh scan of all mesh network routes. This can help resolve connectivity
                      issues and ensure traffic is flowing through the best available paths.
                    </p>
                    <div className="flex items-center gap-3">
                      <Button onClick={handleRouteRecheck} disabled={recheckLoading}>
                        {recheckLoading ? (
                          <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Rechecking...</>
                        ) : (
                          'Recheck Routes'
                        )}
                      </Button>
                      {recheckLastRun && (
                        <span className="text-xs text-muted-foreground">
                          Last recheck: {new Date(recheckLastRun).toLocaleString()}
                        </span>
                      )}
                    </div>
                  </div>
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

        <TabsContent value="registry">
          <div className="space-y-8">
            <RegistryCredentialsTab />
            <ScopedRegistryTab
              title="Scoped Registries"
              description="Registry configurations attached to Organizations, Teams, or Projects. These override the platform default for deployments under their scope."
            />
          </div>
        </TabsContent>

        <TabsContent value="maintenance">
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><Cloud className="h-5 w-5 text-red-500" /> System Maintenance</CardTitle>
                <CardDescription>Perform dangerous maintenance actions on the host server. These actions run asynchronously in the background.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="flex flex-col gap-4 rounded-lg border p-4 bg-muted/20">
                  <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
                    <div className="space-y-1">
                      <h4 className="text-sm font-medium">Clear Orphaned Containers</h4>
                      <p className="text-xs text-muted-foreground">Forcefully delete all stale deployment containers, old AI routers, and unused project addons to free up server RAM and CPU. This will NOT affect running databases.</p>
                      {maintenanceTasks.clear.message && (
                        <p className={cn("text-xs", maintenanceTasks.clear.status === "error" ? "text-destructive" : "text-muted-foreground")}>
                          {maintenanceTasks.clear.message}
                        </p>
                      )}
                    </div>
                    <Button
                      variant="destructive"
                      disabled={maintenanceTasks.clear.status === "queued" || maintenanceTasks.clear.status === "running"}
                      onClick={() => handleMaintenanceAction("clear")}
                      className="w-full sm:w-auto"
                    >
                      {renderMaintenanceButtonContent("clear", "Clear System")}
                    </Button>
                  </div>
                  <div className="flex flex-col justify-between gap-4 border-t pt-4 sm:flex-row sm:items-center">
                    <div className="space-y-1">
                      <h4 className="text-sm font-medium">Sync Proxy Routing</h4>
                      <p className="text-xs text-muted-foreground">Force Traefik/Caddy to reload their configurations and discover new backend IP addresses. Useful if you are encountering 502 Bad Gateway errors.</p>
                      {maintenanceTasks.refresh.message && (
                        <p className={cn("text-xs", maintenanceTasks.refresh.status === "error" ? "text-destructive" : "text-muted-foreground")}>
                          {maintenanceTasks.refresh.message}
                        </p>
                      )}
                    </div>
                    <Button
                      variant="outline"
                      disabled={maintenanceTasks.refresh.status === "queued" || maintenanceTasks.refresh.status === "running"}
                      onClick={() => handleMaintenanceAction("refresh")}
                      className="w-full sm:w-auto"
                    >
                      {renderMaintenanceButtonContent("refresh", "Sync Proxy")}
                     </Button>
                   </div>
                   <div className="flex flex-col justify-between gap-4 border-t pt-4 sm:flex-row sm:items-center">
                     <div className="space-y-1">
                       <h4 className="text-sm font-medium">Update Platform</h4>
                       <p className="text-xs text-muted-foreground">This asks the host updater to pull the latest code and rebuild services. The dashboard may briefly disconnect.</p>
                       {maintenanceTasks.update.message && (
                         <p className={cn("text-xs", maintenanceTasks.update.status === "error" ? "text-destructive" : "text-muted-foreground")}>
                           {maintenanceTasks.update.message}
                         </p>
                       )}
                     </div>
                     <Button
                       variant="default"
                       disabled={maintenanceTasks.update.status === "queued" || maintenanceTasks.update.status === "running"}
                       onClick={() => handleMaintenanceAction("update")}
                       className="w-full sm:w-auto"
                     >
                       {renderMaintenanceButtonContent("update", "Update Platform")}
                     </Button>
                   </div>
                   {maintenanceTasks.update.taskId && (
                     <UpdateTerminalStream updateId={maintenanceTasks.update.taskId} />
                   )}
                 </div>
                 
                 {/* Cancel button */}
                 <div className="flex justify-end mt-4">
                   <Link href="/dashboard">
                     <Button variant="outline">Cancel</Button>
                   </Link>
                  </div>
                </CardContent>
              </Card>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardShell>
  );
}
