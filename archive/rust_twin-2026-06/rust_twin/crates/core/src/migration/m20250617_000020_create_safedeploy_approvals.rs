use sea_orm_migration::prelude::*;

use super::m20250616_000004_create_deployments::Deployments;
use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateSafedeployApprovals;

#[async_trait::async_trait]
impl MigrationTrait for CreateSafedeployApprovals {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(SafedeployApprovals::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(SafedeployApprovals::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(SafedeployApprovals::DeploymentId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SafedeployApprovals::ApproverId)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SafedeployApprovals::Status)
                            .string_len(20)
                            .not_null()
                            .default("pending"),
                    )
                    .col(
                        ColumnDef::new(SafedeployApprovals::Reason)
                            .text()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(SafedeployApprovals::ExpiresAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SafedeployApprovals::ActedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(SafedeployApprovals::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_safedeployapproval_deployment_id")
                            .from(SafedeployApprovals::Table, SafedeployApprovals::DeploymentId)
                            .to(Deployments::Table, Deployments::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_safedeployapproval_approver_id")
                            .from(SafedeployApprovals::Table, SafedeployApprovals::ApproverId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::NoAction),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(SafedeployApprovals::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum SafedeployApprovals {
    #[sea_orm(iden = "deployments_safedeployapproval")]
    Table,
    Id,
    DeploymentId,
    ApproverId,
    Status,
    Reason,
    ExpiresAt,
    ActedAt,
    CreatedAt,
}
