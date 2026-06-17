use sea_orm_migration::prelude::*;

use super::m20250616_000006_create_teams::Teams;

#[derive(DeriveMigrationName)]
pub struct CreateSafedeployPolicies;

#[async_trait::async_trait]
impl MigrationTrait for CreateSafedeployPolicies {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(SafedeployPolicies::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(SafedeployPolicies::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(SafedeployPolicies::TeamId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SafedeployPolicies::Name)
                            .string_len(150)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SafedeployPolicies::RequiredApprovers)
                            .integer()
                            .not_null()
                            .default(1),
                    )
                    .col(
                        ColumnDef::new(SafedeployPolicies::MinApproverRole)
                            .string_len(30)
                            .not_null()
                            .default("ADMIN"),
                    )
                    .col(
                        ColumnDef::new(SafedeployPolicies::BlockOnRecentFailures)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(SafedeployPolicies::MaxConcurrentDeploys)
                            .integer()
                            .not_null()
                            .default(1),
                    )
                    .col(
                        ColumnDef::new(SafedeployPolicies::IsActive)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(SafedeployPolicies::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SafedeployPolicies::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_safedeploypolicy_team_id")
                            .from(
                                SafedeployPolicies::Table,
                                SafedeployPolicies::TeamId,
                            )
                            .to(Teams::Table, Teams::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(SafedeployPolicies::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum SafedeployPolicies {
    #[sea_orm(iden = "deployments_safedeploypolicy")]
    Table,
    Id,
    TeamId,
    Name,
    RequiredApprovers,
    MinApproverRole,
    BlockOnRecentFailures,
    MaxConcurrentDeploys,
    IsActive,
    CreatedAt,
    UpdatedAt,
}
