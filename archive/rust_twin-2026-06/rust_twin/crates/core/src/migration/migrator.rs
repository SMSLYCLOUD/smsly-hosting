use sea_orm_migration::prelude::*;

pub struct Migrator;

#[async_trait::async_trait]
impl MigratorTrait for Migrator {
    fn migrations() -> Vec<Box<dyn MigrationTrait>> {
        vec![
            Box::new(super::m20250616_000001_create_users::CreateUsers),
            Box::new(super::m20250616_000002_create_projects::CreateProjects),
            Box::new(super::m20250616_000003_create_services::CreateServices),
            Box::new(super::m20250616_000004_create_deployments::CreateDeployments),
            Box::new(super::m20250616_000005_create_addons::CreateAddons),
            Box::new(super::m20250616_000006_create_teams::CreateTeams),
            Box::new(super::m20250616_000007_create_team_members::CreateTeamMembers),
            Box::new(super::m20250616_000008_create_api_keys::CreateApiKeys),
            Box::new(super::m20250616_000009_create_crons::CreateCrons),
            Box::new(
                super::m20250616_000010_create_environment_variables::CreateEnvironmentVariables,
            ),
            Box::new(
                super::m20250616_000011_create_platform_licenses::CreatePlatformLicenses,
            ),
            Box::new(super::m20250616_000012_create_usages::CreateUsages),
            // P-batch (2026-06-17)
            Box::new(super::m20250617_000013_create_plans::CreatePlans),
            Box::new(super::m20250617_000014_create_subscriptions::CreateSubscriptions),
            Box::new(super::m20250617_000015_create_invoices::CreateInvoices),
            Box::new(super::m20250617_000016_create_clusters::CreateClusters),
            Box::new(super::m20250617_000017_create_mesh_nodes::CreateMeshNodes),
            Box::new(super::m20250617_000018_create_node_elections::CreateNodeElections),
            Box::new(super::m20250617_000019_create_heartbeat_logs::CreateHeartbeatLogs),
            Box::new(super::m20250617_000020_create_safedeploy_approvals::CreateSafedeployApprovals),
            Box::new(super::m20250617_000021_create_transfer_logs::CreateTransferLogs),
            Box::new(super::m20250617_000022_create_backup_records::CreateBackupRecords),
            Box::new(super::m20250617_000023_create_tunnels::CreateTunnels),
            Box::new(super::m20250617_000024_create_webhooks::CreateWebhooks),
            Box::new(super::m20250617_000025_create_domains::CreateDomains),
            Box::new(super::m20250617_000026_add_deployment_status_check::AddDeploymentStatusCheck),
            Box::new(super::m20250617_000027_create_social_accounts::CreateSocialAccounts),
            Box::new(super::m20250617_000028_create_social_apps::CreateSocialApps),
            Box::new(super::m20250617_000029_create_social_tokens::CreateSocialTokens),
            Box::new(super::m20250617_000030_create_addon_templates::CreateAddonTemplates),
            Box::new(super::m20250617_000031_add_deployment_requester_id::AddDeploymentRequesterId),
        ]
    }
}
