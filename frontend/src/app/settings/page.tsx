"use client";

import { Settings as SettingsIcon, Key, Users, Cloud, Bell, Shield, Lock, Globe, Sparkles, Server, Database } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/ui/page-header";
import { DashboardShell } from "@/components/layout/DashboardShell";

import { ProfileTab } from "@/components/settings/ProfileTab";
import { ApiKeysTab } from "@/components/settings/ApiKeysTab";
import { AlertsTab } from "@/components/settings/AlertsTab";
import { TeamsTab } from "@/components/settings/TeamsTab";
import { SecurityTab } from "@/components/settings/SecurityTab";
import { MtlsTab } from "@/components/settings/MtlsTab";
import { PlatformSettingsTab } from "@/components/settings/PlatformSettingsTab";
import { ProvidersTab } from "@/components/settings/ProvidersTab";
import { AiTab } from "@/components/settings/AiTab";
import { BillingTab } from "@/components/settings/BillingTab";
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

const SETTINGS_SECTIONS = [
  { value: "profile", label: "General", icon: SettingsIcon },
  { value: "api-keys", label: "API Keys", icon: Key },
  { value: "team", label: "Team", icon: Users },
  { value: "billing", label: "Billing", icon: Cloud },
  { value: "notifications", label: "Alerts", icon: Bell },
  { value: "security", label: "Security", icon: Shield },
  { value: "mtls", label: "mTLS", icon: Lock },
  { value: "providers", label: "Cloud", icon: Cloud },
  { value: "ai", label: "AI", icon: Sparkles },
  { value: "oauth", label: "OAuth", icon: Key },
  { value: "autoscaling", label: "Platform Config", icon: Cloud },
  { value: "cloud-storage", label: "Cloud Storage", icon: Cloud },
  { value: "backups", label: "Backups", icon: Cloud },
  { value: "database-replicas", label: "Database", icon: Database },
  { value: "infra", label: "Infra", icon: Server },
  { value: "audit-logs", label: "Audit Logs", icon: Shield },
  { value: "platform", label: "Platform", icon: Globe },
  { value: "registry", label: "Registry", icon: Cloud },
  { value: "maintenance", label: "Maintenance", icon: Server },
] as const;

export default function SettingsPage() {
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

          <TabsContent value="profile"><ProfileTab /></TabsContent>
          <TabsContent value="api-keys"><ApiKeysTab /></TabsContent>
          <TabsContent value="notifications"><AlertsTab /></TabsContent>
          <TabsContent value="team"><TeamsTab /></TabsContent>
          <TabsContent value="security"><SecurityTab /></TabsContent>
          <TabsContent value="mtls"><MtlsTab /></TabsContent>
          <TabsContent value="platform"><PlatformSettingsTab /></TabsContent>
          <TabsContent value="providers"><ProvidersTab /></TabsContent>
          <TabsContent value="ai"><AiTab /></TabsContent>
          <TabsContent value="billing"><BillingTab /></TabsContent>
          <TabsContent value="audit-logs"><AuditLogsTab /></TabsContent>
          <TabsContent value="autoscaling"><PlatformConfigTab /></TabsContent>
          <TabsContent value="cloud-storage"><CloudStorageTab /></TabsContent>
          <TabsContent value="oauth">
            <div className="space-y-6">
              <GitIntegrationCard provider="github" />
              <GitIntegrationCard provider="gitlab" />
              <GitIntegrationCard provider="bitbucket" />
              <GitIntegrationCard provider="google" />
              <WebhookConfigCard />
              <OAuthTab />
            </div>
          </TabsContent>
          <TabsContent value="backups"><BackupKeysTab /></TabsContent>
          <TabsContent value="database-replicas"><DatabaseReplicasTab /></TabsContent>
          <TabsContent value="infra"><InfraTab /></TabsContent>
          <TabsContent value="registry">
            <div className="space-y-8">
              <RegistryCredentialsTab />
              <ScopedRegistryTab title="Scoped Registries" description="Registry configurations attached to Organizations, Teams, or Projects. These override the platform default for deployments under their scope." />
            </div>
          </TabsContent>
          <TabsContent value="maintenance"><MaintenanceTab /></TabsContent>
        </Tabs>
      </div>
    </DashboardShell>
  );
}
