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
        ]
    }
}
