"use client";

import React, { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Settings as SettingsIcon, Users, Cloud, Globe,
  Server, CreditCard, Activity, GitBranch, HardDrive,
  Brain, Building2,
} from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { Separator } from "@/components/ui/separator";

import { ProfileTab } from "@/components/settings/ProfileTab";
import { ApiKeysTab } from "@/components/settings/ApiKeysTab";
import { PreferencesTab } from "@/components/settings/PreferencesTab";
import { AlertsTab } from "@/components/settings/AlertsTab";
import { TeamsTab } from "@/components/settings/TeamsTab";
import { OrganizationsTab } from "@/components/settings/OrganizationsTab";
import { SecurityTab } from "@/components/settings/SecurityTab";
import { PrivacyTab } from "@/components/settings/PrivacyTab";
import { MtlsTab } from "@/components/settings/MtlsTab";
import { PlatformSettingsTab } from "@/components/settings/PlatformSettingsTab";
import { PlatformStorageOverview } from "@/components/settings/PlatformStorageOverview";
import { ProvidersTab } from "@/components/settings/ProvidersTab";
import { AiTab } from "@/components/settings/AiTab";
import { AuditLogsTab } from "@/components/settings/AuditLogsTab";
import { PlatformConfigTab } from "@/components/settings/PlatformConfigTab";
import { PerformanceTab } from "@/components/settings/PerformanceTab";
import { CloudStorageTab } from "@/components/settings/CloudStorageTab";
import { GitIntegrationCard } from "@/components/settings/GitIntegrationCard";
import { WebhookConfigCard } from "@/components/settings/WebhookConfigCard";
import { OAuthTab } from "@/components/settings/OAuthTab";
import BackupKeysTab from "@/components/settings/BackupKeysTab";
import { DatabaseReplicasTab } from "@/components/settings/DatabaseReplicasTab";
import { InfraTab } from "@/components/settings/InfraTab";
import { RegistryCredentialsTab } from "@/components/settings/RegistryCredentialsTab";
import { ScopedRegistryTab } from "@/components/settings/ScopedRegistryTab";
import { MaintenanceTab } from "@/components/settings/MaintenanceTab";
import { BillingTab } from "@/components/settings/BillingTab";
import { usePermissions } from "@/hooks/usePermissions";

const SETTINGS_SECTIONS = [
  {
    value: "account",
    label: "Account",
    icon: Users,
    tooltip: "Profile, password, and 2FA settings",
  },
  {
    value: "teams",
    label: "Teams",
    icon: Building2,
    tooltip: "Organizations, team members, and RBAC",
  },
  {
    value: "git",
    label: "Git & Deploy",
    icon: GitBranch,
    tooltip: "GitHub, GitLab, Bitbucket, and Google integrations with webhooks and OAuth",
  },
  {
    value: "cloud",
    label: "Cloud",
    icon: Cloud,
    tooltip: "Cloud providers (AWS, GCP) and Docker registry credentials",
  },
  {
    value: "infra",
    label: "Infrastructure",
    icon: Server,
    tooltip: "Domain, SSL, CrowdSec WAF, Cloudflare, and database replicas",
  },
  {
    value: "platform",
    label: "Platform",
    icon: Globe,
    tooltip: "Global settings, autoscaling, and mTLS",
  },
  {
    value: "ai",
    label: "AI",
    icon: Brain,
    tooltip: "AI providers, models, API keys, and engine testing",
  },
  {
    value: "storage",
    label: "Storage & Backups",
    icon: HardDrive,
    tooltip: "Cloud storage destinations (S3, GCS, Azure) and backup encryption keys",
  },
  {
    value: "ops",
    label: "Operations",
    icon: Activity,
    tooltip: "Alert rules, audit logs, and platform maintenance",
  },
  {
    value: "billing",
    label: "Billing",
    icon: CreditCard,
    tooltip: "Subscription, invoices, and resource usage",
  },
] as const;

const ADMIN_ONLY_TABS = new Set(["infra", "platform", "ai"]);

