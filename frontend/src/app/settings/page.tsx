"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import {
  Settings as SettingsIcon, Users, Cloud, Globe,
  Server, CreditCard, Activity, GitBranch, HardDrive,
} from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { DashboardShell } from "@/components/layout/DashboardShell";
import { Separator } from "@/components/ui/separator";

import { ProfileTab } from "@/components/settings/ProfileTab";
import { ApiKeysTab } from "@/components/settings/ApiKeysTab";
import { AlertsTab } from "@/components/settings/AlertsTab";
import { TeamsTab } from "@/components/settings/TeamsTab";
import { SecurityTab } from "@/components/settings/SecurityTab";
import { MtlsTab } from "@/components/settings/MtlsTab";
import { PlatformSettingsTab } from "@/components/settings/PlatformSettingsTab";
import { ProvidersTab } from "@/components/settings/ProvidersTab";
import { AiTab } from "@/components/settings/AiTab";
import { AuditLogsTab } from "@/components/settings/AuditLogsTab";
import { PlatformConfigTab } from "@/components/settings/PlatformConfigTab";
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

const SETTINGS_SECTIONS = [
  {
    value: "account",
    label: "Account",
    icon: Users,
    tooltip: "Profile, password, 2FA, API keys, and team management",
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
    tooltip: "Global settings, autoscaling, mTLS, and AI providers",
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

function SettingsContent() {
  const searchParams = useSearchParams();
  const defaultTab = searchParams.get("tab") || "account";

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

        <Tabs defaultValue={defaultTab} className="space-y-6">
          <div className="rounded-xl border border-border/70 bg-card/80 p-2 backdrop-blur-sm">
            <TabsList className="h-auto w-full justify-start gap-2 overflow-x-auto bg-transparent p-0 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
              {SETTINGS_SECTIONS.map((section) => {
                const Icon = section.icon;
                return (
                  <TabsTrigger
                    key={section.value}
                    value={section.value}
                    title={section.tooltip}
                    className="h-9 shrink-0 items-center gap-2 rounded-md border border-transparent px-3 text-sm data-[state=active]:border-primary/40 data-[state=active]:bg-primary/10 data-[state=active]:text-primary"
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
                <WebhookConfigCard provider="github" />
                <OAuthTab provider="github" />
              </div>
              <Separator />

              {/* GitLab */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground">GitLab</h3>
                <GitIntegrationCard provider="gitlab" />
                <WebhookConfigCard provider="gitlab" />
                <OAuthTab provider="gitlab" />
              </div>
              <Separator />

              {/* Bitbucket */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground">Bitbucket</h3>
                <GitIntegrationCard provider="bitbucket" />
                <WebhookConfigCard provider="bitbucket" />
                <OAuthTab provider="bitbucket" />
              </div>
              <Separator />

              {/* Google */}
              <div className="space-y-4">
                <h3 className="text-lg font-semibold text-foreground">Google</h3>
                <GitIntegrationCard provider="google" />
                <OAuthTab provider="google" />
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

          {/* Infrastructure */}
          <TabsContent value="infra">
            <div className="space-y-8">
              <InfraTab />
              <Separator />
              <DatabaseReplicasTab />
            </div>
          </TabsContent>

          {/* Platform */}
          <TabsContent value="platform">
            <div className="space-y-8">
              <PlatformSettingsTab />
              <Separator />
              <PlatformConfigTab />
              <Separator />
              <MtlsTab />
              <Separator />
              <AiTab />
            </div>
          </TabsContent>

          {/* Storage & Backups */}
          <TabsContent value="storage">
            <div className="space-y-8">
              <CloudStorageTab />
              <Separator />
              <BackupKeysTab />
            </div>
          </TabsContent>

          {/* Operations */}
          <TabsContent value="ops">
            <div className="space-y-8">
              <AlertsTab />
              <Separator />
              <AuditLogsTab />
              <Separator />
              <MaintenanceTab />
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