function SettingsContent() {
  const searchParams = useSearchParams();
  const { isStaff, isSuperuser } = usePermissions();
  const isAdmin = isStaff || isSuperuser;
  const visibleSections = SETTINGS_SECTIONS.filter(
    (section) => isAdmin || !ADMIN_ONLY_TABS.has(section.value)
  );
  const rawTab = searchParams.get("tab") || "account";
  const mappedTab = rawTab === "mtls" ? "platform" : rawTab;
  const resolvedTab = visibleSections.some((section) => section.value === mappedTab)
    ? mappedTab
    : "account";
  const [activeTab, setActiveTab] = useState(resolvedTab);
  const githubAppStatus = searchParams.get("github_app");

  useEffect(() => {
    setActiveTab(resolvedTab);
  }, [resolvedTab]);

  // Show a one-time success toast when returning from GitHub App install/creation
  // (backend redirects to /settings?tab=git&github_app=connected).
  useEffect(() => {
    if (!githubAppStatus) return;
    // Lazy import to avoid adding useToast to deps
    import("@/components/ui/use-toast").then(({ toast }) => {
      if (githubAppStatus === "connected") {
        toast({ title: "GitHub App connected", description: "Installation linked. Deploy will now use the App for webhooks and commit statuses." });
      } else if (githubAppStatus === "created") {
        toast({ title: "GitHub App created", description: "App credentials stored. Now install it on your repositories." });
      }
    });
    // Clean the URL so refresh doesn't re-toast
    const url = new URL(window.location.href);
    url.searchParams.delete("github_app");
    window.history.replaceState({}, "", url.toString());
  }, [githubAppStatus]);

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

        <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
          <div className="card-enterprise p-1">
            <TabsList className={`h-auto w-full flex overflow-x-auto xl:grid ${isAdmin ? "xl:grid-cols-10" : "xl:grid-cols-7"} gap-1 bg-transparent p-0`}>
              {visibleSections.map((section) => {
                const Icon = section.icon;
                return (
                  <TabsTrigger
                    key={section.value}
                    value={section.value}
                    title={section.tooltip}
                    className="h-9 shrink-0 items-center justify-center gap-2 rounded border border-transparent px-3 text-sm data-[state=active]:border-primary/30 data-[state=active]:bg-primary/5 data-[state=active]:text-primary"
                  >
                    <Icon className="h-4 w-4" />
                    {section.label}
                  </TabsTrigger>
                );
              })}
            </TabsList>
          </div>

          {/* Account */}
          <TabsContent value="account">
            <div className="space-y-8">
              <ProfileTab />
              <Separator />
              <SecurityTab />
              <Separator />
              <ApiKeysTab />
              <Separator />
              <PreferencesTab />
              <Separator />
              <PrivacyTab />
            </div>
          </TabsContent>

          {/* Teams */}
          <TabsContent value="teams">
            <div className="space-y-8">
              <OrganizationsTab />
              <Separator />
              <TeamsTab />
            </div>
          </TabsContent>

          {/* Git & Deploy */}
          <TabsContent value="git">
            <div className="space-y-10">
              {/* GitHub */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground">GitHub</h3>
                <GitIntegrationCard provider="github" />
                {isAdmin && (
                  <>
                    <WebhookConfigCard provider="github" />
                    <OAuthTab provider="github" />
                  </>
                )}
              </div>
              <Separator />

              {/* GitLab */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground">GitLab</h3>
                <GitIntegrationCard provider="gitlab" />
                {isAdmin && (
                  <>
                    <WebhookConfigCard provider="gitlab" />
                    <OAuthTab provider="gitlab" />
                  </>
                )}
              </div>
              <Separator />

              {/* Bitbucket */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground">Bitbucket</h3>
                <GitIntegrationCard provider="bitbucket" />
                {isAdmin && (
                  <>
                    <WebhookConfigCard provider="bitbucket" />
                    <OAuthTab provider="bitbucket" />
                  </>
                )}
              </div>
              <Separator />

              {/* Google */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground">Google</h3>
                <GitIntegrationCard provider="google" />
                {isAdmin && <OAuthTab provider="google" />}
              </div>
            </div>
          </TabsContent>

          {/* Cloud */}
          <TabsContent value="cloud">
            <div className="space-y-8">
              <ProvidersTab />
              <Separator />
              <RegistryCredentialsTab />
              <Separator />
              <ScopedRegistryTab
                title="Scoped Registries"
                description="Registry configurations attached to Organizations, Teams, or Projects. These override the platform default for deployments under their scope."
              />
            </div>
          </TabsContent>

          {/* Infrastructure (admin only) */}
          {isAdmin && (
            <TabsContent value="infra">
              <div className="space-y-8">
                <InfraTab />
                <Separator />
                <DatabaseReplicasTab />
              </div>
            </TabsContent>
          )}

          {/* Platform (admin only) */}
          {isAdmin && (
            <TabsContent value="platform">
              <div className="space-y-8">
                <PlatformStorageOverview />
                <Separator />
                <PlatformSettingsTab />
                <Separator />
                <PlatformConfigTab />
                <Separator />
                <PerformanceTab />
                <Separator />
                <MtlsTab />
              </div>
            </TabsContent>
          )}

          {/* AI (admin only) */}
          {isAdmin && (
            <TabsContent value="ai">
              <AiTab />
            </TabsContent>
          )}

          {/* Storage & Backups */}
          <TabsContent value="storage">
            <div className="space-y-8">
              <CloudStorageTab />
              {isSuperuser && (
                <>
                  <Separator />
                  <BackupKeysTab />
                </>
              )}
            </div>
          </TabsContent>

          {/* Operations */}
          <TabsContent value="ops">
            <div className="space-y-8">
              {isAdmin && (
                <>
                  <AlertsTab />
                  <Separator />
                </>
              )}
              <AuditLogsTab />
              {isAdmin && (
                <>
                  <Separator />
                  <MaintenanceTab />
                </>
              )}
            </div>
          </TabsContent>

          {/* Billing */}
          <TabsContent value="billing">
            <BillingTab />
          </TabsContent>
        </Tabs>
      </div>
    </DashboardShell>
  );
}

export default function SettingsPage() {
  return (
    <Suspense>
      <SettingsContent />
    </Suspense>
  );
}
